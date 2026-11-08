"""File summaries and detailed properties for the file browser."""

from __future__ import annotations

import os
import re
import stat
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dww_launcher.lmp import DemoError, DemoInfo, format_duration, read_demo
from dww_launcher.mapdata import MapData, MapError, find_maps, load_map
from dww_launcher.wad import WadError, WadFile, identify_game, map_sort_key

WAD_EXTENSIONS = {".wad", ".pwd"}
PK3_EXTENSIONS = {".pk3"}
DEMO_EXTENSIONS = {".lmp"}
DEH_EXTENSIONS = {".deh", ".bex"}
DOOM_EXTENSIONS = WAD_EXTENSIONS | PK3_EXTENSIONS | DEMO_EXTENSIONS | DEH_EXTENSIONS
MOD_EXTENSIONS = WAD_EXTENSIONS | PK3_EXTENSIONS | DEH_EXTENSIONS

_STD_MAP = re.compile(r"^(E[1-9]M[1-9]|MAP\d\d)$")

# Lumps / PK3 files worth pointing out, with a short explanation.
NOTABLE = {
    "DEHACKED": "DeHackEd patch", "UMAPINFO": "UMAPINFO", "MAPINFO": "MAPINFO",
    "ZMAPINFO": "ZMAPINFO", "EMAPINFO": "EMAPINFO", "DMAPINFO": "DMAPINFO", "DECORATE": "DECORATE",
    "ZSCRIPT": "ZScript", "GAMEINFO": "GAMEINFO", "MUSINFO": "MUSINFO", "SNDINFO": "SNDINFO",
    "ANIMDEFS": "ANIMDEFS", "LANGUAGE": "LANGUAGE", "DEHEXTRA": "DEHEXTRA", "OPTIONS": "OPTIONS",
    "COMPLVL": "COMPLVL", "SBARDEF": "SBARDEF", "ID24RES": "ID24 resources",
    "PLAYPAL": "Palette", "COLORMAP": "Colormap", "ENDOOM": "ENDOOM screen",
    "TEXTURE1": "TEXTURE1", "TEXTURE2": "TEXTURE2", "PNAMES": "PNAMES",
    "SWITCHES": "Boom SWITCHES", "ANIMATED": "Boom ANIMATED",
}


def ext(path: str | Path) -> str:
    return Path(path).suffix.lower()  # all extension checks are case-insensitive


def is_doom_file(path: str | Path) -> bool:
    return ext(path) in DOOM_EXTENSIONS


def is_mod_file(path: str | Path) -> bool:
    return ext(path) in MOD_EXTENSIONS


def human_size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def guess_game(map_names: list[str]) -> str:
    names = {m.upper() for m in map_names}
    episodic = any(re.match(r"^E\dM\d$", n) for n in names)
    mapxx = any(re.match(r"^MAP\d\d$", n) for n in names)
    if episodic and mapxx:
        return "Doom / Doom II"
    if mapxx:
        return "Doom II"
    if episodic:
        return "Ultimate Doom" if any(n.startswith("E4") for n in names) else "Doom"
    return ""


# ------------------------------------------------------------------ summaries

@dataclass
class FileSummary:
    path: Path
    kind: str  # IWAD, PWAD, PK3, Demo, DeHackEd, Folder
    levels: str = ""  # level count, or a demo's starting map
    game: str = ""
    seconds: float | None = None  # demo length
    size: int = 0
    mtime: float = 0.0
    error: str = ""
    is_iwad: bool = False

    @property
    def length(self) -> str:
        return format_duration(self.seconds) if self.seconds is not None else ""


def quick_summary(path: Path) -> FileSummary:
    """Cheap summary from the directory entry only (no file contents)."""
    try:
        st = path.stat()
    except OSError as exc:
        return FileSummary(path, "?", error=str(exc))
    if path.is_dir():
        return FileSummary(path, "Folder", mtime=st.st_mtime)
    kind = {".lmp": "Demo", ".pk3": "PK3", ".deh": "DeHackEd", ".bex": "DeHackEd"}.get(ext(path), "WAD")
    return FileSummary(path, kind, size=st.st_size, mtime=st.st_mtime)


def summarize(path: str | Path) -> FileSummary:
    """Full summary for the table (reads the file; run off the UI thread)."""
    path = Path(path)
    s = quick_summary(path)
    if s.error or s.kind == "Folder":
        return s
    try:
        e = ext(path)
        if e in WAD_EXTENSIONS:
            wad = WadFile(path)
            maps = [n for n, _ in find_maps(wad)]
            s.kind, s.is_iwad = wad.kind, wad.kind == "IWAD"
            s.levels = str(len(maps)) if maps else ""
            names = wad.names
            s.game = identify_game(names, [m for m in maps if _STD_MAP.match(m)]) if s.is_iwad else guess_game(maps)
        elif e in PK3_EXTENSIONS:
            maps = pk3_maps(path)
            s.levels = str(len(maps)) if maps else ""
            s.game = guess_game(maps)
        elif e in DEMO_EXTENSIONS:
            d = read_demo(path)
            s.levels = f"{d.map_name} {d.skill_name}"
            s.game = d.iwad or ("Doom" if d.episode > 1 else "Doom II" if d.map > 9 else "")
            s.seconds = d.seconds
    except (WadError, DemoError, zipfile.BadZipFile, OSError) as exc:
        s.error = str(exc)
    return s


