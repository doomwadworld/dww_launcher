# PyInstaller spec for DWW Launcher.  Build with:  pyinstaller packaging/dww-launcher.spec
# Produces dist/dww-launcher/ (Windows/Linux) or dist/DWW Launcher.app (macOS).
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
RES = SRC / "dww_launcher" / "resources"
sys.path.insert(0, str(SRC))
from dww_launcher import __version__  # noqa: E402

# Qt modules the app never imports; keeps the bundle small.
EXCLUDES = [
    "tkinter", "unittest", "pydoc_data",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPdf", "PySide6.QtSvg",
    "PySide6.QtDBus", "PySide6.QtConcurrent", "PySide6.QtXml",
]

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(SRC)],
    datas=[(str(p), "dww_launcher/resources") for p in RES.glob("icon_[0-9]*.png")],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dww-launcher",
    console=False,  # GUI app: no console window on Windows
    icon=str(RES / ("icon.icns" if sys.platform == "darwin" else "icon.ico")),
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name="dww-launcher", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="DWW Launcher.app",
        icon=str(RES / "icon.icns"),
        bundle_identifier="com.doomwadworld.dww-launcher",
        version=__version__,
        info_plist={
            "CFBundleName": "DWW Launcher",
            "CFBundleDisplayName": "DWW Launcher",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
