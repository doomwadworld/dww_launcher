import zipfile
from pathlib import Path

import pytest

from conftest import build_wad, doom_map_lumps
from dww_launcher.fileinfo import (
    details,
    guess_game,
    is_doom_file,
    is_mod_file,
    level_names,
    list_maps,
    read_map,
    summarize,
)

DEMOS = Path(__file__).parent / "data" / "demos"


@pytest.mark.parametrize("name,doom,mod", [
    ("a.wad", True, True), ("A.WAD", True, True), ("b.Pwd", True, True), ("c.PK3", True, True),
    ("d.LMP", True, False), ("e.lmp", True, False), ("f.DEH", True, True), ("g.bex", True, True),
    ("h.txt", False, False), ("i.zip", False, False), ("wad", False, False),
])
def test_extensions_case_insensitive(name, doom, mod):
    assert is_doom_file(name) is doom
    assert is_mod_file(name) is mod


def test_guess_game():
    assert guess_game(["MAP01"]) == "Doom II"
    assert guess_game(["E1M1", "E2M1"]) == "Doom"
    assert guess_game(["E4M1"]) == "Ultimate Doom"
    assert guess_game(["E1M1", "MAP01"]) == "Doom / Doom II"
    assert guess_game(["CUSTOM"]) == ""


def test_summarize_wad(mapwad):
    path = mapwad("TEST.WAD", ["MAP01", "MAP02"])
    s = summarize(path)
    assert (s.kind, s.levels, s.game, s.error) == ("PWAD", "2", "Doom II", "")


def test_summarize_iwad(mapwad):
    s = summarize(mapwad("game.wad", ["E1M1", "E2M1"], kind="IWAD"))
    assert s.kind == "IWAD" and s.is_iwad and s.game == "Doom (registered)"


def test_summarize_demo():
    s = summarize(DEMOS / "dsda_cl9_boom.lmp")
    assert (s.kind, s.levels, s.game, s.length) == ("Demo", "MAP01 UV", "freedoom2.wad", "0:04.49")


def test_summarize_bad_file(tmp_path):
    bad = tmp_path / "broken.wad"
    bad.write_bytes(b"nonsense")
    s = summarize(bad)
    assert s.error and s.kind == "WAD"


def make_pk3(tmp_path) -> Path:
    path = tmp_path / "pack.PK3"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("maps/MAP05.wad", build_wad("PWAD", doom_map_lumps("MAP05")))
        zf.writestr("maps/map02.WAD", build_wad("PWAD", doom_map_lumps("MAP02")))
        zf.writestr("MAPINFO", 'map MAP05 "Pk3 Level"\n')
        zf.writestr("zscript.txt", "class X {}")
    return path


def test_pk3(tmp_path):
    path = make_pk3(tmp_path)
    assert list_maps(path) == ["MAP02", "MAP05"]
    s = summarize(path)
    assert (s.kind, s.levels, s.game) == ("PK3", "2", "Doom II")
    assert len(read_map(path, "MAP05").lines) == 7
    assert level_names(path) == {"MAP05": "Pk3 Level"}
    rows = dict(details(path)[1][1])
    assert rows["Levels"] == "2  (MAP02 – MAP05)" and "ZScript" in rows["Contains"]


def test_level_names_umapinfo(mapwad):
    umapinfo = b'map MAP01 {\n  levelname = "First Level"\n}\nmap MAP02 { levelname = "Second" }\n'
    path = mapwad("u.wad", ["MAP01", "MAP02"], extra=[("UMAPINFO", umapinfo)])
    assert level_names(path) == {"MAP01": "First Level", "MAP02": "Second"}


def test_wad_details(mapwad):
    path = mapwad("d.wad", ["MAP01"], extra=[("DEHACKED", b"x"), ("D_RUNNIN", b"MUS")])
    general, doom, totals = details(path)
    assert totals[0] == "Level totals"
    t = dict(totals[1])
    assert (t["Total things"], t["Total monsters (UV)"], t["Total secrets"], t["Total linedefs"],
            t["Total sidedefs"], t["Total vertices"], t["Total sectors"]) == ("8", "2", "1", "7", "8", "6", "2")
    assert t["Largest map"] == "MAP01 (7 linedefs)"
    assert general[0] == "General" and dict(general[1])["Name"] == "d.wad"
    rows = dict(doom[1])
    assert rows["WAD type"].startswith("PWAD") and rows["Levels"] == "1  (MAP01)"
    assert rows["Map format"] == "Doom (1)" and "DeHackEd patch" in rows["Contains"]
    assert rows["Music lumps"] == "1"


def test_demo_details():
    rows = dict(details(DEMOS / "dsda_cl21_mbf21.lmp")[1][1])
    assert rows["Format"] == "MBF21" and rows["Recorded with"] == "dsda-doom 0.27.5"
    assert rows["Length"] == "0:04.49 (157 tics)" and rows["IWAD"] == "freedoom2.wad"


def test_deh_details(tmp_path):
    p = tmp_path / "x.DEH"
    p.write_text("Patch File for DeHackEd v3.0\nDoom version = 21\nPatch format = 6\n\nThing 1 (Player)\n"
                 "Frame 2\nFrame 3\n")
    rows = dict(details(p)[1][1])
    assert rows["Doom version"] == "21" and rows["Sections"] == "Thing ×1, Frame ×2"


def test_details_of_broken_file(tmp_path):
    p = tmp_path / "bad.lmp"
    p.write_bytes(b"\x01")
    assert details(p)[-1][0] == "Problem"


def test_totals_over_several_maps_and_pk3(mapwad, tmp_path):
    rows = dict(details(mapwad("three.wad", ["MAP01", "MAP02", "MAP03"]))[2][1])
    assert rows["Total things"] == "24" and rows["Total linedefs"] == "21" and rows["Total secrets"] == "3"
    pk3_rows = dict(details(make_pk3(tmp_path))[2][1])
    assert pk3_rows["Total monsters (UV)"] == "4"
    assert len(details(DEMOS / "dsda_cl9_boom.lmp")) == 2  # no totals for non-level files


def test_totals_count_udmf_and_hexen():
    from conftest import build_wad
    from test_mapdata import UDMF

    from dww_launcher.mapdata import wad_totals
    from dww_launcher.wad import WadFile

    wad = WadFile(build_wad("PWAD", [("MAP07", b""), ("TEXTMAP", UDMF), ("ENDMAP", b"")]))
    totals, largest = wad_totals([wad])
    assert totals == {"things": 3, "monsters": 1, "secrets": 1, "linedefs": 2, "sidedefs": 3,
                      "vertices": 3, "sectors": 2}
    assert largest == ("MAP07", 2)
