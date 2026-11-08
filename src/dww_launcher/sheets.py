"""The DWW spreadsheet: published Google Sheets tabs downloaded as CSV and cached locally."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dww_launcher.config import config_path

_BASE = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vQaVNhmFVDV2s_WuuLAz2rqduwYgj7KD9K7tZ2FDtWjW64T1Bzsbr7U9wlqJ4oOA_E6XHp9P7Gh4UuB"
         "/pub?gid={gid}&single=true&output=csv")

REFRESH_INTERVAL = timedelta(hours=12)


@dataclass(frozen=True)
class SheetDef:
    key: str
    title: str
    gid: str
    game: str  # "doom" or "doom2"
    kind: str  # "wads" or "ports"
    link_column: str | None  # column holding the Doomworld URL (Doom 2 tabs have none)

    @property
    def url(self) -> str:
        return _BASE.format(gid=self.gid)

    @property
    def filename(self) -> str:
        return f"{self.key}.csv"


SHEETS: dict[str, SheetDef] = {s.key: s for s in (
    SheetDef("doom2_wads", "DOOM 2 WAD list", "1318963485", "doom2", "wads", None),
    SheetDef("doom2_ports", "DOOM 2 Ports list", "2086896665", "doom2", "ports", None),
    SheetDef("doom1_wads", "DOOM 1 WAD list", "0", "doom", "wads", "link"),
    SheetDef("doom1_ports", "DOOM 1 Ports list", "617921508", "doom", "ports", "doomworld link"),
)}

SheetData = dict[str, list[dict[str, str]]]


def sheets_dir() -> Path:
    return config_path().parent / "sheets"


def parse_csv(text: str) -> list[dict[str, str]]:
    rows = []
    for row in csv.DictReader(text.splitlines()):
        rows.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})
    return rows


def validate_csv(text: str, sheet: SheetDef) -> None:
    """Raise ValueError if downloaded text doesn't look like this sheet (e.g. an HTML error page)."""
    header = next(csv.reader(text.splitlines()), [])
    header = [h.strip() for h in header]
    needed = {"zip file name", "played by"}
    if sheet.link_column:
        needed.add(sheet.link_column)
    else:
        needed.add("id")
    missing = needed - set(header)
    if missing:
        raise ValueError(f"{sheet.title}: missing column(s) {', '.join(sorted(missing))}")


def load_sheets(directory: Path | None = None) -> SheetData:
    """Load whichever cached CSVs exist."""
    directory = directory or sheets_dir()
    data: SheetData = {}
    for sheet in SHEETS.values():
        path = directory / sheet.filename
        try:
            data[sheet.key] = parse_csv(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return data


def save_sheets(texts: dict[str, str], directory: Path | None = None) -> None:
    """Write all downloaded CSVs, replacing each file atomically."""
    directory = directory or sheets_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for key, text in texts.items():
        target = directory / SHEETS[key].filename
        tmp = target.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8", newline="")
        tmp.replace(target)


def all_cached(directory: Path | None = None) -> bool:
    directory = directory or sheets_dir()
    return all((directory / s.filename).is_file() for s in SHEETS.values())


def parse_timestamp(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def download_due(last_download: str, directory: Path | None = None,
                 now: datetime | None = None) -> bool:
    """True if never downloaded, files are missing, or the last download is 12+ hours old."""
    last = parse_timestamp(last_download)
    if last is None or not all_cached(directory):
        return True
    now = now or datetime.now(timezone.utc)
    return now - last >= REFRESH_INTERVAL
