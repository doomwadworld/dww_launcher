import struct
from pathlib import Path

import pytest

from conftest import build_wad, doom_map_lumps
from dww_launcher.mapdata import MapError, find_maps, load_map, map_stats
from dww_launcher.wad import WadFile


def stats(m):
    return dict(map_stats(m))


def test_doom_map():
    wad = WadFile(build_wad("PWAD", doom_map_lumps("MAP01")))
    m = load_map(wad, "MAP01")
    assert m.format == "Doom"
    assert len(m.vertices) == 6 and len(m.lines) == 7 and len(m.things) == 8
    assert sum(1 for line in m.lines if line.two_sided) == 1
    s = stats(m)
    assert s["Monsters (UV)"] == "2"  # easy-only and multiplayer-only imps excluded
    assert s["Most common"] == "2 Imp"
    assert s["Secrets"] == "1"
    assert s["Exit linedef"] == "Yes" and s["Secret exit"] == "No"
    assert s["Keys"] == "Blue keycard"
    assert s["Weapons (UV)"] == "Shotgun"
    assert s["Player starts"] == "1 co-op, 1 deathmatch"
    assert s["Size"] == "256 × 256 units"
    assert s["Sidedefs"] == "8" and s["Sectors"] == "2"


def test_find_maps_by_structure():
    lumps = [("PLAYPAL", b"x")] + doom_map_lumps("E1M1") + doom_map_lumps("CUSTOM") + [("ENDOOM", b"")]
    wad = WadFile(build_wad("PWAD", lumps))
    assert [n for n, _ in find_maps(wad)] == ["E1M1", "CUSTOM"]  # non-standard names too


def test_missing_map():
    with pytest.raises(MapError):
        load_map(WadFile(build_wad("PWAD", doom_map_lumps())), "MAP02")


def test_boom_generalised_secret():
    lumps = doom_map_lumps()
    sectors = struct.pack("<hh8s8shhh", 0, 128, b"F", b"C", 160, 128 | 3, 0)
    lumps = [(n, sectors if n == "SECTORS" else d) for n, d in lumps]
    assert stats(load_map(WadFile(build_wad("PWAD", lumps)), "MAP01"))["Secrets"] == "1"


def test_hexen_format():
    verts = b"".join(struct.pack("<hh", *v) for v in [(0, 0), (64, 0), (64, 64)])
    # v1, v2, flags, special, args*5, front, back
    lines = struct.pack("<HHHB5BHH", 0, 1, 1, 244, 0, 0, 0, 0, 0, 0, 0xFFFF) + \
        struct.pack("<HHHB5BHH", 1, 2, 1, 0, 0, 0, 0, 0, 0, 0, 1)
    # tid, x, y, z, angle, type, flags, special, args*5 (0x0104 = hard skill + single player)
    things = struct.pack("<hhhhhhhB5B", 0, 8, 8, 0, 0, 3001, 0x0104, 0, 0, 0, 0, 0, 0) + \
        struct.pack("<hhhhhhhB5B", 0, 8, 8, 0, 0, 3001, 0x0204, 0, 0, 0, 0, 0, 0)
    sector = struct.pack("<hh8s8shhh", 0, 128, b"F", b"C", 160, 1024, 0)
    lumps = [("MAP01", b""), ("THINGS", things), ("LINEDEFS", lines), ("SIDEDEFS", b""),
             ("VERTEXES", verts), ("SECTORS", sector), ("BEHAVIOR", b"ACS\0")]
    m = load_map(WadFile(build_wad("PWAD", lumps)), "MAP01")
    s = stats(m)
    assert m.format == "Hexen"
    assert s["Monsters (UV)"] == "1"  # the co-op-only one is excluded
    assert s["Secret exit"] == "Yes" and s["Exit linedef"].startswith("No")
    assert s["Secrets"] == "1"


