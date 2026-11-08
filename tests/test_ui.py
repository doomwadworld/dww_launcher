"""GUI tests with a fake source port (run headless via QT_QPA_PLATFORM=offscreen)."""

import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, Qt, QTimer  # noqa: E402
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
def isolated_windows(qapp, monkeypatch, tmp_path):
    """Never touch real settings or spreadsheet data, and close all windows after each test.

    Closing a window saves settings, so the save patch must outlive the close.
    """
    monkeypatch.setattr(Settings, "save", lambda self, path=None: None)
    monkeypatch.setattr("dww_launcher.ui.main_window.sheets_dir", lambda: tmp_path / "sheets")
    monkeypatch.setattr("dww_launcher.ui.main_window.profiles_dir", lambda: tmp_path / "profiles")
    opened = []
    monkeypatch.setattr("dww_launcher.ui.main_window.open_folder", lambda p: opened.append(str(p)) or True)
    monkeypatch.setattr("dww_launcher.ui.main_window.open_url", lambda u: opened.append(u) or True)
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


# ------------------------------------------------------------ submission tab

D2_CSV = ("id,zip file name,WAD file name,played by,maps,date,link,HoF/HoS,notes\n"
          "12493,zzsketch,zzsketch.wad,joe-ilya,3,2014-08-24,link,HoS,\n"
          "12000,fresh,fresh.wad,,1,2014-08-24,link,,\n")
D2P_CSV = ("id,zip file name,WAD file name,played by,maps,date,link,HoF/HoS,source port,map format,notes\n"
           "8599,10line,10linee1.wad,,39,2024-09-22,link,,DSDA-Doom,Vanilla,\n")
D1_CSV = ("zip file name,WAD file name,maps,played by,link,HoF/HoS,notes\n"
          "fresh1,F.WAD,1,,https://www.doomworld.com/idgames/levels/doom/d-f/fresh1,,\n")
D1P_CSV = ("zip file name,maps,doomworld link,played by,required port,HoF/HoS,notes\n"
           "007ltsd,9,https://www.doomworld.com/idgames/levels/doom/Ports/0-9/007ltsd,,zdoom,,\n")
CSV_TEXTS = {"doom2_wads": D2_CSV, "doom2_ports": D2P_CSV, "doom1_wads": D1_CSV, "doom1_ports": D1P_CSV}


@pytest.fixture
def sheet_window(tmp_path, wad_factory):
    from dww_launcher.sheets import save_sheets

    save_sheets(CSV_TEXTS, tmp_path / "sheets")
    iwad = wad_factory("doom2.wad", "IWAD", ["MAP01"])
    demo_dir = tmp_path / "demos"
    demo_dir.mkdir()
    w = MainWindow(Settings(iwads=[str(iwad)], demo_dir=str(demo_dir), auto_download=False))
    return w


def test_submission_lookup_status(qapp, sheet_window):
    w = sheet_window
    w.entry_edit.setText("12493")
    status = w.entry_status.text()
    assert "Matched: zzsketch" in status and "Already played by joe-ilya" in status
    assert w.txt_name_edit.text() == "zzsketch.txt"

    w.entry_edit.setText("https://www.doomworld.com/idgames/levels/doom2/a-c/nothere")
    assert "No match found" in w.entry_status.text()

    w.entry_edit.setText("levels/doom2/deathmatch/a-c/foo")
    assert "Deathmatch" in w.entry_status.text()
    assert not w.create_zip_button.isEnabled() and not w.create_txt_button.isEnabled()
    assert "Deathmatch" in w.create_zip_button.toolTip()

    w.entry_edit.setText("levels/doom/Ports/0-9/007ltsd")
    assert "Matched: 007ltsd" in w.entry_status.text()
    assert "Already played" not in w.entry_status.text()
    assert w.create_zip_button.isEnabled()


def test_hall_of_fame_rules(qapp, sheet_window):
    w = sheet_window
    assert w.create_zip_button.isEnabled()
    w.hof_check.setChecked(True)
    assert not w.create_zip_button.isEnabled()
    assert '"Why?"' in w.create_zip_button.toolTip()
    w.hos_check.setChecked(True)  # mutually exclusive
    assert not w.hof_check.isChecked()
    w.why_edit.setPlainText("   ")
    assert not w.create_zip_button.isEnabled()
    w.why_edit.setPlainText("Amazing")
    assert w.create_zip_button.isEnabled() and w.create_txt_button.isEnabled()
    w.hos_check.setChecked(False)  # both can be unchecked
    assert not w.hof_check.isChecked() and not w.hos_check.isChecked()


def test_counters_and_warnings(qapp, sheet_window):
    w = sheet_window
    w.why_edit.setPlainText("abc")
    w.comments_edit.setPlainText("12345")
    assert w.why_counter.text() == "8/4500" and w.comments_counter.text() == "8/4500"
    assert w.why_warning.isHidden() and w.comments_warning.isHidden()

    w.comments_edit.setPlainText("<b>")
    assert not w.comments_warning.isHidden() and "angle brackets" in w.comments_warning.text()
    assert w.why_warning.isHidden()

    w.comments_edit.setPlainText("x" * 4498)
    assert w.comments_counter.text() == "4501/4500"
    assert "5000 characters" in w.comments_warning.text()
    assert "5000 characters" in w.why_warning.text()
    assert w.create_zip_button.isEnabled()  # a warning, not a blocker


