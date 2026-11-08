"""Regenerate the app icons from resources/icon_source.png.

The source is small pixel art, so it is padded to a square and scaled with
nearest-neighbour filtering to keep the pixels crisp.

Usage: python scripts/make_icons.py   (requires Pillow: pip install pillow)
"""

from pathlib import Path

from PIL import Image

RES = Path(__file__).resolve().parent.parent / "src" / "dww_launcher" / "resources"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def square(img: Image.Image) -> Image.Image:
    side = max(img.size)
    side += side % 2  # even, so the sprite centres cleanly
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return canvas


def main() -> None:
    src = square(Image.open(RES / "icon_source.png").convert("RGBA"))
    images = {}
    for size in SIZES:
        if size >= src.width:  # scale up by the largest whole factor, then pad to size
            factor = size // src.width
            scaled = src.resize((src.width * factor, src.height * factor), Image.NEAREST)
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            img.paste(scaled, ((size - scaled.width) // 2, (size - scaled.height) // 2))
        else:
            img = src.resize((size, size), Image.NEAREST)
        images[size] = img
        img.save(RES / f"icon_{size}.png")
    images[256].save(RES / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    images[512].save(RES / "icon.icns")
    print("Wrote icons to", RES)


if __name__ == "__main__":
    main()
