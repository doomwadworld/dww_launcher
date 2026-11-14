import zipfile

import pytest

from dww_launcher.sheets import parse_csv
from dww_launcher.submission import (
    BRACKET_WARNING,
    OVER_LIMIT_WARNING,
    create_submission_zip,
    identifier_line,
    idgames_path,
    lookup,
    submission_text,
    text_warnings,
)

# Small excerpts shaped like the real tabs (including their quirks).
D2_WADS = parse_csv(
    "id,zip file name,WAD file name,played by,maps,date,link,HoF/HoS,notes\n"
    "12493,zzsketch,zzsketch.wad,joe-ilya,3,2014-08-24,link,HoS,\n"
    "11925,arsenal,ARSENAL.WAD,,1,1995-03-04,link,,\n"
    "11925,arsenal,ARSENAL2.WAD,,1,1995-03-17,link,,\n"
    "11000,ports_test,PT.WAD,,1,1995-03-17,link,,\n"
)
D2_PORTS = parse_csv(
    "id,zip file name,WAD file name,played by,maps,date,link,HoF/HoS,source port,map format,notes\n"
    "8599,10line,10linee1.wad,,39,2024-09-22,link,,DSDA-Doom,Vanilla,\n"
    "8599,10line,10linee2.wad,,39,2024-09-22,link,,DSDA-Doom,Vanilla,\n"
    "7617,artifact,ART.WAD,,1,2001-01-01,link,,ZDoom,,\n"
    "13663,artifact,ARTIFACT.WAD,Someone,1,2020-01-01,link,,ZDoom,,\n"
    "9000,aliens,ALIENS.WAD,,1,1999-01-01,link,,ZDoom,,\n"
)
D1_WADS = parse_csv(
    "zip file name,WAD file name,maps,played by,link,HoF/HoS,notes\n"
    "#1kill,#1KILL.WAD,9,DOOMenstein,https://www.doomworld.com/idgames/levels/doom/0-9/1kill,HoS,\n"
    "fresh,FRESH.WAD,1,,https://www.doomworld.com/idgames/levels/doom/d-f/fresh,,\n"
)
D1_PORTS = parse_csv(
    "zip file name,maps,doomworld link,played by,required port,HoF/HoS,notes\n"
    "007ltsd,9,https://www.doomworld.com/idgames/levels/doom/Ports/0-9/007ltsd,,zdoom,,\n"
)
DATA = {"doom2_wads": D2_WADS, "doom2_ports": D2_PORTS, "doom1_wads": D1_WADS, "doom1_ports": D1_PORTS}


@pytest.mark.parametrize("text,path", [
    ("https://www.doomworld.com/idgames/levels/doom2/a-c/zzsketch", "levels/doom2/a-c/zzsketch"),
    ("https://www.doomworld.com/idgames/levels/doom2/a-c/zzsketch/", "levels/doom2/a-c/zzsketch"),
    ("www.doomworld.com/idgames/levels/doom2/a-c/zzsketch", "levels/doom2/a-c/zzsketch"),
    ("https://www.doomworld.com/idgames/levels/doom2/a-c/zzsketch?foo=1", "levels/doom2/a-c/zzsketch"),
    ("levels/doom2/a-c/zzsketch.zip", "levels/doom2/a-c/zzsketch"),
    ("/levels/doom2/a-c/zzsketch", "levels/doom2/a-c/zzsketch"),
    ("idgames://levels/doom2/a-c/zzsketch.zip", "levels/doom2/a-c/zzsketch"),
    ("ftp://mirror.example/pub/idgames/levels/doom/0-9/1kill.zip", "levels/doom/0-9/1kill"),
])
def test_idgames_path(text, path):
    assert idgames_path(text) == path


def test_empty():
    result = lookup("  ", DATA)
    assert result.entry is None and not result.message


def test_by_id():
    result = lookup("12493", DATA)
    assert result.entry.zip_name == "zzsketch"
    assert result.entry.sheet.key == "doom2_wads"
    assert result.warnings == ["Already played by joe-ilya."]


def test_by_id_groups_rows_and_searches_ports():
    result = lookup("8599", DATA)
    assert result.entry.sheet.key == "doom2_ports"
    assert result.entry.wad_files == ["10linee1.wad", "10linee2.wad"]
    assert result.warnings == []


def test_unknown_id():
    assert "No entry with ID 1" in lookup("1", DATA).message


def test_doom2_wad_url():
    result = lookup("https://www.doomworld.com/idgames/levels/doom2/a-c/arsenal", DATA)
    assert result.entry.entry_id == "11925"
    assert len(result.entry.rows) == 2


def test_doom2_ports_path():
    result = lookup("levels/doom2/Ports/0-9/10line", DATA)
    assert result.entry.entry_id == "8599"


def test_ports_check_is_case_sensitive():
    # "ports_test" contains lowercase "ports", which must not send us to the Ports list.
    result = lookup("levels/doom2/p-r/ports_test", DATA)
    assert result.entry.entry_id == "11000"


