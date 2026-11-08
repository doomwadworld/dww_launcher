"""Interactive map preview: zoom, pan, autoscroll, thing filters, secrets and exits."""

from __future__ import annotations

import math
import time
from collections import Counter

from PySide6.QtCore import QEasingCurve, QEvent, QLineF, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from dww_launcher.mapdata import KEYS, MapData, Thing
from dww_launcher.sprites import SpriteSource

# Thing categories, in the order shown in the filter, with their marker colours.
CATEGORIES = ("Monster", "Player start", "Key", "Weapon", "Ammo", "Health", "Armor", "Power-up",
              "Obstacle", "Decoration", "Teleport", "Other")
CATEGORY_COLORS = {
    "Monster": "#ff4a4a", "Player start": "#3ddc6b", "Key": "#ffd21f", "Weapon": "#35e0d0",
    "Ammo": "#f0b429", "Health": "#ff7eb6", "Armor": "#5b8cff", "Power-up": "#c77dff",
    "Obstacle": "#9a9a9a", "Decoration": "#7a7a7a", "Teleport": "#2ec4b6", "Other": "#9a9a9a",
}
CATEGORY_LABELS = {"Monster": "Monsters", "Player start": "Player starts", "Key": "Keys",
                   "Weapon": "Weapons", "Ammo": "Ammo", "Health": "Health", "Armor": "Armor",
                   "Power-up": "Power-ups", "Obstacle": "Obstacles", "Decoration": "Decorations",
                   "Teleport": "Teleport destinations", "Other": "Other / unknown"}

LAYERS = ("secrets", "exits")
DEFAULT_LAYERS = {
    "secrets": True,
    "exits": True,
    "hidden_categories": ["Decoration", "Obstacle", "Other", "Teleport"],
    "hidden_types": [2, 3, 4, 11],  # co-op and deathmatch starts: only player 1 by default
}

KEY_COLORS = {5: "#3d7bff", 40: "#3d7bff", 6: "#ffd21f", 39: "#ffd21f", 13: "#ff3b3b", 38: "#ff3b3b"}
SKULL_KEYS = {38, 39, 40}

# Drawing order: later kinds are drawn on top.
_KIND_ORDER = {"other": 0, "weapon": 1, "monster": 2, "player": 3, "key": 4}


def thing_kind(t: Thing) -> str:
    """How a thing is drawn: monster/player face tokens, keys, weapons, everything else."""
    return {"Monster": "monster", "Player start": "player", "Key": "key", "Weapon": "weapon"}.get(t.category, "other")


def normalize_layers(layers: dict | None) -> dict:
    """Fill in defaults and drop settings from older versions (things/keys/weapons toggles)."""
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_LAYERS.items()}
    for key in LAYERS:
        if isinstance((layers or {}).get(key), bool):
            out[key] = layers[key]
    for key in ("hidden_categories", "hidden_types"):
        if isinstance((layers or {}).get(key), list):
            out[key] = list(layers[key])
    out["hidden_categories"] = sorted(c for c in set(out["hidden_categories"]) if c in CATEGORIES)
    out["hidden_types"] = sorted({int(t) for t in out["hidden_types"]})
    return out


