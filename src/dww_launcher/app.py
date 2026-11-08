"""Application entry point."""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QNetworkProxyFactory
from PySide6.QtWidgets import QApplication

from dww_launcher import __version__
from dww_launcher.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Doom WAD World Launcher")
    app.setApplicationVersion(__version__)
    # Respect the OS proxy settings (corporate/school networks) for spreadsheet downloads.
    QNetworkProxyFactory.setUseSystemConfiguration(True)
    window = MainWindow()
    window.show()
    QTimer.singleShot(0, window.startup)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
