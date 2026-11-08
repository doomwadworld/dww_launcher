"""Demo submissions: matching an idgames entry in the spreadsheet, and building the files."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from dww_launcher.sheets import SHEETS, SheetData, SheetDef

COMMENT_LIMIT = 4500
YOUTUBE_LIMIT = 5000

OVER_LIMIT_WARNING = (
    f"Over {COMMENT_LIMIT} characters. YouTube video descriptions are limited to "
    f"{YOUTUBE_LIMIT} characters, so your comments may be hosted outside of YouTube."
)
BRACKET_WARNING = "YouTube does not allow angle brackets (< or >) in video descriptions."

_DEATHMATCH_RE = re.compile(r"levels/doom2?/deathmatch/")


# ---------------------------------------------------------------- matching


@dataclass
class Entry:
    """One spreadsheet entry: all rows for one idgames upload (one row per WAD in the zip)."""

    sheet: SheetDef
    rows: list[dict[str, str]]

    @property
    def zip_name(self) -> str:
        return self.rows[0].get("zip file name", "")

    @property
    def entry_id(self) -> str:
        return self.rows[0].get("id", "")

    @property
    def played_by(self) -> list[str]:
        return list(dict.fromkeys(r["played by"] for r in self.rows if r.get("played by")))

    @property
    def wad_files(self) -> list[str]:
        return [r["WAD file name"] for r in self.rows if r.get("WAD file name")]

    def describe(self) -> str:
        parts = [self.zip_name]
        if self.wad_files:
            wads = ", ".join(self.wad_files[:3]) + (", …" if len(self.wad_files) > 3 else "")
            parts.append(f"({wads})")
        parts.append(f"- {self.sheet.title}")
        if self.entry_id:
            parts.append(f"ID {self.entry_id}")
        if self.rows[0].get("maps"):
            parts.append(f"- {self.rows[0]['maps']} map(s)")
        return " ".join(parts)


@dataclass
class Lookup:
    entries: list[Entry] = field(default_factory=list)
    message: str = ""  # explanation when there is no single match
    blocked: bool = False  # submission not allowed for this entry

    @property
    def entry(self) -> Entry | None:
        return self.entries[0] if len(self.entries) == 1 else None

    @property
    def warnings(self) -> list[str]:
        warnings = []
        if self.entry and self.entry.played_by:
            warnings.append("Already played by " + ", ".join(self.entry.played_by) + ".")
        return warnings


def idgames_path(text: str) -> str:
    """Normalise a Doomworld URL or idgames path to e.g. ``levels/doom2/Ports/a-c/foo``."""
    t = text.strip()
    if "://" in t:
        t = re.split(r"[?#]", t, maxsplit=1)[0]
    if t.lower().startswith("idgames://"):
        t = t[len("idgames://"):]
    if "/idgames/" in t:
        t = t.split("/idgames/", 1)[1]
    t = t.strip().strip("/")
    if t.lower().endswith(".zip"):
        t = t[:-4]
    return t


def _group(sheet: SheetDef, rows: list[dict[str, str]], key) -> list[Entry]:
    groups: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        groups.setdefault(key(r), []).append(r)
    return [Entry(sheet, g) for g in groups.values()]


def _find_by_id(entry_id: str, data: SheetData) -> list[Entry]:
    entries = []
    for key in ("doom2_wads", "doom2_ports"):
        rows = [r for r in data.get(key, []) if r.get("id") == entry_id]
        if rows:
            entries.append(Entry(SHEETS[key], rows))
    return entries


def _find_by_path(path: str, sheet: SheetDef, data: SheetData) -> list[Entry]:
    rows = data.get(sheet.key, [])
    if sheet.link_column:  # Doom 1 tabs: compare the full idgames path
        wanted = path.lower()
        matches = [r for r in rows if idgames_path(r.get(sheet.link_column, "")).lower() == wanted]
        return _group(sheet, matches, lambda r: idgames_path(r[sheet.link_column]).lower())
    # Doom 2 tabs have no URLs: compare the zip name (last path component)
    name = path.rsplit("/", 1)[-1].lower()
    matches = [r for r in rows if r.get("zip file name", "").lower() == name]
    return _group(sheet, matches, lambda r: r.get("id", ""))


def lookup(text: str, data: SheetData) -> Lookup:
    """Find the spreadsheet entry for a spreadsheet ID, Doomworld URL or idgames path."""
    text = text.strip()
    if not text:
        return Lookup()

    if text.isdigit():
        if not any(data.get(k) for k in ("doom2_wads", "doom2_ports")):
            return Lookup(message="Spreadsheet data hasn't been downloaded yet.")
        entries = _find_by_id(text, data)
        if not entries:
            return Lookup(message=f"No entry with ID {text} in the DOOM 2 WAD or Ports lists.")
        return Lookup(entries=entries)

    path = idgames_path(text)
    if "/" not in path:
        return Lookup(message="Enter a spreadsheet ID (DOOM 2 only), a Doomworld URL or an idgames path.")
    if _DEATHMATCH_RE.search(path):
        return Lookup(message="Deathmatch maps can't be submitted.", blocked=True)

    segments = path.split("/")
    if "Themes" in path:
        candidates = [SHEETS["doom2_ports"]]
    else:
        game = "doom2" if "doom2" in segments else "doom" if "doom" in segments else None
        if game is None:
            return Lookup(message="Couldn't tell from the path whether this is Doom or Doom II.")
        kind = "ports" if "Ports" in path else "wads"
        candidates = [s for s in SHEETS.values() if s.game == game and s.kind == kind]

    missing = [s.title for s in candidates if s.key not in data]
    if missing:
        return Lookup(message=f"Spreadsheet data hasn't been downloaded yet ({', '.join(missing)}).")

    entries = [e for s in candidates for e in _find_by_path(path, s, data)]
    if not entries:
        where = " / ".join(s.title for s in candidates)
        return Lookup(message=f"No match found in the {where}.")
    if len(entries) > 1:
        ids = ", ".join(e.entry_id or e.zip_name for e in entries)
        return Lookup(entries=entries,
                      message=f"{len(entries)} entries match this name (IDs {ids}). "
                              "Enter the spreadsheet ID instead.")
    return Lookup(entries=entries)


# ------------------------------------------------------------ text checks


def text_warnings(text: str, combined_length: int) -> list[str]:
    warnings = []
    if combined_length > COMMENT_LIMIT:
        warnings.append(OVER_LIMIT_WARNING)
    if "<" in text or ">" in text:
        warnings.append(BRACKET_WARNING)
    return warnings


# ----------------------------------------------------------------- output


def submission_text(hall: str | None, why: str, comments: str) -> str:
    """Line 1: HoF, HoS or N/A. Then the "Why?" text (if any), then the comments."""
    parts = [hall or "N/A"]
    if why.strip():
        parts.append(why.strip())
    if comments.strip():
        parts.append(comments.strip())
    return "\n".join(parts) + "\n"


def ensure_suffix(name: str, suffix: str) -> str:
    return name if name.lower().endswith(suffix) else name + suffix


def create_submission_zip(demos: list[str | Path], txt_name: str, txt_content: str,
                          out_path: str | Path) -> Path:
    """Zip the demos together with the comments text file."""
    paths = [Path(p) for p in demos]
    if not paths:
        raise ValueError("No demos selected")
    for p in paths:
        if not p.is_file():
            raise FileNotFoundError(p)
    names = [p.name.lower() for p in paths] + [txt_name.lower()]
    if len(set(names)) != len(names):
        raise ValueError("Two of the files to be zipped have the same name")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            zf.write(p, p.name)
        zf.writestr(txt_name, txt_content.encode("utf-8"))
    return out_path
