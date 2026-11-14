import struct
from pathlib import Path

import pytest

from dww_launcher.lmp import DemoError, format_duration, parse_demo, read_demo

DATA = Path(__file__).parent / "data" / "demos"


# Real demos: (file, format, map, skill, players, tics, longtics, iwad from footer)
@pytest.mark.parametrize("name,fmt,map_name,skill,players,tics,longtics,iwad", [
    ("dsda_cl2_vanilla.lmp", "Doom v1.9", "MAP01", "UV", 1, 147, False, "freedoom2.wad"),
    ("dsda_cl2_longtics.lmp", "Doom v1.9 (longtics)", "MAP01", "UV", 1, 158, True, "freedoom2.wad"),
    ("dsda_cl9_boom.lmp", "Boom 2.02", "MAP01", "UV", 1, 157, False, "freedoom2.wad"),
    ("dsda_cl21_mbf21.lmp", "MBF21", "MAP01", "UV", 1, 157, True, "freedoom2.wad"),
    ("dsda_dsdademo.lmp", "DSDA-Doom MBF21", "MAP01", "UV", 1, 156, True, "freedoom2.wad"),
    ("freedoom2_DEMO1.lmp", "Doom v1.9", "E1M3 / MAP03", "HMP", 1, 2664, False, ""),
    ("freedoom1_DEMO4.lmp", "Doom v1.9", "E4M6", "UV", 3, 3136, False, ""),
    ("freedoom2_DEMO3.lmp", "Doom v1.9", "MAP22", "UV", 1, 2743, False, "freedoom2.wad"),
])
def test_real_demos(name, fmt, map_name, skill, players, tics, longtics, iwad):
    d = read_demo(DATA / name)
    assert (d.format, d.map_name, d.skill_name, d.players, d.tics, d.longtics, d.iwad) == \
        (fmt, map_name, skill, players, tics, longtics, iwad)
    assert d.complete


def test_footer():
    d = read_demo(DATA / "dsda_cl2_vanilla.lmp")
    assert d.port == "dsda-doom 0.27.5"
    assert d.command_line == '-iwad "freedoom2.wad" -complevel 2'
    assert d.seconds == pytest.approx(147 / 35)


def vanilla(tics: int, players=(1, 0, 0, 0), version=109, end=True, extra=b"", episode=1, mapnum=7) -> bytes:
    header = bytes([version, 3, episode, mapnum, 0, 0, 1, 0, 0, *players])
    per = (5 if version == 111 else 4) * sum(1 for p in players if p)
    return header + b"\x19\x00\x00\x00" * (tics * per // 4) + (b"\x80" if end else b"") + extra


def test_flags_and_multiplayer():
    d = parse_demo(vanilla(70, players=(1, 1, 0, 0)), "x.lmp")
    assert d.players == 2 and d.tics == 70 and d.fast and not d.respawn
    assert format_duration(d.seconds) == "0:02.00"


def test_map_naming():
    assert parse_demo(vanilla(1, episode=2, mapnum=5), "a.lmp").map_name == "E2M5"
    assert parse_demo(vanilla(1, mapnum=15), "a.lmp").map_name == "MAP15"  # Doom 1 has no map 15
    assert parse_demo(vanilla(1, mapnum=7), "a.lmp").map_name == "E1M7 / MAP07"


def test_old_format():
    data = bytes([2, 1, 3, 1, 0, 0, 0]) + b"\x19\x00\x00\x00" * 35 + b"\x80"
    d = parse_demo(data, "old.lmp")
    assert d.format == "Doom 1.0-1.2" and d.skill_name == "HMP" and d.tics == 35


def test_unfinished_demo():
    d = parse_demo(vanilla(10, end=False), "x.lmp")
    assert not d.complete and d.tics == 10


@pytest.mark.parametrize("data", [b"", b"\x6d\x03", bytes([150]) + b"\0" * 20])
def test_bad_demos(data):
    with pytest.raises(DemoError):
        parse_demo(data, "bad.lmp")


def test_read_missing(tmp_path):
    with pytest.raises(DemoError):
        read_demo(tmp_path / "nope.lmp")


@pytest.mark.parametrize("seconds,text", [(0, "0:00.00"), (61.5, "1:01.50"), (3723.25, "1:02:03.25"),
                                          (None, "?")])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


def test_footer_garbage_is_ignored():
    d = parse_demo(vanilla(5, extra=b"PWAD" + struct.pack("<ii", 99, 9999)), "x.lmp")
    assert d.footer == {} and d.tics == 5


def demo_with_cmdline(cmdline: str) -> bytes:
    from conftest import build_wad

    footer = build_wad("PWAD", [("PORTNAME", b"dsda-doom 0.27.5"), ("CMDLINE", cmdline.encode())])
    return vanilla(3) + footer


def test_required_files_from_footer():
    d = read_demo(DATA / "dsda_cl2_vanilla.lmp")
    assert d.required_files == ["freedoom2.wad"]
    d = parse_demo(demo_with_cmdline('-iwad "/games/DOOM2.WAD" -file a.wad "/my wads/b.pk3" -deh fix.deh '
                                     '-complevel 9 -skill 4'), "x.lmp")
    assert d.required_files == ["DOOM2.WAD", "a.wad", "b.pk3", "fix.deh"]


def test_no_footer_means_nothing_required():
    assert read_demo(DATA / "freedoom2_DEMO1.lmp").required_files == []