def test_themes_only_in_doom2_ports():
    result = lookup("https://www.doomworld.com/idgames/Themes/aliens/aliens", DATA)
    assert result.entry.entry_id == "9000"
    assert result.entry.sheet.key == "doom2_ports"


def test_ambiguous_name():
    result = lookup("levels/doom2/Ports/a-c/artifact", DATA)
    assert result.entry is None
    assert len(result.entries) == 2
    assert "7617" in result.message and "13663" in result.message


def test_doom1_matches_link_not_zip_name():
    result = lookup("https://www.doomworld.com/idgames/levels/doom/0-9/1kill", DATA)
    assert result.entry.zip_name == "#1kill"
    assert result.warnings == ["Already played by DOOMenstein."]


def test_doom1_ports():
    result = lookup("levels/doom/Ports/0-9/007ltsd.zip", DATA)
    assert result.entry.sheet.key == "doom1_ports"
    assert result.entry.played_by == []


def test_doom1_not_in_doom2_list():
    result = lookup("levels/doom/a-c/zzsketch", DATA)
    assert result.entry is None
    assert "No match found in the DOOM 1 WAD list" in result.message


@pytest.mark.parametrize("path", ["levels/doom/deathmatch/a-c/foo", "levels/doom2/deathmatch/0-9/bar"])
def test_deathmatch_blocked(path):
    result = lookup(path, DATA)
    assert result.blocked and "Deathmatch" in result.message


def test_unknown_game():
    assert "Doom or Doom II" in lookup("levels/heretic/a-c/foo", DATA).message


def test_not_downloaded():
    assert "hasn't been downloaded" in lookup("levels/doom2/a-c/zzsketch", {}).message
    assert "hasn't been downloaded" in lookup("12493", {}).message


def test_bare_name_rejected():
    assert "Enter a spreadsheet ID" in lookup("zzsketch", DATA).message


def test_text_warnings():
    assert text_warnings("hello", 5) == []
    assert text_warnings("a <b>", 5) == [BRACKET_WARNING]
    assert text_warnings("x", 4501) == [OVER_LIMIT_WARNING]
    assert text_warnings("x", 4500) == []
    assert "5000" in OVER_LIMIT_WARNING


def test_submission_text():
    assert submission_text("12493", "HoF", "Great maps", "Played on UV.\nNo saves.") == \
        "Entry: 12493\nHoF/HoS: HoF\nWhy: Great maps\nComments: Played on UV.\nNo saves.\n"
    assert submission_text("", None, "", "") == "Entry: N/A\nHoF/HoS: N/A\n"
    assert submission_text("x", "HoS", "  ", "c") == "Entry: x\nHoF/HoS: HoS\nComments: c\n"


def test_submission_text_dww_notes_come_last():
    text = submission_text("1", None, "", "Nice.", "  Please credit me as X  ")
    assert text == "Entry: 1\nHoF/HoS: N/A\nComments: Nice.\nDWW Notes: Please credit me as X\n"
    assert submission_text("1", None, "", "", "only notes").endswith("DWW Notes: only notes\n")



@pytest.mark.parametrize("text,expected", [
    ("12493", "12493"),  # ID only: DOOM 2 rows have no URL
    ("https://www.doomworld.com/idgames/levels/doom2/a-c/zzsketch", "12493 | levels/doom2/a-c/zzsketch"),
    ("levels/doom2/Ports/0-9/10line.zip", "8599 | levels/doom2/Ports/0-9/10line"),
    ("https://www.doomworld.com/idgames/levels/doom/0-9/1kill", "levels/doom/0-9/1kill"),  # DOOM 1: no IDs
    ("levels/doom2/a-c/nothere", "levels/doom2/a-c/nothere"),  # no match: what was entered
    ("99999", "99999"),
    ("levels/doom2/Ports/a-c/artifact", "levels/doom2/Ports/a-c/artifact"),  # ambiguous: path only
    ("", "N/A"),
])
def test_identifier_line(text, expected):
    assert identifier_line(text, lookup(text, DATA)) == expected


def test_create_submission_zip(tmp_path):
    demos = []
    for i in (1, 2):
        d = tmp_path / f"zzsketch_{i}.lmp"
        d.write_bytes(b"demo")
        demos.append(d)
    out = create_submission_zip(demos, "zzsketch.txt", "N/A\nhi\n", tmp_path / "out" / "zzsketch.zip")
    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.namelist()) == ["zzsketch.txt", "zzsketch_1.lmp", "zzsketch_2.lmp"]
        assert zf.read("zzsketch.txt") == b"N/A\nhi\n"


def test_create_submission_zip_errors(tmp_path):
    with pytest.raises(ValueError):
        create_submission_zip([], "a.txt", "", tmp_path / "x.zip")
    with pytest.raises(FileNotFoundError):
        create_submission_zip([tmp_path / "missing.lmp"], "a.txt", "", tmp_path / "x.zip")
