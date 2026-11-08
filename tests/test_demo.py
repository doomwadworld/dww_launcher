import json
import zipfile

import pytest

from dww_launcher.command import LaunchOptions
from dww_launcher.demo import file_sha256, package_demo


def test_package_demo(tmp_path, wad_factory):
    iwad = wad_factory("doom2.wad", "IWAD", ["MAP01"])
    pwad = wad_factory("mod.wad", "PWAD", ["MAP01"])
    demo = tmp_path / "run.lmp"
    demo.write_bytes(b"\x6d\x03\x00\x00demo-data\x80")
    opts = LaunchOptions(port="/ports/dsda-doom", iwad=str(iwad), pwads=[str(pwad)],
                         map="MAP01", skill=4, complevel=2, fast=True)

    out = package_demo(demo, opts, tmp_path / "out" / "run.zip", notes="UV-Fast")

    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.namelist()) == ["manifest.json", "run.lmp"]
        assert zf.read("run.lmp") == demo.read_bytes()
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == "dww-demo"
    assert manifest["source_port"] == "dsda-doom"
    assert manifest["iwad"]["name"] == "doom2.wad"
    assert manifest["iwad"]["sha256"] == file_sha256(iwad)
    assert manifest["pwads"][0]["name"] == "mod.wad"
    assert manifest["demo"]["sha256"] == file_sha256(demo)
    assert manifest["flags"] == ["fast"]
    assert manifest["notes"] == "UV-Fast"


def test_package_missing_demo(tmp_path):
    with pytest.raises(FileNotFoundError):
        package_demo(tmp_path / "nope.lmp", LaunchOptions(), tmp_path / "x.zip")
