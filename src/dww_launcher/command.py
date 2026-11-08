"""Building source-port command lines from launch options."""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dww_launcher.wad import warp_args

DEHACKED_SUFFIXES = {".deh", ".bex"}

SKILLS = {
    1: "I'm too young to die",
    2: "Hey, not too rough",
    3: "Hurt me plenty",
    4: "Ultra-Violence",
    5: "Nightmare!",
}

# Common -complevel values understood by PrBoom+/DSDA-Doom and friends.
COMPLEVELS = {
    2: "Doom / Doom II (v1.9)",
    3: "Ultimate Doom",
    4: "Final Doom",
    9: "Boom",
    11: "MBF",
    21: "MBF21",
}


@dataclass
class LaunchOptions:
    port: str = ""
    iwad: str = ""
    pwads: list[str] = field(default_factory=list)
    map: str | None = None
    skill: int | None = None
    complevel: int | None = None
    fast: bool = False
    respawn: bool = False
    nomonsters: bool = False
    record: str | None = None  # demo path without extension; the port appends .lmp
    playdemo: str | None = None
    extra_args: str = ""


def resolve_executable(port: str | Path) -> Path:
    """Return the real executable for a port. Handles macOS .app bundles."""
    path = Path(port)
    if path.suffix.lower() == ".app" and path.is_dir():
        macos = path / "Contents" / "MacOS"
        candidate = macos / path.stem
        if candidate.exists():
            return candidate
        executables = sorted(p for p in macos.iterdir() if p.is_file()) if macos.is_dir() else []
        if executables:
            return executables[0]
    return path


def split_extra_args(text: str) -> list[str]:
    return shlex.split(text, posix=sys.platform != "win32") if text.strip() else []


def build_args(opts: LaunchOptions) -> list[str]:
    """Arguments passed to the source port (not including the executable)."""
    args: list[str] = []
    if opts.iwad:
        args += ["-iwad", opts.iwad]

    files = [p for p in opts.pwads if Path(p).suffix.lower() not in DEHACKED_SUFFIXES]
    dehs = [p for p in opts.pwads if Path(p).suffix.lower() in DEHACKED_SUFFIXES]
    if files:
        args += ["-file", *files]
    if dehs:
        args += ["-deh", *dehs]

    if opts.complevel is not None:
        args += ["-complevel", str(opts.complevel)]

    if opts.playdemo:
        args += ["-playdemo", opts.playdemo]
    else:
        if opts.map:
            args += ["-warp", *warp_args(opts.map)]
        if opts.skill is not None:
            args += ["-skill", str(opts.skill)]
        if opts.fast:
            args.append("-fast")
        if opts.respawn:
            args.append("-respawn")
        if opts.nomonsters:
            args.append("-nomonsters")
        if opts.record:
            args += ["-record", opts.record]

    args += split_extra_args(opts.extra_args)
    return args


def build_command(opts: LaunchOptions) -> list[str]:
    return [str(resolve_executable(opts.port)), *build_args(opts)]


def format_command(cmd: list[str]) -> str:
    """Human-readable, copy-pasteable version of a command."""
    if sys.platform == "win32":
        import subprocess

        return subprocess.list2cmdline(cmd)
    return shlex.join(cmd)
