from pathlib import Path

import pytest

from dww_launcher.command import (
    LaunchOptions,
    build_args,
    build_command,
    resolve_executable,
    split_extra_args,
)


def test_basic_args():
    opts = LaunchOptions(port="/ports/dsda-doom", iwad="/iwads/doom2.wad")
    assert build_command(opts) == [str(Path("/ports/dsda-doom")), "-iwad", "/iwads/doom2.wad"]


def test_full_args():
    opts = LaunchOptions(
        port="dsda-doom",
        iwad="doom2.wad",
        pwads=["sunlust.wad", "fix.deh", "music.wad", "patch.BEX"],
        map="MAP07",
        skill=4,
        complevel=2,
        fast=True,
        nomonsters=True,
        record="/demos/run",
        extra_args='-nomusic -config "my config.cfg"',
    )
    assert build_args(opts) == [
        "-iwad", "doom2.wad",
        "-file", "sunlust.wad", "music.wad",
        "-deh", "fix.deh", "patch.BEX",
        "-complevel", "2",
        "-warp", "7",
        "-skill", "4",
        "-fast",
        "-nomonsters",
        "-record", "/demos/run",
        "-nomusic", "-config", "my config.cfg",
    ]


def test_episode_warp():
    opts = LaunchOptions(iwad="doom.wad", map="E3M2")
    assert build_args(opts)[-3:] == ["-warp", "3", "2"]


def test_playdemo_ignores_gameplay_options():
    opts = LaunchOptions(iwad="doom2.wad", map="MAP01", skill=4, fast=True,
                         record="x", playdemo="demo.lmp")
    assert build_args(opts) == ["-iwad", "doom2.wad", "-playdemo", "demo.lmp"]


def test_resolve_app_bundle(tmp_path):
    bundle = tmp_path / "GZDoom.app"
    exe = bundle / "Contents" / "MacOS" / "GZDoom"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    assert resolve_executable(bundle) == exe
    plain = tmp_path / "dsda-doom"
    assert resolve_executable(plain) == plain


def test_tas_and_dsdademo():
    opts = LaunchOptions(iwad="doom2.wad", tas=True, dsdademo=True, record="/d/x")
    assert build_args(opts) == ["-iwad", "doom2.wad", "-tas", "-dsdademo", "-record", "/d/x"]


def test_timedemo_and_skipsec():
    opts = LaunchOptions(iwad="doom2.wad", playdemo="d.lmp", timedemo=True, skipsec="90")
    assert build_args(opts) == ["-iwad", "doom2.wad", "-timedemo", "d.lmp", "-skipsec", "90"]
    opts.timedemo = False
    opts.skipsec = None
    assert build_args(opts) == ["-iwad", "doom2.wad", "-playdemo", "d.lmp"]


@pytest.mark.parametrize("windows", [False, True])
def test_split_extra_args_quotes(windows):
    assert split_extra_args('-nomusic -config "my config.cfg"', windows=windows) == \
        ["-nomusic", "-config", "my config.cfg"]
    assert split_extra_args("   ", windows=windows) == []


def test_split_extra_args_windows_backslashes():
    assert split_extra_args(r'-config C:\Doom\dsda.cfg -file "C:\My Wads\x.wad"', windows=True) == \
        ["-config", r"C:\Doom\dsda.cfg", "-file", r"C:\My Wads\x.wad"]
