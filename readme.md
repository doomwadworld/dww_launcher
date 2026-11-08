# DWW Launcher

**Doom WAD World Launcher** is a cross-platform (Windows, macOS, Linux) GUI for launching
classic Doom engine games with any source port, IWAD and set of mods, and for recording,
playing back and packaging demos.

> Status: early prototype.

## Features (prototype)

- Keep a list of source ports and IWADs. macOS `.app` bundles are supported.
- IWAD detection: Doom, Ultimate Doom, Doom II, Final Doom (TNT/Plutonia), Freedoom.
- Mod list with drag-and-drop and load ordering. It shows how many levels each WAD contains.
  `.deh`/`.bex` files go to `-deh`, everything else to `-file`.
- Map picker built from the IWAD and mods. Maps a mod replaces or adds are labelled, and adding
  a mod selects its first level.
- Skill, `-complevel` (0-4, 9, 11, 21), `-fast` / `-respawn` / `-nomonsters` / `-tas` / `-dsdademo`,
  and free-form extra arguments.
- **Complevel 0 auto-selection:** with a Doom 1 IWAD, adding a mod whose file date falls between
  1993-12-10 and 1994-09-01 (the Doom v1.2 era) selects complevel 0 and says why.
- A live command-line preview you can copy.
- **Demo recording** with automatic names:
  - No mod with levels: `<iwad>-<map>` (e.g. `doom2-map07`).
  - Mod with levels: `<mod>` (e.g. `onemap`).
  - Multi-level mod with numbering on: `<mod>_<N>` (e.g. `zzsketch_3`), where N is one past the
    highest number already in the demo folder.
  - Type your own name to override; **Auto** goes back to the generated name.
- **Delete unsuccessful attempts:** after the game exits, keep only the newest
  `<name>-NNNNN.lmp` attempt file and rename it to `<name>.lmp`.
- **Demo playback** with `-playdemo` or `-timedemo`, plus optional `-skipsec <seconds>`.
- **Demo packaging:** zip one or more `.lmp` files with a `manifest.json` that records the port,
  IWAD, PWADs (with SHA-256 hashes), skill, complevel and flags. *The final format is still to be decided.*
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
