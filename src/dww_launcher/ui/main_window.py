"""Main launcher window."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QFontDatabase
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
from dww_launcher.demo import package_demo
from dww_launcher.wad import WadError, map_sort_key, read_wad

WAD_FILTER = "Doom files (*.wad *.WAD *.pk3 *.PK3 *.deh *.DEH *.bex *.BEX);;All files (*)"
IWAD_FILTER = "IWAD files (*.wad *.WAD);;All files (*)"
DEMO_FILTER = "Doom demos (*.lmp *.LMP);;All files (*)"


class PwadList(QListWidget):
    """List of mod files that accepts drag-and-drop from the file manager."""

    def __init__(self, on_change, parent=None):
        super().__init__(parent)
        self._on_change = on_change
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.model().rowsMoved.connect(lambda *_: on_change())

    def paths(self) -> list[str]:
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]

    def add_paths(self, paths) -> None:
        existing = set(self.paths())
        for p in paths:
            if p and p not in existing:
                item = QListWidgetItem(Path(p).name)
                item.setData(Qt.ItemDataRole.UserRole, p)
                item.setToolTip(p)
                self.addItem(item)
                existing.add(p)
        self._on_change()

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

        self.setWindowTitle("Doom WAD World Launcher")
        self.resize(820, 760)
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

        self.pwad_list = PwadList(self._refresh)
        self.pwad_list.setMinimumHeight(110)
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

        flags = QHBoxLayout()
        self.fast_check = QCheckBox("-fast")
        self.respawn_check = QCheckBox("-respawn")
        self.nomonsters_check = QCheckBox("-nomonsters")
        for c in (self.fast_check, self.respawn_check, self.nomonsters_check):
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
        self.record_name.setPlaceholderText("auto-generated if empty")
        rec_row = QHBoxLayout()
        rec_row.addWidget(self.record_check)
        rec_row.addWidget(self.record_name, 1)
        dform.addRow(rec_row)

        self.demo_dir_edit = QLineEdit()
        self.demo_dir_edit.setReadOnly(True)
        dform.addRow("Demo folder:", self._with_buttons(
            self.demo_dir_edit, ("Change…", self._choose_demo_dir), ("Open", self._open_demo_dir)))

        demo_buttons = QHBoxLayout()
        play = QPushButton("Play demo…")
        play.clicked.connect(self._play_demo)
        pkg = QPushButton("Package demo…")
        pkg.clicked.connect(self._package_existing_demo)
        demo_buttons.addWidget(play)
        demo_buttons.addWidget(pkg)
        demo_buttons.addStretch()
        dform.addRow(demo_buttons)
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
        for combo in (self.port_combo, self.iwad_combo, self.map_combo,
                      self.skill_combo, self.complevel_combo):
            combo.currentIndexChanged.connect(self._refresh_preview)
        self.iwad_combo.currentIndexChanged.connect(self._refresh)
        for check in (self.fast_check, self.respawn_check, self.nomonsters_check, self.record_check):
            check.toggled.connect(self._refresh_preview)
        self.extra_edit.textChanged.connect(self._refresh_preview)
        self.record_name.textChanged.connect(self._refresh_preview)

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
        for p in s.ports:
            self.port_combo.addItem(self._port_label(p), p)
        for p in s.iwads:
            self.iwad_combo.addItem(Path(p).name, p)
        self._select_data(self.port_combo, s.last_port)
        self._select_data(self.iwad_combo, s.last_iwad)
        self.pwad_list.add_paths(p for p in s.last_pwads)
        self.extra_edit.setText(s.last_extra_args)
        self.demo_dir_edit.setText(s.demo_dir)

    def _save_state(self) -> None:
        s = self.settings
        s.ports = [self.port_combo.itemData(i) for i in range(self.port_combo.count())]
        s.iwads = [self.iwad_combo.itemData(i) for i in range(self.iwad_combo.count())]
        s.last_port = self.port_combo.currentData() or ""
        s.last_iwad = self.iwad_combo.currentData() or ""
        s.last_pwads = self.pwad_list.paths()
        s.last_extra_args = self.extra_edit.text()
        s.demo_dir = self.demo_dir_edit.text()
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

    # --------------------------------------------------------------- refresh
    def _refresh(self) -> None:
        """Re-scan IWAD + PWADs for maps, then update the preview."""
        maps: list[str] = []
        iwad = self.iwad_combo.currentData()
        if iwad:
            try:
                info = read_wad(iwad)
                self.iwad_label.setText(f"{info.game} - {len(info.maps)} maps"
                                        + ("" if info.is_iwad else "  (warning: this is a PWAD)"))
                maps += info.maps
            except WadError as exc:
                self.iwad_label.setText(str(exc))
        else:
            self.iwad_label.setText("Add an IWAD (e.g. DOOM.WAD, DOOM2.WAD, freedoom2.wad)")

        for p in self.pwad_list.paths():
            if Path(p).suffix.lower() == ".wad":
                try:
                    maps += read_wad(p).maps
                except WadError as exc:
                    self._log(str(exc))

        current = self.map_combo.currentData()
        self.map_combo.blockSignals(True)
        self.map_combo.clear()
        self.map_combo.addItem("(start normally)", None)
        for m in sorted(set(maps), key=map_sort_key):
            self.map_combo.addItem(m, m)
        self._select_data(self.map_combo, current)
        self.map_combo.blockSignals(False)
        self._refresh_preview()

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
            record=record,
            playdemo=playdemo,
            extra_args=self.extra_edit.text(),
        )

    def _demo_record_path(self, opts_preview: bool = False) -> str:
        name = self.record_name.text().strip()
        if not name:
            if opts_preview:
                name = "<auto>"
            else:
                iwad = Path(self.iwad_combo.currentData() or "doom").stem.lower()
                map_name = (self.map_combo.currentData() or "start").lower()
                name = f"{iwad}-{map_name}-{datetime.now():%Y%m%d-%H%M%S}"
        name = name.removesuffix(".lmp")
        return str(Path(self.demo_dir_edit.text()) / name)

    def _refresh_preview(self) -> None:
        record = self._demo_record_path(opts_preview=True) if self.record_check.isChecked() else None
        try:
            text = format_command(build_command(self._current_options(record=record)))
        except ValueError as exc:  # e.g. unbalanced quotes in extra args
            text = f"Error: {exc}"
        self.command_preview.setPlainText(text)

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
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

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
        self.launch_button.setEnabled(False)
        proc.start()

    def _on_process_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._log(f"Failed to start: {self.process.errorString() if self.process else error}")
            self._cleanup_process()

    def _on_finished(self, code: int, _status) -> None:
        self._log(f"Exited with code {code}.")
        opts = self._running_opts
        self._cleanup_process()
        if opts and opts.record:
            demo = Path(opts.record + ".lmp")
            if demo.is_file():
                self._log(f"Demo recorded: {demo}")
                if QMessageBox.question(
                        self, "Demo recorded",
                        f"Recorded {demo.name}.\n\nPackage it now?") == QMessageBox.StandardButton.Yes:
                    self._package(demo, opts)
            else:
                self._log("No demo file was written (did the game exit before a level started?).")

    def _cleanup_process(self) -> None:
        if self.process is not None:
            self.process.deleteLater()
        self.process = None
        self._running_opts = None
        self.launch_button.setEnabled(True)

    # --------------------------------------------------------------- package
    def _package_existing_demo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose demo to package", self.demo_dir_edit.text(), DEMO_FILTER)
        if path:
            self._package(Path(path), self._current_options())

    def _package(self, demo: Path, opts: LaunchOptions) -> None:
        out, _ = QFileDialog.getSaveFileName(
            self, "Save demo package", str(demo.with_suffix(".zip")), "Zip archive (*.zip)")
        if not out:
            return
        notes, ok = QInputDialog.getMultiLineText(self, "Demo notes", "Notes (optional):")
        if not ok:
            notes = ""
        try:
            result = package_demo(demo, opts, out, notes)
        except OSError as exc:
            QMessageBox.warning(self, "Packaging failed", str(exc))
            return
        self._log(f"Packaged demo: {result}")

    # ------------------------------------------------------------------ misc
    def _log(self, text: str) -> None:
        if text:
            self.log.appendPlainText(text)
