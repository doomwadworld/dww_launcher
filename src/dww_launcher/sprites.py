"""Thing sprites read from the user's own IWAD (and PWADs): decoding, lookup and face crops.

Nothing is bundled with the launcher; pictures come from whatever WADs are available, so the
icons match the game being played (Doom, Doom II, Freedoom, or a PWAD's replacements).
"""

from __future__ import annotations

import struct

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter

from dww_launcher.wad import Lump, WadError, WadFile

# Thing type -> (sprite name, frame) for Doom / Doom II.
THING_SPRITES: dict[int, tuple[str, str]] = {
    # monsters
    3004: ("POSS", "A"), 9: ("SPOS", "A"), 65: ("CPOS", "A"), 3001: ("TROO", "A"), 3002: ("SARG", "A"),
    58: ("SARG", "A"), 3006: ("SKUL", "A"), 3005: ("HEAD", "A"), 69: ("BOS2", "A"), 3003: ("BOSS", "A"),
    68: ("BSPI", "A"), 71: ("PAIN", "A"), 66: ("SKEL", "A"), 67: ("FATT", "A"), 64: ("VILE", "A"),
    7: ("SPID", "A"), 16: ("CYBR", "A"), 84: ("SSWV", "A"), 72: ("KEEN", "A"), 88: ("BBRN", "A"),
    888: ("DOGS", "A"),
    # players
    1: ("PLAY", "A"), 2: ("PLAY", "A"), 3: ("PLAY", "A"), 4: ("PLAY", "A"), 11: ("PLAY", "A"),
    # weapons
    2005: ("CSAW", "A"), 2001: ("SHOT", "A"), 82: ("SGN2", "A"), 2002: ("MGUN", "A"), 2003: ("LAUN", "A"),
    2004: ("PLAS", "A"), 2006: ("BFUG", "A"),
    # keys
    5: ("BKEY", "A"), 6: ("YKEY", "A"), 13: ("RKEY", "A"), 40: ("BSKU", "A"), 39: ("YSKU", "A"),
    38: ("RSKU", "A"),
    # ammo
    2007: ("CLIP", "A"), 2048: ("AMMO", "A"), 2008: ("SHEL", "A"), 2049: ("SBOX", "A"), 2010: ("ROCK", "A"),
    2046: ("BROK", "A"), 2047: ("CELL", "A"), 17: ("CELP", "A"), 8: ("BPAK", "A"),
    # health, armour, power-ups
    2011: ("STIM", "A"), 2012: ("MEDI", "A"), 2014: ("BON1", "A"), 2013: ("SOUL", "A"), 83: ("MEGA", "A"),
    2015: ("BON2", "A"), 2018: ("ARM1", "A"), 2019: ("ARM2", "A"), 2022: ("PINV", "A"), 2023: ("PSTR", "A"),
    2024: ("PINS", "A"), 2025: ("SUIT", "A"), 2026: ("PMAP", "A"), 2045: ("PVIS", "A"),
    # obstacles
    2035: ("BAR1", "A"), 70: ("FCAN", "A"), 2028: ("COLU", "A"), 85: ("TLMP", "A"), 86: ("TLP2", "A"),
    34: ("CAND", "A"), 35: ("CBRA", "A"), 44: ("TBLU", "A"), 45: ("TGRN", "A"), 46: ("TRED", "A"),
    55: ("SMBT", "A"), 56: ("SMGT", "A"), 57: ("SMRT", "A"), 48: ("ELEC", "A"), 30: ("COL1", "A"),
    32: ("COL3", "A"), 31: ("COL2", "A"), 33: ("COL4", "A"), 36: ("COL5", "A"), 37: ("COL6", "A"),
    47: ("SMIT", "A"), 43: ("TRE1", "A"), 54: ("TRE2", "A"), 41: ("CEYE", "A"), 42: ("FSKU", "A"),
    25: ("POL1", "A"), 26: ("POL6", "A"), 27: ("POL4", "A"), 28: ("POL2", "A"), 29: ("POL3", "A"),
    49: ("GOR1", "A"), 50: ("GOR2", "A"), 51: ("GOR3", "A"), 52: ("GOR4", "A"), 53: ("GOR5", "A"),
    73: ("HDB1", "A"), 74: ("HDB2", "A"), 75: ("HDB3", "A"), 76: ("HDB4", "A"), 77: ("HDB5", "A"),
    78: ("HDB6", "A"),
    # decorations
    59: ("GOR2", "A"), 60: ("GOR4", "A"), 61: ("GOR3", "A"), 62: ("GOR5", "A"), 63: ("GOR1", "A"),
    10: ("PLAY", "W"), 12: ("PLAY", "W"), 15: ("PLAY", "N"), 18: ("POSS", "L"), 19: ("SPOS", "L"),
    20: ("TROO", "M"), 21: ("SARG", "N"), 22: ("HEAD", "L"), 23: ("SKUL", "K"), 24: ("POL5", "A"),
    79: ("POB1", "A"), 80: ("POB2", "A"), 81: ("BRS1", "A"),
}

