"""Filesystem locations: the davinci-axi state directory and the platform Resolve defaults."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_MAC_BASE = "/Library/Application Support/Blackmagic Design/DaVinci Resolve"


def axi_home() -> Path:
    """State directory: `$DAVINCI_AXI_HOME`, else `~/.davinci-axi`."""
    override = os.environ.get("DAVINCI_AXI_HOME")
    return Path(override).expanduser() if override else Path.home() / ".davinci-axi"


def _program_data() -> Path:
    return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))


def default_script_api() -> Path | None:
    """Platform default `Developer/Scripting` folder (`$RESOLVE_SCRIPT_API` is read by callers)."""
    if sys.platform == "darwin":
        return Path(_MAC_BASE) / "Developer" / "Scripting"
    if sys.platform == "win32":
        return (
            _program_data()
            / "Blackmagic Design"
            / "DaVinci Resolve"
            / "Support"
            / "Developer"
            / "Scripting"
        )
    if sys.platform.startswith("linux"):
        return Path("/opt/resolve/Developer/Scripting")
    return None


def license_dir() -> Path | None:
    """Folder that holds the Studio licence file, or None on an unknown platform (assumed)."""
    override = os.environ.get("DAVINCI_AXI_LICENSE_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path(_MAC_BASE) / ".license"
    if sys.platform == "win32":
        return _program_data() / "Blackmagic Design" / "DaVinci Resolve" / "Support" / ".license"
    if sys.platform.startswith("linux"):
        return Path("/opt/resolve/.license")
    return None
