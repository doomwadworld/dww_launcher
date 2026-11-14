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

# Key-locked doors and switches.
KEY_COLOURS = ("red", "yellow", "blue")
DOOM_KEY_LINES = {26: "blue", 32: "blue", 99: "blue", 133: "blue",
                  27: "yellow", 34: "yellow", 134: "yellow", 136: "yellow",
                  28: "red", 33: "red", 135: "red", 137: "red"}
# Boom generalised locked doors: key in bits 6-8 (0 = any key, 7 = all keys).
BOOM_LOCKED_DOOR = range(0x3800, 0x3C00)
BOOM_LOCK_KEYS = {1: "red", 2: "blue", 3: "yellow", 4: "red", 5: "blue", 6: "yellow"}
# ZDoom lock numbers (Hexen-format and UDMF maps) and the specials that take one: special -> arg index.
ZDOOM_LOCKS = {1: "red", 2: "blue", 3: "yellow", 4: "red", 5: "blue", 6: "yellow",
               129: "red", 130: "blue", 131: "yellow"}
ZDOOM_LOCK_ARG = {13: 3, 202: 4, 83: 4, 85: 4}  # Door_LockedRaise, Generic_Door, ACS_LockedExecute(Door)


def _things(category: str, names: dict[int, str]) -> dict[int, tuple[str, str]]:
    return {t: (n, category) for t, n in names.items()}


# Doom / Doom II thing types: name and category, for map tooltips.
THING_INFO: dict[int, tuple[str, str]] = {
    **_things("Monster", MONSTERS),
    **_things("Monster", {88: "Icon of Sin (boss brain)", 89: "Monster spawner", 87: "Spawn spot",
                          72: "Commander Keen", 888: "Helper dog (MBF)"}),
    **_things("Player start", {1: "Player 1 start", 2: "Player 2 start", 3: "Player 3 start",
                               4: "Player 4 start", 11: "Deathmatch start"}),
    **_things("Teleport", {14: "Teleport destination"}),
    **_things("Weapon", WEAPONS),
    **_things("Key", KEYS),
    **_things("Ammo", {2007: "Ammo clip", 2048: "Box of ammo", 2008: "Shotgun shells",
                       2049: "Box of shells", 2010: "Rocket", 2046: "Box of rockets",
                       2047: "Energy cell", 17: "Energy cell pack", 8: "Backpack"}),
    **_things("Health", {2011: "Stimpack", 2012: "Medikit", 2014: "Health bonus",
                         2013: "Soulsphere", 83: "Megasphere"}),
    **_things("Armor", {2015: "Armor bonus", 2018: "Green armor", 2019: "Blue armor"}),
    **_things("Power-up", {2022: "Invulnerability", 2023: "Berserk", 2024: "Partial invisibility",
                           2025: "Radiation suit", 2026: "Computer area map",
                           2045: "Light amplification visor"}),
    **_things("Obstacle", {2035: "Exploding barrel", 70: "Burning barrel", 2028: "Floor lamp",
                           85: "Tall techno lamp", 86: "Short techno lamp", 34: "Candle",
                           35: "Candelabra", 44: "Tall blue firestick", 45: "Tall green firestick",
                           46: "Tall red firestick", 55: "Short blue firestick",
                           56: "Short green firestick", 57: "Short red firestick",
                           48: "Tall techno column", 30: "Tall green pillar", 32: "Tall red pillar",
                           31: "Short green pillar", 33: "Short red pillar",
                           36: "Short green pillar with heart", 37: "Short red pillar with skull",
                           47: "Stalagmite", 43: "Burnt tree", 54: "Large brown tree",
                           41: "Evil eye", 42: "Floating skull rock", 25: "Impaled human",
                           26: "Twitching impaled human", 27: "Skull on a pole",
                           28: "Skull shish kebab", 29: "Pile of skulls and candles",
                           49: "Hanging victim, twitching", 50: "Hanging victim, arms out",
                           51: "Hanging victim, one-legged", 52: "Hanging pair of legs",
                           53: "Hanging leg", 73: "Hanging victim, guts removed",
                           74: "Hanging victim, guts and brain removed",
                           75: "Hanging torso, looking down", 76: "Hanging torso, open skull",
                           77: "Hanging torso, looking up", 78: "Hanging torso, brain removed"}),
    **_things("Decoration", {59: "Hanging victim, arms out", 60: "Hanging pair of legs",
                             61: "Hanging victim, one-legged", 62: "Hanging leg",
                             63: "Hanging victim, twitching", 10: "Bloody mess", 12: "Bloody mess",
                             15: "Dead player", 18: "Dead zombieman", 19: "Dead shotgun guy",
                             20: "Dead imp", 21: "Dead demon", 22: "Dead cacodemon",
                             23: "Dead lost soul", 24: "Pool of blood and guts", 79: "Pool of blood",
                             80: "Pool of blood", 81: "Pool of brains"}),
}


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
    args: tuple[int, ...] = ()  # Hexen/UDMF special arguments
    lock: int = 0  # UDMF "locknumber"