def test_create_text_and_zip(qapp, sheet_window, tmp_path, monkeypatch, no_dialogs):
    w = sheet_window
    demo_dir = Path(w.demo_dir_edit.text())
    w.entry_edit.setText("12493")
    w.hof_check.setChecked(True)
    w.why_edit.setPlainText("Great layouts")
    w.comments_edit.setPlainText("UV max.\nNo saves.")

    w._create_text_file()
    assert (demo_dir / "zzsketch.txt").read_text() == "12493\nHoF\nGreat layouts\nUV max.\nNo saves.\n"

    demos = []
    for i in (1, 2):
        d = demo_dir / f"zzsketch_{i}.lmp"
        d.write_bytes(b"demo")
        demos.append(str(d))
    seen = []
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: (demos, "")))
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (seen.append(a[2]) or a[2], "")))
    w._create_zip_file()
    assert seen == [str(demo_dir / "zzsketch.zip")]
    with zipfile.ZipFile(demo_dir / "zzsketch.zip") as zf:
        assert sorted(zf.namelist()) == ["zzsketch.txt", "zzsketch_1.lmp", "zzsketch_2.lmp"]
        assert zf.read("zzsketch.txt").decode().startswith("12493\nHoF\nGreat layouts\n")


def test_package_button_moved(qapp, sheet_window):
    labels = [b.text() for b in sheet_window.findChildren(type(sheet_window.create_zip_button))]
    assert "Create Zip File…" in labels
    assert not any("Package" in t for t in labels)


# --------------------------------------------------------------- downloads

class _Server:
    """Tiny local HTTP server; /redirect/<key> 302s to /csv/<key> like Google does."""

    def __init__(self, routes):
        import http.server
        import threading

        routes_ = routes

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/redirect/"):
                    self.send_response(302)
                    self.send_header("Location", "/csv/" + self.path.split("/")[-1])
                    self.end_headers()
                    return
                body = routes_.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    from PySide6.QtNetwork import QNetworkProxy

    QNetworkProxy.setApplicationProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
    srv = _Server({f"/csv/{k}": v for k, v in CSV_TEXTS.items()})
    yield srv
    srv.close()


def wait_for_download(w):
    if not w._downloader.running:
        return
    loop = QEventLoop()
    w._downloader.finished.connect(lambda *_: QTimer.singleShot(0, loop.quit))
    QTimer.singleShot(10000, loop.quit)
    loop.exec()


def test_download_saves_and_refreshes(qapp, tmp_path, server):
    urls = {k: f"{server.base}/redirect/{k}" for k in CSV_TEXTS}
    s = Settings(demo_dir=str(tmp_path), auto_download=True)
    w = MainWindow(s, sheet_urls=urls)
    w.entry_edit.setText("12493")
    assert "been downloaded yet" in w.entry_status.text()
    assert w.last_download_action.text() == "Not downloaded yet"

    w.download_sheets(manual=True)
    wait_for_download(w)

    assert sorted(p.name for p in (tmp_path / "sheets").iterdir()) == sorted(f"{k}.csv" for k in CSV_TEXTS)
    assert s.sheets_downloaded_at
    assert w.last_download_action.text().startswith("Last downloaded: ")
    assert "Matched: zzsketch" in w.entry_status.text()  # lookup re-ran with fresh data


def test_download_failure_keeps_timestamp(qapp, tmp_path, server, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))
    urls = {k: f"{server.base}/csv/{k}" for k in CSV_TEXTS}
    urls["doom1_ports"] = f"{server.base}/missing"
    s = Settings(demo_dir=str(tmp_path), auto_download=True)
    w = MainWindow(s, sheet_urls=urls)
    w.download_sheets(manual=True)
    wait_for_download(w)
    assert s.sheets_downloaded_at == ""
    assert warnings and "DOOM 1 Ports list" in warnings[0]
    assert (tmp_path / "sheets" / "doom2_wads.csv").exists()  # good ones are still saved


def test_first_launch_prompt_yes(qapp, tmp_path, server, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes))
    s = Settings(demo_dir=str(tmp_path))
    assert s.auto_download is None
    w = MainWindow(s, sheet_urls={k: f"{server.base}/csv/{k}" for k in CSV_TEXTS})
    w.startup()
    assert len(asked) == 1 and "12 hours" in asked[0]
    assert s.auto_download is True and w.auto_download_action.isChecked()
    wait_for_download(w)
    assert s.sheets_downloaded_at


def test_first_launch_prompt_no(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    s = Settings(demo_dir=str(tmp_path))
    w = MainWindow(s, sheet_urls={k: "http://127.0.0.1:9/x" for k in CSV_TEXTS})
    w.startup()
    assert s.auto_download is False and not w.auto_download_action.isChecked()
    assert not w._downloader.running


def test_startup_skips_recent_download(qapp, tmp_path, no_dialogs):
    from datetime import datetime, timezone

    from dww_launcher.sheets import save_sheets

    save_sheets(CSV_TEXTS, tmp_path / "sheets")
    s = Settings(demo_dir=str(tmp_path), auto_download=True,
                 sheets_downloaded_at=datetime.now(timezone.utc).isoformat())
    w = MainWindow(s, sheet_urls={k: "http://127.0.0.1:9/x" for k in CSV_TEXTS})
    w.startup()
    assert not w._downloader.running  # downloaded less than 12 hours ago
    s.sheets_downloaded_at = "2000-01-01T00:00:00+00:00"
    w.startup()
    assert w._downloader.running
    wait_for_download(w)


def test_txt_name_follows_match_unless_edited(qapp, sheet_window):
    w = sheet_window
    w.entry_edit.setText("12493")
    assert w.txt_name_edit.text() == "zzsketch.txt"
    w.entry_edit.setText("levels/doom2/a-c/nothere")
    assert w.txt_name_edit.text() == ""
    w.entry_edit.setText("12000")
    assert w.txt_name_edit.text() == "fresh.txt"
    w.txt_name_edit.setText("custom.txt")
    w.entry_edit.setText("12493")
    assert w.txt_name_edit.text() == "custom.txt"


def test_app_icon(qapp):
    from dww_launcher.icons import app_icon

    icon = app_icon()
    assert not icon.isNull()
    sizes = {s.width() for s in icon.availableSizes()}
    assert {16, 32, 256} <= sizes



# ------------------------------------------------------- text file folder

def test_txt_dir_follows_demo_dir(qapp, sheet_window, tmp_path, monkeypatch, no_dialogs):
    w = sheet_window
    demo_dir = w.demo_dir_edit.text()
    assert w.txt_same_check.isChecked()
    assert w.txt_dir_edit.text() == demo_dir and not w.txt_dir_change.isEnabled()

    other = tmp_path / "texts"
    w.txt_same_check.setChecked(False)
    assert w.txt_dir_change.isEnabled()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(other)))
    w._choose_txt_dir()
    assert w.txt_dir_edit.text() == str(other)

    w.entry_edit.setText("12000")
    w._create_text_file()
    assert (other / "fresh.txt").read_text().startswith("12000\nN/A\n")
    assert w.settings.txt_dir == str(other) and not w.settings.txt_dir_same_as_demo

    new_demo = tmp_path / "newdemos"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(new_demo)))
    w._choose_demo_dir()
    assert w.txt_dir_edit.text() == str(other)  # not linked
    w.txt_same_check.setChecked(True)
    assert w.txt_dir_edit.text() == str(new_demo)
    w.txt_same_check.setChecked(False)
    assert w.txt_dir_edit.text() == str(other)  # custom folder remembered


