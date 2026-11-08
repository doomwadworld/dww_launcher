"""Packaging recorded demos.

Prototype format (to be refined): a .zip containing the demo lump and a
``manifest.json`` describing exactly how it was recorded, so someone else can
reproduce the playback setup.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from dww_launcher import __version__
from dww_launcher.command import LaunchOptions, resolve_executable

MANIFEST_VERSION = 1


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_entry(path: str | Path) -> dict:
    p = Path(path)
    entry = {"name": p.name}
    if p.is_file():
        entry["size"] = p.stat().st_size
        entry["sha256"] = file_sha256(p)
    return entry


def build_manifest(demo_path: str | Path, opts: LaunchOptions, notes: str = "") -> dict:
    demo_path = Path(demo_path)
    return {
        "format": "dww-demo",
        "manifest_version": MANIFEST_VERSION,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "launcher_version": __version__,
        "demo": _file_entry(demo_path),
        "source_port": resolve_executable(opts.port).name if opts.port else None,
        "iwad": _file_entry(opts.iwad) if opts.iwad else None,
        "pwads": [_file_entry(p) for p in opts.pwads],
        "map": opts.map,
        "skill": opts.skill,
        "complevel": opts.complevel,
        "flags": [f for f in ("fast", "respawn", "nomonsters") if getattr(opts, f)],
        "extra_args": opts.extra_args,
        "notes": notes,
    }


def package_demo(
    demo_path: str | Path, opts: LaunchOptions, out_path: str | Path, notes: str = ""
) -> Path:
    demo_path = Path(demo_path)
    out_path = Path(out_path)
    if not demo_path.is_file():
        raise FileNotFoundError(demo_path)
    manifest = build_manifest(demo_path, opts, notes)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(demo_path, demo_path.name)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    return out_path
