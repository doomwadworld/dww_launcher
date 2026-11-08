"""File browser panel: a table of Doom files plus Detail and Maps tabs."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dww_launcher.fileinfo import (
    DEMO_EXTENSIONS,
    FileSummary,
    details,
    ext,
    human_size,
    is_doom_file,
    is_mod_file,
    level_names,
    list_maps,
    quick_summary,
    read_map,
    summarize,
)
from dww_launcher.mapdata import MapData, map_stats
from dww_launcher.sprites import SpriteSource
from dww_launcher.ui.map_view import MapViewer, MapWindow
from dww_launcher.wad import WadError, WadFile

COLUMNS = ["Name", "Type", "Levels / Map", "Game / IWAD", "Length", "Size", "Modified"]
NAME, TYPE, LEVELS, GAME, LENGTH, SIZE, MODIFIED = range(len(COLUMNS))
PATH_ROLE = Qt.ItemDataRole.UserRole
SORT_ROLE = Qt.ItemDataRole.UserRole + 1


class _Item(QTableWidgetItem):
    """Table item that sorts by SORT_ROLE when set (numbers, dates, folders first)."""

    def __lt__(self, other):
        a, b = self.data(SORT_ROLE), other.data(SORT_ROLE)
        if a is not None and b is not None:
            try:
                return a < b
            except TypeError:
                pass
        return super().__lt__(other)


# ------------------------------------------------------------------ scanning

class _ScanSignals(QObject):
    result = Signal(int, object)  # generation, FileSummary


class _ScanJob(QRunnable):
    def __init__(self, generation: int, paths: list[Path], signals: _ScanSignals, browser):
        super().__init__()
        self.generation, self.paths, self.signals, self.browser = generation, paths, signals, browser

    def run(self):
        for p in self.paths:
            if self.browser._generation != self.generation:
                return  # a newer scan replaced this one
            self.signals.result.emit(self.generation, summarize(p))


# ------------------------------------------------------------------ panel

def _property_tree() -> QTreeWidget:
    tree = QTreeWidget()
    tree.setColumnCount(2)
    tree.setHeaderHidden(True)
    tree.setRootIsDecorated(False)
    tree.setAlternatingRowColors(True)
    tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    tree.header().setStretchLastSection(True)
    tree.setWordWrap(True)
    tree.setUniformRowHeights(False)
    return tree


class FileBrowser(QWidget):
    add_mods = Signal(list)  # paths
    use_iwad = Signal(str)
    play_demo = Signal(str)
    directory_changed = Signal(str)
    layers_changed = Signal(dict)

    def __init__(self, places=None, sprite_iwads=None, parent=None):
        super().__init__(parent)
        self._places = places or (lambda: [])
        self._sprite_iwads = sprite_iwads or (lambda: [])  # IWAD paths, preferred first
        self._sprite_cache: dict[tuple, SpriteSource] = {}
        self._current_sprites: SpriteSource | None = None
        self._generation = 0
        self._rows: dict[str, _Item] = {}  # path -> name item
        self._summaries: dict[str, FileSummary] = {}
        self._signals = _ScanSignals()
        self._signals.result.connect(self._on_result)
        self._detail_path: str | None = None
        self._current_map: MapData | None = None
        self._current_map_title = ""
        self._map_message = "Select a file with levels"
        self.map_window: MapWindow | None = None
        self.directory = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Path bar
        bar = QHBoxLayout()
        self.up_button = QToolButton()
        self.up_button.setText("↑")
        self.up_button.setToolTip("Parent folder")
        self.up_button.clicked.connect(self.go_up)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Folder")
        self.path_edit.returnPressed.connect(lambda: self.set_directory(self.path_edit.text()))
        browse = QToolButton()
        browse.setText("Browse…")
        browse.clicked.connect(self._browse)
        self.places_button = QToolButton()
        self.places_button.setText("Go to")
        self.places_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.places_menu = QMenu(self)
        self.places_menu.aboutToShow.connect(self._fill_places)
        self.places_button.setMenu(self.places_menu)
        refresh = QToolButton()
        refresh.setText("⟳")
        refresh.setToolTip("Refresh")
        refresh.clicked.connect(self.refresh)
        for w in (self.up_button, self.path_edit, browse, self.places_button, refresh):
            bar.addWidget(w, 1 if w is self.path_edit else 0)
        layout.addLayout(bar)

        # Table
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(NAME, Qt.SortOrder.AscendingOrder)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for col, width in ((NAME, 170), (TYPE, 78), (LEVELS, 95), (GAME, 120), (LENGTH, 70), (SIZE, 75)):
            self.table.setColumnWidth(col, width)
        self.table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 6)
        self.table.itemDoubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.status = QLabel()
        self.status.setStyleSheet("color: gray;")

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(self.table, 1)
        top_layout.addWidget(self.status)

        # Detail / Maps tabs
        self.tabs = QTabWidget()
        self.detail_tree = _property_tree()
        self.tabs.addTab(self.detail_tree, "Detail")

        self.viewer = MapViewer()
        self.viewer.layers_changed.connect(self._on_layers_changed)
        self.viewer.popout_requested.connect(self.pop_out_map)
        self.preview = self.viewer.preview
        self.map_list = QListWidget()
        self.map_list.currentItemChanged.connect(self._on_map_selected)
        self.map_stats = _property_tree()
        self.right_split = right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.map_list)
        right.addWidget(self.map_stats)
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 2)
        right.setChildrenCollapsible(False)
        self.maps_split = QSplitter(Qt.Orientation.Horizontal)
        self.maps_split.addWidget(self.viewer)
        self.maps_split.addWidget(right)
        self.maps_split.setStretchFactor(0, 11)  # preview gets the larger share
        self.maps_split.setStretchFactor(1, 9)
        self.maps_split.setSizes([550, 450])
        self.maps_split.setChildrenCollapsible(False)
        self.tabs.addTab(self.maps_split, "Maps")
        self.tabs.setTabVisible(1, False)

        self.main_split = split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)
        layout.addWidget(split, 1)

    def stop(self) -> None:
        """Cancel any background scan (call before closing)."""
        self._generation += 1

    # -------------------------------------------------------------- navigation
    def set_directory(self, path: str | Path) -> bool:
        p = Path(path).expanduser()
        if not p.is_dir():
            self.status.setText(f"Not a folder: {path}")
            return False
        self.directory = str(p.resolve())
        self.path_edit.setText(self.directory)
        self.up_button.setEnabled(Path(self.directory).parent != Path(self.directory))
        self.refresh()
        self.directory_changed.emit(self.directory)
        return True

    def go_up(self) -> None:
        if self.directory:
            self.set_directory(Path(self.directory).parent)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose folder", self.directory)
        if path:
            self.set_directory(path)

    def _fill_places(self) -> None:
        self.places_menu.clear()
        for label, path in self._places():
            if path and Path(path).is_dir():
                self.places_menu.addAction(f"{label}  ({path})", lambda p=path: self.set_directory(p))
        self.places_menu.addAction("Home", lambda: self.set_directory(Path.home()))

    def refresh(self) -> None:
        self._generation += 1
        self._rows.clear()
        self._summaries.clear()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        entries: list[Path] = []
        try:
            with os.scandir(self.directory) as it:
                for e in it:
                    if e.name.startswith("."):
                        continue
                    try:
                        if e.is_dir() or (e.is_file() and is_doom_file(e.name)):
                            entries.append(Path(e.path))
                    except OSError:
                        continue
        except OSError as exc:
            self.status.setText(str(exc))
            self.table.setSortingEnabled(True)
            return
        for path in entries:
            self._add_row(quick_summary(path))
        self.table.setSortingEnabled(True)
        files = [p for p in entries if not p.is_dir()]
        self.status.setText(f"{len(files)} file(s), {len(entries) - len(files)} folder(s)")
        if files:
            QThreadPool.globalInstance().start(_ScanJob(self._generation, files, self._signals, self))
        self._show_details(None)

    def _add_row(self, s: FileSummary) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        folder = s.kind == "Folder"
        name = _Item(("📁 " if folder else "") + s.path.name)
        name.setData(PATH_ROLE, str(s.path))
        name.setData(SORT_ROLE, (0 if folder else 1, s.path.name.lower()))
        name.setToolTip(str(s.path))
        self.table.setItem(row, NAME, name)
        for col in (TYPE, LEVELS, GAME, LENGTH, SIZE, MODIFIED):
            self.table.setItem(row, col, _Item())
        self._rows[str(s.path)] = name
        self._fill_row(row, s)

    def _fill_row(self, row: int, s: FileSummary) -> None:
        folder = s.kind == "Folder"
        values = {
            TYPE: (s.kind, None),
            LEVELS: (s.levels, (int(s.levels) if s.levels.isdigit() else -1, s.levels)),
            GAME: (s.game, None),
            LENGTH: (s.length, s.seconds if s.seconds is not None else -1.0),
            SIZE: ("" if folder else human_size(s.size), -1 if folder else s.size),
            MODIFIED: (datetime.fromtimestamp(s.mtime).strftime("%Y-%m-%d %H:%M") if s.mtime else "", s.mtime),
        }
        for col, (text, key) in values.items():
            item = self.table.item(row, col)
            item.setText(text)
            item.setData(SORT_ROLE, key)
            if col in (LENGTH, SIZE):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if s.error:
            for col in range(len(COLUMNS)):
                self.table.item(row, col).setForeground(QColor("#c0392b"))
            self.table.item(row, NAME).setToolTip(f"{s.path}\n{s.error}")

    def _on_result(self, generation: int, s: FileSummary) -> None:
        if generation != self._generation:
            return
        item = self._rows.get(str(s.path))
        if item is None:
            return
        self._summaries[str(s.path)] = s
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self._fill_row(item.row(), s)
        self.table.setSortingEnabled(sorting)

    # -------------------------------------------------------------- selection
    def selected_paths(self) -> list[str]:
        rows = sorted({i.row() for i in self.table.selectedItems()})
        return [self.table.item(r, NAME).data(PATH_ROLE) for r in rows]

    def _is_iwad(self, path: str) -> bool:
        s = self._summaries.get(path)
        if s is not None:
            return s.is_iwad
        try:
            return ext(path) in (".wad", ".pwd") and WadFile(path).kind == "IWAD"
        except WadError:
            return False

    def _on_double_click(self, item: QTableWidgetItem) -> None:
        path = self.table.item(item.row(), NAME).data(PATH_ROLE)
        if Path(path).is_dir():
            self.set_directory(path)
        elif ext(path) in DEMO_EXTENSIONS:
            self.play_demo.emit(path)
        elif self._is_iwad(path):
            self.use_iwad.emit(path)
        elif is_mod_file(path):
            self.add_mods.emit([path])

    def context_actions(self, paths: list[str]) -> list[QAction]:
        """Actions for the right-click menu (also used by tests)."""
        actions = []
        files = [p for p in paths if not Path(p).is_dir()]
        mods = [p for p in files if is_mod_file(p) and not self._is_iwad(p)]
        if mods:
            a = QAction(f"Add to Mods ({len(mods)})" if len(mods) > 1 else "Add to Mods", self)
            a.triggered.connect(lambda: self.add_mods.emit(mods))
            actions.append(a)
        if len(files) == 1 and self._is_iwad(files[0]):
            a = QAction("Use as IWAD", self)
            a.triggered.connect(lambda: self.use_iwad.emit(files[0]))
            actions.append(a)
        if len(files) == 1 and ext(files[0]) in DEMO_EXTENSIONS:
            a = QAction("Play Demo", self)
            a.triggered.connect(lambda: self.play_demo.emit(files[0]))
            actions.append(a)
        if len(paths) == 1 and Path(paths[0]).is_dir():
            a = QAction("Open Folder", self)
            a.triggered.connect(lambda: self.set_directory(paths[0]))
            actions.append(a)
        if paths:
            a = QAction("Copy Path" if len(paths) == 1 else "Copy Paths", self)
            a.triggered.connect(lambda: QApplication.clipboard().setText("\n".join(paths)))
            actions.append(a)
        a = QAction("Refresh", self)
        a.triggered.connect(self.refresh)
        actions.append(a)
        return actions

    def _context_menu(self, pos) -> None:
        menu = QMenu(self)
        for a in self.context_actions(self.selected_paths()):
            if a.text() in ("Copy Path", "Copy Paths", "Refresh"):
                if not menu.isEmpty() and not menu.actions()[-1].isSeparator():
                    menu.addSeparator()
            menu.addAction(a)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _on_selection(self) -> None:
        paths = self.selected_paths()
        self._show_details(paths[0] if len(paths) == 1 else None)

    # -------------------------------------------------------------- details
    def _show_details(self, path: str | None) -> None:
        self._detail_path = path
        self.detail_tree.clear()
        if not path or Path(path).is_dir():
            self.tabs.setTabVisible(1, False)
            return
        bold = self.detail_tree.font()
        bold.setBold(True)
        for section, rows in details(path):
            header = QTreeWidgetItem([section, ""])
            header.setFont(0, bold)
            header.setFirstColumnSpanned(False)
            self.detail_tree.addTopLevelItem(header)
            for key, value in rows:
                item = QTreeWidgetItem([f"  {key}", value])
                item.setToolTip(1, value)
                self.detail_tree.addTopLevelItem(item)
        self._update_maps_tab(path)

    def _update_maps_tab(self, path: str) -> None:
        try:
            maps = list_maps(path)
        except Exception:  # noqa: BLE001 - an unreadable or damaged file simply has no maps tab
            maps = []
        self.tabs.setTabVisible(1, bool(maps))
        self.map_list.blockSignals(True)
        self.map_list.clear()
        titles = level_names(path) if maps else {}
        for name in maps:
            item = QListWidgetItem(f"{name}   {titles[name]}" if name in titles else name)
            item.setData(PATH_ROLE, name)
            self.map_list.addItem(item)
        self.map_list.blockSignals(False)
        self.map_list.setVisible(len(maps) > 1)
        if maps:
            self.map_list.setCurrentRow(0)
            self._on_map_selected(self.map_list.item(0), None)
        else:
            self._show_map(None, "", "No levels")
            self.map_stats.clear()

    def _on_map_selected(self, item, _previous) -> None:
        self.map_stats.clear()
        if item is None or not self._detail_path:
            return
        name = item.data(PATH_ROLE)
        try:
            m = read_map(self._detail_path, name)
        except Exception as exc:  # noqa: BLE001 - damaged map data must not break the panel
            self._show_map(None, name, f"Can't read {name}: {exc}")
            return
        title = item.text().split("   ", 1)[1] if "   " in item.text() else ""
        self._current_sprites = self.sprites_for(self._detail_path)
        self._show_map(m, f"{Path(self._detail_path).name} - {name}" + (f" {title}" if title else ""))
        bold = self.map_stats.font()
        bold.setBold(True)
        title = QTreeWidgetItem([name, item.text().split("   ", 1)[1] if "   " in item.text() else ""])
        title.setFont(0, bold)
        self.map_stats.addTopLevelItem(title)
        for key, value in map_stats(m):
            row = QTreeWidgetItem([key, value])
            row.setToolTip(1, value)
            self.map_stats.addTopLevelItem(row)

    # -------------------------------------------------------------- map views
    def sprites_for(self, path: str) -> SpriteSource | None:
        """Sprites for previewing ``path``: its own pictures first, then the IWADs.

        An IWAD being previewed is used before the launcher's selected IWAD; other known IWADs
        fill in sprites the first ones lack (e.g. Doom II monsters with a Doom 1 IWAD selected).
        """
        iwads = [p for p in self._sprite_iwads() if p]
        sources = []
        if ext(path) in (".wad", ".pwd"):
            sources.append(path)
        sources += [p for p in iwads if p != path]
        key = []
        for p in sources:
            try:
                key.append((p, Path(p).stat().st_mtime))
            except OSError:
                continue
        key = tuple(key)
        if not key:
            return None
        if key not in self._sprite_cache:
            if len(self._sprite_cache) > 8:
                self._sprite_cache.clear()
            self._sprite_cache[key] = SpriteSource.from_paths([p for p, _ in key])
        return self._sprite_cache[key]

    def _show_map(self, m: MapData | None, title: str, message: str = "") -> None:
        self._current_map, self._current_map_title = m, title
        self._map_message = message or "No levels"
        sprites = self._current_sprites if m is not None else None
        self.viewer.set_map(m, message, sprites)
        if self.map_window is not None:
            self.map_window.show_map(m, title, message, sprites)

    def pop_out_map(self) -> None:
        """Show the current map in its own resizable window (kept in sync with the selection)."""
        if self.map_window is None:
            self.map_window = MapWindow(self.window())
            self.map_window.viewer.layers_changed.connect(self._on_layers_changed)
            self.map_window.closed.connect(self._on_map_window_closed)
        self.map_window.viewer.set_layers(self.viewer.layers())
        self.map_window.show_map(self._current_map, self._current_map_title,
                                 "" if self._current_map else self._map_message,
                                 self._current_sprites if self._current_map else None)
        self.map_window.show()
        self.map_window.raise_()
        self.map_window.activateWindow()

    def _on_map_window_closed(self) -> None:
        self.map_window = None

    def _on_layers_changed(self, layers: dict) -> None:
        for viewer in (self.viewer, self.map_window.viewer if self.map_window else None):
            if viewer is not None and viewer.layers() != layers:
                viewer.set_layers(layers)
        self.layers_changed.emit(layers)

    def layers(self) -> dict[str, bool]:
        return self.viewer.layers()

    def set_layers(self, layers: dict[str, bool]) -> None:
        self.viewer.set_layers(layers)

    # -------------------------------------------------------------- persistence
    def save_state(self) -> dict[str, str]:
        """Splitter positions and column widths, as hex strings for the settings file."""
        return {
            "main_split": self.main_split.saveState().toHex().data().decode(),
            "maps_split": self.maps_split.saveState().toHex().data().decode(),
            "right_split": self.right_split.saveState().toHex().data().decode(),
            "columns": self.table.horizontalHeader().saveState().toHex().data().decode(),
        }

    def restore_state(self, state: dict[str, str]) -> None:
        targets = {"main_split": self.main_split.restoreState, "maps_split": self.maps_split.restoreState,
                   "right_split": self.right_split.restoreState,
                   "columns": self.table.horizontalHeader().restoreState}
        for key, restore in targets.items():
            value = state.get(key) if isinstance(state, dict) else None
            if isinstance(value, str) and value:
                try:
                    restore(QByteArray.fromHex(value.encode()))
                except (TypeError, ValueError):
                    pass

    def close_map_window(self) -> None:
        if self.map_window is not None:
            self.map_window.close()