# ------------------------------------------------------------------ PK3 / maps

def pk3_maps(path: str | Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        names = [Path(n).stem.upper() for n in zf.namelist()
                 if n.lower().startswith("maps/") and n.lower().endswith(".wad")]
    return sorted(set(names), key=_map_key)


def _map_key(name: str):
    try:
        return (0, map_sort_key(name))
    except (ValueError, IndexError):
        return (1, name)


def list_maps(path: str | Path) -> list[str]:
    """Map names in a WAD or PK3, in a sensible order."""
    e = ext(path)
    if e in PK3_EXTENSIONS:
        return pk3_maps(path)
    if e in WAD_EXTENSIONS:
        return [n for n, _ in find_maps(WadFile(path))]
    return []


def read_map(path: str | Path, name: str) -> MapData:
    """Load one map from a WAD, or from maps/<name>.wad inside a PK3."""
    e = ext(path)
    if e in PK3_EXTENSIONS:
        with zipfile.ZipFile(path) as zf:
            entry = next((n for n in zf.namelist() if n.lower().startswith("maps/")
                          and Path(n).stem.upper() == name.upper() and n.lower().endswith(".wad")), None)
            if entry is None:
                raise MapError(f"{name} not found")
            wad = WadFile(zf.read(entry), name=entry)
        found = find_maps(wad)
        if not found:
            raise MapError(f"{entry} contains no map")
        return load_map(wad, found[0][0])
    return load_map(WadFile(path), name)


def level_names(path: str | Path) -> dict[str, str]:
    """Level titles from UMAPINFO / MAPINFO / ZMAPINFO, if the file has them."""
    texts = []
    try:
        e = ext(path)
        if e in WAD_EXTENSIONS:
            wad = WadFile(path)
            for lump in wad.lumps:
                if lump.name in ("UMAPINFO", "MAPINFO", "ZMAPINFO") and lump.size < 2_000_000:
                    texts.append(wad.read(lump).decode("utf-8", "replace"))
        elif e in PK3_EXTENSIONS:
            with zipfile.ZipFile(path) as zf:
                for n in zf.namelist():
                    if Path(n).stem.upper() in ("UMAPINFO", "MAPINFO", "ZMAPINFO") and "/" not in n.strip("/"):
                        texts.append(zf.read(n).decode("utf-8", "replace"))
    except (WadError, OSError, zipfile.BadZipFile):
        return {}
    names: dict[str, str] = {}
    for text in texts:
        text = re.sub(r"//[^\n]*", "", text)
        for m in re.finditer(r'\bmap\s+(\w+)\s*\{(.*?)\}', text, re.S | re.I):  # UMAPINFO
            t = re.search(r'levelname\s*=\s*"([^"]*)"', m.group(2), re.I)
            if t:
                names.setdefault(m.group(1).upper(), t.group(1))
        for m in re.finditer(r'\bmap\s+(\w+)\s+"([^"]*)"', text, re.I):  # (Z)MAPINFO
            names.setdefault(m.group(1).upper(), m.group(2))
    return names


# ------------------------------------------------------------------ details

Section = tuple[str, list[tuple[str, str]]]


def _general(path: Path) -> Section:
    st = path.stat()
    rows = [
        ("Name", path.name),
        ("Location", str(path.parent)),
        ("Size", f"{human_size(st.st_size)} ({st.st_size:,} bytes)"),
        ("Modified", datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")),
    ]
    created = getattr(st, "st_birthtime", None)
    if created is None and os.name == "nt":
        created = st.st_ctime
    if created:
        rows.append(("Created", datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M:%S")))
    rows.append(("Accessed", datetime.fromtimestamp(st.st_atime).strftime("%Y-%m-%d %H:%M:%S")))
    if os.name != "nt":
        rows.append(("Permissions", stat.filemode(st.st_mode)))
    rows.append(("Read-only", "Yes" if not os.access(path, os.W_OK) else "No"))
    return ("General", rows)


def _map_range(maps: list[str]) -> str:
    if not maps:
        return "None"
    return f"{len(maps)}  ({maps[0]} – {maps[-1]})" if len(maps) > 1 else f"1  ({maps[0]})"


def _wad_details(path: Path) -> Section:
    wad = WadFile(path)
    maps = [n for n, _ in find_maps(wad)]
    names = wad.names
    formats = {}
    for name, marker in find_maps(wad):
        nxt = names[marker + 1] if marker + 1 < len(names) else ""
        fmt = "UDMF" if nxt == "TEXTMAP" else "Hexen" if "BEHAVIOR" in names[marker + 1:marker + 13] else "Doom"
        formats[fmt] = formats.get(fmt, 0) + 1

    def between(start: tuple[str, ...], end: tuple[str, ...]) -> int:
        count, inside = 0, False
        for n in names:
            if n in start:
                inside = True
            elif n in end:
                inside = False
            elif inside and n:
                count += 1
        return count

    music = sum(1 for n in names if n.startswith("D_") or n.startswith("MUS_"))
    sounds = sum(1 for n in names if n.startswith("DS") or n.startswith("DP"))
    notable = [label for key, label in NOTABLE.items() if key in names]
    game = identify_game(names, [m for m in maps if _STD_MAP.match(m)]) if wad.kind == "IWAD" else guess_game(maps)
    rows = [
        ("WAD type", f"{wad.kind} ({'game data' if wad.kind == 'IWAD' else 'add-on'})"),
        ("Game", game or "Unknown"),
        ("Lumps", str(len(names))),
        ("Levels", _map_range(maps)),
        ("Map format", ", ".join(f"{k} ({v})" for k, v in formats.items()) if formats else "-"),
        ("Sprites", str(between(("S_START", "SS_START"), ("S_END", "SS_END")))),
        ("Flats", str(between(("F_START", "FF_START"), ("F_END", "FF_END")))),
        ("Patches", str(between(("P_START", "PP_START"), ("P_END", "PP_END")))),
        ("Music lumps", str(music)),
        ("Sound lumps", str(sounds)),
        ("Contains", ", ".join(notable) if notable else "-"),
    ]
    demos = [n for n in names if re.match(r"^DEMO\d+$", n)]
    if demos:
        rows.append(("Demo lumps", ", ".join(demos)))
    return ("DOOM", rows)


def _pk3_details(path: Path) -> Section:
    with zipfile.ZipFile(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
    stems = {Path(i.filename).stem.upper() for i in infos}
    top = sorted({i.filename.split("/", 1)[0] + ("/" if "/" in i.filename else "") for i in infos})
    maps = pk3_maps(path)
    notable = [label for key, label in NOTABLE.items() if key in stems]
    return ("DOOM", [
        ("Archive type", "PK3 (zip)"),
        ("Game", guess_game(maps) or "Unknown"),
        ("Files", str(len(infos))),
        ("Uncompressed size", human_size(sum(i.file_size for i in infos))),
        ("Levels", _map_range(maps)),
        ("Top-level folders", ", ".join(t for t in top if t.endswith("/")) or "-"),
        ("Contains", ", ".join(notable) if notable else "-"),
        ("Note", "PK3 files need a port that supports them (e.g. GZDoom, Eternity, Woof)"),
    ])


def _demo_details(path: Path) -> Section:
    d: DemoInfo = read_demo(path)
    flags = [f for f in ("fast", "respawn", "nomonsters") if getattr(d, f)]
    rows = [
        ("Format", d.format),
        ("Version byte", str(d.version)),
        ("Starting map", d.map_name),
        ("Skill", f"{d.skill} ({d.skill_name})"),
        ("Players", f"{d.players}{' (deathmatch)' if d.deathmatch else ''}"),
        ("Length", f"{format_duration(d.seconds)} ({d.tics} tics)" if d.tics is not None else "Unknown"),
        ("Long tics", "Yes" if d.longtics else "No"),
        ("Flags", ", ".join(f"-{f}" for f in flags) if flags else "None"),
        ("Complete", "Yes" if d.complete else "No (no end marker - unfinished or damaged)"),
    ]
    if d.port:
        rows.append(("Recorded with", d.port))
    if d.iwad:
        rows.append(("IWAD", d.iwad))
    if d.files:
        rows.append(("Files", ", ".join(d.files)))
    if d.command_line:
        rows.append(("Command line", d.command_line))
    return ("Demo", rows)


def _deh_details(path: Path) -> Section:
    text = path.read_text(encoding="latin-1", errors="replace")
    lines = text.splitlines()
    def value(key: str) -> str:
        for ln in lines:
            if ln.lower().startswith(key) and "=" in ln:
                return ln.split("=", 1)[1].strip()
        return ""

    version, patch = value("doom version"), value("patch format")
    blocks = {}
    for ln in lines:
        m = re.match(r"^(Thing|Frame|Weapon|Ammo|Sound|Sprite|Pointer|Cheat|Misc|Text|\[\w+\])\b", ln)
        if m:
            blocks[m.group(1)] = blocks.get(m.group(1), 0) + 1
    return ("DeHackEd", [
        ("Doom version", version or "-"),
        ("Patch format", patch or "-"),
        ("Lines", str(len(lines))),
        ("Sections", ", ".join(f"{k} ×{v}" for k, v in blocks.items()) or "-"),
    ])


def details(path: str | Path) -> list[Section]:
    """Property sections for the Detail tab: General, then format-specific information."""
    path = Path(path)
    sections = [_general(path)]
    e = ext(path)
    try:
        if e in WAD_EXTENSIONS:
            sections.append(_wad_details(path))
        elif e in PK3_EXTENSIONS:
            sections.append(_pk3_details(path))
        elif e in DEMO_EXTENSIONS:
            sections.append(_demo_details(path))
        elif e in DEH_EXTENSIONS:
            sections.append(_deh_details(path))
    except (WadError, DemoError, MapError, zipfile.BadZipFile, OSError, ValueError) as exc:
        sections.append(("Problem", [("Error", str(exc))]))
    return sections