def test_open_buttons_use_safe_opener(qapp, sheet_window):
    import dww_launcher.ui.main_window as mw

    w = sheet_window
    calls = []
    mw.open_folder = lambda p: calls.append(p) or True  # (restored by monkeypatch fixture)
    w._open_demo_dir()
    w._open_folder(w.txt_dir_edit.text())
    assert calls == [w.demo_dir_edit.text(), w.txt_dir_edit.text()]


# --------------------------------------------------------------- profiles

def test_profile_roundtrip(qapp, tmp_path, wad_factory, monkeypatch, no_dialogs):
    iwad = wad_factory("doom2.wad", "IWAD", ["MAP01", "MAP02", "MAP03"])
    pwad = wad_factory("zzsketch.wad", "PWAD", ["MAP01", "MAP02"])
    port = tmp_path / "dsda-doom"
    port.write_text("")
    w = MainWindow(Settings(ports=[str(port)], iwads=[str(iwad)], demo_dir=str(tmp_path / "d"),
                            auto_download=False))
    w.pwad_list.add_paths([str(pwad)])
    w.map_combo.setCurrentIndex(w.map_combo.findData("MAP02"))
    w._select_data(w.skill_combo, 3)
    w._select_data(w.complevel_combo, 9)
    w.fast_check.setChecked(True)
    w.tas_check.setChecked(True)
    w.extra_edit.setText("-nomusic")
    w.record_check.setChecked(True)
    w.keep_latest_check.setChecked(True)
    w.timedemo_check.setChecked(True)
    w.skipsec_edit.setText("30")
    w.txt_same_check.setChecked(False)
    snapshot = w.capture_profile()
    preview = w.command_preview.toPlainText()

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Speedrun: UV/Fast", True)))
    w._save_profile_as()
    assert w.current_profile == "Speedrun: UV/Fast"
    assert "Speedrun: UV/Fast" in w.windowTitle()

    # Change everything, then load the profile back.
    w.pwad_list.clear()
    w._refresh()
    for c in w.flag_checks:
        c.setChecked(False)
    w.extra_edit.setText("")
    w._select_data(w.complevel_combo, None)
    w.record_check.setChecked(False)
    w.load_profile_named("Speedrun: UV/Fast")

    assert w.capture_profile() == snapshot
    assert w.command_preview.toPlainText() == preview


