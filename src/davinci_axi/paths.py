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


def extras_dir() -> Path | None:
    """Folder of the AI Extras packages Resolve's Extras Download Manager installs.

    `$DAVINCI_AXI_EXTRAS_DIR` wins; under `DAVINCI_AXI_FAKE=1` it is `axi_home()/extras` (absent
    unless a test seeds it). The macOS folder was read on a real install; the Windows folder is
    ASSUMED from a Blackmagic forum post and the Linux one by analogy with the other Resolve paths.
    """
    override = os.environ.get("DAVINCI_AXI_EXTRAS_DIR")
    if override:
        return Path(override).expanduser()
    if os.environ.get("DAVINCI_AXI_FAKE") == "1":
        return axi_home() / "extras"
    if sys.platform == "darwin":
        return Path(_MAC_BASE) / "Extras"
    if sys.platform == "win32":
        return _program_data() / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Extras"
    if sys.platform.startswith("linux"):
        return Path("/opt/resolve/Extras")
    return None


def lut_dirs() -> list[Path]:
    """Folders Resolve discovers LUTs in; the first one is where `color lut install` copies to.

    `$DAVINCI_AXI_LUT_DIRS` (`os.pathsep`-separated) comes first: it models the custom LUT paths
    of Preferences > System > General, which live in a binary file and cannot be read. Under
    `DAVINCI_AXI_FAKE=1` the master folder is `axi_home()/LUT`. The macOS master folder was read on
    a real install; the Windows and Linux folders are ASSUMED by analogy with `extras_dir`.
    """
    dirs = [
        Path(part).expanduser()
        for part in os.environ.get("DAVINCI_AXI_LUT_DIRS", "").split(os.pathsep)
        if part
    ]
    if os.environ.get("DAVINCI_AXI_FAKE") == "1":
        dirs.append(axi_home() / "LUT")
    elif sys.platform == "darwin":
        dirs.append(Path(_MAC_BASE) / "LUT")
    elif sys.platform == "win32":
        dirs.append(_program_data() / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "LUT")
    elif sys.platform.startswith("linux"):
        dirs.append(Path("/opt/resolve/LUT"))
    return dirs


def lut_install_dir() -> Path:
    """Where `color lut install` copies a LUT: `lut_dirs()[0]`."""
    dirs = lut_dirs()
    if not dirs:
        raise RuntimeError("no LUT folder is known on this platform; set DAVINCI_AXI_LUT_DIRS")
    return dirs[0]