@dataclass
class Thing:
    type: int
    x: float
    y: float
    uv: bool  # present on Ultra-Violence
    single: bool  # present in single player
    angle: int = 0  # degrees, 0 = east, counter-clockwise
    skills: tuple[bool, bool, bool] = (True, True, True)  # easy (1-2), medium (3), hard (4-5)
    ambush: bool = False  # "deaf" monster

    @property
    def name(self) -> str:
        return THING_INFO.get(self.type, (f"Unknown thing (type {self.type})", "Other"))[0]

    @property
    def category(self) -> str:
        return THING_INFO.get(self.type, ("", "Other"))[1]

    def describe(self) -> str:
        """Multi-line description for a tooltip."""
        lines = [self.name, f"{self.category} · type {self.type}"]
        skill_names = [n for n, on in zip(("easy", "medium", "hard"), self.skills) if on]
        notes = ["all skills" if len(skill_names) == 3 else
                 ("skills: " + ", ".join(skill_names) if skill_names else "no skill level")]
        if not self.single:
            notes.append("multiplayer only")
        if self.ambush:
            notes.append("ambush")
        lines.append(" · ".join(notes).capitalize())
        lines.append(f"At ({int(self.x)}, {int(self.y)}), facing {_compass(self.angle)}")
        return "\n".join(lines)


def _compass(angle: int) -> str:
    names = ("east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east")
    return names[round((angle % 360) / 45) % 8]


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

    def key_colour(self, line: Line) -> str | None:
        """"red", "yellow" or "blue" for a door/switch that needs that key, else None."""
        if self.format == "Doom":
            if line.special in DOOM_KEY_LINES:
                return DOOM_KEY_LINES[line.special]
            if line.special in BOOM_LOCKED_DOOR:
                return BOOM_LOCK_KEYS.get((line.special & 0x01C0) >> 6)
            return None
        lock = line.lock
        if not lock and line.special in ZDOOM_LOCK_ARG and len(line.args) > ZDOOM_LOCK_ARG[line.special]:
            lock = line.args[ZDOOM_LOCK_ARG[line.special]]
        return ZDOOM_LOCKS.get(lock)

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


def _skills(flags: int) -> tuple[bool, bool, bool]:
    return bool(flags & 1), bool(flags & 2), bool(flags & 4)


