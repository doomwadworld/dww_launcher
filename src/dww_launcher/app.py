"""Application entry point."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from dww_launcher import __version__
from dww_launcher.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Doom WAD World Launcher")
    app.setApplicationVersion(__version__)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
