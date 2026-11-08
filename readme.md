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
- Source port output is shown in the launcher. Settings are saved between sessions.
- **Profiles** (toolbar): save every setting (except auto-download) as a named profile and load it
  back later: port, IWAD, mods, map, skill, complevel, flags, extra args, demo options and
  folders. Profiles are JSON files in a `profiles` folder inside the config directory.
- **About** (toolbar): version and links to the YouTube channel, Discord and GitHub.
- Press **Ctrl+C** in the terminal you started it from to quit (settings are saved).

### File browser

**File Browser ▸** on the right of the toolbar (or **Ctrl+B**) slides out a panel on the right.

- **File table** of `.wad`, `.pwd`, `.pk3`, `.lmp`, `.deh` and `.bex` files (any letter case) and
  folders. Columns: type, number of levels (or a demo's starting map and skill), game or IWAD,
  demo length, size and date. Click a heading to sort.
- **Double-click:** a PWAD/PK3/DEH is added to the mods list, an IWAD becomes the IWAD, a demo is
  played with the current settings, and a folder is opened. Right-click for the same actions on
  several files at once, plus Copy Path.
- **Detail tab:** file properties (size, dates, permissions) plus Doom details:
  - WADs: type, game, lumps, levels, map formats, sprite/flat/patch/music/sound counts, and
    notable lumps such as DEHACKED, UMAPINFO, MAPINFO, DECORATE and ZSCRIPT.
  - Demos: format (vanilla, longtics, Boom, MBF, MBF21, DSDA-Doom), starting map, skill,
    players, length, flags, and, when the port wrote one, the port and command line used.
- **Maps tab** (WADs and PK3s with levels): an automap-style preview with exits highlighted, and
  statistics: things, monsters on UV, secrets, exit and secret-exit lines, keys, weapons, player
  starts, line/side/vertex/sector counts and map size. Doom, Hexen and UDMF maps are supported.
  For files with several levels, a list of maps (with titles from UMAPINFO/MAPINFO) sits above
  the statistics.
  - **Zoom** with the scroll wheel (around the cursor), **pan** by dragging with the left button,
    **double-click** or **Fit** to see the whole map. When zoomed in, **middle-click** starts
    autoscroll: move away from the marker to scroll that way, faster the further you go. Click
    again, or release after holding, to stop.
  - **Layer toggles**, whose icons double as the legend:
    - **Things** (red monsters, green player start, grey everything else on UV single player)
    - **Keys** (cards as squares, skull keys as diamonds, in their colours)
    - **Weapons** (cyan triangles)
    - **Secrets** (secret sectors filled purple)

    Keys and weapons work independently of Things, so you can show only those.
  - **Hover** over a thing to highlight it and see a tooltip with its name, category and type
    number, the skill levels it appears on, ambush/multiplayer-only flags, its position and
    which way it faces. Things stacked on the same spot are listed together.
  - **⧉ Pop out** opens the preview in its own resizable window that follows the selected map.
- The panel remembers its width, internal layout, column widths and layer toggles.

### Demo Submission tab

- **Entry lookup:** enter a spreadsheet ID (DOOM 2 lists only), a Doomworld URL or an idgames path.
  The launcher finds the matching row in the DWW spreadsheet:
  - Paths containing `Ports` are looked up in the Ports lists, paths containing `Themes` in the
    DOOM 2 Ports list, and everything else in the WAD lists. `doom` or `doom2` in the path picks the game.
  - Deathmatch paths (`levels/doom/deathmatch/`, `levels/doom2/deathmatch/`) can't be submitted.
  - Warnings for entries that have already been played and for paths with no match.
  - DOOM 1 entries are matched on their full Doomworld link. DOOM 2 entries are matched on the zip
    name, because the DOOM 2 tabs have no URLs in their CSV export. If a zip name matches more than
    one entry, you're asked for the spreadsheet ID instead.
- **Hall of Fame/Shame:** HoF / HoS checkboxes (mutually exclusive) and a "Why?" box. Required if
  either box is checked.
- **Comments** box. Both boxes share one `[characters]/4500` counter and warn about YouTube's
  5000-character description limit and its ban on angle brackets.
- **Create Text File** writes `[zip file name].txt` to the text file folder, which can follow
  the demo folder or be set separately. The file contains:
  1. the spreadsheet ID and/or idgames path, e.g. `12493 | levels/doom2/v-z/zzsketch`
  2. `HoF`, `HoS` or `N/A`
  3. the "Why?" text (if any)
  4. the comments
- **Create Zip File…** zips the chosen demos together with that text file as `[zip file name].zip`.

### Spreadsheet data

- The four DWW spreadsheet tabs (DOOM 1/2 WAD and Ports lists) are downloaded as CSV from Google
  Sheets' "Publish to web" links. No account or API key is needed.
- Files are cached in a `sheets` folder inside the config directory, so lookups also work offline.
- **Options → Auto-Download Spreadsheet Data:** download on startup, at most once every 12 hours.
  You're asked about this on first launch.
- **Options → Re-download Spreadsheet Data Now** for a manual refresh.

## Downloads

Standalone builds (no Python needed) are produced by GitHub Actions for:

| Platform | File | How to run |
|---|---|---|
| Windows 10/11 (x64) | `dww-launcher-<version>-windows-x64.zip` | Extract, run `dww-launcher.exe` |
| macOS 11+ (Apple Silicon) | `dww-launcher-<version>-macos-arm64.zip` | Extract, open `DWW Launcher.app` |
| Linux (x86_64, glibc 2.35+) | `dww-launcher-<version>-linux-x86_64.tar.gz` | Extract, run `dww-launcher/dww-launcher` |

- Tagged versions are on the [Releases](../../releases) page. Builds of every push are attached to
  the workflow runs on the [Actions](../../actions) tab.
- The builds are not code-signed yet. Windows SmartScreen may warn ("More info → Run anyway").
  macOS will refuse to open the app the first time: right-click it and choose **Open**, or allow
  it in **System Settings → Privacy & Security**.
- On Linux, Qt needs `libxcb-cursor0` (Debian/Ubuntu: `sudo apt install libxcb-cursor0`), which
  some distributions don't install by default.

### Making a release

1. Bump `__version__` in `src/dww_launcher/__init__.py` and commit.
2. Tag and push: `git tag v0.2.0 && git push origin v0.2.0`.
   The tag must match `__version__`, or the build fails.

### Building locally

```bash
pip install . pyinstaller
pyinstaller packaging/dww-launcher.spec
```

The result is in `dist/`. Run it with `--smoke-test` to check that it starts.

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

The GUI tests run headless (`QT_QPA_PLATFORM=offscreen`) and use a fake source port and a local HTTP server.

## Project layout

```
src/dww_launcher/
  app.py              entry point
  wad.py              WAD reader: IWAD/PWAD, lump list, maps, game detection
  command.py          LaunchOptions -> source port command line
  demo.py             demo naming, sequence numbers, attempt cleanup
  sheets.py           spreadsheet tab definitions, CSV cache, 12-hour refresh check
  sheet_download.py   background CSV download (Qt networking)
  submission.py       spreadsheet lookup, comment checks, submission text/zip
  config.py           persistent settings (JSON in the OS config dir)
  icons.py            application icon (resources/)
  desktop.py          starting other programs with a clean environment (file manager, browser)
  profiles.py         named configuration profiles
  lmp.py              demo (.lmp) parsing: formats, length, port footer
  mapdata.py          map geometry and statistics (Doom/Hexen/UDMF)
  fileinfo.py         file summaries and properties for the file browser
  ui/file_browser.py  the file browser panel
  ui/map_view.py      interactive map preview (zoom, pan, layers, pop-out window)
  ui/main_window.py   the main window
packaging/            PyInstaller spec and entry point
scripts/make_icons.py regenerates the icon files from resources/icon_source.png
.github/workflows/    CI: tests on all platforms, builds executables, publishes releases
tests/                test suite (tests/data/demos: real demo files, see its README)
```