UDMF = b'''
namespace = "zdoom";
// a comment
vertex { x = 0.0; y = 0.0; }
vertex { x = 512.0; y = 0.0; }
vertex { x = 512.0; y = 256.0; }
linedef { v1 = 0; v2 = 1; sidefront = 0; special = 243; }
linedef { v1 = 1; v2 = 2; sidefront = 1; sideback = 2; }
sidedef { sector = 0; } sidedef { sector = 0; } sidedef { sector = 0; }
sector { texturefloor = "F"; textureceiling = "C"; heightceiling = 128; secret = true; }
sector { special = 0; }
thing { type = 3001; x = 10.0; y = 10.0; skill4 = true; single = true; }
thing { type = 3004; x = 20.0; y = 10.0; skill1 = true; single = true; }
thing { type = 1; x = 0.0; y = 0.0; skill4 = true; single = true; }
'''


def test_udmf_format():
    lumps = [("MAP07", b""), ("TEXTMAP", UDMF), ("ZNODES", b""), ("ENDMAP", b"")]
    wad = WadFile(build_wad("PWAD", lumps))
    assert [n for n, _ in find_maps(wad)] == ["MAP07"]
    m = load_map(wad, "MAP07")
    s = stats(m)
    assert m.format == "UDMF"
    assert len(m.vertices) == 3 and len(m.lines) == 2 and m.lines[1].two_sided
    assert s["Monsters (UV)"] == "1" and s["Secrets"] == "1" and s["Exit linedef"] == "Yes"
    assert s["Sidedefs"] == "3" and s["Size"] == "512 × 256 units"


FREEDOOM2 = Path("/usr/share/games/doom/freedoom2.wad")


@pytest.mark.skipif(not FREEDOOM2.exists(), reason="Freedoom not installed")
def test_real_freedoom_maps():
    wad = WadFile(FREEDOOM2)
    maps = find_maps(wad)
    assert len(maps) == 32
    assert stats(load_map(wad, "MAP30"))["Exit linedef"].startswith("No (ends when the Icon of Sin")
    for name, _ in maps:
        assert load_map(wad, name).lines


def test_sector_outlines_and_side_sectors():
    lumps = doom_map_lumps()
    # Left half of the square is sector 0, right half (the secret) is sector 1; line 6 divides them.
    sides = [0, 1, 1, 1, 0, 0, 0, 1]
    sidedefs = b"".join(struct.pack("<hh8s8s8sH", 0, 0, b"-", b"-", b"-", s) for s in sides)
    lumps = [(n, sidedefs if n == "SIDEDEFS" else d) for n, d in lumps]
    m = load_map(WadFile(build_wad("PWAD", lumps)), "MAP01")
    assert m.side_sectors == sides and m.sector_secret == [False, True]
    for sector, corners in ((0, {0, 4, 5, 3}), (1, {4, 1, 2, 5})):
        (loop,) = m.sector_outlines(sector)
        assert loop[0] == loop[-1] and set(loop) == corners
    assert m.sector_outlines(5) == []


def test_sector_outline_open_chain():
    """A damaged sector whose lines don't close still yields an outline to draw."""
    m = load_map(WadFile(build_wad("PWAD", doom_map_lumps())), "MAP01")
    m.side_sectors = [0, 0, 1, 0, 0, 0, 0, 1]  # sector 0 is missing its right edge
    assert any(loop[0] != loop[-1] for loop in m.sector_outlines(0))


def test_udmf_side_sectors():
    lumps = [("MAP07", b""), ("TEXTMAP", UDMF), ("ENDMAP", b"")]
    m = load_map(WadFile(build_wad("PWAD", lumps)), "MAP07")
    assert m.side_sectors == [0, 0, 0] and m.sector_secret == [True, False]
    assert m.lines[0].front == 0 and m.lines[1].back == 2


