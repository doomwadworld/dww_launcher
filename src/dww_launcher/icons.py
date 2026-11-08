"""Application icon."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon

RESOURCES = Path(__file__).resolve().parent / "resources"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def app_icon() -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        path = RESOURCES / f"icon_{size}.png"
        if path.is_file():
            icon.addFile(str(path))
    return icon
