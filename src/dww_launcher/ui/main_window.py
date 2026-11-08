"""Main launcher window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QProcess, QRegularExpression, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontDatabase, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dww_launcher.command import (
    COMPLEVELS,
    SKILLS,
    LaunchOptions,
    build_command,
    format_command,
    resolve_executable,
)
from dww_launcher.config import Settings
from dww_launcher.demo import (
    auto_demo_name,
    consolidate_attempts,
    default_package_name,
    package_demos,
    recorded_files,
    strip_lmp,
)
from dww_launcher.wad import WadError, WadInfo, file_date, is_v12_era, map_sort_key, read_wad

WAD_FILTER = "Doom files (*.wad *.WAD *.pk3 *.PK3 *.deh *.DEH *.bex *.BEX);;All files (*)"
IWAD_FILTER = "IWAD files (*.wad *.WAD);;All files (*)"
DEMO_FILTER = "Doom demos (*.lmp *.LMP);;All files (*)"

SEQUENCE_TOOLTIP = (
    "When a mod with more than one level is loaded, name demos <mod>_1, <mod>_2, <mod>_3, ...\n"
    "The next number is one past the highest <mod>_N.lmp already in the demo folder\n"
    "(it does not depend on which map you pick)."
)
KEEP_LATEST_TOOLTIP = (
    "Some ports (e.g. DSDA-Doom) save every attempt as a separate file:\n"
    "<demo name>-00001.lmp, <demo name>-00002.lmp, ...\n\n"
    "When enabled, after the game exits all of these are deleted except the most\n"
    "recent one, which is then renamed to <demo name>.lmp."
)


class PwadList(QListWidget):
    """List of mod files that accepts drag-and-drop from the file manager."""

    changed = Signal()  # reordered
    added = Signal(list)  # newly added paths

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.model().rowsMoved.connect(lambda *_: self.changed.emit())

    def paths(self) -> list[str]:
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]

    def add_paths(self, paths) -> None:
        existing = set(self.paths())
        new = []
        for p in paths:
            if p and p not in existing:
                item = QListWidgetItem(Path(p).name)
                item.setData(Qt.ItemDataRole.UserRole, p)
                item.setToolTip(p)
                self.addItem(item)
                existing.add(p)
                new.append(p)
        if new:
            self.added.emit(new)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            self.add_paths(u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile())
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None):
        super().__init__()
        self.settings = settings if settings is not None else Settings.load()
        self.process: QProcess | None = None
        self._running_opts: LaunchOptions | None = None
        self._running_keep_latest = False
        self._wad_cache: dict[str, tuple[float, WadInfo | None]] = {}
        self._map_sources: dict[str, str | None] = {}  # map -> PWAD path (None = IWAD)
        self._name_manual = False  # user typed their own demo name
        self._setting_complevel = False

        self.setWindowTitle("Doom WAD World Launcher")
        self.resize(840, 860)
        self._build_ui()
        self._restore_state()
        self._refresh()

    # ----------------------------------------------------------------- layout
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        # Game setup ---------------------------------------------------------
        setup = QGroupBox("Game")
        form = QFormLayout(setup)

        self.port_combo = QComboBox()
        form.addRow("Source port:", self._with_buttons(
            self.port_combo, ("Add…", self._add_port), ("Remove", self._remove_port)))

        self.iwad_combo = QComboBox()
        form.addRow("IWAD:", self._with_buttons(
            self.iwad_combo, ("Add…", self._add_iwad), ("Remove", self._remove_iwad)))
        self.iwad_label = QLabel()
        self.iwad_label.setStyleSheet("color: gray;")
        form.addRow("", self.iwad_label)

        self.pwad_list = PwadList()
        self.pwad_list.setMinimumHeight(110)
        self.pwad_list.changed.connect(self._refresh)
        self.pwad_list.added.connect(self._on_pwads_added)
        pwad_buttons = QVBoxLayout()
        for text, slot in (("Add…", self._add_pwads), ("Remove", self._remove_pwads),
                           ("Up", lambda: self._move_pwad(-1)), ("Down", lambda: self._move_pwad(1)),
                           ("Clear", self._clear_pwads)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            pwad_buttons.addWidget(b)
        pwad_buttons.addStretch()
        pwad_row = QHBoxLayout()
        pwad_row.addWidget(self.pwad_list, 1)
        pwad_row.addLayout(pwad_buttons)
        form.addRow("Mods (PWAD/DEH):", pwad_row)
        root.addWidget(setup)

        # Options ------------------------------------------------------------
        options = QGroupBox("Options")
        oform = QFormLayout(options)

        self.map_combo = QComboBox()
        oform.addRow("Map:", self.map_combo)

        self.skill_combo = QComboBox()
        self.skill_combo.addItem("(default)", None)
        for value, name in SKILLS.items():
            self.skill_combo.addItem(f"{value} - {name}", value)
        self.skill_combo.setCurrentIndex(4)  # Ultra-Violence
        oform.addRow("Skill:", self.skill_combo)

        self.complevel_combo = QComboBox()
        self.complevel_combo.addItem("(port default)", None)
        for value, name in COMPLEVELS.items():
            self.complevel_combo.addItem(f"{value} - {name}", value)
        oform.addRow("Complevel:", self.complevel_combo)
        self.complevel_note = QLabel()
        self.complevel_note.setWordWrap(True)
        self.complevel_note.setStyleSheet("color: #b36b00;")
        self.complevel_note.hide()
        oform.addRow("", self.complevel_note)

        flags = QHBoxLayout()
        self.fast_check = QCheckBox("-fast")
        self.respawn_check = QCheckBox("-respawn")
        self.nomonsters_check = QCheckBox("-nomonsters")
        self.tas_check = QCheckBox("-tas")
        self.dsdademo_check = QCheckBox("-dsdademo")
        self.flag_checks = (self.fast_check, self.respawn_check, self.nomonsters_check,
                            self.tas_check, self.dsdademo_check)
        for c in self.flag_checks:
            flags.addWidget(c)
        flags.addStretch()
        oform.addRow("Flags:", flags)

        self.extra_edit = QLineEdit()
        self.extra_edit.setPlaceholderText("Any additional parameters, e.g. -nomusic -width 1280")
        oform.addRow("Extra args:", self.extra_edit)
        root.addWidget(options)

        # Demo ---------------------------------------------------------------
        demo = QGroupBox("Demo")
        dform = QFormLayout(demo)

        self.record_check = QCheckBox("Record a demo")
        self.record_name = QLineEdit()
        self.record_name.setToolTip("Demo file name (.lmp is added automatically).\n"
                                    "Filled in automatically; type your own to override it.")
        auto_btn = QPushButton("Auto")
        auto_btn.setToolTip("Go back to the automatically generated name")
        auto_btn.clicked.connect(self._reset_auto_name)
        rec_row = QHBoxLayout()
        rec_row.addWidget(self.record_check)
        rec_row.addWidget(self.record_name, 1)
        rec_row.addWidget(QLabel(".lmp"))
        rec_row.addWidget(auto_btn)
        dform.addRow(rec_row)

        self.sequence_check = QCheckBox("Number demos for multi-level mods (<mod>_1, <mod>_2, …)")
        self.sequence_check.setToolTip(SEQUENCE_TOOLTIP)
        self.keep_latest_check = QCheckBox("Delete unsuccessful attempts (keep only the latest)")
        self.keep_latest_check.setToolTip(KEEP_LATEST_TOOLTIP)
        dform.addRow(self.sequence_check)
        dform.addRow(self.keep_latest_check)

        self.demo_dir_edit = QLineEdit()
        self.demo_dir_edit.setReadOnly(True)
        dform.addRow("Demo folder:", self._with_buttons(
            self.demo_dir_edit, ("Change…", self._choose_demo_dir), ("Open", self._open_demo_dir)))

        play_row = QHBoxLayout()
        play = QPushButton("Play demo…")
        play.clicked.connect(self._play_demo)
        self.timedemo_check = QCheckBox("Use -timedemo")
        self.timedemo_check.setToolTip("Play back with -timedemo (benchmark mode) instead of -playdemo")
        pkg = QPushButton("Package demo(s)…")
        pkg.clicked.connect(self._package_existing_demos)
        play_row.addWidget(play)
        play_row.addWidget(self.timedemo_check)
        play_row.addStretch()
        play_row.addWidget(pkg)
        dform.addRow(play_row)

        self.skipsec_edit = QLineEdit()
        self.skipsec_edit.setPlaceholderText("seconds")
        self.skipsec_edit.setMaximumWidth(120)
        self.skipsec_edit.setToolTip("If set, adds -skipsec <seconds> to demo playback")
        self.skipsec_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"\d{0,6}(\.\d{0,3})?")))
        skip_row = QHBoxLayout()
        skip_row.addWidget(QLabel("Skip to:"))
        skip_row.addWidget(self.skipsec_edit)
        skip_row.addStretch()
        dform.addRow(skip_row)
        root.addWidget(demo)

        # Command preview + launch -------------------------------------------
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.command_preview = QPlainTextEdit()
        self.command_preview.setReadOnly(True)
        self.command_preview.setFont(mono)
        self.command_preview.setMaximumHeight(70)
        root.addWidget(QLabel("Command line:"))
        root.addWidget(self.command_preview)

        launch_row = QHBoxLayout()
        copy = QPushButton("Copy command")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.command_preview.toPlainText()))
        self.launch_button = QPushButton("Launch")
        self.launch_button.setDefault(True)
        self.launch_button.setMinimumHeight(36)
        self.launch_button.clicked.connect(self._launch)
        launch_row.addWidget(copy)
        launch_row.addStretch()
        launch_row.addWidget(self.launch_button)
        root.addLayout(launch_row)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(mono)
        self.log.setPlaceholderText("Source port output appears here.")
        root.addWidget(self.log, 1)

        self.setCentralWidget(central)

        # Any change refreshes the preview.
        for combo in (self.port_combo, self.map_combo, self.skill_combo):
            combo.currentIndexChanged.connect(self._refresh_preview)
        self.iwad_combo.currentIndexChanged.connect(self._on_iwad_changed)
        self.complevel_combo.currentIndexChanged.connect(self._on_complevel_changed)
        for check in (*self.flag_checks, self.record_check, self.sequence_check):
            check.toggled.connect(self._refresh_preview)
        self.extra_edit.textChanged.connect(self._refresh_preview)
        self.record_name.textEdited.connect(self._on_name_edited)
        self.record_name.editingFinished.connect(self._refresh_preview)

    @staticmethod
    def _with_buttons(widget, *buttons) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(widget, 1)
        for text, slot in buttons:
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        return row

    # ------------------------------------------------------------------ state
    def _restore_state(self) -> None:
        s = self.settings
        self.demo_dir_edit.setText(s.demo_dir)
        self.sequence_check.setChecked(s.auto_sequence)
        self.keep_latest_check.setChecked(s.keep_latest_attempt)
        self.extra_edit.setText(s.last_extra_args)
        for p in s.ports:
            self.port_combo.addItem(self._port_label(p), p)
        self._select_data(self.port_combo, s.last_port)
        self.iwad_combo.blockSignals(True)
        for p in s.iwads:
            self.iwad_combo.addItem(Path(p).name, p)
        self._select_data(self.iwad_combo, s.last_iwad)
        self.iwad_combo.blockSignals(False)
        self.pwad_list.add_paths(s.last_pwads)

    def _save_state(self) -> None:
        s = self.settings
        s.ports = [self.port_combo.itemData(i) for i in range(self.port_combo.count())]
        s.iwads = [self.iwad_combo.itemData(i) for i in range(self.iwad_combo.count())]
        s.last_port = self.port_combo.currentData() or ""
        s.last_iwad = self.iwad_combo.currentData() or ""
        s.last_pwads = self.pwad_list.paths()
        s.last_extra_args = self.extra_edit.text()
        s.demo_dir = self.demo_dir_edit.text()
        s.auto_sequence = self.sequence_check.isChecked()
        s.keep_latest_attempt = self.keep_latest_check.isChecked()
        try:
            s.save()
        except OSError as exc:
            self._log(f"Could not save settings: {exc}")

    def closeEvent(self, event):
        self._save_state()
        super().closeEvent(event)

    @staticmethod
    def _port_label(path: str) -> str:
        p = Path(path)
        return f"{p.stem}  ({p.parent})"

    @staticmethod
    def _select_data(combo: QComboBox, data) -> None:
        idx = combo.findData(data)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _wad(self, path: str) -> WadInfo | None:
        """Read a WAD, cached by modification time. Returns None if unreadable."""
        try:
            mtime = Path(path).stat().st_mtime
        except OSError:
            return None
        cached = self._wad_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            info = read_wad(path)
        except WadError as exc:
            self._log(str(exc))
            info = None
        self._wad_cache[path] = (mtime, info)
        return info

    @staticmethod
    def _is_wad(path: str) -> bool:
        return Path(path).suffix.lower() == ".wad"

    # --------------------------------------------------------------- refresh
    def _refresh(self) -> None:
        """Re-scan IWAD + PWADs for maps, then update the preview."""
        iwad = self.iwad_combo.currentData()
        sources: dict[str, str | None] = {}
        iwad_maps: set[str] = set()
        if iwad:
            info = self._wad(iwad)
            if info is None:
                self.iwad_label.setText("Could not read this IWAD")
            else:
                self.iwad_label.setText(f"{info.game} - {len(info.maps)} maps"
                                        + ("" if info.is_iwad else "  (warning: this is a PWAD)"))
                sources.update(dict.fromkeys(info.maps))
                iwad_maps = set(info.maps)
        else:
            self.iwad_label.setText("Add an IWAD (e.g. DOOM.WAD, DOOM2.WAD, freedoom2.wad)")

        # Later files override earlier ones, just like in the engine.
        for i in range(self.pwad_list.count()):
            item = self.pwad_list.item(i)
            p = item.data(Qt.ItemDataRole.UserRole)
            info = self._wad(p) if self._is_wad(p) else None
            n = len(info.maps) if info else 0
            item.setText(Path(p).name + (f"   ({n} level{'s' if n != 1 else ''})" if n else ""))
            if info:
                sources.update(dict.fromkeys(info.maps, p))
        self._map_sources = sources

        current = self.map_combo.currentData()
        self.map_combo.blockSignals(True)
        self.map_combo.clear()
        self.map_combo.addItem("(start normally)", None)
        for m in sorted(sources, key=map_sort_key):
            src = sources[m]
            if src is None:
                label = m
            elif m in iwad_maps:
                label = f"{m}   — replaced by {Path(src).name}"
            else:
                label = f"{m}   — new in {Path(src).name}"
            self.map_combo.addItem(label, m)
        self._select_data(self.map_combo, current)
        self.map_combo.blockSignals(False)
        self._refresh_preview()

    def _on_pwads_added(self, paths: list[str]) -> None:
        self._refresh()
        # Jump to the first level of the (last) newly added mod that has levels.
        for p in reversed(paths):
            info = self._wad(p) if self._is_wad(p) else None
            if info and info.maps:
                self._select_data(self.map_combo, min(info.maps, key=map_sort_key))
                break
        self._check_v12_complevel(paths)

    def _on_iwad_changed(self) -> None:
        self._refresh()
        self._check_v12_complevel(self.pwad_list.paths())

    def _on_complevel_changed(self) -> None:
        if not self._setting_complevel:
            self.complevel_note.hide()
        self._refresh_preview()

    def _check_v12_complevel(self, pwads: list[str]) -> None:
        """Select complevel 0 for Doom 1 + a mod dated in the v1.2 era."""
        iwad = self.iwad_combo.currentData()
        info = self._wad(iwad) if iwad else None
        if not info or not info.is_doom1:
            return
        old = next((p for p in pwads if self._is_wad(p) and is_v12_era(p)), None)
        if old is None or self.complevel_combo.currentData() == 0:
            return
        self._setting_complevel = True
        self._select_data(self.complevel_combo, 0)
        self._setting_complevel = False
        msg = (f"Complevel 0 (Doom v1.2) was selected automatically: {Path(old).name} is dated "
               f"{file_date(old):%Y-%m-%d}, from the Doom v1.2 era (1993-12-10 to 1994-09-01), "
               f"and the IWAD is Doom 1.")
        self.complevel_note.setText(msg)
        self.complevel_note.show()
        self._log(msg)

    # ------------------------------------------------------------ demo names
    def _naming_mod(self) -> str | None:
        """The PWAD demos are named after: the one providing the selected map,
        otherwise the first PWAD with levels."""
        src = self._map_sources.get(self.map_combo.currentData() or "")
        if src:
            return src
        for p in self.pwad_list.paths():
            info = self._wad(p) if self._is_wad(p) else None
            if info and info.maps:
                return p
        return None

    def _compute_auto_name(self) -> str:
        mod = self._naming_mod()
        info = self._wad(mod) if mod else None
        map_name = self.map_combo.currentData()
        if map_name is None:
            iwad_maps = [m for m, src in self._map_sources.items() if src is None]
            map_name = min(iwad_maps, key=map_sort_key) if iwad_maps else None
        return auto_demo_name(
            iwad=self.iwad_combo.currentData() or "",
            map_name=map_name,
            mod=mod,
            mod_map_count=len(info.maps) if info else 0,
            sequence=self.sequence_check.isChecked(),
            demo_dir=self.demo_dir_edit.text(),
        )

    def _sync_auto_name(self) -> None:
        if self._name_manual:
            return
        name = self._compute_auto_name()
        if self.record_name.text() != name:
            self.record_name.setText(name)

    def _on_name_edited(self, text: str) -> None:
        self._name_manual = bool(text.strip())
        self._refresh_preview(sync_name=False)

    def _reset_auto_name(self) -> None:
        self._name_manual = False
        self._refresh_preview()

    def _demo_record_path(self) -> str:
        name = strip_lmp(self.record_name.text().strip()) or self._compute_auto_name()
        return str(Path(self.demo_dir_edit.text()) / name)

    def _refresh_preview(self, *_, sync_name: bool = True) -> None:
        if sync_name:
            self._sync_auto_name()
        record = self._demo_record_path() if self.record_check.isChecked() else None
        try:
            text = format_command(build_command(self._current_options(record=record)))
        except ValueError as exc:  # e.g. unbalanced quotes in extra args
            text = f"Error: {exc}"
        self.command_preview.setPlainText(text)

    def _current_options(self, record: str | None = None, playdemo: str | None = None) -> LaunchOptions:
        return LaunchOptions(
            port=self.port_combo.currentData() or "",
            iwad=self.iwad_combo.currentData() or "",
            pwads=self.pwad_list.paths(),
            map=self.map_combo.currentData(),
            skill=self.skill_combo.currentData(),
            complevel=self.complevel_combo.currentData(),
            fast=self.fast_check.isChecked(),
            respawn=self.respawn_check.isChecked(),
            nomonsters=self.nomonsters_check.isChecked(),
            tas=self.tas_check.isChecked(),
            dsdademo=self.dsdademo_check.isChecked(),
            record=record,
            playdemo=playdemo,
            timedemo=self.timedemo_check.isChecked(),
            skipsec=self.skipsec_edit.text().strip().rstrip(".") or None,
            extra_args=self.extra_edit.text(),
        )

    # ------------------------------------------------------- file management
    def _add_port(self) -> None:
        # On macOS the native dialog lets users pick .app bundles directly.
        path = QFileDialog.getOpenFileName(self, "Select source port executable")[0]
        if path and self.port_combo.findData(path) < 0:
            self.port_combo.addItem(self._port_label(path), path)
        if path:
            self._select_data(self.port_combo, path)
            self._save_state()

    def _remove_port(self) -> None:
        if self.port_combo.currentIndex() >= 0:
            self.port_combo.removeItem(self.port_combo.currentIndex())
            self._save_state()

    def _add_iwad(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select IWAD(s)", filter=IWAD_FILTER)
        for path in paths:
            try:
                info = read_wad(path)
            except WadError as exc:
                QMessageBox.warning(self, "Not a WAD", str(exc))
                continue
            if not info.is_iwad:
                QMessageBox.information(
                    self, "Not an IWAD",
                    f"{Path(path).name} is a PWAD (a mod). Add it to the Mods list instead.")
                continue
            if self.iwad_combo.findData(path) < 0:
                self.iwad_combo.addItem(Path(path).name, path)
            self._select_data(self.iwad_combo, path)
        self._save_state()

    def _remove_iwad(self) -> None:
        if self.iwad_combo.currentIndex() >= 0:
            self.iwad_combo.removeItem(self.iwad_combo.currentIndex())
            self._save_state()

    def _add_pwads(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select mod files", filter=WAD_FILTER)
        self.pwad_list.add_paths(paths)

    def _remove_pwads(self) -> None:
        for item in self.pwad_list.selectedItems():
            self.pwad_list.takeItem(self.pwad_list.row(item))
        self._refresh()

    def _clear_pwads(self) -> None:
        self.pwad_list.clear()
        self._refresh()

    def _move_pwad(self, delta: int) -> None:
        row = self.pwad_list.currentRow()
        new = row + delta
        if row < 0 or not 0 <= new < self.pwad_list.count():
            return
        item = self.pwad_list.takeItem(row)
        self.pwad_list.insertItem(new, item)
        self.pwad_list.setCurrentRow(new)
        self._refresh()

    def _choose_demo_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Demo folder", self.demo_dir_edit.text())
        if path:
            self.demo_dir_edit.setText(path)
            self._refresh_preview()
            self._save_state()

    def _open_demo_dir(self) -> None:
        Path(self.demo_dir_edit.text()).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.demo_dir_edit.text()))

    # ---------------------------------------------------------------- launch
    def _launch(self) -> None:
        record = None
        if self.record_check.isChecked():
            Path(self.demo_dir_edit.text()).mkdir(parents=True, exist_ok=True)
            record = self._demo_record_path()
        self._start(self._current_options(record=record))

    def _play_demo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Play demo", self.demo_dir_edit.text(), DEMO_FILTER)
        if path:
            self._start(self._current_options(playdemo=path))

    def _start(self, opts: LaunchOptions) -> None:
        if self.process is not None:
            QMessageBox.information(self, "Already running", "A game is already running.")
            return
        if not opts.port:
            QMessageBox.warning(self, "No source port", "Add a source port first.")
            return
        if not opts.iwad:
            QMessageBox.warning(self, "No IWAD", "Add an IWAD first.")
            return
        try:
            cmd = build_command(opts)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid options", str(exc))
            return

        exe = resolve_executable(opts.port)
        self._save_state()
        self._log(f"$ {format_command(cmd)}")

        proc = QProcess(self)
        proc.setProgram(cmd[0])
        proc.setArguments(cmd[1:])
        # Many ports look for their config and resources next to the executable.
        proc.setWorkingDirectory(str(exe.parent))
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: self._log(bytes(proc.readAllStandardOutput()).decode(errors="replace").rstrip()))
        proc.errorOccurred.connect(self._on_process_error)
        proc.finished.connect(self._on_finished)
        self.process = proc
        self._running_opts = opts
        self._running_keep_latest = self.keep_latest_check.isChecked()
        self.launch_button.setEnabled(False)
        proc.start()

    def _on_process_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._log(f"Failed to start: {self.process.errorString() if self.process else error}")
            self._cleanup_process()

    def _on_finished(self, code: int, _status) -> None:
        self._log(f"Exited with code {code}.")
        opts, keep_latest = self._running_opts, self._running_keep_latest
        self._cleanup_process()
        if opts and opts.record:
            self._after_recording(Path(opts.record), keep_latest)
        self._refresh_preview()  # e.g. advance the sequence number

    def _after_recording(self, record: Path, keep_latest: bool) -> None:
        demo_dir, name = record.parent, record.name
        if keep_latest:
            try:
                result = consolidate_attempts(demo_dir, name)
            except OSError as exc:
                self._log(f"Could not clean up attempts: {exc}")
            else:
                if result.deleted:
                    self._log(f"Deleted {len(result.deleted)} unsuccessful attempt(s): "
                              + ", ".join(p.name for p in result.deleted))
                if result.renamed:
                    self._log(f"Kept latest attempt as {result.kept.name}")
                elif result.conflict:
                    self._log(f"Kept latest attempt {result.kept.name}; not renamed because "
                              f"{result.conflict.name} already exists.")
        files = recorded_files(demo_dir, name)
        if files:
            self._log("Demo file(s) in folder: " + ", ".join(p.name for p in files))
        else:
            self._log("No demo file was written (did the game exit before a level started?).")

    def _cleanup_process(self) -> None:
        if self.process is not None:
            self.process.deleteLater()
        self.process = None
        self._running_opts = None
        self.launch_button.setEnabled(True)

    # --------------------------------------------------------------- package
    def _package_existing_demos(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose demo(s) to package", self.demo_dir_edit.text(), DEMO_FILTER)
        if not paths:
            return
        demos = [Path(p) for p in paths]
        default = demos[0].parent / f"{default_package_name(demos)}.zip"
        out, _ = QFileDialog.getSaveFileName(
            self, "Save demo package", str(default), "Zip archive (*.zip)")
        if not out:
            return
        notes, ok = QInputDialog.getMultiLineText(self, "Demo notes", "Notes (optional):")
        try:
            result = package_demos(demos, self._current_options(), out, notes if ok else "")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Packaging failed", str(exc))
            return
        self._log(f"Packaged {len(demos)} demo(s): {result}")

    # ------------------------------------------------------------------ misc
    def _log(self, text: str) -> None:
        if text:
            self.log.appendPlainText(text)
