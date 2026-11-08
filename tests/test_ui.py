"""GUI tests with a fake source port (run headless via QT_QPA_PLATFORM=offscreen)."""

import os
import sys
import zipfile
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox  # noqa: E402

from dww_launcher.config import Settings  # noqa: E402
from dww_launcher.ui.main_window import MainWindow  # noqa: E402

# Mimics DSDA-Doom: each attempt in a session writes <name>-NNNNN.lmp with the next
# free counter. "-attempts N" (test-only) simulates restarting the level N-1 times.
FAKE_PORT = """\
import os, sys, time
args = sys.argv[1:]
print("fake port args:", " ".join(args))
attempts = int(args[args.index("-attempts") + 1]) if "-attempts" in args else 1
if "-record" in args:
    base = args[args.index("-record") + 1]
    for _ in range(attempts):
        n = 1
        while os.path.exists(f"{base}-{n:05d}.lmp"):
            n += 1
        with open(f"{base}-{n:05d}.lmp", "wb") as f:
            f.write(f"attempt {n}".encode())
        time.sleep(0.02)
"""


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_windows(qapp, monkeypatch):
    """Never write real settings, and close all windows after each test.

    Closing a window saves settings, so the save patch must outlive the close.
    """
    monkeypatch.setattr(Settings, "save", lambda self, path=None: None)
    yield
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    QApplication.sendPostedEvents()
    QApplication.processEvents()


@pytest.fixture
def no_dialogs(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("unexpected dialog")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fail))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fail))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(fail))


def map_labels(w):
    return [w.map_combo.itemText(i) for i in range(w.map_combo.count())]


def run_game(w):
    w._launch()
    loop = QEventLoop()
    w.process.finished.connect(lambda *_: QTimer.singleShot(0, loop.quit))
    QTimer.singleShot(10000, loop.quit)
    loop.exec()


@pytest.fixture
def fake_port(tmp_path):
    if sys.platform == "win32":
        pytest.skip("fake port uses a shebang script")
    port = tmp_path / "fakeport"
    port.write_text(f"#!{sys.executable}\n{FAKE_PORT}")
    port.chmod(0o755)
    return port


def test_maps_and_auto_select(qapp, tmp_path, wad_factory):
    iwad = wad_factory("doom2.wad", "IWAD", ["MAP01", "MAP02", "MAP03"])
    w = MainWindow(Settings(iwads=[str(iwad)], demo_dir=str(tmp_path / "demos")))
    assert w.record_name.text() == "doom2-map01"

    pwad = wad_factory("zzsketch.wad", "PWAD", ["MAP02", "MAP33"])
    w.pwad_list.add_paths([str(pwad)])

    assert map_labels(w) == [
        "(start normally)", "MAP01", "MAP02   — replaced by zzsketch.wad", "MAP03",
        "MAP33   — new in zzsketch.wad",
    ]
    assert w.map_combo.currentData() == "MAP02"  # mod's first level auto-selected
    assert w.pwad_list.item(0).text() == "zzsketch.wad   (2 levels)"
    assert w.record_name.text() == "zzsketch_1"

    w.record_check.setChecked(True)
    assert f"-record {tmp_path / 'demos' / 'zzsketch_1'}" in w.command_preview.toPlainText()

    w.sequence_check.setChecked(False)
    assert w.record_name.text() == "zzsketch"


def test_manual_name_is_kept(qapp, tmp_path, wad_factory):
    iwad = wad_factory("doom2.wad", "IWAD", ["MAP01", "MAP02"])
    w = MainWindow(Settings(iwads=[str(iwad)], demo_dir=str(tmp_path)))
    w.record_name.textEdited.emit("my-run")
    w.record_name.setText("my-run")
    w.map_combo.setCurrentIndex(w.map_combo.findData("MAP02"))
    assert w.record_name.text() == "my-run"
    w._reset_auto_name()
    assert w.record_name.text() == "doom2-map02"


def test_v12_complevel(qapp, tmp_path, wad_factory):
    iwad = wad_factory("doom.wad", "IWAD", ["E1M1", "E2M1", "E3M1"])
    old = wad_factory("old.wad", "PWAD", ["E1M1"])
    ts = datetime(1994, 3, 15, 12).timestamp()
    os.utime(old, (ts, ts))
    new = wad_factory("new.wad", "PWAD", ["E1M2"])

    w = MainWindow(Settings(iwads=[str(iwad)], demo_dir=str(tmp_path)))
    w.pwad_list.add_paths([str(new)])
    assert w.complevel_combo.currentData() is None

    w.pwad_list.add_paths([str(old)])
    assert w.complevel_combo.currentData() == 0
    assert not w.complevel_note.isHidden()
    assert "old.wad" in w.complevel_note.text() and "1994-03-15" in w.complevel_note.text()

    w._select_data(w.complevel_combo, 2)  # user changes it -> note disappears
    assert w.complevel_note.isHidden()


