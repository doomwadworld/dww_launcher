from dww_launcher.config import Settings


def test_roundtrip(tmp_path):
    path = tmp_path / "cfg" / "settings.json"
    s = Settings(ports=["/a/dsda-doom"], iwads=["/w/doom2.wad"], last_pwads=["m.wad"])
    s.save(path)
    assert Settings.load(path) == s


def test_missing_or_corrupt(tmp_path):
    assert Settings.load(tmp_path / "missing.json") == Settings()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert Settings.load(bad) == Settings()


def test_ignores_unknown_keys(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"ports": ["x"], "future_option": 1}')
    assert Settings.load(path).ports == ["x"]