def test_thing_descriptions():
    m = load_map(WadFile(build_wad("PWAD", doom_map_lumps())), "MAP01")
    by_type = {}
    for t in m.things:
        by_type.setdefault(t.type, []).append(t)
    imp_uv, imp_hard, imp_easy, imp_mp = by_type[3001]
    assert imp_uv.name == "Imp" and imp_uv.category == "Monster"
    assert imp_uv.describe().splitlines() == ["Imp", "Monster · type 3001", "All skills", "At (64, 64), facing east"]
    assert "Skills: hard" in imp_hard.describe()
    assert "Skills: easy" in imp_easy.describe()
    assert "multiplayer only" in imp_mp.describe()
    assert by_type[5][0].name == "Blue keycard" and by_type[5][0].category == "Key"
    assert by_type[2001][0].category == "Weapon"
    assert by_type[1][0].category == "Player start"


def test_thing_flags_and_unknown_types():
    from dww_launcher.mapdata import Thing

    t = Thing(3005, 0, 0, uv=True, single=True, angle=135, skills=(False, False, True), ambush=True)
    assert t.describe().splitlines()[2:] == ["Skills: hard · ambush", "At (0, 0), facing north-west"]
    odd = Thing(31337, 1.5, -2.5, uv=True, single=True)
    assert odd.name == "Unknown thing (type 31337)" and odd.category == "Other"


def test_udmf_thing_flags():
    lumps = [("MAP07", b""), ("TEXTMAP", UDMF), ("ENDMAP", b"")]
    m = load_map(WadFile(build_wad("PWAD", lumps)), "MAP07")
    assert m.things[0].skills == (False, False, True)
    assert m.things[1].skills == (True, False, False)


def _lines_map(fmt, lines):
    from dww_launcher.mapdata import MapData

    return MapData("MAP01", fmt, vertices=[(0, 0), (64, 0)], lines=lines)


@pytest.mark.parametrize("special,colour", [
    (26, "blue"), (32, "blue"), (99, "blue"), (133, "blue"),
    (27, "yellow"), (34, "yellow"), (134, "yellow"), (136, "yellow"),
    (28, "red"), (33, "red"), (135, "red"), (137, "red"),
    (0x3800 | (1 << 6), "red"), (0x3800 | (5 << 6), "blue"), (0x3800 | (6 << 6), "yellow"),
    (0x3800, None), (0x3800 | (7 << 6), None),  # Boom "any key" / "all keys"
    (1, None), (11, None),
])
def test_doom_key_lines(special, colour):
    from dww_launcher.mapdata import Line

    m = _lines_map("Doom", [Line(0, 1, special, False)])
    assert m.key_colour(m.lines[0]) == colour


def test_hexen_and_udmf_key_lines():
    from dww_launcher.mapdata import Line

    m = _lines_map("Hexen", [Line(0, 1, 13, False, args=(0, 0, 0, 2, 0)),      # Door_LockedRaise, blue card
                             Line(0, 1, 202, False, args=(0, 0, 0, 0, 131)),   # Generic_Door, any yellow
                             Line(0, 1, 83, False, args=(0, 0, 0, 0, 4)),      # ACS_LockedExecute, red skull
                             Line(0, 1, 13, False, args=(0, 0, 0, 0, 0)),
                             Line(0, 1, 0, False, lock=1)])                    # UDMF locknumber
    assert [m.key_colour(ln) for ln in m.lines] == ["blue", "yellow", "red", None, "red"]
    assert dict(map_stats(m))["Key-locked lines"] == "2 red, 1 yellow, 1 blue"


def test_udmf_lock_fields_are_read():
    text = (b'namespace="zdoom"; vertex{x=0;y=0;} vertex{x=8;y=0;}'
            b'linedef{v1=0;v2=1;sidefront=0;special=13;arg3=3;} linedef{v1=0;v2=1;sidefront=0;locknumber=2;}')
    m = load_map(WadFile(build_wad("PWAD", [("MAP01", b""), ("TEXTMAP", text), ("ENDMAP", b"")])), "MAP01")
    assert [m.key_colour(ln) for ln in m.lines] == ["yellow", "blue"]


def test_doom_map_has_no_key_lines():
    m = load_map(WadFile(build_wad("PWAD", doom_map_lumps())), "MAP01")
    assert dict(map_stats(m))["Key-locked lines"] == "None"
