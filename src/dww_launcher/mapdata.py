"""Reading map geometry and computing map statistics (Doom, Hexen and UDMF formats)."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from dww_launcher.wad import WadError, WadFile

# Lumps that belong to a map (after its marker lump).
MAP_LUMPS = {
    "THINGS", "LINEDEFS", "SIDEDEFS", "VERTEXES", "SEGS", "SSECTORS", "NODES", "SECTORS",
    "REJECT", "BLOCKMAP", "BEHAVIOR", "SCRIPTS", "TEXTMAP", "ZNODES", "DIALOGUE", "ENDMAP",
    "LEAFS", "LIGHTS", "MACROS",
}

# Doom / Doom II thing types that count towards kills.
MONSTERS = {
    3004: "Zombieman", 9: "Shotgun guy", 65: "Heavy weapon dude", 3001: "Imp", 3002: "Demon",
    58: "Spectre", 3006: "Lost soul", 3005: "Cacodemon", 69: "Hell knight", 3003: "Baron of Hell",
    68: "Arachnotron", 71: "Pain elemental", 66: "Revenant", 67: "Mancubus", 64: "Arch-vile",
    7: "Spiderdemon", 16: "Cyberdemon", 84: "Wolfenstein SS",
}
KEYS = {5: "Blue keycard", 6: "Yellow keycard", 13: "Red keycard",
        40: "Blue skull key", 39: "Yellow skull key", 38: "Red skull key"}
WEAPONS = {2005: "Chainsaw", 2001: "Shotgun", 82: "Super shotgun", 2002: "Chaingun",
           2003: "Rocket launcher", 2004: "Plasma gun", 2006: "BFG9000"}
PLAYER_STARTS = {1, 2, 3, 4}
DEATHMATCH_START = 11
BOSS_BRAIN = 88

# Exit line specials.
DOOM_EXITS = {11: "normal", 52: "normal", 197: "normal", 51: "secret", 124: "secret", 198: "secret"}
HEXEN_EXITS = {243: "normal", 74: "normal", 75: "normal", 244: "secret"}


class MapError(Exception):
    pass


@dataclass
class Line:
    v1: int
    v2: int
    special: int
    two_sided: bool
    front: int = -1  # sidedef indices (-1 = none)
    back: int = -1


@dataclass
class Thing:
    type: int
    x: float
    y: float
    uv: bool  # present on Ultra-Violence
    single: bool  # present in single player


@dataclass
class MapData:
    name: str
    format: str  # "Doom", "Hexen" or "UDMF"
    vertices: list[tuple[float, float]] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    things: list[Thing] = field(default_factory=list)
    sector_specials: list[int] = field(default_factory=list)
    sector_secret: list[bool] = field(default_factory=list)  # per sector
    side_sectors: list[int] = field(default_factory=list)  # sector of each sidedef
    sidedefs: int = 0

    @property
    def secret_sectors(self) -> int:
        return sum(self.sector_secret)

    def exit_kind(self, line: Line) -> str | None:
        table = DOOM_EXITS if self.format == "Doom" else HEXEN_EXITS
        return table.get(line.special)

    def _side_sector(self, side: int) -> int:
        return self.side_sectors[side] if 0 <= side < len(self.side_sectors) else -1

    def sector_outlines(self, sector: int) -> list[list[int]]:
        """Closed vertex loops bounding ``sector`` (for filling it in a preview).

        Lines whose front or back side belongs to the sector are chained end to end; holes
        come out as separate loops, so an odd-even fill draws them correctly.
        """
        edges = [(ln.v1, ln.v2) for ln in self.lines
                 if self._side_sector(ln.front) == sector or self._side_sector(ln.back) == sector]
        edges = [e for e in edges if e[0] != e[1]]
        by_vertex: dict[int, list[int]] = {}
        for i, (a, b) in enumerate(edges):
            by_vertex.setdefault(a, []).append(i)
            by_vertex.setdefault(b, []).append(i)
        used = [False] * len(edges)
        loops = []
        for start in range(len(edges)):
            if used[start]:
                continue
            used[start] = True
            first, current = edges[start]
            loop = [first, current]
            while current != first:
                nxt = next((i for i in by_vertex.get(current, ()) if not used[i]), None)
                if nxt is None:
                    break  # open chain (damaged map): still usable as an outline
                used[nxt] = True
                a, b = edges[nxt]
                current = b if a == current else a
                loop.append(current)
            if len(loop) >= 3:
                loops.append(loop)
        return loops


# ------------------------------------------------------------------ finding maps

def find_maps(wad: WadFile) -> list[tuple[str, int]]:
    """(map name, marker lump index) for every map, found by structure, not by name."""
    names = wad.names
    maps = []
    for i in range(len(names) - 1):
        if names[i + 1] in ("THINGS", "TEXTMAP") and names[i] not in MAP_LUMPS:
            maps.append((names[i], i))
    return maps


def _map_lumps(wad: WadFile, marker: int) -> dict[str, int]:
    found = {}
    for i in range(marker + 1, len(wad.lumps)):
        name = wad.lumps[i].name
        if name not in MAP_LUMPS or name in found:
            break
        found[name] = i
        if name == "ENDMAP":
            break
    return found


# ------------------------------------------------------------------ parsing

def load_map(wad: WadFile, name: str) -> MapData:
    markers = dict(find_maps(wad))
    if name.upper() not in markers:
        raise MapError(f"{name} not found in {wad.name}")
    lumps = _map_lumps(wad, markers[name.upper()])
    try:
        if "TEXTMAP" in lumps:
            return _parse_udmf(name.upper(), wad.read(lumps["TEXTMAP"]))
        hexen = "BEHAVIOR" in lumps
        data = {k: wad.read(i) for k, i in lumps.items()}
        return _parse_binary(name.upper(), data, hexen)
    except (WadError, struct.error, ValueError, IndexError) as exc:
        raise MapError(f"{name}: {exc}") from exc


def _parse_binary(name: str, data: dict[str, bytes], hexen: bool) -> MapData:
    m = MapData(name=name, format="Hexen" if hexen else "Doom")
    v = data.get("VERTEXES", b"")
    m.vertices = [(x, y) for x, y in struct.iter_unpack("<hh", v[:len(v) // 4 * 4])]

    ld = data.get("LINEDEFS", b"")
    def side(i: int) -> int:
        return -1 if i == 0xFFFF else i

    if hexen:  # v1, v2, flags, special(byte), 5 args, front, back
        for v1, v2, _f, special, *_args, front, back in struct.iter_unpack("<HHHB5BHH", ld[:len(ld) // 16 * 16]):
            m.lines.append(Line(v1, v2, special, back != 0xFFFF, side(front), side(back)))
    else:  # v1, v2, flags, special, tag, front, back
        for v1, v2, _f, special, _tag, front, back in struct.iter_unpack("<HHHHHHH", ld[:len(ld) // 14 * 14]):
            m.lines.append(Line(v1, v2, special, back != 0xFFFF, side(front), side(back)))

    th = data.get("THINGS", b"")
    if hexen:  # tid, x, y, z, angle, type, flags, special, 5 args
        for _tid, x, y, _z, _a, ttype, flags, *_rest in struct.iter_unpack("<hhhhhhhB5B", th[:len(th) // 20 * 20]):
            m.things.append(Thing(ttype, x, y, uv=bool(flags & 0x0004), single=bool(flags & 0x0100)))
    else:  # x, y, angle, type, flags
        for x, y, _a, ttype, flags in struct.iter_unpack("<hhhhh", th[:len(th) // 10 * 10]):
            m.things.append(Thing(ttype, x, y, uv=bool(flags & 0x0004), single=not flags & 0x0010))

    sec = data.get("SECTORS", b"")
    for _fl, _ce, _ft, _ct, _light, special, _tag in struct.iter_unpack("<hh8s8shhh", sec[:len(sec) // 26 * 26]):
        m.sector_specials.append(special)
    if hexen:  # ZDoom "Doom in Hexen" format: secret flag bit
        m.sector_secret = [bool(s & 1024) for s in m.sector_specials]
    else:  # vanilla type 9, or the Boom generalised secret bit
        m.sector_secret = [s == 9 or bool(s & 128) for s in m.sector_specials]
    sd = data.get("SIDEDEFS", b"")
    # x offset, y offset, upper, lower and middle textures, sector
    m.side_sectors = [sector for *_rest, sector in struct.iter_unpack("<hh8s8s8sH", sd[:len(sd) // 30 * 30])]
    m.sidedefs = len(m.side_sectors)
    return m


_UDMF_BLOCK = re.compile(r"(\w+)\s*\{([^}]*)\}", re.S)
_UDMF_FIELD = re.compile(r"(\w+)\s*=\s*(\"(?:[^\"\\]|\\.)*\"|[^;]+?)\s*;", re.S)
_UDMF_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def _udmf_value(raw: str):
    raw = raw.strip()
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    if raw.startswith('"'):
        return raw[1:-1]
    try:
        return float(raw) if any(c in raw for c in ".eE") and not raw.lower().startswith("0x") else int(raw, 0)
    except ValueError:
        return raw


def _parse_udmf(name: str, text: bytes) -> MapData:
    m = MapData(name=name, format="UDMF")
    source = _UDMF_COMMENT.sub("", text.decode("utf-8", "replace"))
    for kind, body in _UDMF_BLOCK.findall(source):
        f = {k.lower(): _udmf_value(v) for k, v in _UDMF_FIELD.findall(body)}
        kind = kind.lower()
        if kind == "vertex":
            m.vertices.append((float(f.get("x", 0)), float(f.get("y", 0))))
        elif kind == "linedef":
            front, back = int(f.get("sidefront", -1)), int(f.get("sideback", -1))
            m.lines.append(Line(int(f.get("v1", 0)), int(f.get("v2", 0)), int(f.get("special", 0)),
                                back != -1, front, back))
        elif kind == "sidedef":
            m.sidedefs += 1
            m.side_sectors.append(int(f.get("sector", -1)))
        elif kind == "sector":
            special = int(f.get("special", 0))
            m.sector_specials.append(special)
            m.sector_secret.append(f.get("secret") is True or bool(special & 1024) or special in (9, 195))
        elif kind == "thing":
            m.things.append(Thing(int(f.get("type", 0)), float(f.get("x", 0)), float(f.get("y", 0)),
                                  uv=bool(f.get("skill4")), single=bool(f.get("single"))))
    return m


# ------------------------------------------------------------------ statistics

def map_stats(m: MapData) -> list[tuple[str, str]]:
    """Human-readable (label, value) rows for the Maps tab."""
    uv_single = [t for t in m.things if t.uv and t.single]
    monsters = [t for t in uv_single if t.type in MONSTERS]
    exits = {m.exit_kind(line) for line in m.lines} - {None}
    keys = sorted({KEYS[t.type] for t in m.things if t.type in KEYS})
    weapons = sorted({WEAPONS[t.type] for t in uv_single if t.type in WEAPONS},
                     key=list(WEAPONS.values()).index)

    if m.vertices:
        xs = [v[0] for v in m.vertices]
        ys = [v[1] for v in m.vertices]
        size = f"{int(max(xs) - min(xs))} × {int(max(ys) - min(ys))} units"
    else:
        size = "-"

    if "normal" in exits:
        exit_text = "Yes"
    elif any(t.type == BOSS_BRAIN for t in m.things):
        exit_text = "No (ends when the Icon of Sin is killed)"
    else:
        exit_text = "No (may end via a boss death or script)"

    counts = {}
    for t in monsters:
        counts[MONSTERS[t.type]] = counts.get(MONSTERS[t.type], 0) + 1
    biggest = ", ".join(f"{n} {name}" for name, n in sorted(counts.items(), key=lambda kv: -kv[1])[:4])

    return [
        ("Format", m.format),
        ("Things", str(len(m.things))),
        ("Monsters (UV)", str(len(monsters))),
        ("Most common", biggest or "-"),
        ("Secrets", str(m.secret_sectors)),
        ("Exit linedef", exit_text),
        ("Secret exit", "Yes" if "secret" in exits else "No"),
        ("Keys", ", ".join(keys) if keys else "None"),
        ("Weapons (UV)", ", ".join(weapons) if weapons else "None"),
        ("Player starts", f"{sum(1 for t in m.things if t.type in PLAYER_STARTS)} co-op, "
                          f"{sum(1 for t in m.things if t.type == DEATHMATCH_START)} deathmatch"),
        ("Linedefs", str(len(m.lines))),
        ("Sidedefs", str(m.sidedefs)),
        ("Vertices", str(len(m.vertices))),
        ("Sectors", str(len(m.sector_specials))),
        ("Size", size),
    ]