# Monsters / players shown as a face close-up: fraction of the sprite's height (from the top)
# taken as a square. 1.0 = the whole sprite (things that are essentially all face).
FACE_CROP: dict[int, float] = {
    3004: 0.34, 9: 0.34, 65: 0.34, 84: 0.32, 3001: 0.38, 3002: 0.6, 58: 0.6, 3006: 1.0, 3005: 1.0,
    69: 0.32, 3003: 0.32, 68: 0.6, 71: 1.0, 66: 0.26, 67: 0.55, 64: 0.25, 7: 0.75, 16: 0.3, 72: 0.4,
    88: 1.0, 888: 0.6, 1: 0.3, 2: 0.3, 3: 0.3, 4: 0.3, 11: 0.3,
}


def read_palette(data: bytes) -> list[tuple[int, int, int]]:
    if len(data) < 768:
        raise ValueError("palette lump too short")
    return [tuple(data[i * 3:i * 3 + 3]) for i in range(256)]


def decode_picture(data: bytes, palette: list[tuple[int, int, int]] | None) -> QImage | None:
    """Decode a Doom picture ("patch") lump, or a PNG lump. Returns None if it isn't one."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        img = QImage()
        return img if img.loadFromData(data) else None
    if palette is None or len(data) < 8:
        return None
    width, height, _left, _top = struct.unpack_from("<hhhh", data)
    if not (0 < width <= 4096 and 0 < height <= 4096) or len(data) < 8 + 4 * width:
        return None
    pixels = bytearray(width * height * 4)  # RGBA, transparent by default
    try:
        offsets = struct.unpack_from(f"<{width}I", data, 8)
        for x, pos in enumerate(offsets):
            if pos >= len(data):
                return None
            top = -1
            while True:
                delta = data[pos]
                if delta == 0xFF:
                    break
                top = top + delta if delta <= top else delta  # "tall patch" relative offsets
                length = data[pos + 1]
                run = data[pos + 3:pos + 3 + length]
                if len(run) < length:
                    return None
                for i, index in enumerate(run):
                    y = top + i
                    if 0 <= y < height:
                        r, g, b = palette[index]
                        o = (y * width + x) * 4
                        pixels[o:o + 4] = bytes((r, g, b, 255))
                pos += length + 4
    except (IndexError, struct.error):
        return None
    return QImage(bytes(pixels), width, height, width * 4, QImage.Format.Format_RGBA8888).copy()


def _flip_horizontally(img: QImage) -> QImage:
    if hasattr(img, "flipped"):  # Qt 6.9+
        return img.flipped(Qt.Orientation.Horizontal)
    return img.mirrored(True, False)


def face_crop(img: QImage, fraction: float) -> QImage:
    """A square from the top of the sprite, centred on its opaque pixels."""
    w, h = img.width(), img.height()
    side = max(1, min(int(round(h * fraction)) if fraction < 1 else max(w, h), max(w, h)))
    rows = min(side, h)
    # horizontal centre of the opaque pixels in the rows being kept
    total = weighted = 0
    for y in range(0, rows, max(1, rows // 24)):
        for x in range(w):
            if img.pixelColor(x, y).alpha():
                total += 1
                weighted += x
    cx = weighted / total if total else w / 2
    out = QImage(side, side, QImage.Format.Format_RGBA8888)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    src = QRect(int(round(cx - side / 2)), 0, side, rows)
    p.drawImage(QRect(0, 0, side, rows) if rows == side else QRect(0, (side - rows) // 2, side, rows), img, src)
    p.end()
    return out


class SpriteSource:
    """Looks up sprites in a list of WADs, highest priority first (e.g. PWAD, then IWADs)."""

    def __init__(self, wads: list[WadFile]):
        self.wads = wads
        self._index: dict[str, tuple[WadFile, Lump]] = {}
        self.palette = None
        for wad in wads:
            for name, lump in self._sprite_lumps(wad):
                self._index.setdefault(name, (wad, lump))
            if self.palette is None:
                i = wad.find("PLAYPAL")
                if i >= 0:
                    try:
                        self.palette = read_palette(wad.read(i))
                    except (ValueError, WadError):
                        pass
        self._images: dict[int, QImage | None] = {}
        self._by_sprite: dict[str, list[tuple[str, tuple[WadFile, Lump]]]] = {}
        for name, entry in self._index.items():
            self._by_sprite.setdefault(name[:4], []).append((name, entry))

    @classmethod
    def from_paths(cls, paths: list[str]) -> "SpriteSource":
        wads = []
        for p in paths:
            try:
                wads.append(WadFile(p))
            except WadError:
                continue
        return cls(wads)

    @staticmethod
    def _sprite_lumps(wad: WadFile):
        inside = False
        for lump in wad.lumps:
            if lump.name in ("S_START", "SS_START"):
                inside = True
            elif lump.name in ("S_END", "SS_END"):
                inside = False
            elif inside and lump.size > 0:
                yield lump.name, lump

    @property
    def available(self) -> bool:
        return bool(self._index) and self.palette is not None

    def frame(self, sprite: str, frame: str) -> QImage | None:
        """The front-facing picture of ``sprite`` ``frame`` (rotation 1, or 0 for no rotations).

        A lump name holds one or two frame/rotation pairs (``TROOA1``, ``TROOA2A8``,
        ``BSPIA1D1``); a match in the second pair means the picture is drawn mirrored.
        """
        entry, mirrored = None, False
        candidates = self._by_sprite.get(sprite, ())
        for rot in ("1", "0"):
            want = frame + rot
            entry = next((e for name, e in candidates if name[4:6] == want), None)
            if entry is None:
                entry = next((e for name, e in candidates if name[6:8] == want), None)
                mirrored = entry is not None
            if entry is not None:
                break
        if entry is None:
            return None
        wad, lump = entry
        try:
            img = decode_picture(wad.read(lump), self.palette)
        except WadError:
            return None
        if img is not None and mirrored:
            img = _flip_horizontally(img)
        return img

    def thing_image(self, thing_type: int) -> QImage | None:
        """Icon for a thing type: a face close-up for monsters and players, else the sprite."""
        if thing_type in self._images:
            return self._images[thing_type]
        img = None
        if thing_type in THING_SPRITES:
            img = self.frame(*THING_SPRITES[thing_type])
            if img is not None and thing_type in FACE_CROP:
                img = face_crop(img, FACE_CROP[thing_type])
        self._images[thing_type] = img
        return img


def has_face(thing_type: int) -> bool:
    return thing_type in FACE_CROP
