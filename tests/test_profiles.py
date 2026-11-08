import json

from dww_launcher.profiles import (
    Profile,
    delete_profile,
    list_profiles,
    load_profile,
    profile_exists,
    save_profile,
)


def test_roundtrip(tmp_path):
    p = Profile(port="/p/dsda-doom", iwad="/w/doom2.wad", pwads=["a.wad", "b.deh"], map="MAP07",
                skill=5, complevel=0, fast=True, dsdademo=True, record=True, record_name="run",
                demo_dir="/demos", skipsec="12.5", txt_dir="/txt", txt_dir_same_as_demo=False)
    save_profile("UV Max", p, tmp_path)
    assert load_profile("UV Max", tmp_path) == p
    assert list_profiles(tmp_path) == ["UV Max"]
    assert profile_exists("UV Max", tmp_path)


def test_names_with_unsafe_characters(tmp_path):
    save_profile('a/b:c*?"<>|', Profile(skill=1), tmp_path)
    save_profile("a_b_c", Profile(skill=2), tmp_path)
    assert list_profiles(tmp_path) == ['a/b:c*?"<>|', "a_b_c"]
    assert load_profile('a/b:c*?"<>|', tmp_path).skill == 1
    assert load_profile("a_b_c", tmp_path).skill == 2
    assert all("/" not in f.name and ":" not in f.name for f in tmp_path.iterdir())


def test_overwrite_and_delete(tmp_path):
    save_profile("x", Profile(skill=1), tmp_path)
    save_profile("x", Profile(skill=2), tmp_path)
    assert load_profile("x", tmp_path).skill == 2
    assert len(list(tmp_path.glob("*.json"))) == 1
    delete_profile("x", tmp_path)
    assert list_profiles(tmp_path) == [] and not profile_exists("x", tmp_path)


def test_unknown_and_missing_keys(tmp_path):
    (tmp_path / "old.json").write_text(json.dumps({"name": "old", "skill": 2, "future": 1}))
    p = load_profile("old", tmp_path)
    assert p.skill == 2 and p.pwads == [] and p.txt_dir_same_as_demo is True


def test_list_skips_corrupt(tmp_path):
    (tmp_path / "bad.json").write_text("{nope")
    save_profile("good", Profile(), tmp_path)
    assert list_profiles(tmp_path) == ["good"]
    assert list_profiles(tmp_path / "missing") == []
