"""Reading Doom demo (.lmp) files: format, starting map, players, length, and port footer.

Supported layouts (verified against demos recorded by DSDA-Doom and Freedoom's own demos):

========================  ===========  ======================  =============
Format                    Version      Header                  Bytes per tic
========================  ===========  ======================  =============
Doom 1.0-1.2              0-4 (skill)  7                       4
Doom 1.4-1.9              104-110      13                      4
Doom 1.9 longtics         111          13                      5
Boom / MBF / PrBoom       200-214      109 (64 option bytes)   4 (5 if longtics)
MBF21                     221          90 (no compat byte,     5
                                       46 option bytes)
DSDA-Doom extended        0xFF prefix  prefix + inner header   stored in the prefix
========================  ===========  ======================  =============

Each tic holds one command per player in the game; the stream ends with a 0x80 marker.
PrBoom+/DSDA-Doom append a small PWAD "footer" with lumps such as PORTNAME and CMDLINE.
"""

from __future__ import annotations

import re
import shlex
import struct
from dataclasses import dataclass, field
from pathlib import Path

TICRATE = 35
END_MARKER = 0x80
SKILL_NAMES = {1: "ITYTD", 2: "HNTR", 3: "HMP", 4: "UV", 5: "NM"}

_DSDA_SIG = b"\xff\x1dDSDA\xe6"
_BOOM_SIGS = (b"\x1dBoom\xe6", b"\x1dMBF\xe6\x00")


class DemoError(Exception):
    pass


@dataclass
class DemoInfo:
    path: Path
    size: int
    version: int
    format: str
    skill: int  # 1-5
    episode: int
    map: int
    players: int
    tics: int | None  # None if the length couldn't be determined
    longtics: bool = False
    deathmatch: bool = False
    respawn: bool = False
    fast: bool = False
    nomonsters: bool = False
    complete: bool = True  # False if the end marker is missing (truncated/unfinished demo)
    footer: dict[str, str] = field(default_factory=dict)  # lump name -> text

    @property
    def seconds(self) -> float | None:
        return None if self.tics is None else self.tics / TICRATE

    @property
    def skill_name(self) -> str:
        return SKILL_NAMES.get(self.skill, str(self.skill))

    @property
    def port(self) -> str:
        return self.footer.get("PORTNAME", "")

    @property
    def command_line(self) -> str:
        return self.footer.get("CMDLINE", "")

    def _cmdline_values(self, option: str) -> list[str]:
        try:
            args = shlex.split(self.command_line)
        except ValueError:
            args = self.command_line.split()
        values, collecting = [], False
        for a in args:
            if a.startswith("-"):
                collecting = a.lower() == option
            elif collecting:
                values.append(a)
        return values

    @property
    def iwad(self) -> str:
        """IWAD named in the port's footer (e.g. ``doom2.wad``), if any."""
        values = self._cmdline_values("-iwad")
        return Path(values[0]).name if values else ""

    @property
    def files(self) -> list[str]:
        return [Path(v).name for v in self._cmdline_values("-file")]

    @property
    def map_name(self) -> str:
        """E2M5 / MAP07, or e.g. "E1M3 / MAP03" when Doom vs Doom II can't be told apart."""
        iwad = self.iwad.lower()
        doom2 = bool(re.search(r"doom2|tnt|plutonia|freedoom2|freedm|hacx|chex3d2", iwad))
        doom1 = bool(iwad) and not doom2
        if self.episode > 1 or doom1:
            return f"E{self.episode}M{self.map}"
        if doom2 or self.map > 9:  # Doom 1 maps only go up to 9
            return f"MAP{self.map:02d}"
        return f"E1M{self.map} / MAP{self.map:02d}"


def format_duration(seconds: float | None) -> str:
    """Demo-style time: ``m:ss.cc`` or ``h:mm:ss.cc``."""
    if seconds is None:
        return "?"
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}" if h else f"{m}:{s:02d}.{cs:02d}"


def _format_name(version: int) -> str:
    if version <= 4:
        return "Doom 1.0-1.2"
    if 104 <= version <= 110:
        return f"Doom v1.{version - 100}"
    if version == 111:
        return "Doom v1.9 (longtics)"
    return {200: "Boom 2.00", 201: "Boom 2.01", 202: "Boom 2.02", 203: "MBF",
            210: "PrBoom 2.1", 211: "PrBoom 2.2", 212: "PrBoom 2.3", 213: "PrBoom 2.4",
            214: "PrBoom+ (longtics)", 221: "MBF21"}.get(version, f"version {version}")


def _parse_footer(data: bytes) -> dict[str, str]:
    """PrBoom+/DSDA-Doom footer: a tiny PWAD of text lumps."""
    if len(data) < 12 or data[:4] != b"PWAD":
        return {}
    try:
        n, offset = struct.unpack_from("<ii", data, 4)
        lumps = {}
        for i in range(min(n, 64)):
            pos, size, raw = struct.unpack_from("<ii8s", data, offset + 16 * i)
            name = raw.split(b"\0", 1)[0].decode("ascii", "replace")
            text = data[pos:pos + size].split(b"\0", 1)[0].decode("utf-8", "replace").strip()
            lumps[name] = text
        return lumps
    except struct.error:
        return {}