def _parse_binary(name: str, data: dict[str, bytes], hexen: bool) -> MapData:
    m = MapData(name=name, format="Hexen" if hexen else "Doom")
    v = data.get("VERTEXES", b"")
    m.vertices = [(x, y) for x, y in struct.iter_unpack("<hh", v[:len(v) // 4 * 4])]

    ld = data.get("LINEDEFS", b"")
    def side(i: int) -> int:
        return -1 if i == 0xFFFF else i

    if hexen:  # v1, v2, flags, special(byte), 5 args, front, back
        for v1, v2, _f, special, *args, front, back in struct.iter_unpack("<HHHB5BHH", ld[:len(ld) // 16 * 16]):
            m.lines.append(Line(v1, v2, special, back != 0xFFFF, side(front), side(back), tuple(args)))
    else:  # v1, v2, flags, special, tag, front, back
        for v1, v2, _f, special, _tag, front, back in struct.iter_unpack("<HHHHHHH", ld[:len(ld) // 14 * 14]):
            m.lines.append(Line(v1, v2, special, back != 0xFFFF, side(front), side(back)))

    th = data.get("THINGS", b"")
    if hexen:  # tid, x, y, z, angle, type, flags, special, 5 args
        for _tid, x, y, _z, angle, ttype, flags, *_rest in struct.iter_unpack("<hhhhhhhB5B", th[:len(th) // 20 * 20]):
            m.things.append(Thing(ttype, x, y, uv=bool(flags & 0x0004), single=bool(flags & 0x0100),
                                  angle=angle, skills=_skills(flags), ambush=bool(flags & 0x0008)))
    else:  # x, y, angle, type, flags
        for x, y, angle, ttype, flags in struct.iter_unpack("<hhhhh", th[:len(th) // 10 * 10]):
            m.things.append(Thing(ttype, x, y, uv=bool(flags & 0x0004), single=not flags & 0x0010,
                                  angle=angle, skills=_skills(flags), ambush=bool(flags & 0x0008)))

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
            args = tuple(int(f.get(f"arg{i}", 0)) for i in range(5))
            m.lines.append(Line(int(f.get("v1", 0)), int(f.get("v2", 0)), int(f.get("special", 0)),
                                back != -1, front, back, args, int(f.get("locknumber", 0))))
        elif kind == "sidedef":
            m.sidedefs += 1
            m.side_sectors.append(int(f.get("sector", -1)))
        elif kind == "sector":
            special = int(f.get("special", 0))
            m.sector_specials.append(special)
            m.sector_secret.append(f.get("secret") is True or bool(special & 1024) or special in (9, 195))
        elif kind == "thing":
            skills = (bool(f.get("skill1") or f.get("skill2")), bool(f.get("skill3")),
                      bool(f.get("skill4") or f.get("skill5")))
            m.things.append(Thing(int(f.get("type", 0)), float(f.get("x", 0)), float(f.get("y", 0)),
                                  uv=bool(f.get("skill4")), single=bool(f.get("single")),
                                  angle=int(f.get("angle", 0)), skills=skills, ambush=bool(f.get("ambush"))))
    return m


# ------------------------------------------------------------------ totals

TOTAL_KEYS = ("things", "monsters", "secrets", "linedefs", "sidedefs", "vertices", "sectors")


def map_counts(wad: WadFile, name: str, marker: int) -> dict[str, int]:
    """Counts for one map. Binary maps are counted from lump sizes (only THINGS and SECTORS
    are read, for monsters and secrets), so totals for big WADs stay quick."""
    lumps = _map_lumps(wad, marker)
    if "TEXTMAP" in lumps:
        m = _parse_udmf(name, wad.read(lumps["TEXTMAP"]))
        sizes = {"things": len(m.things), "linedefs": len(m.lines), "sidedefs": m.sidedefs,
                 "vertices": len(m.vertices), "sectors": len(m.sector_specials)}
    else:
        hexen = "BEHAVIOR" in lumps
        size = {k: wad.lumps[i].size for k, i in lumps.items()}
        sizes = {"things": size.get("THINGS", 0) // (20 if hexen else 10),
                 "linedefs": size.get("LINEDEFS", 0) // (16 if hexen else 14),
                 "sidedefs": size.get("SIDEDEFS", 0) // 30, "vertices": size.get("VERTEXES", 0) // 4,
                 "sectors": size.get("SECTORS", 0) // 26}
        m = _parse_binary(name, {k: wad.read(lumps[k]) for k in ("THINGS", "SECTORS") if k in lumps}, hexen)
    sizes["monsters"] = sum(1 for t in m.things if t.uv and t.single and t.type in MONSTERS)
    sizes["secrets"] = m.secret_sectors
    return sizes


def wad_totals(wads: list[WadFile]) -> tuple[dict[str, int], tuple[str, int] | None]:
    """Summed counts over every map in the given WADs, and the map with the most linedefs."""
    totals = dict.fromkeys(TOTAL_KEYS, 0)
    largest = None
    for wad in wads:
        for name, marker in find_maps(wad):
            try:
                counts = map_counts(wad, name, marker)
            except (WadError, struct.error, ValueError, IndexError):
                continue
            for k in TOTAL_KEYS:
                totals[k] += counts[k]
            if largest is None or counts["linedefs"] > largest[1]:
                largest = (name, counts["linedefs"])
    return totals, largest


# ------------------------------------------------------------------ statistics

def _key_line_summary(m: MapData) -> str:
    counts = {c: 0 for c in KEY_COLOURS}
    for line in m.lines:
        colour = m.key_colour(line)
        if colour:
            counts[colour] += 1
    parts = [f"{n} {c}" for c, n in counts.items() if n]
    return ", ".join(parts) if parts else "None"


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
        ("Key-locked lines", _key_line_summary(m)),
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
