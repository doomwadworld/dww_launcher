from datetime import datetime, timedelta, timezone

import pytest

from dww_launcher.sheets import (
    SHEETS,
    all_cached,
    download_due,
    load_sheets,
    parse_csv,
    save_sheets,
    validate_csv,
)

D2_CSV = ("id,zip file name,WAD file name,played by,maps,date,link,HoF/HoS,notes\r\n"
          "12493,zzsketch,zzsketch.wad,joe-ilya,3,2014-08-24,link,HoS,\r\n")
D1_CSV = ("zip file name,WAD file name,maps,played by,link,HoF/HoS,notes\r\n"
          '#1kill,#1KILL.WAD,9,DOOMenstein,https://www.doomworld.com/idgames/levels/doom/0-9/1kill,HoS,"a, b"\r\n')


def all_texts():
    return {k: (D1_CSV if s.game == "doom" else D2_CSV) for k, s in SHEETS.items()}


def test_parse_csv():
    rows = parse_csv(D1_CSV)
    assert rows == [{"zip file name": "#1kill", "WAD file name": "#1KILL.WAD", "maps": "9",
                     "played by": "DOOMenstein",
                     "link": "https://www.doomworld.com/idgames/levels/doom/0-9/1kill",
                     "HoF/HoS": "HoS", "notes": "a, b"}]


def test_validate_csv():
    validate_csv(D2_CSV, SHEETS["doom2_wads"])
    validate_csv(D1_CSV, SHEETS["doom1_wads"])
    with pytest.raises(ValueError):
        validate_csv("<!DOCTYPE html><html>Sign in</html>", SHEETS["doom2_wads"])
    with pytest.raises(ValueError):  # Doom 1 tab without its link column
        validate_csv(D2_CSV, SHEETS["doom1_ports"])


def test_save_and_load(tmp_path):
    assert load_sheets(tmp_path) == {}
    assert not all_cached(tmp_path)
    save_sheets(all_texts(), tmp_path)
    assert all_cached(tmp_path)
    data = load_sheets(tmp_path)
    assert set(data) == set(SHEETS)
    assert data["doom2_wads"][0]["id"] == "12493"
    assert not list(tmp_path.glob("*.tmp"))


def test_download_due(tmp_path):
    now = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
    assert download_due("", tmp_path, now)  # never downloaded
    recent = (now - timedelta(hours=11, minutes=59)).isoformat()
    assert download_due(recent, tmp_path, now)  # files missing
    save_sheets(all_texts(), tmp_path)
    assert not download_due(recent, tmp_path, now)
    assert download_due((now - timedelta(hours=12)).isoformat(), tmp_path, now)
    assert download_due("garbage", tmp_path, now)
