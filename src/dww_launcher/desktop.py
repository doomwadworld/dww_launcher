"""Starting other programs safely: source ports, the file manager and the web browser.

A PyInstaller build points LD_LIBRARY_PATH, QT_PLUGIN_PATH and QML2_IMPORT_PATH (and on
Windows, PATH) at its own bundled copy of Qt. Child processes inherit those variables, so a
system Qt program started from the launcher -- e.g. Dolphin opened via xdg-open -- would load
the launcher's Qt instead of the system's and can abort at startup. Every process we start
therefore gets an environment with the bundle's directories removed.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QUrl
from PySide6.QtGui import QDesktopServices

# Path-list variables the PyInstaller bootloader / Qt runtime hook may point into the bundle.
_PATH_VARS = (
    "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "PATH",
    "QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QML_IMPORT_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
)


def bundle_dir() -> str | None:
    """The PyInstaller bundle directory, or None when running from source."""
    return getattr(sys, "_MEIPASS", None) if getattr(sys, "frozen", False) else None


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == \
            os.path.realpath(root)
    except ValueError:  # different drives on Windows
        return False


def clean_environment(env: dict[str, str] | None = None, bundle: str | None = None) -> dict[str, str]:
    """A copy of ``env`` (default: os.environ) without the bundle's private paths."""
    env = dict(os.environ if env is None else env)
    bundle = bundle if bundle is not None else bundle_dir()
    if not bundle:
        return env
    for var in _PATH_VARS:
        original = env.pop(f"{var}_ORIG", None)  # PyInstaller saves some originals this way
        if original is not None:
            env[var] = original
            continue
        if var not in env:
            continue
        parts = [p for p in env[var].split(os.pathsep) if p and not _inside(p, bundle)]
        if parts:
            env[var] = os.pathsep.join(parts)
        else:
            del env[var]
    return env


def process_environment() -> QProcessEnvironment:
    qenv = QProcessEnvironment()
    for key, value in clean_environment().items():
        qenv.insert(key, value)
    return qenv


def _xdg_open(target: str) -> bool:
    opener = shutil.which("xdg-open", path=clean_environment().get("PATH"))
    if not opener:
        return False
    proc = QProcess()
    proc.setProgram(opener)
    proc.setArguments([target])
    proc.setProcessEnvironment(process_environment())
    ok, _pid = proc.startDetached()
    return ok


def open_url(url: str) -> bool:
    """Open a web link or file:// URL in the user's default application."""
    if bundle_dir() and sys.platform.startswith("linux"):
        # QDesktopServices would run xdg-open with our (bundle) environment.
        if _xdg_open(url):
            return True
    return QDesktopServices.openUrl(QUrl(url))


def open_folder(path: str | Path) -> bool:
    """Show a folder in the system file manager (creating it if needed)."""
    folder = Path(path)
    folder.mkdir(parents=True, exist_ok=True)
    return open_url(QUrl.fromLocalFile(str(folder.resolve())).toString())
