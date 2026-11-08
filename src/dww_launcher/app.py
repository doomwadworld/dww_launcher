"""Application entry point."""

from __future__ import annotations

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
    window = MainWindow()
    window.show()
    QTimer.singleShot(0, window.startup)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
