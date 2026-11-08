import os
import struct

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from conftest import build_wad  # noqa: E402
from dww_launcher.sprites import SpriteSource, decode_picture, face_crop, read_palette  # noqa: E402
from dww_launcher.wad import WadFile  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def app():
    # A full QApplication: the widget tests that run later in the same process need one.
    return QApplication.instance() or QApplication([])


PALETTE_BYTES = bytes(v for i in range(256) for v in (i, 255 - i, (i * 7) % 256))
PALETTE = read_palette(PALETTE_BYTES)


def patch(width: int, height: int, columns: list[list[tuple[int, list[int]]]]) -> bytes:
    """Doom picture: columns are lists of (topdelta, [palette indexes]) posts."""
    header = struct.pack("<hhhh", width, height, width // 2, height)
    body, offsets = b"", []
    base = 8 + 4 * width
    for posts in columns:
        offsets.append(base + len(body))
        for top, pixels in posts:
            body += bytes([top, len(pixels), 0]) + bytes(pixels) + b"\0"
        body += b"\xff"
    return header + struct.pack(f"<{width}I", *offsets) + body


def rgba(img: QImage, x: int, y: int):
    c = img.pixelColor(x, y)
    return c.red(), c.green(), c.blue(), c.alpha()


def test_decode_patch():
    data = patch(2, 3, [[(0, [10, 20])], [(1, [30, 40])]])
    img = decode_picture(data, PALETTE)
    assert (img.width(), img.height()) == (2, 3)
    assert rgba(img, 0, 0) == (10, 245, 70, 255)
    assert rgba(img, 0, 1) == (20, 235, 140, 255)
    assert rgba(img, 0, 2)[3] == 0  # transparent where no post covers
    assert rgba(img, 1, 0)[3] == 0 and rgba(img, 1, 2)[:3] == (40, 215, 24)


def test_tall_patch_relative_offsets():
    # Second post's topdelta (1) is <= the first's (2), so it is relative: row 3.
    img = decode_picture(patch(1, 5, [[(2, [1]), (1, [2])]]), PALETTE)
    assert rgba(img, 0, 2)[0] == 1 and rgba(img, 0, 3)[0] == 2


@pytest.mark.parametrize("data", [b"", b"\x00" * 6, struct.pack("<hhhh", 0, 5, 0, 0),
                                  struct.pack("<hhhh", 2, 2, 0, 0) + struct.pack("<2I", 999, 999)])
def test_bad_pictures(data):
    assert decode_picture(data, PALETTE) is None


def test_png_lump():
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    src = QImage(3, 2, QImage.Format.Format_RGBA8888)
    src.fill(QColor(1, 2, 3))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    src.save(buf, "PNG")
    img = decode_picture(bytes(ba), None)
    assert (img.width(), img.height()) == (3, 2)


def test_face_crop_is_square_from_the_top():
    img = QImage(20, 40, QImage.Format.Format_RGBA8888)
    img.fill(QColor(0, 0, 0, 0))
    for x in range(10, 18):  # opaque "head" right of centre, near the top
        for y in range(0, 8):
            img.setPixelColor(x, y, QColor(200, 0, 0))
    face = face_crop(img, 0.25)
    assert (face.width(), face.height()) == (10, 10)
    assert face.pixelColor(5, 2).alpha() == 255  # centred on the opaque pixels


def sprite_wad(lumps, palette=True):
    pic = patch(4, 8, [[(0, [5] * 8)]] * 4)
    entries = ([("PLAYPAL", PALETTE_BYTES)] if palette else []) + [("S_START", b"")]
    entries += [(name, pic) for name in lumps] + [("S_END", b"")]
    return WadFile(build_wad("IWAD", entries))


def test_sprite_lookup_and_thing_images():
    src = SpriteSource([sprite_wad(["TROOA1", "BSPIA1D1", "SKULA8A1", "SHOTA0", "TROOB1"])])
    assert src.available
    assert src.frame("TROO", "A") is not None
    assert src.frame("BSPI", "A") is not None  # two frames sharing one lump
    assert src.frame("BSPI", "D") is not None
    assert src.frame("SKUL", "A") is not None  # only as the mirrored half of a pair
    assert src.frame("CYBR", "A") is None
    imp = src.thing_image(3001)
    assert imp.width() == imp.height()  # monsters become a square face close-up
    shotgun = src.thing_image(2001)
    assert (shotgun.width(), shotgun.height()) == (4, 8)  # items keep their shape
    assert src.thing_image(16) is None and src.thing_image(99999) is None


def test_sprite_priority_and_palette():
    pwad_pic = patch(2, 2, [[(0, [9, 9])]] * 2)
    pwad = WadFile(build_wad("PWAD", [("SS_START", b""), ("SHOTA0", pwad_pic), ("SS_END", b"")]))
    src = SpriteSource([pwad, sprite_wad(["SHOTA0"])])
    assert src.thing_image(2001).width() == 2  # the PWAD's replacement wins
    assert not SpriteSource([sprite_wad(["SHOTA0"], palette=False)]).available
    assert not SpriteSource([]).available


def test_mirrored_frame_is_flipped():
    left_only = patch(2, 1, [[(0, [50])], []])  # opaque pixel in the left column only
    wad = WadFile(build_wad("IWAD", [("PLAYPAL", PALETTE_BYTES), ("S_START", b""),
                                     ("SKULA8A1", left_only), ("TROOA1", left_only), ("S_END", b"")]))
    src = SpriteSource([wad])
    normal, mirrored = src.frame("TROO", "A"), src.frame("SKUL", "A")
    assert rgba(normal, 0, 0)[3] == 255 and rgba(normal, 1, 0)[3] == 0
    assert rgba(mirrored, 0, 0)[3] == 0 and rgba(mirrored, 1, 0)[3] == 255
