"""Application entry point."""

from __future__ import annotations

import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QNetworkProxyFactory
from PySide6.QtWidgets import QApplication

from dww_launcher import __version__
from dww_launcher.icons import app_icon
from dww_launcher.ui.main_window import MainWindow

APP_ID = "doomwadworld.dww-launcher"


def _set_windows_app_id() -> None:
    """Make the Windows taskbar show our icon instead of Python's."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass


def main() -> int:
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("Doom WAD World Launcher")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("dww-launcher")  # matches a dww-launcher.desktop file on Linux
    app.setWindowIcon(app_icon())
    # Respect the OS proxy settings (corporate/school networks) for spreadsheet downloads.
    QNetworkProxyFactory.setUseSystemConfiguration(True)
    if "--smoke-test" in sys.argv:
        return _smoke_test(app)
    window = MainWindow()
    window.show()
    install_interrupt_handler(app, window)
    QTimer.singleShot(0, window.startup)
    return app.exec()


def install_interrupt_handler(app: QApplication, window=None) -> None:
    """Quit cleanly (saving settings) on Ctrl+C in the terminal, or SIGTERM.

    Python only runs signal handlers when the interpreter gets control, which never happens
    while Qt's C++ event loop is idle; a short no-op timer hands control back regularly.
    """

    def handle(signum, _frame):
        print(f"\n{signal.Signals(signum).name} received, quitting.", file=sys.stderr)
        if window is not None:
            window.close()  # saves settings
        app.quit()

    signal.signal(signal.SIGINT, handle)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle)
    timer = QTimer(app)
    timer.timeout.connect(lambda: None)
    timer.start(200)
    app._interrupt_timer = timer  # keep a reference


def _smoke_test(app: QApplication) -> int:
    """Build the main window and exit. Used by CI to check packaged builds actually start.

    Runs with in-memory settings so nothing is written to the user's config directory.
    """
    from PySide6.QtNetwork import QNetworkAccessManager

    from dww_launcher.config import Settings

    problems = []
    if app.windowIcon().isNull():
        problems.append("application icon missing")
    class _UnsavedSettings(Settings):
        def save(self, path=None) -> None:
            pass

    window = MainWindow(_UnsavedSettings(auto_download=False))
    window.show()
    app.processEvents()
    if window.tabs.count() != 2:
        problems.append("main window incomplete")
    QNetworkAccessManager()  # QtNetwork (used for spreadsheet downloads) is bundled
    window.close()
    for p in problems:
        print("SMOKE TEST FAILED:", p, file=sys.stderr)
    if not problems:
        print(f"Smoke test passed (DWW Launcher {__version__})")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
