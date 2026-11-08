"""End-to-end GUI test with a fake source port (runs headless via QT_QPA_PLATFORM=offscreen)."""

import os
import sys
import zipfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox  # noqa: E402

from dww_launcher.config import Settings  # noqa: E402
from dww_launcher.ui.main_window import MainWindow  # noqa: E402

FAKE_PORT = """\
import sys
args = sys.argv[1:]
print("fake port args:", " ".join(args))
if "-record" in args:
    with open(args[args.index("-record") + 1] + ".lmp", "wb") as f:
        f.write(b"fake demo")
"""


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.skipif(sys.platform == "win32", reason="fake port uses a shebang script")
def test_record_and_package(qapp, tmp_path, wad_factory, monkeypatch):
    port = tmp_path / "fakeport"
    port.write_text(f"#!{sys.executable}\n{FAKE_PORT}")
    port.chmod(0o755)
    iwad = wad_factory("doom2.wad", "IWAD", ["MAP01", "MAP02"])
    pwad = wad_factory("mod.wad", "PWAD", ["MAP33"])
    demo_dir = tmp_path / "demos"

    settings = Settings(ports=[str(port)], iwads=[str(iwad)], demo_dir=str(demo_dir),
                        last_pwads=[str(pwad)])
    monkeypatch.setattr(Settings, "save", lambda self, path=None: None)
    w = MainWindow(settings)

    assert [w.map_combo.itemText(i) for i in range(w.map_combo.count())] == \
        ["(start normally)", "MAP01", "MAP02", "MAP33"]
    assert "Doom II" in w.iwad_label.text()

    w.map_combo.setCurrentIndex(w.map_combo.findData("MAP02"))
    w.record_check.setChecked(True)
    w.record_name.setText("myrun")
    preview = w.command_preview.toPlainText()
    assert "-warp 2" in preview and "-skill 4" in preview and "-record" in preview

    out_zip = tmp_path / "myrun.zip"
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out_zip), "")))
    monkeypatch.setattr(QInputDialog, "getMultiLineText", staticmethod(lambda *a, **k: ("notes", True)))

    w._launch()
    loop = QEventLoop()
    w.process.finished.connect(lambda *_: QTimer.singleShot(0, loop.quit))
    QTimer.singleShot(10000, loop.quit)
    loop.exec()

    assert (demo_dir / "myrun.lmp").read_bytes() == b"fake demo"
    assert "fake port args:" in w.log.toPlainText()
    with zipfile.ZipFile(out_zip) as zf:
        assert set(zf.namelist()) == {"myrun.lmp", "manifest.json"}
    assert w.launch_button.isEnabled()