def test_profile_menu_and_delete(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    w = MainWindow(Settings(demo_dir=str(tmp_path), auto_download=False))
    w._rebuild_profiles_menu()
    texts = [a.text() for a in w.profiles_menu.actions()]
    assert "Save Profile As…" in texts and "No saved profiles" in texts

    w._save_profile("A")
    w._save_profile("B")
    w._rebuild_profiles_menu()
    texts = [a.text() for a in w.profiles_menu.actions()]
    assert 'Update "B"' in texts and "A" in texts and "B" in texts
    assert [a for a in w.profiles_menu.actions() if a.text() == "B"][0].isChecked()

    w._delete_profile("B")
    assert w.current_profile is None
    w._rebuild_profiles_menu()
    assert "B" not in [a.text() for a in w.profiles_menu.actions()]


def test_profile_missing_files_warned(qapp, tmp_path, no_dialogs):
    from dww_launcher.profiles import Profile, save_profile

    save_profile("old", Profile(iwad=str(tmp_path / "gone.wad"), pwads=[str(tmp_path / "gone2.wad")]),
                 tmp_path / "profiles")
    w = MainWindow(Settings(demo_dir=str(tmp_path), auto_download=False))
    w.load_profile_named("old")
    assert "gone.wad" in w.log.toPlainText() and "gone2.wad" in w.log.toPlainText()


# ------------------------------------------------------------------ about

def test_about_dialog(qapp, tmp_path):
    import dww_launcher.ui.main_window as mw
    from dww_launcher import __version__

    w = MainWindow(Settings(demo_dir=str(tmp_path), auto_download=False))
    dialog = mw.AboutDialog(w)
    text = dialog.text.text()
    assert f"Version {__version__}" in text
    for url in ("https://www.youtube.com/@DOOMWADWorld", "https://discord.com/invite/aUsWnqaePy",
                "https://github.com/doomwadworld/dww_launcher"):
        assert url in text
    opened = []
    mw.open_url = lambda u: opened.append(u)  # restored by the fixture's monkeypatch
    dialog.text.linkActivated.disconnect()
    dialog.text.linkActivated.connect(lambda u: mw.open_url(u))
    dialog.text.linkActivated.emit("https://discord.com/invite/aUsWnqaePy")
    assert opened == ["https://discord.com/invite/aUsWnqaePy"]


# ---------------------------------------------- clean child environment

ENV_PORT = """\
import os, sys
print("CHILD LD_LIBRARY_PATH=" + os.environ.get("LD_LIBRARY_PATH", "<unset>"))
print("CHILD QT_PLUGIN_PATH=" + os.environ.get("QT_PLUGIN_PATH", "<unset>"))
"""


def test_source_port_gets_clean_environment(qapp, tmp_path, wad_factory, monkeypatch, no_dialogs):
    """A frozen build's bundled-Qt paths must not leak into the source port (or file manager)."""
    if sys.platform == "win32":
        pytest.skip("fake port uses a shebang script")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr("dww_launcher.desktop.bundle_dir", lambda: str(bundle))
    monkeypatch.setenv("LD_LIBRARY_PATH", f"{bundle}{os.pathsep}/usr/lib/custom")
    monkeypatch.setenv("QT_PLUGIN_PATH", str(bundle / "PySide6" / "Qt" / "plugins"))
    port = tmp_path / "envport"
    port.write_text(f"#!{sys.executable}\n{ENV_PORT}")
    port.chmod(0o755)
    iwad = wad_factory("doom2.wad", "IWAD", ["MAP01"])
    w = MainWindow(Settings(ports=[str(port)], iwads=[str(iwad)], demo_dir=str(tmp_path),
                            auto_download=False))
    run_game(w)
    log = w.log.toPlainText()
    assert "CHILD LD_LIBRARY_PATH=/usr/lib/custom" in log
    assert "CHILD QT_PLUGIN_PATH=<unset>" in log


# ------------------------------------------------------------ file browser

DEMO_DIR = Path(__file__).parent / "data" / "demos"


@pytest.fixture
def browse_dir(tmp_path, mapwad):
    import shutil
    import zipfile

    from conftest import build_wad, doom_map_lumps

    d = tmp_path / "wads"
    d.mkdir()
    (d / "sub").mkdir()
    mapwad("wads/MULTI.WAD", ["MAP01", "MAP02", "MAP03"], extra=[("UMAPINFO", b'map MAP02 { levelname = "Two" }')])
    mapwad("wads/single.pwd", ["MAP07"])
    mapwad("wads/game.wad", ["E1M1", "E1M2"], kind="IWAD")
    with zipfile.ZipFile(d / "pack.Pk3", "w") as zf:
        zf.writestr("maps/MAP04.wad", build_wad("PWAD", doom_map_lumps("MAP04")))
    shutil.copy(DEMO_DIR / "dsda_cl9_boom.lmp", d / "RUN.LMP")
    (d / "fix.deh").write_text("Doom version = 21\n")
    (d / "readme.txt").write_text("no")
    (d / "song.mp3").write_bytes(b"no")
    return d


def wait_for_scan(fb, timeout=5000):
    from PySide6.QtCore import QElapsedTimer, QThreadPool

    QThreadPool.globalInstance().waitForDone(timeout)
    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < 500:
        QApplication.processEvents()


def browser_rows(fb):
    return {fb.table.item(r, 0).text().replace("📁 ", ""): [fb.table.item(r, c).text() for c in range(7)]
            for r in range(fb.table.rowCount())}


def select(fb, name):
    for r in range(fb.table.rowCount()):
        if fb.table.item(r, 0).text().endswith(name):
            fb.table.clearSelection()
            fb.table.selectRow(r)
            return r
    raise AssertionError(name)


def test_browser_lists_doom_files(qapp, browse_dir):
    from dww_launcher.ui.file_browser import FileBrowser

    fb = FileBrowser()
    fb.set_directory(browse_dir)
    wait_for_scan(fb)
    rows = browser_rows(fb)
    assert set(rows) == {"sub", "MULTI.WAD", "single.pwd", "game.wad", "pack.Pk3", "RUN.LMP", "fix.deh"}
    assert rows["MULTI.WAD"][1:4] == ["PWAD", "3", "Doom II"]
    assert rows["game.wad"][1:4] == ["IWAD", "2", "Doom (shareware)"]
    assert rows["pack.Pk3"][1:3] == ["PK3", "1"]
    assert rows["RUN.LMP"][1:5] == ["Demo", "MAP01 UV", "freedoom2.wad", "0:04.49"]
    assert fb.table.item(0, 0).text() == "📁 sub"  # folders first

    fb.table.sortByColumn(5, Qt.SortOrder.DescendingOrder)  # size, numerically
    sizes = [fb.table.item(r, 5).data(Qt.ItemDataRole.UserRole + 1) for r in range(fb.table.rowCount())]
    assert sizes == sorted(sizes, reverse=True)
    fb.stop()


def test_browser_detail_and_maps_tabs(qapp, browse_dir):
    from dww_launcher.ui.file_browser import FileBrowser

    fb = FileBrowser()
    fb.set_directory(browse_dir)
    wait_for_scan(fb)

    select(fb, "MULTI.WAD")
    detail = [fb.detail_tree.topLevelItem(i).text(0).strip() for i in range(fb.detail_tree.topLevelItemCount())]
    assert detail[:2] == ["General", "Name"] and "DOOM" in detail and "Levels" in detail
    assert fb.tabs.isTabVisible(1)
    assert [fb.map_list.item(i).text() for i in range(3)] == ["MAP01", "MAP02   Two", "MAP03"]
    assert not fb.map_list.isHidden()
    assert fb.preview.map.name == "MAP01" and fb.preview.map.lines
    fb.map_list.setCurrentRow(1)
    stats = {fb.map_stats.topLevelItem(i).text(0): fb.map_stats.topLevelItem(i).text(1)
             for i in range(fb.map_stats.topLevelItemCount())}
    assert stats["MAP02"] == "Two" and stats["Exit linedef"] == "Yes" and stats["Monsters (UV)"] == "2"

    select(fb, "single.pwd")
    assert fb.tabs.isTabVisible(1) and fb.map_list.isHidden()  # one map: no list needed

    select(fb, "pack.Pk3")
    assert fb.preview.map.name == "MAP04"

    select(fb, "RUN.LMP")
    assert not fb.tabs.isTabVisible(1)
    detail = {fb.detail_tree.topLevelItem(i).text(0).strip(): fb.detail_tree.topLevelItem(i).text(1)
              for i in range(fb.detail_tree.topLevelItemCount())}
    assert detail["Format"] == "Boom 2.02" and detail["Length"] == "0:04.49 (157 tics)"
    fb.stop()


def test_browser_navigation(qapp, browse_dir):
    from dww_launcher.ui.file_browser import FileBrowser

    fb = FileBrowser(places=lambda: [("Demo folder", str(browse_dir))])
    fb.set_directory(browse_dir)
    wait_for_scan(fb)
    fb._on_double_click(fb.table.item(select(fb, "sub"), 0))
    assert fb.directory == str((browse_dir / "sub").resolve())
    fb.go_up()
    assert fb.directory == str(browse_dir.resolve())
    assert not fb.set_directory(browse_dir / "missing")
    fb._fill_places()
    assert any("Demo folder" in a.text() for a in fb.places_menu.actions())
    fb.stop()


def test_browser_actions_reach_launcher(qapp, browse_dir, tmp_path, monkeypatch, no_dialogs):
    w = MainWindow(Settings(demo_dir=str(tmp_path), file_browser_dir=str(browse_dir), auto_download=False))
    started = []
    monkeypatch.setattr(w, "_start", lambda opts: started.append(opts))
    w.set_file_browser_visible(True)
    fb = w.file_browser
    wait_for_scan(fb)
    assert fb.directory == str(browse_dir.resolve())

    # Double-click: PWAD -> mods, IWAD -> IWAD, demo -> play
    fb._on_double_click(fb.table.item(select(fb, "MULTI.WAD"), 0))
    assert w.pwad_list.paths() == [str(browse_dir / "MULTI.WAD")]
    assert w.map_combo.currentData() == "MAP01"  # first level of the added mod selected
    fb._on_double_click(fb.table.item(select(fb, "game.wad"), 0))
    assert w.iwad_combo.currentData() == str(browse_dir / "game.wad")
    assert str(browse_dir / "game.wad") not in w.pwad_list.paths()
    fb._on_double_click(fb.table.item(select(fb, "RUN.LMP"), 0))
    assert started and started[0].playdemo == str(browse_dir / "RUN.LMP")

    # Context menu with several files selected
    fb.table.clearSelection()
    for name in ("single.pwd", "pack.Pk3", "fix.deh", "game.wad"):
        r = [r for r in range(fb.table.rowCount()) if fb.table.item(r, 0).text() == name][0]
        fb.table.selectionModel().select(fb.table.model().index(r, 0),
                                         fb.table.selectionModel().SelectionFlag.Select
                                         | fb.table.selectionModel().SelectionFlag.Rows)
    actions = {a.text(): a for a in fb.context_actions(fb.selected_paths())}
    assert "Add to Mods (3)" in actions and "Use as IWAD" not in actions  # IWAD isn't a mod
    actions["Add to Mods (3)"].trigger()
    assert {Path(p).name for p in w.pwad_list.paths()} == {"MULTI.WAD", "single.pwd", "pack.Pk3", "fix.deh"}

    select(fb, "game.wad")
    assert "Use as IWAD" in {a.text() for a in fb.context_actions(fb.selected_paths())}
    select(fb, "RUN.LMP")
    assert "Play Demo" in {a.text() for a in fb.context_actions(fb.selected_paths())}


def test_browser_window_grows_and_shrinks(qapp, tmp_path):
    w = MainWindow(Settings(demo_dir=str(tmp_path), auto_download=False))
    w.resize(700, 800)
    w.show()
    qapp.processEvents()
    before = w.width()
    w.browser_action.setChecked(True)
    qapp.processEvents()
    assert w.browser_dock.isVisible()
    grown = w.width()
    assert grown >= before
    w.browser_action.setChecked(False)
    qapp.processEvents()
    assert not w.browser_dock.isVisible()
    assert w.width() == before  # back to exactly the original width
    w.browser_dock.close()  # closing via the dock's X keeps the toolbar button in sync
    assert not w.browser_action.isChecked()


def test_browser_state_is_saved(qapp, browse_dir, tmp_path):
    s = Settings(demo_dir=str(tmp_path), file_browser_dir=str(browse_dir), file_browser_visible=True,
                 auto_download=False)
    w = MainWindow(s)
    assert w.browser_action.isChecked()
    w.file_browser.set_directory(browse_dir / "sub")
    w._save_state()
    assert s.file_browser_dir == str((browse_dir / "sub").resolve()) and s.file_browser_visible
    w.file_browser.stop()


# ------------------------------------------------------------------ Ctrl+C

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
def test_ctrl_c_quits(qapp, tmp_path):
    import signal

    from dww_launcher.app import install_interrupt_handler

    saved = []
    w = MainWindow(Settings(demo_dir=str(tmp_path), auto_download=False))
    w.settings.save = lambda path=None: saved.append(True)
    w.show()
    old = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    try:
        install_interrupt_handler(qapp, w)
        QTimer.singleShot(100, lambda: os.kill(os.getpid(), signal.SIGINT))
        QTimer.singleShot(5000, qapp.quit)  # safety net
        timer = __import__("time").monotonic()
        qapp.exec()
        assert __import__("time").monotonic() - timer < 4  # quit by the signal, not the safety net
        assert not w.isVisible() and saved  # window closed and settings saved
    finally:
        signal.signal(signal.SIGINT, old[0])
        signal.signal(signal.SIGTERM, old[1])
        qapp._interrupt_timer.stop()


# ------------------------------------------------------------ map preview

def _square_map():
    from conftest import build_wad, doom_map_lumps
    from dww_launcher.mapdata import load_map
    from dww_launcher.wad import WadFile

    return load_map(WadFile(build_wad("PWAD", doom_map_lumps())), "MAP01")


@pytest.fixture
def preview(qapp):
    from dww_launcher.ui.map_view import MapPreview

    pv = MapPreview()
    pv.resize(400, 300)
    pv.set_map(_square_map())
    return pv


def first_marker(preview, kind):
    return next(t for k, t in preview._markers if k == kind)


def test_preview_layers_prepared(preview):
    from collections import Counter

    # UV single-player things: 2 imps, player 1 + deathmatch start, a key and a shotgun
    assert Counter(k for k, _ in preview._markers) == {"monster": 2, "player": 2, "key": 1, "weapon": 1}
    visible = Counter(t.type for _, t in preview.visible_markers())
    assert visible == {3001: 2, 1: 1, 5: 1, 2001: 1}  # deathmatch starts are hidden by default
    assert preview.legend_entries() == [("exit", "exit")]
    preview.grab()  # paints without errors
    preview.set_thing_filter(set(), set())
    preview.set_layer("exits", False)
    assert preview.legend_entries() == []
    preview.grab()


def test_preview_zoom_keeps_point_under_cursor(preview):
    from PySide6.QtCore import QPointF

    anchor = QPointF(300, 100)
    before = preview.screen_to_map(anchor)
    preview.zoom_at(3.0, anchor)
    assert preview.zoom == pytest.approx(3.0)
    after = preview.screen_to_map(anchor)
    assert (after.x(), after.y()) == pytest.approx((before.x(), before.y()), abs=1e-6)
    preview.zoom_at(1e9)
    assert preview.zoom == preview.MAX_ZOOM
    preview.fit()
    assert preview.zoom == 1.0 and not preview.zoomed_in


def test_preview_wheel_and_drag(preview):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest

    ev = QWheelEvent(QPointF(200, 150), QPointF(200, 150), QPoint(0, 0), QPoint(0, 240),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    preview.wheelEvent(ev)
    assert preview.zoom == pytest.approx(1.0015 ** 240)  # two notches, ~1.2x each
    c0 = QPointF(preview.center)
    QTest.mousePress(preview, Qt.MouseButton.LeftButton, pos=QPoint(200, 150))
    QTest.mouseMove(preview, QPoint(150, 150))
    QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, pos=QPoint(150, 150))
    assert preview.center.x() > c0.x()  # dragging left reveals more of the right side
    QTest.mouseDClick(preview, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    assert preview.zoom == 1.0


def test_preview_middle_click_autoscroll(preview):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtTest import QTest

    QTest.mouseClick(preview, Qt.MouseButton.MiddleButton, pos=QPoint(200, 150))
    assert preview._auto_anchor is None  # not zoomed in: nothing to scroll
    preview.zoom_at(4.0)
    QTest.mouseClick(preview, Qt.MouseButton.MiddleButton, pos=QPoint(200, 150))
    assert preview._auto_anchor is not None and preview._auto_timer.isActive()  # click = stays on
    c0 = QPointF(preview.center)
    preview._auto_cursor = QPointF(200, 60)  # mouse above the marker -> scroll up
    for _ in range(5):
        preview._autoscroll_step()
    assert preview.center.y() > c0.y() and preview.center.x() == pytest.approx(c0.x())
    QTest.mouseClick(preview, Qt.MouseButton.LeftButton, pos=QPoint(200, 60))
    assert preview._auto_anchor is None and not preview._auto_timer.isActive()


def test_layer_toggles_and_popout(qapp, browse_dir):
    from dww_launcher.ui.file_browser import FileBrowser

    fb = FileBrowser()
    fb.set_directory(browse_dir)
    wait_for_scan(fb)
    changes = []
    fb.layers_changed.connect(changes.append)
    select(fb, "MULTI.WAD")
    fb.viewer.thing_filter.show_all()
    assert fb.preview.hidden_categories == set() and changes[-1]["hidden_categories"] == []

    fb.pop_out_map()
    win = fb.map_window
    assert win is not None and win.isVisible()
    assert win.viewer.preview.map.name == "MAP01" and win.viewer.layers() == fb.layers()
    assert not win.viewer.popout_button.isVisibleTo(win)
    fb.map_list.setCurrentRow(2)
    assert win.viewer.preview.map.name == "MAP03" and "MAP03" in win.windowTitle()
    win.viewer.checks["secrets"].setChecked(False)  # toggles stay in sync both ways
    assert not fb.viewer.checks["secrets"].isChecked() and not fb.preview.layers["secrets"]
    win.close()
    assert fb.map_window is None
    fb.stop()


def test_browser_size_layout_and_layers_persist(qapp, browse_dir, tmp_path, monkeypatch):
    s = Settings(demo_dir=str(tmp_path), file_browser_dir=str(browse_dir), auto_download=False,
                 file_browser_width=410)
    w = MainWindow(s)
    requested = []
    original = w.resizeDocks
    monkeypatch.setattr(w, "resizeDocks", lambda docks, sizes, o: requested.append(sizes[0]) or original(docks, sizes, o))
    w.show()
    w.browser_action.setChecked(True)
    qapp.processEvents()
    assert requested == [410]  # opens at the remembered width

    w.file_browser.main_split.setSizes([100, 300])
    viewer = w.file_browser.viewer
    viewer.checks["exits"].setChecked(False)
    keys = next(viewer.thing_filter.tree.topLevelItem(i) for i in range(viewer.thing_filter.tree.topLevelItemCount())
                if viewer.thing_filter.tree.topLevelItem(i).text(0) == "Keys")
    keys.setCheckState(0, Qt.CheckState.Unchecked)
    w._save_state()
    assert s.map_layers == {"secrets": True, "exits": False,
                            "hidden_categories": ["Decoration", "Key", "Obstacle", "Other", "Teleport"],
                            "hidden_types": [2, 3, 4, 11]}
    assert set(s.file_browser_state) == {"main_split", "maps_split", "right_split", "columns"}
    # The width the user leaves the panel at is remembered; a width squeezed below the
    # minimum (e.g. by a small screen) is not.
    monkeypatch.setattr(w.browser_dock, "width", lambda: 450)
    w._save_state()
    assert s.file_browser_width == 450
    monkeypatch.setattr(w.browser_dock, "width", lambda: 200)
    w._save_state()
    assert s.file_browser_width == 450
    sizes = w.file_browser.main_split.sizes()
    w.file_browser.stop()

    w2 = MainWindow(s)  # a new session restores layout and toggles
    assert w2.file_browser.layers() == s.map_layers
    w2.file_browser.main_split.resize(w.file_browser.main_split.size())
    assert w2.file_browser.main_split.saveState() == w.file_browser.main_split.saveState()
    assert sizes[0] < sizes[1]
    w2.file_browser.stop()


# ------------------------------------------------------------ hover tooltips

def test_hover_highlights_and_describes_things(preview):
    from PySide6.QtCore import QEvent, QPoint, QPointF
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QToolTip

    preview.set_thing_filter(set(), set())
    preview.show()
    imp = first_marker(preview, "monster")
    c = preview.map_to_screen(imp.x, imp.y)
    assert [t.name for _, t in preview.markers_at(c)][:1] == ["Imp"]
    assert preview.tooltip_for(c).startswith("Imp\nMonster · type 3001")

    QTest.mouseMove(preview, c.toPoint())
    assert [t for _, t in preview._hover][:1] == [imp]
    preview.grab()  # paints the highlight without errors

    preview.event(QHelpEvent(QEvent.Type.ToolTip, c.toPoint(), preview.mapToGlobal(c.toPoint())))
    assert QToolTip.text().startswith("Imp")

    far = QPoint(2, 2)
    QTest.mouseMove(preview, far)
    assert preview._hover == []
    preview.usage_hint = "hint"
    preview.event(QHelpEvent(QEvent.Type.ToolTip, far, preview.mapToGlobal(far)))
    assert QToolTip.text() == "hint"

    preview.set_thing_filter({"Monster"}, set())  # hidden things can't be hovered
    assert preview.markers_at(c) == [] and preview._hover == []
    key = first_marker(preview, "key")
    assert preview.tooltip_for(preview.map_to_screen(key.x, key.y)).startswith("Blue keycard\nKey")


def test_hover_lists_stacked_things(qapp):
    from dww_launcher.mapdata import MapData, Thing
    from dww_launcher.ui.map_view import MapPreview

    m = MapData("MAP01", "Doom", vertices=[(0, 0), (512, 512)])
    m.lines = [__import__("dww_launcher.mapdata", fromlist=["Line"]).Line(0, 1, 0, False)]
    m.things = [Thing(3001, 100, 100, True, True) for _ in range(5)]
    pv = MapPreview()
    pv.resize(300, 300)
    pv.set_map(m)
    pv.set_thing_filter(set(), set())
    text = pv.tooltip_for(pv.map_to_screen(100, 100))
    assert text.count("Imp") == 3 and text.endswith("…and 2 more here")


# ------------------------------------------------------------ sprite icons

def test_preview_uses_sprite_icons(qapp, preview):
    from test_sprites import PALETTE_BYTES, patch

    from conftest import build_wad
    from dww_launcher.sprites import SpriteSource
    from dww_launcher.wad import WadFile

    pic = patch(8, 16, [[(0, [7] * 16)]] * 8)
    wad = WadFile(build_wad("IWAD", [("PLAYPAL", PALETTE_BYTES), ("S_START", b""), ("TROOA1", pic),
                                     ("BKEYA0", pic), ("S_END", b"")]))
    m = preview.map
    preview.set_map(m, sprites=SpriteSource([wad]))
    preview.set_thing_filter(set(), set())
    imp = first_marker(preview, "monster")
    assert preview.icon(3001) is not None and preview.icon(2001) is None  # no shotgun sprite: shape
    assert preview.marker_radius("monster", imp) == preview.icon_box("monster") / 2
    c = preview.map_to_screen(imp.x, imp.y)
    assert preview.markers_at(c)[0][1] is imp
    preview.grab()  # icons, fallback shapes and tokens all paint
    preview.set_map(m, sprites=None)
    assert preview.icon(3001) is None


def test_browser_picks_sprite_wads(qapp, browse_dir):
    from dww_launcher.ui.file_browser import FileBrowser

    iwad = str(browse_dir / "game.wad")
    fb = FileBrowser(sprite_iwads=lambda: [iwad])
    pwad = str(browse_dir / "MULTI.WAD")
    src = fb.sprites_for(pwad)
    assert [w.name for w in src.wads] == ["MULTI.WAD", "game.wad"]  # the PWAD's own sprites first
    assert [w.name for w in fb.sprites_for(iwad).wads] == ["game.wad"]
    assert [w.name for w in fb.sprites_for(str(browse_dir / "pack.Pk3")).wads] == ["game.wad"]
    assert fb.sprites_for(pwad) is src  # cached
    assert FileBrowser().sprites_for(str(browse_dir / "pack.Pk3")) is None


def test_launcher_offers_selected_iwad_first(qapp, tmp_path, wad_factory):
    a = wad_factory("a.wad", "IWAD", ["MAP01"])
    b = wad_factory("b.wad", "IWAD", ["E1M1"])
    w = MainWindow(Settings(iwads=[str(a), str(b)], last_iwad=str(b), demo_dir=str(tmp_path),
                            auto_download=False))
    assert w._sprite_iwads() == [str(b), str(a)]


# ------------------------------------------------------------ thing filter, exits

def _filter_item(tf, label):
    for i in range(tf.tree.topLevelItemCount()):
        item = tf.tree.topLevelItem(i)
        if item.text(0) == label:
            return item
        for j in range(item.childCount()):
            if item.child(j).text(0) == label:
                return item.child(j)
    raise AssertionError(label)


def test_normalize_layers_migrates_old_settings():
    from dww_launcher.ui.map_view import DEFAULT_LAYERS, normalize_layers

    assert normalize_layers({"things": True, "keys": False, "weapons": True, "secrets": False}) == \
        {**DEFAULT_LAYERS, "secrets": False}
    out = normalize_layers({"hidden_categories": ["Monster", "Bogus"], "hidden_types": [5, "6"], "exits": "x"})
    assert out["hidden_categories"] == ["Monster"] and out["hidden_types"] == [5, 6] and out["exits"] is True


def test_thing_filter_tree(qapp):
    from dww_launcher.ui.map_view import MapViewer

    viewer = MapViewer()
    viewer.set_map(_square_map())
    tf = viewer.thing_filter
    seen = []
    viewer.layers_changed.connect(seen.append)
    monsters = _filter_item(tf, "Monsters")
    assert monsters.text(1) == "2" and monsters.checkState(0) == Qt.CheckState.Checked
    assert _filter_item(tf, "Imp").text(1) == "2"
    assert _filter_item(tf, "Player starts").checkState(0) == Qt.CheckState.PartiallyChecked  # DM start hidden
    assert _filter_item(tf, "Ammo").text(0) == "Ammo" and _filter_item(tf, "Health").text(0) == "Health"

    _filter_item(tf, "Imp").setCheckState(0, Qt.CheckState.Unchecked)  # one type off
    assert 3001 in viewer.preview.hidden_types and seen[-1]["hidden_types"] == [2, 3, 4, 11, 3001]
    assert _filter_item(tf, "Monsters").checkState(0) == Qt.CheckState.PartiallyChecked
    assert not any(t.type == 3001 for _, t in viewer.preview.visible_markers())

    _filter_item(tf, "Monsters").setCheckState(0, Qt.CheckState.Checked)  # whole category back on
    assert 3001 not in viewer.preview.hidden_types

    _filter_item(tf, "Weapons").setCheckState(0, Qt.CheckState.Unchecked)
    assert "Weapon" in viewer.preview.hidden_categories
    _filter_item(tf, "Shotgun").setCheckState(0, Qt.CheckState.Checked)  # one type of a hidden category
    assert "Weapon" not in viewer.preview.hidden_categories
    assert any(t.type == 2001 for _, t in viewer.preview.visible_markers())

    tf.hide_all()
    assert viewer.preview.visible_markers() == []
    tf.show_all()
    assert len(viewer.preview.visible_markers()) == 6
    tf.reset()
    assert viewer.layers() == {**__import__("dww_launcher.ui.map_view", fromlist=["x"]).DEFAULT_LAYERS}


def test_exits_toggle_and_legend_jump(qapp, preview):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    assert preview.exit_count("exit") == 1
    assert len(preview._lines["one"]) == 6  # the exit line is still drawn as a wall
    preview.show()
    preview.grab()
    rect, kind = preview._legend_rects[0]
    assert kind == "exit"
    preview._animation.setDuration(0)
    QTest.mouseClick(preview, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    target = preview._exit_groups["exit"][0]
    assert preview.zoom >= 1.0 and preview._flash == (target, "exit")
    QTest.qWait(50)
    assert (preview.center.x(), preview.center.y()) == pytest.approx((target.center().x(), target.center().y()))

    preview.set_layer("exits", False)
    preview.grab()
    assert preview._legend_rects == [] and preview.legend_entries() == []


def test_exit_clusters_and_cycling(qapp):
    from PySide6.QtCore import QLineF

    from dww_launcher.mapdata import Line, MapData
    from dww_launcher.ui.map_view import MapPreview, _cluster

    # two lines of one switch close together, and a second exit far away
    boxes = _cluster([QLineF(0, 0, 0, 64), QLineF(16, 0, 16, 64), QLineF(4000, 4000, 4064, 4000)])
    assert len(boxes) == 2
    m = MapData("MAP01", "Doom", vertices=[(0, 0), (0, 64), (16, 0), (16, 64), (4000, 4000), (4064, 4000)])
    m.lines = [Line(0, 1, 11, False), Line(2, 3, 11, False), Line(4, 5, 52, False)]
    pv = MapPreview()
    pv.resize(400, 300)
    pv.set_map(m)
    assert pv.exit_count("exit") == 2 and pv.legend_entries() == [("exit", "exit (2)")]
    first = pv.jump_to_exit("exit", animate=False)
    second = pv.jump_to_exit("exit", animate=False)
    third = pv.jump_to_exit("exit", animate=False)
    assert first != second and third == first  # cycles through the exits
    assert pv.jump_to_exit("secret") is None