class MapPreview(QWidget):
    """Automap-style map drawing.

    Mouse: wheel zooms around the cursor, left-drag pans, double-click fits the map.
    When zoomed in, a middle click starts browser-style autoscroll: move the mouse away
    from the marker to scroll that way (faster further out); click again, or release after
    holding, to stop. Clicking an exit in the legend jumps to it.
    """

    BACKGROUND = QColor("#121212")
    ONE_SIDED = QColor("#d8d8d8")
    TWO_SIDED = QColor("#6a6a6a")
    EXIT = QColor("#ff7a1a")
    SECRET_EXIT = QColor("#c46bff")
    SECRET_FILL = QColor(176, 77, 255, 70)
    SECRET_EDGE = QColor(196, 120, 255, 200)

    MIN_ZOOM, MAX_ZOOM = 0.5, 400.0
    AUTOSCROLL_DEAD_ZONE = 8  # pixels

    def __init__(self, parent=None):
        super().__init__(parent)
        self.map: MapData | None = None
        self.message = "Select a file with levels"
        settings = normalize_layers(None)
        self.layers = {k: settings[k] for k in LAYERS}
        self.hidden_categories: set[str] = set(settings["hidden_categories"])
        self.hidden_types: set[int] = set(settings["hidden_types"])
        self.zoom = 1.0
        self.center = QPointF()
        self._bounds = QRectF()
        self._lines: dict[str, list[QLineF]] = {}
        self._exit_groups: dict[str, list[QRectF]] = {}
        self._jump_index: dict[str, int] = {}
        self._secret_path = QPainterPath()
        self._markers: list[tuple[str, Thing]] = []
        self._hover: list[tuple[str, Thing]] = []  # markers under the mouse
        self._legend_rects: list[tuple[QRectF, str]] = []
        self.sprites: SpriteSource | None = None
        self._icons: dict[int, QPixmap | None] = {}
        self.usage_hint = ""  # tooltip shown when not over a thing
        self._drag = None  # (start pos, start center)
        self._auto_anchor: QPointF | None = None
        self._auto_cursor = QPointF()
        self._auto_started = 0.0
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(16)
        self._auto_timer.timeout.connect(self._autoscroll_step)
        self._flash: tuple[QRectF, str] | None = None  # exit group just jumped to
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(350)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.valueChanged.connect(self._animate_step)
        self._anim_from = self._anim_to = (QPointF(), 1.0)
        self.setMinimumSize(160, 120)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)

    # ---------------------------------------------------------------- data
    def set_map(self, m: MapData | None, message: str = "", sprites: SpriteSource | None = None) -> None:
        self._animation.stop()
        self.map, self.message = m, message
        self.set_sprites(sprites)
        self._lines, self._markers, self._secret_path = {}, [], QPainterPath()
        self._exit_groups, self._jump_index, self._flash = {}, {}, None
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
            q = QLineF(*m.vertices[line.v1], *m.vertices[line.v2])
            groups["two" if line.two_sided else "one"].append(q)  # always drawn as a wall
            kind = m.exit_kind(line)
            if kind:  # and highlighted on top when exits are shown
                groups["secret" if kind == "secret" else "exit"].append(q)
        self._lines = groups
        self._exit_groups = {k: _cluster(groups[k]) for k in ("exit", "secret") if groups[k]}

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

        # Things present on UV in single player (keys whatever their flags).
        markers = [(thing_kind(t), t) for t in m.things if t.type in KEYS or (t.uv and t.single)]
        markers.sort(key=lambda kt: _KIND_ORDER[kt[0]])
        self._markers = markers

    def set_sprites(self, sprites: SpriteSource | None) -> None:
        """Use pictures from the given WADs for things (None = plain shapes)."""
        self.sprites = sprites if sprites is not None and sprites.available else None
        self._icons: dict[int, QPixmap | None] = {}
        self.update()

    def icon(self, thing_type: int) -> QPixmap | None:
        if self.sprites is None:
            return None
        if thing_type not in self._icons:
            img = self.sprites.thing_image(thing_type)
            self._icons[thing_type] = QPixmap.fromImage(img) if img is not None and not img.isNull() else None
        return self._icons[thing_type]

    def set_layer(self, name: str, visible: bool) -> None:
        self.layers[name] = visible
        self._hover = []
        self.update()

    def set_thing_filter(self, hidden_categories, hidden_types) -> None:
        self.hidden_categories = set(hidden_categories)
        self.hidden_types = set(hidden_types)
        self._hover = []
        self.update()

    def thing_visible(self, t: Thing) -> bool:
        return t.category not in self.hidden_categories and t.type not in self.hidden_types

    def thing_counts(self) -> Counter:
        """How many of each thing type the preview can show on this map."""
        return Counter(t.type for _, t in self._markers)

    # ---------------------------------------------------------------- markers
    def _marker_base(self) -> float:
        return min(max(16 * self.scale, 2.5), 8.0)

    # Icon box sizes: (map units per icon, min px, max px)
    _ICON_SIZES = {"monster": (44, 12, 40), "player": (44, 12, 40), "key": (36, 14, 30),
                   "weapon": (44, 14, 34), "other": (32, 11, 28)}

    def icon_box(self, kind: str) -> float:
        units, low, high = self._ICON_SIZES[kind]
        return min(max(units * self.scale, low), high)

    def marker_radius(self, kind: str, t: Thing | None = None) -> float:
        if t is not None and self.icon(t.type) is not None:
            return self.icon_box(kind) / 2
        base = self._marker_base()
        return {"other": base * 0.8, "monster": base, "player": base}.get(kind, base + 2)

    def visible_markers(self) -> list[tuple[str, Thing]]:
        """(kind, thing) for every marker currently drawn, bottom to top."""
        return [(kind, t) for kind, t in self._markers if self.thing_visible(t)]

    def markers_at(self, pos: QPointF, slop: float = 3.0) -> list[tuple[str, Thing]]:
        """Markers under a screen position, topmost first."""
        hits = []
        for kind, t in reversed(self.visible_markers()):
            c = self.map_to_screen(t.x, t.y)
            d = math.hypot(c.x() - pos.x(), c.y() - pos.y())
            if d <= self.marker_radius(kind, t) + slop:
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
        self._animation.stop()
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
        self._animation.stop()
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

    # ---------------------------------------------------------------- exits
    def exit_count(self, kind: str) -> int:
        """Number of separate exits of a kind ("exit" or "secret"); nearby lines count once."""
        return len(self._exit_groups.get(kind, ()))

    def jump_to_exit(self, kind: str = "exit", animate: bool = True) -> QRectF | None:
        """Move the view to the next exit of ``kind`` (cycling through several)."""
        groups = self._exit_groups.get(kind)
        if not groups:
            return None
        index = self._jump_index.get(kind, -1) + 1
        index %= len(groups)
        self._jump_index[kind] = index
        target = groups[index]
        area = target.adjusted(-384, -384, 384, 384)  # show some surroundings
        base = self._base_scale()
        zoom = min((self.width() - 24) / (area.width() * base), (self.height() - 24) / (area.height() * base))
        zoom = min(max(zoom, 1.0), self.MAX_ZOOM)
        self._flash = (target, kind)
        self._flash_timer.start(1600)
        if animate:
            self._anim_from, self._anim_to = (QPointF(self.center), self.zoom), (target.center(), zoom)
            self._animation.stop()
            self._animation.setStartValue(0.0)
            self._animation.setEndValue(1.0)
            self._animation.start()
        else:
            self.center, self.zoom = target.center(), zoom
            self.update()
        return target

    def _animate_step(self, t) -> None:
        (c0, z0), (c1, z1) = self._anim_from, self._anim_to
        t = float(t)
        self.center = QPointF(c0.x() + (c1.x() - c0.x()) * t, c0.y() + (c1.y() - c0.y()) * t)
        self.zoom = z0 * (z1 / z0) ** t  # zoom changes evenly on a log scale
        self.update()

    def _end_flash(self) -> None:
        self._flash = None
        self.update()

    def _legend_row_at(self, pos: QPointF) -> str | None:
        for rect, kind in self._legend_rects:
            if rect.contains(pos):
                return kind
        return None

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
        legend = self._legend_row_at(event.position())
        if event.button() == Qt.MouseButton.LeftButton and legend:
            self.jump_to_exit("secret" if legend == "secretexit" else "exit")
        elif event.button() == Qt.MouseButton.LeftButton and self.map:
            self._animation.stop()
            self._drag = (event.position(), QPointF(self.center))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() == Qt.MouseButton.MiddleButton and self.zoomed_in:
            self._animation.stop()
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
            if self._legend_row_at(event.position()):
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                hover = []
            else:
                self.unsetCursor()
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
            legend = self._legend_row_at(pos)
            text = self.tooltip_for(pos) if self._drag is None and self._auto_anchor is None else ""
            if legend:
                kind = "secret" if legend == "secretexit" else "exit"
                n = self.exit_count(kind)
                what = "secret exit" if kind == "secret" else "exit"
                QToolTip.showText(event.globalPos(), f"Click to show the {what}" +
                                  (f" ({n} of them: click again for the next)" if n > 1 else ""), self)
            elif text:
                kind, t = self.markers_at(pos)[0]
                c = self.map_to_screen(t.x, t.y)
                r = self.marker_radius(kind, t) + 3
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
        if event.button() == Qt.MouseButton.LeftButton and not self._legend_row_at(event.position()):
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
        self._legend_rects = []
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
        line_styles = [("two", self.TWO_SIDED, 1.0), ("one", self.ONE_SIDED, 1.0)]
        if self.layers.get("exits"):
            line_styles += [("exit", self.EXIT, 2.5), ("secret", self.SECRET_EXIT, 2.5)]
        for key, color, width in line_styles:
            if self._lines.get(key):
                pen = QPen(color, width)
                pen.setCosmetic(True)  # width in pixels at any zoom
                p.setPen(pen)
                p.drawLines(self._lines[key])
        p.restore()

        if self._flash is not None:  # draw attention to the exit just jumped to
            rect, kind = self._flash
            a, b = self.map_to_screen(rect.left(), rect.top()), self.map_to_screen(rect.right(), rect.bottom())
            box = QRectF(a, b).normalized().adjusted(-10, -10, 10, 10)
            color = QColor(self.SECRET_EXIT if kind == "secret" else self.EXIT)
            p.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
            color.setAlpha(40)
            p.setBrush(color)
            p.drawRoundedRect(box, 6, 6)

        # Things are drawn in screen space so markers stay readable at any zoom.
        visible = self.visible_markers()
        for kind, t in visible:
            self._draw_marker(p, kind, t, self.map_to_screen(t.x, t.y), self.marker_radius(kind, t))
        shown = {id(t) for _, t in visible}
        for kind, t in self._hover:
            if id(t) in shown:  # hovered: glow, ring and a slightly larger marker on top
                c = self.map_to_screen(t.x, t.y)
                r = self.marker_radius(kind, t)
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

    @staticmethod
    def thing_color(t: Thing) -> QColor:
        if t.type in KEY_COLORS:
            return QColor(KEY_COLORS[t.type])
        return QColor(CATEGORY_COLORS.get(t.category, CATEGORY_COLORS["Other"]))

    def _draw_marker(self, p: QPainter, kind: str, t: Thing, c: QPointF, r: float) -> None:
        pix = self.icon(t.type)
        if pix is not None:
            self._draw_icon(p, kind, t, pix, c, r)
            return
        color = self.thing_color(t)
        if kind in ("other", "monster", "player"):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(c, r, r)
            return
        p.setPen(QPen(QColor(0, 0, 0, 200), 1))
        p.setBrush(color)
        if kind == "weapon":
            p.drawPolygon(QPolygonF([QPointF(c.x(), c.y() - r), QPointF(c.x() + r * 0.9, c.y() + r * 0.7),
                                     QPointF(c.x() - r * 0.9, c.y() + r * 0.7)]))
        elif t.type in SKULL_KEYS:
            p.drawPolygon(QPolygonF([QPointF(c.x(), c.y() - r), QPointF(c.x() + r, c.y()),
                                     QPointF(c.x(), c.y() + r), QPointF(c.x() - r, c.y())]))
        else:
            p.drawRect(QRectF(c.x() - r * 0.75, c.y() - r * 0.75, r * 1.5, r * 1.5))

    def _draw_icon(self, p: QPainter, kind: str, t: Thing, pix: QPixmap, c: QPointF, r: float) -> None:
        box = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        color = self.thing_color(t)
        # Translucent tile in the thing's colour behind every icon, so sprites stand out.
        fill = QColor(color)
        fill.setAlpha(110 if kind in ("monster", "player") else 85)
        edge = QColor(color)
        edge.setAlpha(235 if kind in ("monster", "player") else 190)
        p.setPen(QPen(edge, 1.5 if kind in ("monster", "player") else 1.0))
        p.setBrush(fill)
        p.drawRoundedRect(box, 3, 3)
        box = box.adjusted(1.5, 1.5, -1.5, -1.5)
        # fit the picture in the box, keeping its proportions
        scale = min(box.width() / pix.width(), box.height() / pix.height())
        w, h = pix.width() * scale, pix.height() * scale
        target = QRectF(box.center().x() - w / 2, box.center().y() - h / 2, w, h)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, scale < 1)  # crisp pixels when enlarged
        if t.type == 58:  # spectre: partially invisible
            p.setOpacity(0.45)
        p.drawPixmap(target, pix, QRectF(pix.rect()))
        p.setOpacity(1.0)

    def legend_entries(self) -> list[tuple[str, str]]:
        """(kind, label) rows for the small in-map legend: the exits, which can be clicked."""
        if not self.layers.get("exits"):
            return []
        rows = []
        for kind, key, label in (("exit", "exit", "exit"), ("secretexit", "secret", "secret exit")):
            n = self.exit_count(key)
            if n:
                rows.append((kind, f"{label} ({n})" if n > 1 else label))
        return rows

    def _paint_legend(self, p: QPainter) -> None:
        rows = self.legend_entries()
        if not rows:
            return
        fm = p.fontMetrics()
        line_h = max(fm.height(), 14) + 4
        width = 30 + max(fm.horizontalAdvance(text) for _, text in rows) + 24
        box = QRectF(8, self.height() - 8 - line_h * len(rows) - 8, width, line_h * len(rows) + 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 185))
        p.drawRoundedRect(box, 5, 5)
        y = box.top() + 4
        for kind, text in rows:
            row = QRectF(box.left(), y, width, line_h)
            self._legend_rects.append((row, kind))
            self._legend_symbol(p, kind, QPointF(box.left() + 16, y + line_h / 2))
            p.setPen(QColor("#dedede"))
            p.drawText(QRectF(box.left() + 30, y, width - 30, line_h), Qt.AlignmentFlag.AlignVCenter, text)
            p.drawText(QRectF(box.left(), y, width - 8, line_h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "➜")
            y += line_h

    @classmethod
    def swatch(cls, color: QColor | str, shape: str = "tile", size: QSize = QSize(16, 16)) -> QIcon:
        """Small coloured icon: a translucent tile (things), a line (exits) or a sector patch."""
        pix = QPixmap(size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(color)
        if shape == "line":
            p.setPen(QPen(color, 3))
            p.drawLine(QPointF(2, size.height() / 2), QPointF(size.width() - 2, size.height() / 2))
        else:
            fill = QColor(color)
            fill.setAlpha(110 if shape == "tile" else 70)
            p.setPen(QPen(color, 1.5))
            p.setBrush(fill)
            p.drawRoundedRect(QRectF(1.5, 1.5, size.width() - 3, size.height() - 3), 3, 3)
        p.end()
        return QIcon(pix)

    def _legend_symbol(self, p: QPainter, kind: str, c: QPointF) -> None:
        p.setPen(QPen(self.EXIT if kind == "exit" else self.SECRET_EXIT, 2.5))
        p.drawLine(QPointF(c.x() - 8, c.y()), QPointF(c.x() + 8, c.y()))

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


def _cluster(lines: list[QLineF], distance: float = 256.0) -> list[QRectF]:
    """Group nearby exit lines (e.g. the sides of one switch) and return their bounding boxes.

    Works on plain coordinates: a vertical or horizontal line has a zero-width QRectF, which
    Qt treats as empty (it never "intersects" anything).
    """
    boxes: list[list[float]] = []  # [min x, min y, max x, max y]
    for line in lines:
        x0, x1 = sorted((line.x1(), line.x2()))
        y0, y1 = sorted((line.y1(), line.y2()))
        for box in boxes:
            if x0 - distance <= box[2] and box[0] <= x1 + distance and \
                    y0 - distance <= box[3] and box[1] <= y1 + distance:
                box[:] = [min(box[0], x0), min(box[1], y0), max(box[2], x1), max(box[3], y1)]
                break
        else:
            boxes.append([x0, y0, x1, y1])
    return [QRectF(QPointF(b[0], b[1]), QPointF(b[2], b[3])) for b in boxes]


# ------------------------------------------------------------------ thing filter

class ThingFilter(QWidget):
    """Tree of thing categories and types with check boxes (lives inside the Things menu)."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        settings = normalize_layers(None)
        self.hidden_categories: set[str] = set(settings["hidden_categories"])
        self.hidden_types: set[int] = set(settings["hidden_types"])
        self._counts: Counter = Counter()
        self._names: dict[int, tuple[str, str]] = {}
        self._icons: dict[int, QIcon] = {}
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        buttons = QHBoxLayout()
        for text, slot in (("Show all", self.show_all), ("Hide all", self.hide_all), ("Defaults", self.reset)):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        layout.addLayout(buttons)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setStretchLastSection(False)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree)
        self.setMinimumSize(300, 380)
        self._rebuild()  # categories are listed even before a map is shown

    def set_map_things(self, things: list[Thing], counts: Counter, icon_for) -> None:
        """Show the categories, and the types present on the map with counts and icons."""
        self._counts = counts
        self._names = {}
        for t in things:
            self._names.setdefault(t.type, (t.name, t.category))
        self._icons = {}
        for type_ in counts:
            pix = icon_for(type_)
            if pix is not None:
                self._icons[type_] = QIcon(pix)
        self._rebuild()

    def _type_icon(self, type_: int, category: str) -> QIcon:
        if type_ in self._icons:
            return self._icons[type_]
        return MapPreview.swatch(KEY_COLORS.get(type_, CATEGORY_COLORS.get(category, "#9a9a9a")))

    def _rebuild(self) -> None:
        expanded = {self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                    for i in range(self.tree.topLevelItemCount()) if self.tree.topLevelItem(i).isExpanded()}
        self._updating = True
        self.tree.clear()
        by_cat: dict[str, list[int]] = {c: [] for c in CATEGORIES}
        for type_ in self._counts:
            cat = self._names.get(type_, ("", "Other"))[1]
            by_cat[cat if cat in by_cat else "Other"].append(type_)
        for cat in CATEGORIES:
            types = sorted(by_cat[cat], key=lambda t: self._names.get(t, (str(t),))[0])
            total = sum(self._counts[t] for t in types)
            item = QTreeWidgetItem([CATEGORY_LABELS[cat], str(total) if total else ""])
            item.setData(0, Qt.ItemDataRole.UserRole, cat)
            item.setIcon(0, MapPreview.swatch(CATEGORY_COLORS[cat]))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setForeground(1, QColor("#8a8a8a"))
            if not total:
                item.setForeground(0, QColor("#8a8a8a"))
            for type_ in types:
                child = QTreeWidgetItem([self._names.get(type_, (str(type_),))[0], str(self._counts[type_])])
                child.setData(0, Qt.ItemDataRole.UserRole, type_)
                child.setIcon(0, self._type_icon(type_, cat))
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setForeground(1, QColor("#8a8a8a"))
                child.setCheckState(0, Qt.CheckState.Unchecked if cat in self.hidden_categories
                                    or type_ in self.hidden_types else Qt.CheckState.Checked)
                item.addChild(child)
            item.setCheckState(0, self._category_state(cat, types))
            self.tree.addTopLevelItem(item)
            item.setExpanded(cat in expanded)
        self._updating = False

    def _category_state(self, cat: str, types: list[int]) -> Qt.CheckState:
        if cat in self.hidden_categories:
            return Qt.CheckState.Unchecked
        if any(t in self.hidden_types for t in types):
            return Qt.CheckState.PartiallyChecked
        return Qt.CheckState.Checked

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        value = item.data(0, Qt.ItemDataRole.UserRole)
        on = item.checkState(0) != Qt.CheckState.Unchecked
        if isinstance(value, str):  # a category: all its types on or off
            types = [item.child(i).data(0, Qt.ItemDataRole.UserRole) for i in range(item.childCount())]
            if on:
                self.hidden_categories.discard(value)
                self.hidden_types.difference_update(types)
            else:
                self.hidden_categories.add(value)
        else:  # a single type
            cat = item.parent().data(0, Qt.ItemDataRole.UserRole)
            if on:
                self.hidden_types.discard(value)
                if cat in self.hidden_categories:  # turn the category on with only this type
                    self.hidden_categories.discard(cat)
                    siblings = [item.parent().child(i).data(0, Qt.ItemDataRole.UserRole)
                                for i in range(item.parent().childCount())]
                    self.hidden_types.update(t for t in siblings if t != value)
            else:
                self.hidden_types.add(value)
        # Update check marks in place: rebuilding would delete the item whose signal we're in.
        self._refresh_states()
        self.changed.emit()

    def _refresh_states(self) -> None:
        self._updating = True
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            cat = item.data(0, Qt.ItemDataRole.UserRole)
            types = []
            for j in range(item.childCount()):
                child = item.child(j)
                type_ = child.data(0, Qt.ItemDataRole.UserRole)
                types.append(type_)
                child.setCheckState(0, Qt.CheckState.Unchecked if cat in self.hidden_categories
                                    or type_ in self.hidden_types else Qt.CheckState.Checked)
            item.setCheckState(0, self._category_state(cat, types))
        self._updating = False

    def set_state(self, hidden_categories, hidden_types) -> None:
        self.hidden_categories, self.hidden_types = set(hidden_categories), set(hidden_types)
        self._refresh_states()

    def show_all(self) -> None:
        self.set_state(set(), set())
        self.changed.emit()

    def hide_all(self) -> None:
        self.set_state(set(CATEGORIES), self.hidden_types)
        self.changed.emit()

    def reset(self) -> None:
        d = normalize_layers(None)
        self.set_state(d["hidden_categories"], d["hidden_types"])
        self.changed.emit()


class MapViewer(QWidget):
    """Map preview with a Things filter menu, layer toggles, Fit and an optional Pop out button."""

    layers_changed = Signal(dict)
    popout_requested = Signal()

    TOOLTIPS = {
        "secrets": "Secret sectors (purple)",
        "exits": "Highlight exit lines (orange) and secret exit lines (violet)",
    }
    LAYER_SWATCHES = {"secrets": (MapPreview.SECRET_EDGE, "patch"), "exits": (MapPreview.EXIT, "line")}

    def __init__(self, popout_button: bool = True, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        bar = QHBoxLayout()
        bar.setContentsMargins(2, 0, 2, 0)

        self.things_button = QToolButton()
        self.things_button.setText("Things ▾")
        self.things_button.setToolTip("Choose which kinds of things are shown\n"
                                      "(things present on Ultra-Violence in single player)")
        self.things_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.things_menu = QMenu(self.things_button)
        self.thing_filter = ThingFilter()
        action = QWidgetAction(self.things_menu)
        action.setDefaultWidget(self.thing_filter)
        self.things_menu.addAction(action)
        self.things_button.setMenu(self.things_menu)
        self.thing_filter.changed.connect(self._on_filter_changed)
        bar.addWidget(self.things_button)

        self.checks: dict[str, QCheckBox] = {}
        for name in LAYERS:
            box = QCheckBox(name.capitalize())
            box.setChecked(DEFAULT_LAYERS[name])
            box.setToolTip(self.TOOLTIPS[name])
            color, shape = self.LAYER_SWATCHES[name]
            box.setIcon(MapPreview.swatch(color, shape, QSize(22, 14)))
            box.setIconSize(QSize(22, 14))
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

    def set_map(self, m: MapData | None, message: str = "", sprites: SpriteSource | None = None) -> None:
        self.preview.set_map(m, message, sprites)
        things = [t for _, t in self.preview._markers]
        self.thing_filter.set_map_things(things, self.preview.thing_counts(), self.preview.icon)

    def layers(self) -> dict:
        return normalize_layers({
            **{n: box.isChecked() for n, box in self.checks.items()},
            "hidden_categories": sorted(self.thing_filter.hidden_categories),
            "hidden_types": sorted(self.thing_filter.hidden_types),
        })

    def set_layers(self, layers: dict) -> None:
        layers = normalize_layers(layers)
        for name, box in self.checks.items():
            box.blockSignals(True)
            box.setChecked(layers[name])
            box.blockSignals(False)
            self.preview.set_layer(name, layers[name])
        self.thing_filter.set_state(layers["hidden_categories"], layers["hidden_types"])
        self.preview.set_thing_filter(layers["hidden_categories"], layers["hidden_types"])

    def _on_toggle(self, name: str, on: bool) -> None:
        self.preview.set_layer(name, on)
        self.layers_changed.emit(self.layers())

    def _on_filter_changed(self) -> None:
        self.preview.set_thing_filter(self.thing_filter.hidden_categories, self.thing_filter.hidden_types)
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

    def show_map(self, m: MapData | None, title: str, message: str = "",
                 sprites: SpriteSource | None = None) -> None:
        self.setWindowTitle(f"{title} - Map Preview" if title else "Map Preview")
        self.viewer.set_map(m, message, sprites)

    def closeEvent(self, event):
        super().closeEvent(event)
        self.closed.emit()
