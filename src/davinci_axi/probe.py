"""Out-of-band probes that need no Resolve API: is the app running, is it Studio (KTD1, KTD2)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from davinci_axi import paths

STARTUP_SECONDS = 120.0


def is_fake() -> bool:
    """True when `DAVINCI_AXI_FAKE=1` selects the built-in fake world."""
    return os.environ.get("DAVINCI_AXI_FAKE") == "1"


def process_running() -> bool:
    """Is a DaVinci Resolve process running? Fake mode reads the FakeWorld connection flag."""
    if is_fake():
        from davinci_axi.fake import current_world

        return current_world().connection != "not_running"
    try:
        if sys.platform == "darwin":
            done = subprocess.run(
                ["pgrep", "-x", "Resolve"], capture_output=True, timeout=2, check=False
            )
            return done.returncode == 0
        if sys.platform == "win32":
            done = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Resolve.exe", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return "resolve.exe" in done.stdout.lower()
        return _linux_process_running()
    except (OSError, subprocess.SubprocessError):
        return False


def _parse_etime(text: str) -> float | None:
    """Parse `ps -o etime=` output: `05`, `01:05`, `02:03:04` or `1-02:03:04` (seconds)."""
    try:
        days_part, sep, rest = text.strip().partition("-")
        days = int(days_part) if sep else 0
        clock = rest if sep else days_part
        total = 0
        for piece in clock.split(":"):
            total = total * 60 + int(piece)
        return float(days * 86400 + total)
    except ValueError:
        return None


def process_age_seconds() -> float | None:
    """How long the Resolve process has run (macOS `ps etime`); None when unknown.

    Fake mode answers 5.0 for the "starting" world and 600.0 otherwise.
    """
    if is_fake():
        from davinci_axi.fake import current_world

        return 5.0 if current_world().connection == "starting" else 600.0
    if sys.platform != "darwin":
        return None
    try:
        found = subprocess.run(
            ["pgrep", "-x", "Resolve"], capture_output=True, text=True, timeout=2, check=False
        )
        pids = found.stdout.split()
        if found.returncode != 0 or not pids:
            return None
        shown = subprocess.run(
            ["ps", "-o", "etime=", "-p", pids[0]],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return _parse_etime(shown.stdout) if shown.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _linux_process_running() -> bool:
    for comm in Path("/proc").glob("[0-9]*/comm"):
        try:
            if comm.read_text().strip().lower() == "resolve":
                return True
        except OSError:
            continue
    return False


def studio_licence_present() -> bool:
    """Is a Studio licence file installed? Unknown platforms report True (edition unknown).

    A Free install has no `.davinciresolvestudio_*.lic` file (ASSUMED, verify on a Free machine).
    Fake mode reads the FakeWorld connection flag.
    """
    if is_fake():
        from davinci_axi.fake import current_world

        return current_world().connection != "free"
    folder = paths.license_dir()
    if folder is None:
        return True
    try:
        names = [entry.name.lower() for entry in folder.iterdir()]
    except OSError:
        return False
    return any(n.startswith(".davinciresolvestudio_") and n.endswith(".lic") for n in names)
