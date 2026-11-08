import struct
from pathlib import Path

import pytest


def make_wad(path: Path, kind: str, lump_names: list[str]) -> Path:
    """Write a minimal WAD with empty lumps."""
    header_size = 12
    directory = b"".join(
        struct.pack("<ii8s", header_size, 0, name.encode("ascii")) for name in lump_names
    )
    path.write_bytes(struct.pack("<4sii", kind.encode(), len(lump_names), header_size) + directory)
    return path


@pytest.fixture
def wad_factory(tmp_path):
    def factory(name: str, kind: str, lumps: list[str]) -> Path:
        return make_wad(tmp_path / name, kind, lumps)

    return factory


def build_wad(kind: str, lumps: list[tuple[str, bytes]]) -> bytes:
    """A WAD with real lump contents."""
    body, directory, offset = b"", b"", 12
    for name, data in lumps:
        directory += struct.pack("<ii8s", offset, len(data), name.encode())
        body += data
        offset += len(data)
    return struct.pack("<4sii", kind.encode(), len(lumps), offset) + body + directory


def doom_map_lumps(name: str = "MAP01") -> list[tuple[str, bytes]]:
    """A tiny Doom-format map: two sectors (one secret), an exit line, assorted things.

    Things: player 1 start, 2 imps on UV, 1 easy-only imp, 1 multiplayer-only imp,
    a blue keycard, a shotgun and a deathmatch start.
    """
    verts = [(0, 0), (256, 0), (256, 256), (0, 256), (128, 0), (128, 256)]
    # v1, v2, flags, special, tag, front, back
    lines = [(0, 4, 1, 0, 0, 0, 0xFFFF), (4, 1, 1, 0, 0, 1, 0xFFFF), (1, 2, 1, 11, 0, 2, 0xFFFF),
             (2, 5, 1, 0, 0, 3, 0xFFFF), (5, 3, 1, 0, 0, 4, 0xFFFF), (3, 0, 1, 0, 0, 5, 0xFFFF),
             (4, 5, 4, 0, 0, 6, 7)]
    things = [(32, 32, 0, 1, 7), (64, 64, 0, 3001, 7), (96, 64, 0, 3001, 4), (100, 64, 0, 3001, 1),
              (110, 64, 0, 3001, 7 | 16), (200, 200, 0, 5, 7), (150, 150, 0, 2001, 7), (10, 10, 0, 11, 7)]
    sectors = [(0, 128, b"FLOOR4_8", b"CEIL3_5", 160, 0, 0), (0, 128, b"FLOOR4_8", b"CEIL3_5", 160, 9, 0)]
    return [
        (name, b""),
        ("THINGS", b"".join(struct.pack("<hhhhh", *t) for t in things)),
        ("LINEDEFS", b"".join(struct.pack("<HHHHHHH", *ln) for ln in lines)),
        ("SIDEDEFS", b"\0" * 30 * 8),
        ("VERTEXES", b"".join(struct.pack("<hh", *v) for v in verts)),
        ("SEGS", b""), ("SSECTORS", b""), ("NODES", b""),
        ("SECTORS", b"".join(struct.pack("<hh8s8shhh", *s) for s in sectors)),
        ("REJECT", b""), ("BLOCKMAP", b""),
    ]


@pytest.fixture
def mapwad(tmp_path):
    """Write a PWAD containing the given maps (each built by doom_map_lumps) plus extra lumps."""

    def factory(filename: str, maps: list[str], extra: list[tuple[str, bytes]] = (), kind="PWAD"):
        lumps = list(extra)
        for m in maps:
            lumps += doom_map_lumps(m)
        path = tmp_path / filename
        path.write_bytes(build_wad(kind, lumps))
        return path

    return factory
