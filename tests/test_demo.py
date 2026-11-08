import json
import os
import zipfile

import pytest

from dww_launcher.command import LaunchOptions
from dww_launcher.demo import (
    auto_demo_name,
    consolidate_attempts,
    default_package_name,
    file_sha256,
    next_sequence_number,
    package_demos,
    recorded_files,
)


def touch(path, content=b"demo", mtime=None):
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


# ------------------------------------------------------------------ naming


def test_sequence_number_empty_and_missing_dir(tmp_path):
    assert next_sequence_number(tmp_path, "zzsketch") == 1
    assert next_sequence_number(tmp_path / "nope", "zzsketch") == 1


def test_sequence_number_uses_highest_existing(tmp_path):
    for name in ("zzsketch_1.lmp", "zzsketch_3.LMP", "zzsketch_x.lmp", "other_9.lmp", "zzsketch.lmp"):
        touch(tmp_path / name)
    assert next_sequence_number(tmp_path, "zzsketch") == 4


def test_sequence_number_counts_unconsolidated_attempts(tmp_path):
    touch(tmp_path / "zzsketch_1.lmp")
    touch(tmp_path / "zzsketch_2-00003.lmp")
    assert next_sequence_number(tmp_path, "zzsketch") == 3


def test_auto_name_iwad_and_map(tmp_path):
    assert auto_demo_name("/w/DOOM2.WAD", "MAP07", None, 0, True, tmp_path) == "doom2-map07"


def test_auto_name_single_level_mod(tmp_path):
    assert auto_demo_name("/w/doom2.wad", "MAP01", "/m/onemap.wad", 1, True, tmp_path) == "onemap"


def test_auto_name_multi_level_mod(tmp_path):
    touch(tmp_path / "zzsketch_1.lmp")
    touch(tmp_path / "zzsketch_2.lmp")
    assert auto_demo_name("doom2.wad", "MAP05", "/m/zzsketch.wad", 9, True, tmp_path) == "zzsketch_3"
    assert auto_demo_name("doom2.wad", "MAP05", "/m/zzsketch.wad", 9, False, tmp_path) == "zzsketch"


# --------------------------------------------------------- attempt cleanup


def test_consolidate_keeps_latest_and_renames(tmp_path):
    a1 = touch(tmp_path / "run-00001.lmp", b"1", mtime=1000)
    a2 = touch(tmp_path / "run-00002.lmp", b"2", mtime=2000)
    a3 = touch(tmp_path / "run-00003.lmp", b"3", mtime=3000)
    other = touch(tmp_path / "runner-00001.lmp")

    result = consolidate_attempts(tmp_path, "run")

    assert set(result.deleted) == {a1, a2}
    assert result.renamed
    assert not a3.exists()
    assert (tmp_path / "run.lmp").read_bytes() == b"3"
    assert other.exists()


def test_consolidate_uses_modification_time(tmp_path):
    touch(tmp_path / "run-00001.lmp", b"newest", mtime=5000)
    touch(tmp_path / "run-00002.lmp", b"older", mtime=1000)
    consolidate_attempts(tmp_path, "run")
    assert (tmp_path / "run.lmp").read_bytes() == b"newest"


def test_consolidate_does_not_overwrite(tmp_path):
    existing = touch(tmp_path / "run.lmp", b"keep me")
    latest = touch(tmp_path / "run-00001.lmp", b"new")
    result = consolidate_attempts(tmp_path, "run.lmp")
    assert result.conflict == existing and not result.renamed
    assert existing.read_bytes() == b"keep me" and latest.exists()


def test_consolidate_nothing(tmp_path):
    result = consolidate_attempts(tmp_path, "run")
    assert result.kept is None and result.deleted == []


def test_recorded_files(tmp_path):
    touch(tmp_path / "run.lmp")
    touch(tmp_path / "run-00001.lmp")
    assert {p.name for p in recorded_files(tmp_path, "run")} == {"run.lmp", "run-00001.lmp"}


# --------------------------------------------------------------- packaging


def test_package_multiple_demos(tmp_path, wad_factory):
    iwad = wad_factory("doom2.wad", "IWAD", ["MAP01"])
    pwad = wad_factory("zzsketch.wad", "PWAD", ["MAP01", "MAP02"])
    demos = [touch(tmp_path / f"zzsketch_{i}.lmp", f"demo{i}".encode()) for i in (1, 2, 3)]
    opts = LaunchOptions(port="/ports/dsda-doom", iwad=str(iwad), pwads=[str(pwad)],
                         skill=4, complevel=2, fast=True, dsdademo=True)

    out = package_demos(demos, opts, tmp_path / "out" / "zzsketch.zip", notes="UV-Fast")

    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.namelist()) == ["manifest.json", "zzsketch_1.lmp", "zzsketch_2.lmp",
                                         "zzsketch_3.lmp"]
        assert zf.read("zzsketch_2.lmp") == b"demo2"
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["source_port"] == "dsda-doom"
    assert manifest["iwad"]["sha256"] == file_sha256(iwad)
    assert [d["name"] for d in manifest["demos"]] == ["zzsketch_1.lmp", "zzsketch_2.lmp", "zzsketch_3.lmp"]
    assert manifest["flags"] == ["fast", "dsdademo"]
    assert manifest["notes"] == "UV-Fast"


def test_package_rejects_duplicate_names(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    demos = [touch(tmp_path / "a" / "x.lmp"), touch(tmp_path / "b" / "x.lmp")]
    with pytest.raises(ValueError):
        package_demos(demos, LaunchOptions(), tmp_path / "x.zip")


def test_package_missing_demo(tmp_path):
    with pytest.raises(FileNotFoundError):
        package_demos([tmp_path / "nope.lmp"], LaunchOptions(), tmp_path / "x.zip")


def test_default_package_name(tmp_path):
    assert default_package_name([tmp_path / "zz_1.lmp", tmp_path / "zz_2.lmp"]) == "zz"
    assert default_package_name([tmp_path / "a.lmp", tmp_path / "b.lmp"]) == "a"
