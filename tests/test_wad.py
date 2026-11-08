import pytest

from dww_launcher.wad import WadError, map_sort_key, read_wad, warp_args


def test_read_iwad_doom2(wad_factory):
    path = wad_factory("doom2.wad", "IWAD", ["PLAYPAL", "MAP01", "THINGS", "MAP02", "MAP32"])
    info = read_wad(path)
    assert info.is_iwad
    assert info.maps == ["MAP01", "MAP02", "MAP32"]
    assert info.game == "Doom II"


@pytest.mark.parametrize(
    "lumps,game",
    [
        (["E1M1", "E2M1", "E3M1", "E4M1"], "The Ultimate Doom"),
        (["E1M1", "E2M1"], "Doom (registered)"),
        (["E1M1"], "Doom (shareware)"),
        (["MAP01", "CAVERN7"], "Final Doom: TNT Evilution"),
        (["MAP01", "WFALL1"], "Final Doom: The Plutonia Experiment"),
        (["FREEDOOM", "MAP01"], "Freedoom: Phase 2"),
        (["FREEDOOM", "E1M1"], "Freedoom: Phase 1"),
    ],
)
def test_identify_game(wad_factory, lumps, game):
    assert read_wad(wad_factory("x.wad", "IWAD", lumps)).game == game


def test_pwad(wad_factory):
    info = read_wad(wad_factory("mod.wad", "PWAD", ["MAP05"]))
    assert not info.is_iwad
    assert info.maps == ["MAP05"]


def test_not_a_wad(tmp_path):
    bad = tmp_path / "bad.wad"
    bad.write_bytes(b"PK\x03\x04garbage!")
    with pytest.raises(WadError):
        read_wad(bad)


def test_truncated(tmp_path):
    bad = tmp_path / "short.wad"
    bad.write_bytes(b"IWAD")
    with pytest.raises(WadError):
        read_wad(bad)


def test_warp_args():
    assert warp_args("E2M5") == ["2", "5"]
    assert warp_args("map07") == ["7"]
    assert warp_args("MAP32") == ["32"]
    with pytest.raises(ValueError):
        warp_args("STARTAN3")


def test_map_sort_key():
    assert sorted(["MAP10", "MAP02", "MAP01"], key=map_sort_key) == ["MAP01", "MAP02", "MAP10"]
    assert sorted(["E2M1", "E1M9", "E1M1"], key=map_sort_key) == ["E1M1", "E1M9", "E2M1"]
