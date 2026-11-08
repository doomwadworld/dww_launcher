"""Interactive map preview: zoom, pan, autoscroll, layers (things, keys, weapons, secrets)."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QEvent, QLineF, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QSizePolicy, QToolButton, QToolTip, QVBoxLayout, QWidget

from dww_launcher.mapdata import DEATHMATCH_START, KEYS, MONSTERS, WEAPONS, MapData, Thing

LAYERS = ("things", "keys", "weapons", "secrets")
DEFAULT_LAYERS = {"things": False, "keys": True, "weapons": True, "secrets": True}

KEY_COLORS = {5: "#3d7bff", 40: "#3d7bff", 6: "#ffd21f", 39: "#ffd21f", 13: "#ff3b3b", 38: "#ff3b3b"}
SKULL_KEYS = {38, 39, 40}


class MapPreview(QWidget):
    """Automap-style map drawing.

    Mouse: wheel zooms around the cursor, left-drag pans, double-click fits the map.
    When zoomed in, a middle click starts browser-style autoscroll: move the mouse away
    from the marker to scroll that way (faster further out); click again, or release after
    holding, to stop.
    """

    BACKGROUND = QColor("#121212")
    ONE_SIDED = QColor("#d8d8d8")
    TWO_SIDED = QColor("#6a6a6a")
    EXIT = QColor("#ff7a1a")
    SECRET_EXIT = QColor("#c46bff")
    SECRET_FILL = QColor(176, 77, 255, 70)
    SECRET_EDGE = QColor(196, 120, 255, 200)
    MONSTER = QColor("#ff4a4a")
    PLAYER = QColor("#3ddc6b")
    OTHER = QColor("#9a9a9a")
    WEAPON = QColor("#35e0d0")

    MIN_ZOOM, MAX_ZOOM = 0.5, 400.0
    AUTOSCROLL_DEAD_ZONE = 8  # pixels

    def __init__(self, parent=None):
        super().__init__(parent)
        self.map: MapData | None = None
        self.message = "Select a file with levels"
        self.layers = dict(DEFAULT_LAYERS)
        self.zoom = 1.0
        self.center = QPointF()
        self._bounds = QRectF()
        self._lines: dict[str, list[QLineF]] = {}
        self._secret_path = QPainterPath()
        self._things: dict[str, list] = {}
        self._hover: list[tuple[str, Thing]] = []  # markers under the mouse
        self.usage_hint = ""  # tooltip shown when not over a thing
        self._drag = None  # (start pos, start center)
        self._auto_anchor: QPointF | None = None
        self._auto_cursor = QPointF()
        self._auto_started = 0.0
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(16)
        self._auto_timer.timeout.connect(self._autoscroll_step)
        self.setMinimumSize(160, 120)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)

    # ---------------------------------------------------------------- data
    def set_map(self, m: MapData | None, message: str = "") -> None:
        self.map, self.message = m, message
        self._lines, self._things, self._secret_path = {}, {}, QPainterPath()
        self._hover = []
        if m and m.vertices:
            xs = [v[0] for v in m.vertices]
            ys = [v[1] for v in m.vertices]
            self._bounds = QRectF(min(xs), min(ys), max(max(xs) - min(xs), 1), max(max(ys) - min(ys), 1))
            self._prepare(m)
        else:
            self._bounds = QRectF()
        self._stop_autoscroll()
        self.fit()

    def _prepare(self, m: MapData) -> None:
        n = len(m.vertices)
        groups = {"two": [], "one": [], "exit": [], "secret": []}
        for line in m.lines:
            if line.v1 >= n or line.v2 >= n:
                continue
            kind = m.exit_kind(line)
            key = "secret" if kind == "secret" else "exit" if kind else "two" if line.two_sided else "one"
            groups[key].append(QLineF(*m.vertices[line.v1], *m.vertices[line.v2]))
        self._lines = groups

        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        for sector, secret in enumerate(m.sector_secret):
            if secret:
                for loop in m.sector_outlines(sector):
                    pts = [QPointF(*m.vertices[v]) for v in loop if v < n]
                    if len(pts) >= 3:
                        path.addPolygon(QPolygonF(pts))
                        path.closeSubpath()
        self._secret_path = path

        things: dict[str, list[Thing]] = {"monster": [], "player": [], "other": [], "key": [], "weapon": []}
        for t in m.things:
            if t.type in KEYS:  # keys are shown whatever their skill flags
                things["key"].append(t)
            elif not (t.uv and t.single):
                continue
            elif t.type in WEAPONS:
                things["weapon"].append(t)
            elif t.type in MONSTERS:
                things["monster"].append(t)
            elif t.type == 1:
                things["player"].append(t)
            elif t.type not in (2, 3, 4, DEATHMATCH_START):
                things["other"].append(t)
        self._things = things

    def set_layer(self, name: str, visible: bool) -> None:
        self.layers[name] = visible
        self._hover = []
        self.update()

    # ---------------------------------------------------------------- markers
    # (layer, kind) in drawing order; later entries are drawn on top.
    _MARKER_ORDER = (("things", "other"), ("things", "monster"), ("things", "player"),
                     ("weapons", "weapon"), ("keys", "key"))

    def _marker_base(self) -> float:
        return min(max(16 * self.scale, 2.5), 8.0)

    def marker_radius(self, kind: str) -> float:
        base = self._marker_base()
        return {"other": base * 0.7, "monster": base, "player": base}.get(kind, base + 2)

    def visible_markers(self) -> list[tuple[str, Thing]]:
        """(kind, thing) for every marker currently drawn, bottom to top."""
        return [(kind, t) for layer, kind in self._MARKER_ORDER if self.layers.get(layer)
                for t in self._things.get(kind, ())]

    def markers_at(self, pos: QPointF, slop: float = 3.0) -> list[tuple[str, Thing]]:
        """Markers under a screen position, topmost first."""
        hits = []
        for kind, t in reversed(self.visible_markers()):
            c = self.map_to_screen(t.x, t.y)
            d = math.hypot(c.x() - pos.x(), c.y() - pos.y())
            if d <= self.marker_radius(kind) + slop:
                hits.append((kind, t))
        return hits

    def tooltip_for(self, pos: QPointF) -> str:
        hits = self.markers_at(pos)
        if not hits:
            return ""
        text = "\n\n".join(t.describe() for _, t in hits[:3])
        if len(hits) > 3:
            text += f"\n\n…and {len(hits) - 3} more here"
        return text

    # ---------------------------------------------------------------- view
    def _base_scale(self) -> float:
        if self._bounds.isEmpty():
            return 1.0
        margin = 12
        return min((self.width() - 2 * margin) / self._bounds.width(),
                   (self.height() - 2 * margin) / self._bounds.height())

    @property
    def scale(self) -> float:
        return self._base_scale() * self.zoom

    @property
    def zoomed_in(self) -> bool:
        return self.zoom > 1.01

    def fit(self) -> None:
        self.zoom = 1.0
        self.center = self._bounds.center() if not self._bounds.isEmpty() else QPointF()
        self.update()

    def map_to_screen(self, x: float, y: float) -> QPointF:
        s = self.scale
        return QPointF(self.width() / 2 + (x - self.center.x()) * s,
                       self.height() / 2 - (y - self.center.y()) * s)  # map Y points up

    def screen_to_map(self, p: QPointF) -> QPointF:
        s = self.scale
        return QPointF(self.center.x() + (p.x() - self.width() / 2) / s,
                       self.center.y() - (p.y() - self.height() / 2) / s)

    def _clamp_center(self) -> None:
        b = self._bounds
        if b.isEmpty():
            return
        self.center = QPointF(min(max(self.center.x(), b.left()), b.right()),
                              min(max(self.center.y(), b.top()), b.bottom()))

    def zoom_at(self, factor: float, anchor: QPointF | None = None) -> None:
        """Zoom by ``factor`` keeping the map point under ``anchor`` (screen pos) in place."""
        if self.map is None or self._bounds.isEmpty():
            return
        anchor = anchor if anchor is not None else QPointF(self.width() / 2, self.height() / 2)
        before = self.screen_to_map(anchor)
        self.zoom = min(max(self.zoom * factor, self.MIN_ZOOM), self.MAX_ZOOM)
        after = self.screen_to_map(anchor)
        self.center += before - after
        self._clamp_center()
        self.update()

    def pan_pixels(self, dx: float, dy: float) -> None:
        """Move the view by a screen distance (positive dx shows more of the right side)."""
        s = self.scale
        self.center = QPointF(self.center.x() + dx / s, self.center.y() - dy / s)
        self._clamp_center()
        self.update()

    # ---------------------------------------------------------------- mouse
    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            self.zoom_at(1.0015 ** delta, event.position())
        event.accept()

    def mousePressEvent(self, event):
        if self._auto_anchor is not None:  # any click ends autoscroll
            self._stop_autoscroll()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.map:
            self._drag = (event.position(), QPointF(self.center))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() == Qt.MouseButton.MiddleButton and self.zoomed_in:
            self._auto_anchor = event.position()
            self._auto_cursor = event.position()
            self._auto_started = time.monotonic()
            self._auto_timer.start()
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            start, center = self._drag
            d = event.position() - start
            s = self.scale
            self.center = QPointF(center.x() - d.x() / s, center.y() + d.y() / s)
            self._clamp_center()
            self.update()
        elif self._auto_anchor is not None:
            self._auto_cursor = event.position()
        else:
            hover = self.markers_at(event.position())
            if [id(t) for _, t in hover] != [id(t) for _, t in self._hover]:
                self._hover = hover
                if not hover:
                    QToolTip.hideText()
                self.update()
        event.accept()

    def leaveEvent(self, event):
        if self._hover:
            self._hover = []
            self.update()
        super().leaveEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            pos = QPointF(event.pos())
            text = self.tooltip_for(pos) if self._drag is None and self._auto_anchor is None else ""
            if text:
                kind, t = self.markers_at(pos)[0]
                c = self.map_to_screen(t.x, t.y)
                r = self.marker_radius(kind) + 3
                area = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r).toAlignedRect()
                QToolTip.showText(event.globalPos(), text, self, area)
            elif self.usage_hint:
                QToolTip.showText(event.globalPos(), self.usage_hint, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            self._drag = None
            self.unsetCursor()
        elif event.button() == Qt.MouseButton.MiddleButton and self._auto_anchor is not None:
            moved = (self._auto_cursor - self._auto_anchor).manhattanLength() > self.AUTOSCROLL_DEAD_ZONE
            if moved or time.monotonic() - self._auto_started > 0.3:
                self._stop_autoscroll()  # press-and-hold mode ends on release; a click keeps it on
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            self.unsetCursor()
            self.fit()
        event.accept()

    def _stop_autoscroll(self) -> None:
        self._auto_timer.stop()
        if self._auto_anchor is not None:
            self._auto_anchor = None
            self.unsetCursor()
            self.update()

    def _autoscroll_step(self) -> None:
        if self._auto_anchor is None:
            self._auto_timer.stop()
            return
        d = self._auto_cursor - self._auto_anchor
        dist = math.hypot(d.x(), d.y())
        if dist <= self.AUTOSCROLL_DEAD_ZONE:
            return
        speed = (dist - self.AUTOSCROLL_DEAD_ZONE) * 0.12  # screen pixels per tick
        self.pan_pixels(d.x() / dist * speed, d.y() / dist * speed)

    # ---------------------------------------------------------------- painting
    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), self.BACKGROUND)
        m = self.map
        if not m or not m.vertices or not m.lines:
            p.setPen(QColor("#9a9a9a"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message or "No geometry")
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.scale

        p.save()
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(s, -s)
        p.translate(-self.center.x(), -self.center.y())
        if self.layers.get("secrets") and not self._secret_path.isEmpty():
            pen = QPen(self.SECRET_EDGE, 1.5)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(QBrush(self.SECRET_FILL))
            p.drawPath(self._secret_path)
            p.setBrush(Qt.BrushStyle.NoBrush)
        for key, color, width in (("two", self.TWO_SIDED, 1.0), ("one", self.ONE_SIDED, 1.0),
                                  ("exit", self.EXIT, 2.5), ("secret", self.SECRET_EXIT, 2.5)):
            if self._lines.get(key):
                pen = QPen(color, width)
                pen.setCosmetic(True)  # width in pixels at any zoom
                p.setPen(pen)
                p.drawLines(self._lines[key])
        p.restore()

        # Things are drawn in screen space so markers stay readable at any zoom.
        for kind, t in self.visible_markers():
            self._draw_marker(p, kind, t, self.map_to_screen(t.x, t.y), self.marker_radius(kind))
        visible = {id(t) for _, t in self.visible_markers()}
        for kind, t in self._hover:
            if id(t) in visible:  # hovered: glow, ring and a slightly larger marker on top
                c = self.map_to_screen(t.x, t.y)
                r = self.marker_radius(kind)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 60))
                p.drawEllipse(c, r + 7, r + 7)
                p.setPen(QPen(QColor(255, 255, 255, 235), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(c, r + 4, r + 4)
                self._draw_marker(p, kind, t, c, r * 1.3)

        self._paint_legend(p)
        if self._auto_anchor is not None:
            self._paint_autoscroll_marker(p, self._auto_anchor)

    def _draw_marker(self, p: QPainter, kind: str, t: Thing, c: QPointF, r: float) -> None:
        if kind in ("other", "monster", "player"):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush({"other": self.OTHER, "monster": self.MONSTER, "player": self.PLAYER}[kind])
            p.drawEllipse(c, r, r)
            return
        p.setPen(QPen(QColor(0, 0, 0, 200), 1))
        if kind == "weapon":
            p.setBrush(self.WEAPON)
            p.drawPolygon(QPolygonF([QPointF(c.x(), c.y() - r), QPointF(c.x() + r * 0.9, c.y() + r * 0.7),
                                     QPointF(c.x() - r * 0.9, c.y() + r * 0.7)]))
        elif kind == "key":
            p.setBrush(QColor(KEY_COLORS[t.type]))
            if t.type in SKULL_KEYS:
                p.drawPolygon(QPolygonF([QPointF(c.x(), c.y() - r), QPointF(c.x() + r, c.y()),
                                         QPointF(c.x(), c.y() + r), QPointF(c.x() - r, c.y())]))
            else:
                p.drawRect(QRectF(c.x() - r * 0.75, c.y() - r * 0.75, r * 1.5, r * 1.5))

    def legend_entries(self) -> list[tuple[str, str]]:
        """(kind, label) rows for the small in-map legend.

        Only the exit lines are listed here; the layer toggles above the map carry icons
        that act as the legend for things, keys, weapons and secret sectors.
        """
        rows = []
        if self._lines.get("exit"):
            rows.append(("exit", "exit"))
        if self._lines.get("secret"):
            rows.append(("secretexit", "secret exit"))
        return rows

    def _paint_legend(self, p: QPainter) -> None:
        rows = self.legend_entries()
        if not rows:
            return
        fm = p.fontMetrics()
        line_h = max(fm.height(), 14) + 2
        width = 30 + max(fm.horizontalAdvance(text) for _, text in rows) + 10
        box = QRectF(8, self.height() - 8 - line_h * len(rows) - 8, width, line_h * len(rows) + 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 185))
        p.drawRoundedRect(box, 5, 5)
        y = box.top() + 4
        for kind, text in rows:
            mid = QPointF(box.left() + 16, y + line_h / 2)
            self._legend_symbol(p, kind, mid)
            p.setPen(QColor("#dedede"))
            p.drawText(QRectF(box.left() + 30, y, width - 30, line_h), Qt.AlignmentFlag.AlignVCenter, text)
            y += line_h

    @classmethod
    def layer_icon(cls, layer: str) -> QIcon:
        """Small icon showing how a layer is drawn (used on the toggle check boxes)."""
        pix = QPixmap(30, 16)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(cls.BACKGROUND)
        p.drawRoundedRect(QRectF(0, 0, 30, 16), 3, 3)
        if layer == "things":
            for x, color in ((7, cls.MONSTER), (15, cls.PLAYER), (23, cls.OTHER)):
                p.setBrush(color)
                p.drawEllipse(QPointF(x, 8), 3.2, 3.2)
        elif layer == "keys":
            cls._legend_symbol(cls, p, "key", QPointF(15, 8))
        elif layer == "weapons":
            cls._legend_symbol(cls, p, "weapon", QPointF(15, 8.5))
        elif layer == "secrets":
            cls._legend_symbol(cls, p, "secret", QPointF(15, 8))
        p.end()
        return QIcon(pix)

    def _legend_symbol(self, p: QPainter, kind: str, c: QPointF) -> None:
        if kind in ("exit", "secretexit"):
            p.setPen(QPen(self.EXIT if kind == "exit" else self.SECRET_EXIT, 2.5))
            p.drawLine(QPointF(c.x() - 8, c.y()), QPointF(c.x() + 8, c.y()))
            return
        p.setPen(QPen(QColor(0, 0, 0, 200), 1))
        if kind == "secret":
            p.setPen(QPen(self.SECRET_EDGE, 1.5))
            p.setBrush(self.SECRET_FILL)
            p.drawRect(QRectF(c.x() - 7, c.y() - 5, 14, 10))
        elif kind in ("monster", "player", "other"):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush({"monster": self.MONSTER, "player": self.PLAYER, "other": self.OTHER}[kind])
            p.drawEllipse(c, 4, 4)
        elif kind == "key":
            p.setBrush(QColor(KEY_COLORS[5]))
            p.drawRect(QRectF(c.x() - 9, c.y() - 4, 8, 8))
            p.setBrush(QColor(KEY_COLORS[38]))
            p.drawPolygon(QPolygonF([QPointF(c.x() + 5, c.y() - 5), QPointF(c.x() + 10, c.y()),
                                     QPointF(c.x() + 5, c.y() + 5), QPointF(c.x(), c.y())]))
        elif kind == "weapon":
            p.setBrush(self.WEAPON)
            p.drawPolygon(QPolygonF([QPointF(c.x(), c.y() - 6), QPointF(c.x() + 6, c.y() + 5),
                                     QPointF(c.x() - 6, c.y() + 5)]))

    def _paint_autoscroll_marker(self, p: QPainter, c: QPointF) -> None:
        p.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawEllipse(c, 13, 13)
        p.setBrush(QColor(255, 255, 255, 220))
        p.setPen(Qt.PenStyle.NoPen)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            tip = QPointF(c.x() + dx * 9, c.y() + dy * 9)
            base = QPointF(c.x() + dx * 4, c.y() + dy * 4)
            side = QPointF(dy * 3.5, dx * 3.5)
            p.drawPolygon(QPolygonF([tip, base + side, base - side]))
        p.drawEllipse(c, 1.8, 1.8)


class MapViewer(QWidget):
    """Map preview with layer toggles, a Fit button and an optional Pop out button."""

    layers_changed = Signal(dict)
    popout_requested = Signal()

    TOOLTIPS = {
        "things": "Things present on Ultra-Violence in single player:\n"
                  "red = monsters, green = player 1 start, grey = everything else\n"
                  "(keys and weapons have their own toggles)",
        "keys": "Keycards (squares) and skull keys (diamonds), in their own colours",
        "weapons": "Weapon pickups (cyan triangles)",
        "secrets": "Secret sectors (purple)",
    }

    def __init__(self, popout_button: bool = True, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        bar = QHBoxLayout()
        bar.setContentsMargins(2, 0, 2, 0)
        self.checks: dict[str, QCheckBox] = {}
        for name in LAYERS:
            box = QCheckBox(name.capitalize())
            box.setChecked(DEFAULT_LAYERS[name])
            box.setToolTip(self.TOOLTIPS[name])
            box.setIcon(MapPreview.layer_icon(name))
            box.setIconSize(QSize(30, 16))
            box.toggled.connect(lambda on, n=name: self._on_toggle(n, on))
            self.checks[name] = box
            bar.addWidget(box)
        bar.addStretch()
        self.fit_button = QToolButton()
        self.fit_button.setText("Fit")
        self.fit_button.setToolTip("Show the whole map (or double-click the map)")
        bar.addWidget(self.fit_button)
        self.popout_button = QToolButton()
        self.popout_button.setText("⧉ Pop out")
        self.popout_button.setToolTip("Open the map preview in its own window")
        self.popout_button.clicked.connect(self.popout_requested)
        self.popout_button.setVisible(popout_button)
        bar.addWidget(self.popout_button)
        bar_widget = QWidget()
        bar_widget.setLayout(bar)
        # Let the toggle row be clipped rather than forcing a minimum panel width.
        bar_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        bar_widget.setMinimumWidth(0)
        layout.addWidget(bar_widget)
        self.preview = MapPreview()
        self.preview.usage_hint = ("Scroll to zoom · drag to pan · double-click to fit\n"
                                   "Middle-click when zoomed in to autoscroll\n"
                                   "Hover over a thing to see what it is")
        self.fit_button.clicked.connect(self.preview.fit)
        layout.addWidget(self.preview, 1)

    def set_map(self, m: MapData | None, message: str = "") -> None:
        self.preview.set_map(m, message)

    def layers(self) -> dict[str, bool]:
        return {n: box.isChecked() for n, box in self.checks.items()}

    def set_layers(self, layers: dict[str, bool]) -> None:
        for name, box in self.checks.items():
            if name in layers:
                box.blockSignals(True)
                box.setChecked(bool(layers[name]))
                box.blockSignals(False)
                self.preview.set_layer(name, bool(layers[name]))

    def _on_toggle(self, name: str, on: bool) -> None:
        self.preview.set_layer(name, on)
        self.layers_changed.emit(self.layers())


class MapWindow(QWidget):
    """Stand-alone window holding a map viewer (the popped-out preview)."""

    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.viewer = MapViewer(popout_button=False)
        layout.addWidget(self.viewer)
        self.resize(900, 720)

    def show_map(self, m: MapData | None, title: str, message: str = "") -> None:
        self.setWindowTitle(f"{title} - Map Preview" if title else "Map Preview")
        self.viewer.set_map(m, message)

    def closeEvent(self, event):
        super().closeEvent(event)
        self.closed.emit()
