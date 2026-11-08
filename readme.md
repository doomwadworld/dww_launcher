# DWW Launcher

**Doom WAD World Launcher** is a cross-platform (Windows, macOS, Linux) GUI for launching
classic Doom engine games with any source port, IWAD and set of mods, and for recording,
playing back and packaging demos.

> Status: early prototype.

## Features (prototype)

- Keep a list of source ports and IWADs. macOS `.app` bundles are supported.
- IWAD detection: Doom, Ultimate Doom, Doom II, Final Doom (TNT/Plutonia), Freedoom.
- Mod list with drag-and-drop and load ordering. `.deh`/`.bex` files go to `-deh`, everything else to `-file`.
- Map picker filled in from the maps in the IWAD and PWADs (`-warp`).
- Skill, `-complevel`, `-fast` / `-respawn` / `-nomonsters`, and free-form extra arguments.
- A live command-line preview you can copy.
- Demo recording (`-record`) into a demo folder, and playback (`-playdemo`).
- Demo packaging: a `.zip` holding the `.lmp` plus a `manifest.json` that records the port, IWAD,
  PWADs (with SHA-256 hashes), map, skill, complevel and flags. *The final format is still to be decided.*
- Source port output is shown in the launcher. Settings are saved between sessions.

## Running from source

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m dww_launcher           # or: dww-launcher
```

## Tests

```bash
pytest
```

The GUI test runs headless (`QT_QPA_PLATFORM=offscreen`) and uses a fake source port.

## Project layout

```
src/dww_launcher/
  app.py              entry point
  wad.py              WAD reader: IWAD/PWAD, lump list, maps, game detection
  command.py          LaunchOptions -> source port command line
  demo.py             demo packaging (zip + manifest.json)
  config.py           persistent settings (JSON in the OS config dir)
  ui/main_window.py   the main window
tests/
```
