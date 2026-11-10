"""Which AI Extras packages are installed, read from Resolve's Extras directory (F11).

Resolve's Extras Download Manager keeps one sub directory per package under the Extras folder
(`paths.extras_dir`). Each holds a `log.dpl1` whose first string is the package name. The format is
undocumented and was inferred from one sample, so the answer is tri-state: a directory that cannot
be read, or a package directory that cannot be named, makes an absent package `unknown`, never
`missing` (a parse failure must not refuse a call that might work).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from davinci_axi import paths

# The eight DDM package names of Resolve 21.1.1, read from the Resolve binary.
PACKAGES: tuple[str, ...] = (
    "AI IntelliSearch - Faster",
    "AI IntelliSearch - Better",
    "AI IntelliSearch - Data Files",
    "AI Slate ID",
    "AI Speech Generator",
    "AI Motion Deblur",
    "AI Voice Training",
    "Extended AI Transcription Language Support 2",
)
INSTALL_MENU = "the DaVinci Resolve Studio application menu > Extras Download Manager"
LOG_NAME = "log.dpl1"
_MAGIC = b"dpl1"
_FIRST_STRING = 8
_MAX_NAME = 200

Status = Literal["installed", "missing", "unknown"]


@dataclass(frozen=True)
class Package:
    id: str
    name: str | None
    path: Path


@dataclass(frozen=True)
class Report:
    dir: Path | None
    readable: bool
    packages: tuple[Package, ...]

    def names(self) -> list[str]:
        return sorted(p.name for p in self.packages if p.name is not None)

    def unnamed(self) -> int:
        return sum(1 for p in self.packages if p.name is None)

    def status(self, package: str) -> Status:
        if not self.readable:
            return "unknown"
        if package in self.names():
            return "installed"
        return "unknown" if self.unnamed() else "missing"

    def summary(self) -> str:
        if not self.readable:
            where = self.dir if self.dir is not None else "the Extras directory"
            return f"unknown ({where} not found)"
        names = self.names()
        if not names:
            return "none installed" if not self.unnamed() else f"unknown ({self.unnamed()} unnamed)"
        text = f"{len(names)} installed ({', '.join(names)})"
        return f"{text}, {self.unnamed()} unnamed" if self.unnamed() else text


@dataclass(frozen=True)
class Check:
    package: str
    status: Status
    report: Report


def package_name(log: Path) -> str | None:
    """The package name from a `log.dpl1`, or None when the file is not in the known layout."""
    try:
        data = log.read_bytes()
    except (OSError, ValueError):
        return None
    if data[4:8] != _MAGIC:
        return None
    for offset in range(_FIRST_STRING, len(data) - 4, 4):
        (length,) = struct.unpack_from("<I", data, offset)
        if not 1 <= length <= _MAX_NAME:
            continue
        end = offset + 4 + length
        raw = data[offset + 4 : end]
        if len(raw) != length or not all(32 <= b < 127 for b in raw):
            continue
        if end < len(data) and data[end] != 0:
            continue
        return raw.decode("ascii")
    return None


def scan(directory: Path | None = None) -> Report:
    """Read the Extras directory; never raises."""
    root = directory if directory is not None else paths.extras_dir()
    if root is None:
        return Report(None, False, ())
    try:
        if not root.is_dir():
            return Report(root, False, ())
        children = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return Report(root, False, ())
    return Report(
        root, True, tuple(Package(p.name, package_name(p / LOG_NAME), p) for p in children)
    )


def check(package: str) -> Check:
    report = scan()
    return Check(package, report.status(package), report)


def install_hint(package: str) -> str:
    return f"Open {INSTALL_MENU} and install '{package}', then retry"
