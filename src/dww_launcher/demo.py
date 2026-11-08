"""Demo naming and attempt cleanup."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# ------------------------------------------------------------------ naming


def strip_lmp(name: str) -> str:
    return name[:-4] if name.lower().endswith(".lmp") else name


def _lmp_files(demo_dir: str | Path) -> list[Path]:
    d = Path(demo_dir)
    if not d.is_dir():
        return []
    return [p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".lmp"]


def next_sequence_number(demo_dir: str | Path, base: str) -> int:
    """Next N for ``<base>_N.lmp``: one past the highest N already in the folder.

    Unconsolidated attempt files (``<base>_N-00001.lmp``) count as well.
    """
    pattern = re.compile(re.escape(base) + r"_(\d+)(?:-\d{5})?\.lmp", re.IGNORECASE)
    numbers = [int(m.group(1)) for p in _lmp_files(demo_dir) if (m := pattern.fullmatch(p.name))]
    return max(numbers, default=0) + 1


def auto_demo_name(
    iwad: str,
    map_name: str | None,
    mod: str | None,
    mod_map_count: int,
    sequence: bool,
    demo_dir: str | Path,
) -> str:
    """Default demo name (without .lmp).

    - Mod with levels, sequence numbering on and more than one level: ``<mod>_<N>``
    - Mod with levels otherwise: ``<mod>``
    - No mod with levels: ``<iwad>-<map>``
    """
    if mod:
        stem = Path(mod).stem
        if sequence and mod_map_count > 1:
            return f"{stem}_{next_sequence_number(demo_dir, stem)}"
        return stem
    iwad_stem = Path(iwad).stem if iwad else "doom"
    return f"{iwad_stem}-{map_name or 'start'}".lower()


# ------------------------------------------------------- attempt cleanup


@dataclass
class AttemptCleanup:
    kept: Path | None = None  # final location of the surviving demo
    deleted: list[Path] = field(default_factory=list)
    renamed: bool = False
    conflict: Path | None = None  # set when the rename target already existed


def attempt_files(demo_dir: str | Path, name: str) -> list[Path]:
    """``<name>-NNNNN.lmp`` files, oldest first."""
    pattern = re.compile(re.escape(name) + r"-(\d{5})\.lmp", re.IGNORECASE)
    found = [(p, int(m.group(1))) for p in _lmp_files(demo_dir) if (m := pattern.fullmatch(p.name))]
    found.sort(key=lambda pc: (pc[0].stat().st_mtime, pc[1]))
    return [p for p, _ in found]


def consolidate_attempts(demo_dir: str | Path, name: str) -> AttemptCleanup:
    """Delete every ``<name>-NNNNN.lmp`` except the newest, then rename it to ``<name>.lmp``.

    The rename is skipped (and reported as a conflict) if ``<name>.lmp`` already exists.
    """
    name = strip_lmp(name)
    attempts = attempt_files(demo_dir, name)
    result = AttemptCleanup()
    if not attempts:
        return result
    *older, latest = attempts
    for p in older:
        p.unlink()
        result.deleted.append(p)
    target = Path(demo_dir) / f"{name}.lmp"
    if target.exists():
        result.kept = latest
        result.conflict = target
    else:
        latest.rename(target)
        result.kept = target
        result.renamed = True
    return result


def recorded_files(demo_dir: str | Path, name: str) -> list[Path]:
    """All files a recording session named ``name`` may have produced."""
    name = strip_lmp(name)
    files = attempt_files(demo_dir, name)
    plain = Path(demo_dir) / f"{name}.lmp"
    if plain.is_file():
        files.append(plain)
    return files


# --------------------------------------------------------------- packaging


def default_package_name(demo_paths: list[Path]) -> str:
    """``zzsketch`` for zzsketch_1.lmp + zzsketch_2.lmp; otherwise the first demo's name."""
    stems = [re.sub(r"_\d+$", "", Path(p).stem) for p in demo_paths]
    return stems[0] if len(set(stems)) == 1 else Path(demo_paths[0]).stem