def test_v12_complevel_not_for_doom2(qapp, tmp_path, wad_factory):
    iwad = wad_factory("doom2.wad", "IWAD", ["MAP01"])
    old = wad_factory("old.wad", "PWAD", ["MAP01"])
    ts = datetime(1994, 3, 15, 12).timestamp()
    os.utime(old, (ts, ts))
    w = MainWindow(Settings(iwads=[str(iwad)], demo_dir=str(tmp_path)))
    w.pwad_list.add_paths([str(old)])
    assert w.complevel_combo.currentData() is None


def test_record_sequence_and_keep_latest(qapp, tmp_path, wad_factory, fake_port, no_dialogs):
    iwad = wad_factory("doom2.wad", "IWAD", ["MAP01"])
    pwad = wad_factory("zzsketch.wad", "PWAD", ["MAP01", "MAP02", "MAP03"])
    demo_dir = tmp_path / "demos"
    w = MainWindow(Settings(ports=[str(fake_port)], iwads=[str(iwad)], last_pwads=[str(pwad)],
                            demo_dir=str(demo_dir), keep_latest_attempt=True))
    w.record_check.setChecked(True)
    w.dsdademo_check.setChecked(True)
    assert w.record_name.text() == "zzsketch_1"

    # Simulate leftover attempts from an earlier, crashed session of the same demo.
    demo_dir.mkdir()
    (demo_dir / "zzsketch_1-00001.lmp").write_bytes(b"old attempt")
    w._refresh_preview()
    assert w.record_name.text() == "zzsketch_2"  # unconsolidated attempts count too
    (demo_dir / "zzsketch_1-00001.lmp").unlink()
    w._refresh_preview()

    run_game(w)
    assert "-dsdademo" in w.log.toPlainText()
    assert (demo_dir / "zzsketch_1.lmp").read_bytes() == b"attempt 1"
    assert w.record_name.text() == "zzsketch_2"  # advanced after recording

    # Three attempts in one session -> only the newest survives as zzsketch_2.lmp
    w.extra_edit.setText("-attempts 3")
    run_game(w)
    assert "Deleted 2 unsuccessful attempt(s)" in w.log.toPlainText()
    assert sorted(p.name for p in demo_dir.iterdir()) == ["zzsketch_1.lmp", "zzsketch_2.lmp"]
    assert (demo_dir / "zzsketch_2.lmp").read_bytes() == b"attempt 3"
    assert w.record_name.text() == "zzsketch_3"

    # With the toggle off, attempts are left alone (and still advance the sequence).
    w.keep_latest_check.setChecked(False)
    run_game(w)
    assert {p.name for p in demo_dir.glob("zzsketch_3-*.lmp")} == {
        "zzsketch_3-00001.lmp", "zzsketch_3-00002.lmp", "zzsketch_3-00003.lmp"}
    assert w.record_name.text() == "zzsketch_4"


def test_playback_options(qapp, tmp_path, wad_factory, fake_port, no_dialogs, monkeypatch):
    iwad = wad_factory("doom2.wad", "IWAD", ["MAP01"])
    demo = tmp_path / "x.lmp"
    demo.write_bytes(b"demo")
    w = MainWindow(Settings(ports=[str(fake_port)], iwads=[str(iwad)], demo_dir=str(tmp_path)))
    w.timedemo_check.setChecked(True)
    w.skipsec_edit.setText("90")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(demo), "")))
    w._play_demo()
    loop = QEventLoop()
    w.process.finished.connect(lambda *_: QTimer.singleShot(0, loop.quit))
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    assert f"-timedemo {demo} -skipsec 90" in w.log.toPlainText()


def test_package_multiple(qapp, tmp_path, wad_factory, no_dialogs, monkeypatch):
    iwad = wad_factory("doom2.wad", "IWAD", ["MAP01"])
    demos = []
    for i in (1, 2):
        d = tmp_path / f"zzsketch_{i}.lmp"
        d.write_bytes(b"demo")
        demos.append(str(d))
    out_zip = tmp_path / "pkg.zip"
    seen_default = []
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: (demos, "")))
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (seen_default.append(a[2]) or str(out_zip), "")))
    monkeypatch.setattr(QInputDialog, "getMultiLineText", staticmethod(lambda *a, **k: ("", True)))

    w = MainWindow(Settings(iwads=[str(iwad)], demo_dir=str(tmp_path)))
    w._package_existing_demos()

    assert seen_default == [str(tmp_path / "zzsketch.zip")]
    with zipfile.ZipFile(out_zip) as zf:
        assert set(zf.namelist()) == {"zzsketch_1.lmp", "zzsketch_2.lmp", "manifest.json"}
