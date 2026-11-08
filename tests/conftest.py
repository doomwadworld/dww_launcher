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
