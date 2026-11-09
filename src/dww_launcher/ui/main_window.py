"""Main launcher window."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QProcess, QRect, QRegularExpression, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFontDatabase, QPalette, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
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
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QToolButton,
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
from dww_launcher import __version__
from dww_launcher.config import Settings
from dww_launcher.desktop import open_folder, open_url, process_environment
from dww_launcher.demo import (
    auto_demo_name,
    consolidate_attempts,
    default_package_name,
    recorded_files,
    strip_lmp,
)
from dww_launcher.profiles import (
    Profile,
    delete_profile,
    list_profiles,
    load_profile,
    profile_exists,
    profiles_dir,
    save_profile,
)
from dww_launcher.sheet_download import SheetDownloader
from dww_launcher.sheets import download_due, load_sheets, parse_timestamp, save_sheets, sheets_dir
from dww_launcher.submission import (
    COMMENT_LIMIT,
    Lookup,
    create_submission_zip,
    ensure_suffix,
    identifier_line,
    lookup,
    submission_text,
    text_warnings,
)
from dww_launcher.ui.file_browser import FileBrowser
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
AUTO_DOWNLOAD_PROMPT = (
    "DWW Launcher can download the latest WAD and source port lists from the DWW spreadsheet "
    "automatically.\n\nIf you turn this on, it downloads them when the launcher starts, at most "
    "once every 12 hours.\n\nYou can change this later under Options → Auto-Download Spreadsheet Data."
    "\n\nTurn on automatic downloads?"
)
ZIP_TOOLTIP = "Choose demos and zip them together with the text file"
TXT_TOOLTIP = "Write the text file to the demo folder"
YOUTUBE_URL = "https://www.youtube.com/@DOOMWADWorld"
DISCORD_URL = "https://discord.com/invite/aUsWnqaePy"
GITHUB_URL = "https://github.com/doomwadworld/dww_launcher"
BROWSER_WIDTH = 640  # default width of the file browser panel (then remembered)
MIN_BROWSER_WIDTH = 320
ENTRY_PLACEHOLDER = "e.g. 12493, https://www.doomworld.com/idgames/levels/doom2/a-c/zzsketch, levels/doom/Ports/a-c/foo"


class _ModItemDelegate(QStyledItemDelegate):
    """Draws a mod's folder, dimmed, to the right of its name."""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        path = index.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
        x = text_rect.left() + opt.fontMetrics.horizontalAdvance(opt.text) + 18
        area = QRect(x, text_rect.top(), text_rect.right() - x, text_rect.height())
        if area.width() < 40:
            return
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        color = opt.palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
        color.setAlpha(150 if selected else 120)
        painter.save()
        painter.setPen(color)
        folder = str(Path(path).parent)
        painter.drawText(area, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         opt.fontMetrics.elidedText(folder, Qt.TextElideMode.ElideMiddle, area.width()))
        painter.restore()


