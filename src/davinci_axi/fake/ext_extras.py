"""Test helper: write an AI Extras package log in the real `dpl1` layout (not an extension)."""

from __future__ import annotations

import re
import struct
from pathlib import Path


def _string(text: str) -> bytes:
    raw = text.encode("ascii") + b"\0"
    return struct.pack("<I", len(text)) + raw + b"\0" * (-len(raw) % 4)


def write_package(root: Path, name: str, package_id: str | None = None) -> Path:
    """Create `<root>/<id>/log.dpl1` with the name as its first string; return the package dir."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    directory = root / (package_id or f"fake-{slug}")
    directory.mkdir(parents=True, exist_ok=True)
    body = b"4\0\0\0dpl1" + b"\0" * 0x84 + _string(name) + _string("en_US") + _string(name)
    (directory / "log.dpl1").write_bytes(body)
    return directory