def _walk(data: bytes, start: int, bpt: int, end_hint: int | None = None) -> tuple[int, bool]:
    """Return (tic count, found end marker) walking tic-by-tic from ``start``."""
    pos, tics = start, 0
    limit = len(data) if end_hint is None else min(end_hint, len(data))
    while pos < limit:
        if data[pos] == END_MARKER:
            return tics, True
        pos += bpt
        tics += 1
    return tics if pos == limit else tics - 1, False


def _best_walk(data: bytes, start: int, candidates: list[int]) -> tuple[int, int, bool]:
    """Pick the bytes-per-tic whose end marker lands on a plausible boundary."""
    fallback = None
    for bpt in candidates:
        tics, found = _walk(data, start, bpt)
        if found:
            end = start + tics * bpt
            rest = data[end + 1:]
            if not rest or rest[:4] == b"PWAD" or len(rest) < bpt:
                return bpt, tics, True
            fallback = fallback or (bpt, tics, True)
        elif fallback is None:
            fallback = (bpt, tics, False)
    return fallback


def read_demo(path: str | Path) -> DemoInfo:
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DemoError(f"{path.name}: {exc}") from exc
    return parse_demo(data, path)


def parse_demo(data: bytes, path: Path | str = "") -> DemoInfo:
    path = Path(path)
    if len(data) < 8:
        raise DemoError(f"{path.name}: too small to be a demo")

    dsda_tics = None
    base = 0
    fmt_prefix = ""
    if data[:7] == _DSDA_SIG:
        # DSDA-Doom extended demo: version, end-marker offset and tic count (big-endian).
        fmt_prefix = "DSDA-Doom "
        dsda_tics = struct.unpack_from(">I", data, 12)[0] if len(data) >= 16 else None
        sig = min((p for p in (data.find(s, 7, 64) for s in _BOOM_SIGS) if p > 0), default=-1)
        if sig < 1:
            raise DemoError(f"{path.name}: unrecognised DSDA demo header")
        base = sig - 1

    v = data[base]
    info = dict(version=v, longtics=False, deathmatch=False, respawn=False, fast=False, nomonsters=False)
    if v <= 4 and base == 0:
        skill, episode, mapnum = data[0], data[1], data[2]
        players = sum(1 for b in data[3:7] if b)
        header, candidates = 7, [4]
    elif 104 <= v <= 111:
        skill, episode, mapnum = data[base + 1], data[base + 2], data[base + 3]
        info.update(deathmatch=bool(data[base + 4]), respawn=bool(data[base + 5]),
                    fast=bool(data[base + 6]), nomonsters=bool(data[base + 7]))
        players = sum(1 for b in data[base + 9:base + 13] if b)
        header = base + 13
        candidates = [5] if v == 111 else [4]
        info["longtics"] = v == 111
    elif 200 <= v <= 214 or v == 221:
        if data[base + 1:base + 7] not in _BOOM_SIGS:
            raise DemoError(f"{path.name}: unrecognised Boom-format header")
        # Boom/MBF: version, signature, compatibility byte, skill, episode, map, deathmatch,
        # console player, options. MBF21 has no compatibility byte and 46 option bytes.
        fields = base + 7 if v == 221 else base + 8
        skill, episode, mapnum = data[fields], data[fields + 1], data[fields + 2]
        info["deathmatch"] = bool(data[fields + 3])
        pig = fields + 5 + (46 if v == 221 else 64)
        players = sum(1 for b in data[pig:pig + 32] if b)
        header = pig + 32
        candidates = [5, 4] if v in (214, 221) else [4, 5]
    else:
        raise DemoError(f"{path.name}: unknown demo version {v}")

    if header >= len(data):
        raise DemoError(f"{path.name}: truncated demo header")
    players = max(players, 1)
    if dsda_tics is not None:
        end = struct.unpack_from(">I", data, 8)[0]
        bpt = (end - header) // dsda_tics if dsda_tics else 0
        tics, complete = dsda_tics, 0 < end < len(data) and data[end] == END_MARKER
        if not complete or bpt <= 0:  # fall back to scanning
            bpt, tics, complete = _best_walk(data, header, [6 * players, 5 * players, 4 * players])
    else:
        bpt, tics, complete = _best_walk(data, header, [c * players for c in candidates])
    info["longtics"] = info["longtics"] or v == 221 or bpt // players == 5
    end = header + tics * bpt
    footer = _parse_footer(data[end + 1:]) if complete else {}

    return DemoInfo(
        path=path, size=len(data), format=fmt_prefix + _format_name(v),
        skill=skill + 1, episode=episode, map=mapnum, players=players,
        tics=tics, complete=complete, footer=footer, **info,
    )