class PwadList(QListWidget):
    """List of mod files that accepts drag-and-drop from the file manager."""

    changed = Signal()  # reordered
    added = Signal(list)  # newly added paths

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setItemDelegate(_ModItemDelegate(self))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
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
    def __init__(self, settings: Settings | None = None, sheet_dir: Path | None = None,
                 sheet_urls: dict[str, str] | None = None, profile_dir: Path | None = None):
        super().__init__()
        self.settings = settings if settings is not None else Settings.load()
        self._sheet_dir = sheet_dir or sheets_dir()
        self._profile_dir = profile_dir or profiles_dir()
        self.current_profile: str | None = None
        self._sheet_data = load_sheets(self._sheet_dir)
        self._lookup = Lookup()
        self._auto_txt_name = ""
        self._manual_download = False
        self._downloader = SheetDownloader(urls=sheet_urls, parent=self)
        self._downloader.finished.connect(self._on_sheets_downloaded)
        self.process: QProcess | None = None
        self._running_opts: LaunchOptions | None = None
        self._running_keep_latest = False
        self._wad_cache: dict[str, tuple[float, WadInfo | None]] = {}
        self._map_sources: dict[str, str | None] = {}  # map -> PWAD path (None = IWAD)
        self._name_manual = False  # user typed their own demo name
        self._setting_complevel = False

        self.setWindowTitle("DOOM WAD World Launcher")
        self.resize(860, 940)
        self._build_ui()
        self._restore_state()
        self._refresh()

    # ----------------------------------------------------------------- layout
    def _build_ui(self) -> None:
        self._build_toolbar()
        game_tab = QWidget()
        root = QVBoxLayout(game_tab)

        # Game setup ---------------------------------------------------------
        setup = QGroupBox("Source port and WADs")
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
        self.pwad_list.customContextMenuRequested.connect(self._mods_context_menu)
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
        self.mod_maps_check = QCheckBox("Mod maps only")
        self.mod_maps_check.setToolTip("List only the levels that the loaded mods add or replace")
        self.mod_maps_check.toggled.connect(self._refresh)
        map_row = QHBoxLayout()
        map_row.addWidget(self.map_combo, 1)
        map_row.addWidget(self.mod_maps_check)
        oform.addRow("Map:", map_row)

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
        play_row.addWidget(play)
        play_row.addWidget(self.timedemo_check)
        play_row.addStretch()
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

        root.addStretch()

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(mono)
        self.log.setPlaceholderText("Source port output and messages appear here.")
        self.log.setMinimumHeight(90)

        self.tabs = QTabWidget()
        self.tabs.addTab(game_tab, "Game")
        self.tabs.addTab(self._build_submission_tab(), "Demo Submission")
        central = QSplitter(Qt.Orientation.Vertical)
        central.addWidget(self.tabs)
        central.addWidget(self.log)
        central.setStretchFactor(0, 4)
        central.setStretchFactor(1, 1)
        central.setChildrenCollapsible(False)
        self.setCentralWidget(central)
        self._build_file_browser()

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

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Toolbar")
        toolbar.setMovable(False)
        menu = QMenu(self)
        self.auto_download_action = QAction("Auto-Download Spreadsheet Data", self, checkable=True)
        self.auto_download_action.setToolTip("Download the spreadsheet data on startup, at most every 12 hours")
        self.auto_download_action.toggled.connect(self._set_auto_download)
        self.redownload_action = QAction("Re-download Spreadsheet Data Now", self)
        self.redownload_action.triggered.connect(lambda: self.download_sheets(manual=True))
        self.last_download_action = QAction("", self)
        self.last_download_action.setEnabled(False)
        menu.addAction(self.auto_download_action)
        menu.addAction(self.redownload_action)
        menu.addSeparator()
        menu.addAction(self.last_download_action)
        menu.setToolTipsVisible(True)
        self.options_button = QToolButton()
        self.options_button.setText("Options")
        self.options_button.setMenu(menu)
        self.options_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(self.options_button)

        self.profiles_menu = QMenu(self)
        self.profiles_menu.aboutToShow.connect(self._rebuild_profiles_menu)
        self.profiles_button = QToolButton()
        self.profiles_button.setText("Profiles")
        self.profiles_button.setMenu(self.profiles_menu)
        self.profiles_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(self.profiles_button)

        about = QToolButton()
        about.setText("About")
        about.clicked.connect(self.show_about)
        toolbar.addWidget(about)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.browser_action = QAction("File Browser ▸", self, checkable=True)
        self.browser_action.setShortcut("Ctrl+B")
        self.browser_action.setToolTip("Show or hide the file browser (Ctrl+B)")
        self.browser_action.toggled.connect(self.set_file_browser_visible)
        toolbar.addAction(self.browser_action)

    def _build_submission_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(QLabel("Spreadsheet ID, Doomworld URL, or idgames Path:"))
        self.entry_edit = QLineEdit()
        self.entry_edit.setPlaceholderText(ENTRY_PLACEHOLDER)
        self.entry_edit.setToolTip(
            "Identifies the idgames entry this submission is for:\n"
            "- a spreadsheet ID (DOOM 2 WAD and Ports lists only), e.g. 12493\n"
            "- a Doomworld URL, e.g. https://www.doomworld.com/idgames/levels/doom2/a-c/foo\n"
            "- an idgames path, e.g. levels/doom2/Ports/a-c/foo")
        self.entry_edit.textChanged.connect(self._run_lookup)
        layout.addWidget(self.entry_edit)
        self.entry_status = QLabel()
        self.entry_status.setWordWrap(True)
        self.entry_status.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.entry_status)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        hall = QGroupBox("Hall of Fame/Shame")
        hall_layout = QVBoxLayout(hall)
        checks = QHBoxLayout()
        self.hof_check = QCheckBox("HoF")
        self.hos_check = QCheckBox("HoS")
        self.hof_check.toggled.connect(lambda on: on and self.hos_check.setChecked(False))
        self.hos_check.toggled.connect(lambda on: on and self.hof_check.setChecked(False))
        checks.addWidget(self.hof_check)
        checks.addWidget(self.hos_check)
        checks.addStretch()
        hall_layout.addLayout(checks)
        hall_layout.addWidget(QLabel("Why?"))
        self.why_edit = QPlainTextEdit()
        self.why_counter, self.why_warning = QLabel(), QLabel()
        hall_layout.addWidget(self.why_edit, 1)
        hall_layout.addLayout(self._counter_row(self.why_warning, self.why_counter))
        splitter.addWidget(hall)

        comments = QGroupBox("Comments")
        comments_layout = QVBoxLayout(comments)
        self.comments_edit = QPlainTextEdit()
        self.comments_edit.setPlaceholderText(
            "Put additional comments you want added to the video description here.")
        self.comments_counter, self.comments_warning = QLabel(), QLabel()
        comments_layout.addWidget(self.comments_edit, 1)
        comments_layout.addLayout(self._counter_row(self.comments_warning, self.comments_counter))
        splitter.addWidget(comments)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Text file folder:"))
        self.txt_dir_edit = QLineEdit()
        self.txt_dir_edit.setReadOnly(True)
        folder_row.addWidget(self.txt_dir_edit, 1)
        self.txt_dir_change = QPushButton("Change…")
        self.txt_dir_change.clicked.connect(self._choose_txt_dir)
        folder_row.addWidget(self.txt_dir_change)
        txt_dir_open = QPushButton("Open")
        txt_dir_open.clicked.connect(lambda: self._open_folder(self.txt_dir_edit.text()))
        folder_row.addWidget(txt_dir_open)
        self.txt_same_check = QCheckBox("Same as demo folder")
        self.txt_same_check.setToolTip("Always save the text file in the demo folder (Game tab)")
        self.txt_same_check.toggled.connect(self._on_txt_same_toggled)
        folder_row.addWidget(self.txt_same_check)
        layout.addLayout(folder_row)

        buttons = QHBoxLayout()
        buttons.addWidget(QLabel("Text file:"))
        self.txt_name_edit = QLineEdit()
        self.txt_name_edit.setPlaceholderText("[zip file name].txt")
        buttons.addWidget(self.txt_name_edit, 1)
        self.create_txt_button = QPushButton("Create Text File")
        self.create_txt_button.clicked.connect(self._create_text_file)
        self.create_zip_button = QPushButton("Create Zip File…")
        self.create_zip_button.clicked.connect(self._create_zip_file)
        buttons.addWidget(self.create_txt_button)
        buttons.addWidget(self.create_zip_button)
        layout.addLayout(buttons)

        for widget in (self.hof_check, self.hos_check):
            widget.toggled.connect(self._update_submission_state)
        for edit in (self.why_edit, self.comments_edit):
            edit.textChanged.connect(self._update_submission_state)
        return tab

    @staticmethod
    def _counter_row(warning: QLabel, counter: QLabel) -> QHBoxLayout:
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #c0392b;")
        warning.hide()
        row = QHBoxLayout()
        row.addWidget(warning, 1)
        row.addWidget(counter, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        return row

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
        self._txt_dir_custom = s.txt_dir or s.demo_dir
        self.txt_same_check.setChecked(s.txt_dir_same_as_demo)
        self._on_txt_same_toggled(s.txt_dir_same_as_demo)
        self.sequence_check.setChecked(s.auto_sequence)
        self.keep_latest_check.setChecked(s.keep_latest_attempt)
        self.auto_download_action.blockSignals(True)
        self.auto_download_action.setChecked(bool(s.auto_download))
        self.auto_download_action.blockSignals(False)
        self._update_last_download_label()
        self._update_submission_state()
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
        self.file_browser.restore_state(s.file_browser_state)
        self.file_browser.set_layers(s.map_layers)
        self.mod_maps_check.setChecked(s.mod_maps_only)
        if s.file_browser_visible:
            self.browser_action.setChecked(True)

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
        s.txt_dir_same_as_demo = self.txt_same_check.isChecked()
        s.txt_dir = self._txt_dir_custom
        s.file_browser_dir = self.file_browser.directory or s.file_browser_dir
        s.file_browser_visible = self.browser_dock.isVisibleTo(self) if self.isVisible() \
            else self.browser_action.isChecked()
        self._remember_browser_width()
        s.mod_maps_only = self.mod_maps_check.isChecked()
        s.file_browser_floating = self.browser_dock.isFloating()
        s.file_browser_state = self.file_browser.save_state()
        s.map_layers = self.file_browser.layers()
        try:
            s.save()
        except OSError as exc:
            self._log(f"Could not save settings: {exc}")

    def closeEvent(self, event):
        self._save_state()
        self.file_browser.stop()
        self.file_browser.close_map_window()
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
        mod_only = self.mod_maps_check.isChecked()
        for m in sorted(sources, key=map_sort_key):
            src = sources[m]
            if src is None and mod_only:
                continue
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

    def mods_context_actions(self, paths: list[str]) -> list[QAction]:
        """Right-click actions for the selected mods (also used by tests)."""
        actions = []
        if len(paths) == 1:
            path = paths[0]
            a = QAction("Show in File Browser", self)
            a.triggered.connect(lambda: self.reveal_in_browser(path))
            actions.append(a)
            a = QAction("Open Containing Folder", self)
            a.triggered.connect(lambda: self._open_folder(str(Path(path).parent)))
            actions.append(a)
        if paths:
            a = QAction("Copy Path" if len(paths) == 1 else "Copy Paths", self)
            a.triggered.connect(lambda: QApplication.clipboard().setText("\n".join(paths)))
            actions.append(a)
            a = QAction("Remove" if len(paths) == 1 else f"Remove ({len(paths)})", self)
            a.triggered.connect(self._remove_pwads)
            actions.append(a)
        return actions

    def _mods_context_menu(self, pos) -> None:
        item = self.pwad_list.itemAt(pos)
        if item is not None and not item.isSelected():
            self.pwad_list.clearSelection()
            item.setSelected(True)
        paths = [i.data(Qt.ItemDataRole.UserRole) for i in self.pwad_list.selectedItems()]
        actions = self.mods_context_actions(paths)
        if not actions:
            return
        menu = QMenu(self)
        for a in actions:
            if a.text().startswith("Copy"):
                menu.addSeparator()
            menu.addAction(a)
        menu.exec(self.pwad_list.viewport().mapToGlobal(pos))

    def reveal_in_browser(self, path: str) -> None:
        """Open the file browser at a file's folder with the file selected."""
        self.set_file_browser_visible(True)
        self.file_browser.reveal(path)
        if self.browser_dock.isFloating():
            self.browser_dock.raise_()
            self.browser_dock.activateWindow()

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
            self._set_demo_dir(path)
            self._save_state()

    def _set_demo_dir(self, path: str) -> None:
        self.demo_dir_edit.setText(path)
        if self.txt_same_check.isChecked():
            self.txt_dir_edit.setText(path)
        self._refresh_preview()

    def _open_demo_dir(self) -> None:
        self._open_folder(self.demo_dir_edit.text())

    def _open_folder(self, path: str) -> None:
        if not path:
            return
        try:
            ok = open_folder(path)
        except OSError as exc:
            ok = False
            self._log(f"Could not create {path}: {exc}")
        if not ok:
            self._log(f"Could not open {path} in the file manager.")

    def _on_txt_same_toggled(self, same: bool) -> None:
        self.txt_dir_change.setEnabled(not same)
        self.txt_dir_edit.setText(self.demo_dir_edit.text() if same else self._txt_dir_custom)

    def _choose_txt_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Text file folder", self.txt_dir_edit.text())
        if path:
            self._txt_dir_custom = path
            self.txt_dir_edit.setText(path)
            self._save_state()

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
        # Don't pass the launcher's bundled-Qt library paths on to the source port.
        proc.setProcessEnvironment(process_environment())
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

    # ------------------------------------------------------- spreadsheet data
    def startup(self) -> None:
        """Run once the window is shown: first-launch question, then auto-download."""
        if self.settings.auto_download is None:
            answer = QMessageBox.question(self, "Auto-Download Spreadsheet Data", AUTO_DOWNLOAD_PROMPT)
            enabled = answer == QMessageBox.StandardButton.Yes
            self.auto_download_action.blockSignals(True)
            self.auto_download_action.setChecked(enabled)
            self.auto_download_action.blockSignals(False)
            self._set_auto_download(enabled)
        elif self.settings.auto_download:
            self._download_if_due()

    def _set_auto_download(self, enabled: bool) -> None:
        self.settings.auto_download = enabled
        self._save_state()
        if enabled:
            self._download_if_due()

    def _download_if_due(self) -> None:
        if download_due(self.settings.sheets_downloaded_at, self._sheet_dir):
            self.download_sheets()

    def download_sheets(self, manual: bool = False) -> None:
        if self._downloader.running:
            return
        self._manual_download = manual
        self.redownload_action.setEnabled(False)
        self.statusBar().showMessage("Downloading spreadsheet data…")
        self._downloader.start()

    def _on_sheets_downloaded(self, texts: dict, errors: list) -> None:
        self.redownload_action.setEnabled(True)
        if texts:
            try:
                save_sheets(texts, self._sheet_dir)
            except OSError as exc:
                errors = [*errors, f"Could not save spreadsheet data: {exc}"]
        if not errors:
            self.settings.sheets_downloaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._save_state()
            self.statusBar().showMessage("Spreadsheet data updated.", 8000)
            self._log("Spreadsheet data downloaded.")
        else:
            msg = "Spreadsheet download failed:\n" + "\n".join(errors)
            self.statusBar().showMessage("Spreadsheet download failed (see log).", 8000)
            self._log(msg)
            if self._manual_download:
                QMessageBox.warning(self, "Download failed", msg)
        self._sheet_data = load_sheets(self._sheet_dir)
        self._update_last_download_label()
        self._run_lookup()

    def _update_last_download_label(self) -> None:
        last = parse_timestamp(self.settings.sheets_downloaded_at)
        text = f"Last downloaded: {last.astimezone():%Y-%m-%d %H:%M}" if last else "Not downloaded yet"
        self.last_download_action.setText(text)

    # ------------------------------------------------------------ submission
    def _run_lookup(self) -> None:
        self._lookup = result = lookup(self.entry_edit.text(), self._sheet_data)
        lines = []
        if result.entry:
            lines.append(f'<span style="color:#2e7d32;">Matched: {html.escape(result.entry.describe())}</span>')
        # Follow the match, unless the user has typed their own file name.
        auto_name = f"{result.entry.zip_name}.txt" if result.entry else ""
        if self.txt_name_edit.text() == self._auto_txt_name:
            self.txt_name_edit.setText(auto_name)
        self._auto_txt_name = auto_name
        for w in result.warnings:
            lines.append(f'<span style="color:#b36b00;">Warning: {html.escape(w)}</span>')
        if result.message:
            color = "#c0392b" if result.blocked else "#b36b00"
            lines.append(f'<span style="color:{color};">{html.escape(result.message)}</span>')
        self.entry_status.setText("<br>".join(lines))
        self.entry_status.setVisible(bool(lines))
        self._update_submission_state()

    def _hall(self) -> str | None:
        return "HoF" if self.hof_check.isChecked() else "HoS" if self.hos_check.isChecked() else None

    def _update_submission_state(self) -> None:
        why, comments = self.why_edit.toPlainText(), self.comments_edit.toPlainText()
        total = len(why) + len(comments)
        for text, counter, warning in ((why, self.why_counter, self.why_warning),
                                       (comments, self.comments_counter, self.comments_warning)):
            counter.setText(f"{total}/{COMMENT_LIMIT}")
            counter.setStyleSheet("color: #c0392b; font-weight: bold;" if total > COMMENT_LIMIT else "")
            warnings = text_warnings(text, total)
            warning.setText("\n".join(warnings))
            warning.setVisible(bool(warnings))

        reasons = []
        if self._hall() and not why.strip():
            reasons.append(f'{self._hall()} is checked: explain why in the "Why?" box.')
        if self._lookup.blocked:
            reasons.append(self._lookup.message)
        for button, default in ((self.create_zip_button, ZIP_TOOLTIP), (self.create_txt_button, TXT_TOOLTIP)):
            button.setEnabled(not reasons)
            button.setToolTip("\n".join(reasons) if reasons else default)

    def _submission_text(self) -> str:
        return submission_text(identifier_line(self.entry_edit.text(), self._lookup), self._hall(),
                               self.why_edit.toPlainText(), self.comments_edit.toPlainText())

    def _txt_name(self, fallback: str) -> str:
        return ensure_suffix(self.txt_name_edit.text().strip() or fallback, ".txt")

    def _create_text_file(self) -> None:
        name = self._txt_name("comments")
        target = Path(self.txt_dir_edit.text() or self.demo_dir_edit.text()) / name
        if target.exists() and QMessageBox.question(
                self, "Overwrite?", f"{name} already exists. Overwrite it?") != QMessageBox.StandardButton.Yes:
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self._submission_text(), encoding="utf-8", newline="\n")
        except OSError as exc:
            QMessageBox.warning(self, "Could not write file", str(exc))
            return
        self._log(f"Wrote {target}")

    def _create_zip_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose demo(s) for this submission", self.demo_dir_edit.text(), DEMO_FILTER)
        if not paths:
            return
        demos = [Path(p) for p in paths]
        entry = self._lookup.entry
        base = entry.zip_name if entry else default_package_name(demos)
        out, _ = QFileDialog.getSaveFileName(
            self, "Save submission zip", str(Path(self.demo_dir_edit.text()) / f"{base}.zip"),
            "Zip archive (*.zip)")
        if not out:
            return
        txt_name = self._txt_name(base)
        try:
            result = create_submission_zip(demos, txt_name, self._submission_text(), out)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not create zip", str(exc))
            return
        self._log(f"Created {result} ({len(demos)} demo(s) + {txt_name})")

    # -------------------------------------------------------------- profiles
    def capture_profile(self) -> Profile:
        return Profile(
            port=self.port_combo.currentData() or "",
            iwad=self.iwad_combo.currentData() or "",
            pwads=self.pwad_list.paths(),
            map=self.map_combo.currentData(),
            mod_maps_only=self.mod_maps_check.isChecked(),
            skill=self.skill_combo.currentData(),
            complevel=self.complevel_combo.currentData(),
            fast=self.fast_check.isChecked(),
            respawn=self.respawn_check.isChecked(),
            nomonsters=self.nomonsters_check.isChecked(),
            tas=self.tas_check.isChecked(),
            dsdademo=self.dsdademo_check.isChecked(),
            extra_args=self.extra_edit.text(),
            record=self.record_check.isChecked(),
            record_name=self.record_name.text().strip() if self._name_manual else None,
            auto_sequence=self.sequence_check.isChecked(),
            keep_latest_attempt=self.keep_latest_check.isChecked(),
            demo_dir=self.demo_dir_edit.text(),
            timedemo=self.timedemo_check.isChecked(),
            skipsec=self.skipsec_edit.text(),
            txt_dir=self._txt_dir_custom,
            txt_dir_same_as_demo=self.txt_same_check.isChecked(),
        )

    def apply_profile(self, p: Profile) -> list[str]:
        """Apply a profile; returns warnings about files that no longer exist."""
        missing = [f for f in (p.port, p.iwad, *p.pwads) if f and not Path(f).exists()]
        if p.port and self.port_combo.findData(p.port) < 0:
            self.port_combo.addItem(self._port_label(p.port), p.port)
        self._select_data(self.port_combo, p.port)
        if p.iwad and self.iwad_combo.findData(p.iwad) < 0:
            self.iwad_combo.addItem(Path(p.iwad).name, p.iwad)
        self.iwad_combo.blockSignals(True)
        if p.iwad:
            self._select_data(self.iwad_combo, p.iwad)
        self.iwad_combo.blockSignals(False)

        self.demo_dir_edit.setText(p.demo_dir or self.demo_dir_edit.text())
        self._txt_dir_custom = p.txt_dir or self.demo_dir_edit.text()
        self.txt_same_check.setChecked(p.txt_dir_same_as_demo)
        self._on_txt_same_toggled(p.txt_dir_same_as_demo)

        self.pwad_list.clear()
        self.pwad_list.add_paths(p.pwads)  # may auto-select a map / complevel; overridden below
        self.mod_maps_check.blockSignals(True)
        self.mod_maps_check.setChecked(p.mod_maps_only)
        self.mod_maps_check.blockSignals(False)
        self._refresh()
        self._select_data(self.map_combo, p.map)
        if p.map is None:
            self.map_combo.setCurrentIndex(0)
        self._select_data(self.skill_combo, p.skill)
        self._select_data(self.complevel_combo, p.complevel)
        for check, value in ((self.fast_check, p.fast), (self.respawn_check, p.respawn),
                             (self.nomonsters_check, p.nomonsters), (self.tas_check, p.tas),
                             (self.dsdademo_check, p.dsdademo), (self.record_check, p.record),
                             (self.sequence_check, p.auto_sequence),
                             (self.keep_latest_check, p.keep_latest_attempt),
                             (self.timedemo_check, p.timedemo)):
            check.setChecked(value)
        self.extra_edit.setText(p.extra_args)
        self.skipsec_edit.setText(p.skipsec)
        self._name_manual = bool(p.record_name)
        if p.record_name:
            self.record_name.setText(p.record_name)
        self._refresh_preview()
        return missing

    def _rebuild_profiles_menu(self) -> None:
        menu = self.profiles_menu
        menu.clear()
        menu.addAction("Save Profile As…", self._save_profile_as)
        if self.current_profile:
            menu.addAction(f'Update "{self.current_profile}"',
                           lambda: self._save_profile(self.current_profile))
        names = list_profiles(self._profile_dir)
        menu.addSeparator()
        if names:
            header = menu.addAction("Load profile:")
            header.setEnabled(False)
            for name in names:
                action = menu.addAction(name, lambda n=name: self.load_profile_named(n))
                action.setCheckable(True)
                action.setChecked(name == self.current_profile)
            delete_menu = menu.addMenu("Delete Profile")
            for name in names:
                delete_menu.addAction(name, lambda n=name: self._delete_profile(n))
        else:
            empty = menu.addAction("No saved profiles")
            empty.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Open Profiles Folder", lambda: self._open_folder(str(self._profile_dir)))

    def _save_profile_as(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Profile", "Profile name:",
                                        text=self.current_profile or "")
        name = name.strip()
        if not ok or not name:
            return
        if profile_exists(name, self._profile_dir) and QMessageBox.question(
                self, "Replace profile?", f'A profile named "{name}" already exists. Replace it?'
        ) != QMessageBox.StandardButton.Yes:
            return
        self._save_profile(name)

    def _save_profile(self, name: str) -> None:
        try:
            save_profile(name, self.capture_profile(), self._profile_dir)
        except OSError as exc:
            QMessageBox.warning(self, "Could not save profile", str(exc))
            return
        self._set_current_profile(name)
        self._log(f'Saved profile "{name}".')

    def load_profile_named(self, name: str) -> None:
        try:
            profile = load_profile(name, self._profile_dir)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Could not load profile", f'"{name}": {exc}')
            return
        missing = self.apply_profile(profile)
        self._set_current_profile(name)
        self._save_state()
        self._log(f'Loaded profile "{name}".')
        if missing:
            self._log("Warning: these files from the profile no longer exist:\n  " + "\n  ".join(missing))

    def _delete_profile(self, name: str) -> None:
        if QMessageBox.question(self, "Delete profile?", f'Delete the profile "{name}"?') \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_profile(name, self._profile_dir)
        except OSError as exc:
            QMessageBox.warning(self, "Could not delete profile", str(exc))
            return
        if name == self.current_profile:
            self._set_current_profile(None)
        self._log(f'Deleted profile "{name}".')

    def _set_current_profile(self, name: str | None) -> None:
        self.current_profile = name
        self.setWindowTitle("DOOM WAD World Launcher" + (f" - {name}" if name else ""))

    # ---------------------------------------------------------- file browser
    def _build_file_browser(self) -> None:
        self.file_browser = FileBrowser(places=self._browser_places, sprite_iwads=self._sprite_iwads)
        self.file_browser.add_mods.connect(self._browser_add_mods)
        self.file_browser.use_iwad.connect(self._browser_use_iwad)
        self.file_browser.play_demo.connect(self._browser_play_demo)
        self.browser_dock = _BrowserDock("File Browser", self)
        self.browser_dock.setObjectName("FileBrowserDock")
        self.browser_dock.setWidget(self.file_browser)
        self.browser_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.browser_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable
                                      | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                                      | QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.browser_dock.closed.connect(lambda: self.browser_action.setChecked(False))
        self.browser_dock.topLevelChanged.connect(self._on_browser_floating_changed)
        self.file_browser.detach_requested.connect(self.toggle_browser_detached)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.browser_dock)
        self.browser_dock.hide()

    def _sprite_iwads(self) -> list[str]:
        """IWADs to take thing sprites from: the selected one first, then the others."""
        current = self.iwad_combo.currentData()
        others = [self.iwad_combo.itemData(i) for i in range(self.iwad_combo.count())]
        return list(dict.fromkeys([current, *others] if current else others))

    def _browser_places(self) -> list[tuple[str, str]]:
        places = [("Demo folder", self.demo_dir_edit.text())]
        if self.iwad_combo.currentData():
            places.append(("IWAD folder", str(Path(self.iwad_combo.currentData()).parent)))
        for folder in dict.fromkeys(str(Path(p).parent) for p in self.pwad_list.paths()):
            places.append(("Mod folder", folder))
        if self.port_combo.currentData():
            places.append(("Source port folder", str(resolve_executable(self.port_combo.currentData()).parent)))
        if not self.txt_same_check.isChecked():
            places.append(("Text file folder", self.txt_dir_edit.text()))
        return places

    def set_file_browser_visible(self, visible: bool) -> None:
        """Slide the file browser out of (or back into) the right side of the window."""
        if self.browser_action.isChecked() != visible:
            self.browser_action.setChecked(visible)  # re-enters via toggled
            return
        if visible == self.browser_dock.isVisibleTo(self):
            return
        if visible:
            if not self.file_browser.directory:
                start = self.settings.file_browser_dir or self.demo_dir_edit.text()
                if not self.file_browser.set_directory(start):
                    self.file_browser.set_directory(Path.home())
            if self.settings.file_browser_floating:
                self._show_browser_floating()
            else:
                self._grow_for_browser()
                self.browser_dock.show()
                self._fit_docked_browser()
        else:
            self._remember_browser_width()
            self.browser_dock.hide()
            self._shrink_after_browser()

    def _browser_panel_width(self) -> int:
        return max(int(self.settings.file_browser_width or BROWSER_WIDTH), MIN_BROWSER_WIDTH)

    def _grow_for_browser(self) -> None:
        """Widen the window to make room for the docked panel."""
        self._width_before_browser = None
        self._width_with_browser = None
        if self.isMaximized() or self.isFullScreen():
            return
        self._width_before_browser = self.width()
        screen = self.screen().availableGeometry()
        frame = self.frameGeometry().width() - self.width()
        width = max(self.width(), min(self.width() + self._browser_panel_width(), screen.width() - frame))
        x = max(screen.left(), min(self.x(), screen.right() + 1 - width - frame))
        self.resize(width, self.height())
        if x != self.x():
            self.move(x, self.y())

    def _fit_docked_browser(self) -> None:
        self.resizeDocks([self.browser_dock], [self._browser_panel_width()], Qt.Orientation.Horizontal)
        # Qt may widen the window further to fit the dock; note the final width once laid out.
        QTimer.singleShot(0, self._note_browser_width)

    def _shrink_after_browser(self) -> None:
        """Give back the width the docked panel took, keeping any resizing done meanwhile."""
        before = getattr(self, "_width_before_browser", None)
        self._width_before_browser = None
        if before is None or self.isMaximized() or self.isFullScreen():
            return
        after = getattr(self, "_width_with_browser", None) or self.width()
        target = before + self.width() - after
        # Deferred until the layout has dropped the dock's minimum width.
        QTimer.singleShot(0, lambda: self.resize(max(target, self.minimumSizeHint().width()), self.height()))

    def _show_browser_floating(self) -> None:
        self.browser_dock.setFloating(True)
        geo = self.settings.file_browser_float_geometry
        saved = isinstance(geo, list) and len(geo) == 4 and geo[2] > 100 and geo[3] > 100
        if saved:
            self.browser_dock.setGeometry(self._on_screen(QRect(*geo)))
        else:
            self.browser_dock.resize(self._browser_panel_width(), max(self.height(), 600))
        self.browser_dock.show()
        if not saved:  # beside the main window, once it has given back the panel's width
            QTimer.singleShot(0, self._place_floating_browser)

    def _place_floating_browser(self) -> None:
        rect = QRect(self.frameGeometry().right() + 8, self.y(), self.browser_dock.width(), self.browser_dock.height())
        self.browser_dock.setGeometry(self._on_screen(rect))

    def _on_screen(self, rect: QRect) -> QRect:
        """``rect`` moved (and if need be shrunk) to fit on the screen showing the main window."""
        screen = self.screen().availableGeometry()
        w, h = min(rect.width(), screen.width()), min(rect.height(), screen.height())
        x = min(max(rect.x(), screen.left()), screen.right() + 1 - w)
        y = min(max(rect.y(), screen.top()), screen.bottom() + 1 - h)
        return QRect(x, y, w, h)

    def toggle_browser_detached(self) -> None:
        """Move the file browser between the main window and a window of its own."""
        if not self.browser_dock.isVisible():
            self.settings.file_browser_floating = not self.settings.file_browser_floating
            self.browser_action.setChecked(True)
            return
        if self.browser_dock.isFloating():
            self.browser_dock.setFloating(False)
        else:
            self._remember_browser_width()
            self.browser_dock.setFloating(True)
            self._show_browser_floating()

    def _on_browser_floating_changed(self, floating: bool) -> None:
        self.file_browser.set_detached(floating)
        if not self.browser_dock.isVisible():
            return
        self.settings.file_browser_floating = floating
        if floating:  # the main window no longer needs room for the panel
            self._shrink_after_browser()
        else:  # docked again (button or dragged back): make room
            self._grow_for_browser()
            self._fit_docked_browser()

    def _remember_browser_width(self) -> None:
        """Store the panel's size (docked width, or detached window geometry) for next time."""
        if not self.browser_dock.isVisible():
            return
        if self.browser_dock.isFloating():
            g = self.browser_dock.geometry()
            self.settings.file_browser_float_geometry = [g.x(), g.y(), g.width(), g.height()]
        elif self.browser_dock.width() >= MIN_BROWSER_WIDTH:
            self.settings.file_browser_width = self.browser_dock.width()

    def _note_browser_width(self) -> None:
        if self.browser_dock.isVisible():
            self._width_with_browser = self.width()

    def _browser_add_mods(self, paths: list) -> None:
        before = set(self.pwad_list.paths())
        self.pwad_list.add_paths(paths)
        added = [p for p in self.pwad_list.paths() if p not in before]
        if added:
            self._log("Added to mods: " + ", ".join(Path(p).name for p in added))
        else:
            self._log("Already in the mods list: " + ", ".join(Path(p).name for p in paths))

    def _browser_use_iwad(self, path: str) -> None:
        if self.iwad_combo.findData(path) < 0:
            self.iwad_combo.addItem(Path(path).name, path)
        self._select_data(self.iwad_combo, path)
        self._save_state()
        self._log(f"IWAD set to {Path(path).name}")

    def _browser_play_demo(self, path: str) -> None:
        self._start(self._current_options(playdemo=path))

    # ----------------------------------------------------------------- about
    def show_about(self) -> None:
        AboutDialog(self).exec()

    # ------------------------------------------------------------------ misc
    def _log(self, text: str) -> None:
        if text:
            self.log.appendPlainText(text)


class _BrowserDock(QDockWidget):
    closed = Signal()

    def closeEvent(self, event):
        super().closeEvent(event)
        self.closed.emit()


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About DWW Launcher")
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(self.windowIcon().pixmap(64, 64))
        top.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        links = [("YouTube", YOUTUBE_URL), ("Discord", DISCORD_URL), ("GitHub", GITHUB_URL)]
        self.text = QLabel(
            f"<h3>DOOM WAD World Launcher</h3><p>Version {html.escape(__version__)}</p>"
            + "".join(f'<p>{label}: <a href="{url}">{html.escape(url)}</a></p>' for label, url in links)
        )
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.text.setOpenExternalLinks(False)  # open via desktop.open_url (clean environment)
        self.text.linkActivated.connect(open_url)
        top.addWidget(self.text, 1)
        layout.addLayout(top)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
