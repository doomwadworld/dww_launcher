"""Minimal WAD file reader: header, lump directory, map list and IWAD detection."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

_MAP_RE = re.compile(r"^(E[1-9]M[1-9]|MAP[0-9]{2})$")


class WadError(Exception):
    """Raised when a file is not a readable WAD."""


@dataclass
class WadInfo:
    path: Path
    kind: str  # "IWAD" or "PWAD"
    lumps: list[str] = field(default_factory=list)
    maps: list[str] = field(default_factory=list)

    @property
    def is_iwad(self) -> bool:
        return self.kind == "IWAD"

    @property
    def is_doom1(self) -> bool:
        """True for the original episodic Doom IWADs (shareware, registered, Ultimate)."""
        return self.game in DOOM1_GAMES

    @property
    def game(self) -> str:
        """Best-guess human-readable name of the game this IWAD provides."""
        return identify_game(self.lumps, self.maps)


DOOM1_GAMES = {"Doom (shareware)", "Doom (registered)", "The Ultimate Doom"}

# Doom was released on 1993-12-10; by 1994-09-01 most mappers had moved on from v1.2.
V12_ERA_START = date(1993, 12, 10)
V12_ERA_END = date(1994, 9, 1)


def file_date(path: str | Path) -> date | None:
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime).date()
    except OSError:
        return None


def is_v12_era(path: str | Path) -> bool:
    """True if the file's modification date falls in the Doom v1.2 era."""
    d = file_date(path)
    return d is not None and V12_ERA_START <= d <= V12_ERA_END


def read_wad(path: str | Path) -> WadInfo:
    path = Path(path)
    try:
        with path.open("rb") as f:
            header = f.read(12)
            if len(header) != 12:
                raise WadError(f"{path.name}: file too small to be a WAD")
            ident, num_lumps, dir_offset = struct.unpack("<4sii", header)
            kind = ident.decode("ascii", errors="replace")
            if kind not in ("IWAD", "PWAD"):
                raise WadError(f"{path.name}: not a WAD file (header {ident!r})")
            if num_lumps < 0 or dir_offset < 0:
                raise WadError(f"{path.name}: corrupt WAD header")
            f.seek(dir_offset)
            directory = f.read(16 * num_lumps)
            if len(directory) != 16 * num_lumps:
                raise WadError(f"{path.name}: truncated lump directory")
    except OSError as exc:
        raise WadError(f"{path.name}: {exc}") from exc

    lumps = []
    for i in range(num_lumps):
        _, _, raw_name = struct.unpack_from("<ii8s", directory, i * 16)
        lumps.append(raw_name.split(b"\0", 1)[0].decode("ascii", errors="replace").upper())
    maps = list(dict.fromkeys(name for name in lumps if _MAP_RE.match(name)))
    return WadInfo(path=path, kind=kind, lumps=lumps, maps=maps)


def identify_game(lumps: list[str], maps: list[str]) -> str:
    lump_set = set(lumps)
    map_set = set(maps)
    if "FREEDOOM" in lump_set:
        return "Freedoom: Phase 2" if "MAP01" in map_set else "Freedoom: Phase 1"
    if "MAP01" in map_set:
        if "CAVERN7" in lump_set:
            return "Final Doom: TNT Evilution"
        if "WFALL1" in lump_set:
            return "Final Doom: The Plutonia Experiment"
        return "Doom II"
    if "E4M1" in map_set:
        return "The Ultimate Doom"
    if "E2M1" in map_set:
        return "Doom (registered)"
    if "E1M1" in map_set:
        return "Doom (shareware)"
    return "Unknown"


def warp_args(map_name: str) -> list[str]:
    """Convert a map lump name to -warp arguments: E2M5 -> ['2', '5'], MAP07 -> ['7']."""
    name = map_name.upper()
    m = re.fullmatch(r"E([1-9])M([1-9])", name)
    if m:
        return [m.group(1), m.group(2)]
    m = re.fullmatch(r"MAP([0-9]{2})", name)
    if m:
        return [str(int(m.group(1)))]
    raise ValueError(f"Not a valid map name: {map_name}")


def map_sort_key(name: str) -> tuple[int, int]:
    m = re.fullmatch(r"E([1-9])M([1-9])", name)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return (0, int(name[3:]))
