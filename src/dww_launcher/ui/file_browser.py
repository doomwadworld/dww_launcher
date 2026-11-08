"""File browser panel: a table of Doom files plus Detail and Maps tabs."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen
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


# ------------------------------------------------------------------ map preview

class MapPreview(QWidget):
    """Minimal automap-style drawing of a map's linedefs."""

    BACKGROUND = QColor("#121212")
    ONE_SIDED = QColor("#d8d8d8")
    TWO_SIDED = QColor("#6a6a6a")
    EXIT = QColor("#ff7a1a")
    SECRET_EXIT = QColor("#c46bff")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.map: MapData | None = None
        self.message = "Select a file with levels"
        self.setMinimumSize(160, 120)

    def set_map(self, m: MapData | None, message: str = "") -> None:
        self.map, self.message = m, message
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), self.BACKGROUND)
        m = self.map
        if not m or not m.vertices or not m.lines:
            p.setPen(QColor("#9a9a9a"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message or "No geometry")
            return
        xs = [v[0] for v in m.vertices]
        ys = [v[1] for v in m.vertices]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        w, h = max(maxx - minx, 1), max(maxy - miny, 1)
        margin = 10
        scale = min((self.width() - 2 * margin) / w, (self.height() - 2 * margin) / h)
        ox = (self.width() - w * scale) / 2
        oy = (self.height() - h * scale) / 2

        def pt(i):
            x, y = m.vertices[i]
            return QPointF(ox + (x - minx) * scale, oy + (maxy - y) * scale)  # map Y points up

        groups = {"two": [], "one": [], "exit": [], "secret": []}
        n = len(m.vertices)
        for line in m.lines:
            if line.v1 >= n or line.v2 >= n:
                continue
            kind = m.exit_kind(line)
            key = "secret" if kind == "secret" else "exit" if kind else "two" if line.two_sided else "one"
            groups[key].append((pt(line.v1), pt(line.v2)))

        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for key, color, width in (("two", self.TWO_SIDED, 1.0), ("one", self.ONE_SIDED, 1.0),
                                  ("exit", self.EXIT, 2.5), ("secret", self.SECRET_EXIT, 2.5)):
            if groups[key]:
                p.setPen(QPen(color, width))
                for a, b in groups[key]:
                    p.drawLine(a, b)

        legend = [(c, t) for key, c, t in (("exit", self.EXIT, "exit"), ("secret", self.SECRET_EXIT, "secret exit"))
                  if groups[key]]
        y = 6
        for color, text in legend:
            p.setPen(QPen(color, 2.5))
            p.drawLine(QPointF(8, y + 7), QPointF(24, y + 7))
            p.setPen(QColor("#cfcfcf"))
            p.drawText(QRectF(30, y, 200, 14), Qt.AlignmentFlag.AlignVCenter, text)
            y += 16


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

    def __init__(self, places=None, parent=None):
        super().__init__(parent)
        self._places = places or (lambda: [])
        self._generation = 0
        self._rows: dict[str, _Item] = {}  # path -> name item
        self._summaries: dict[str, FileSummary] = {}
        self._signals = _ScanSignals()
        self._signals.result.connect(self._on_result)
        self._detail_path: str | None = None
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

        self.preview = MapPreview()
        self.map_list = QListWidget()
        self.map_list.currentItemChanged.connect(self._on_map_selected)
        self.map_stats = _property_tree()
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.map_list)
        right.addWidget(self.map_stats)
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 2)
        right.setChildrenCollapsible(False)
        self.maps_split = QSplitter(Qt.Orientation.Horizontal)
        self.maps_split.addWidget(self.preview)
        self.maps_split.addWidget(right)
        self.maps_split.setStretchFactor(0, 11)  # preview gets the larger share
        self.maps_split.setStretchFactor(1, 9)
        self.maps_split.setSizes([550, 450])
        self.maps_split.setChildrenCollapsible(False)
        self.tabs.addTab(self.maps_split, "Maps")
        self.tabs.setTabVisible(1, False)

        split = QSplitter(Qt.Orientation.Vertical)
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
            self.preview.set_map(None, "No levels")
            self.map_stats.clear()

    def _on_map_selected(self, item, _previous) -> None:
        self.map_stats.clear()
        if item is None or not self._detail_path:
            return
        name = item.data(PATH_ROLE)
        try:
            m = read_map(self._detail_path, name)
        except Exception as exc:  # noqa: BLE001 - damaged map data must not break the panel
            self.preview.set_map(None, f"Can't read {name}: {exc}")
            return
        self.preview.set_map(m)
        bold = self.map_stats.font()
        bold.setBold(True)
        title = QTreeWidgetItem([name, item.text().split("   ", 1)[1] if "   " in item.text() else ""])
        title.setFont(0, bold)
        self.map_stats.addTopLevelItem(title)
        for key, value in map_stats(m):
            row = QTreeWidgetItem([key, value])
            row.setToolTip(1, value)
            self.map_stats.addTopLevelItem(row)
