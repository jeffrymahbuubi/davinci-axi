"""`--version` text: the package version, plus the commit the install was built from.

Imports stay light (stdlib only) because `--version` is answered before any other import.
A `_build_info.py` written by `scripts/reinstall.sh` wins: it is the commit the install was
built from.
"""

from __future__ import annotations

import importlib
import json
import re
import subprocess
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from pathlib import Path

from davinci_axi import __version__

_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}(\.dirty)?$")
_CHECKOUT_NOTE = " (source checkout HEAD; reinstall after new commits)"


def _git_describe(path: Path) -> str | None:
    """`a475092` or `a475092.dirty` for the git checkout at `path`; None on any failure."""
    cmd = ["git", "-C", str(path), "describe", "--always", "--dirty", "--exclude=*", "--abbrev=7"]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    text = done.stdout.strip()
    if text.endswith("-dirty"):
        text = text[: -len("-dirty")] + ".dirty"
    return text or None


def _file_url_to_path(url: str) -> Path:
    from urllib.request import url2pathname  # lazy: only for file:// installs

    return Path(url2pathname(url[len("file://") :]))


def _commit_from_direct_url(dist: Distribution) -> tuple[str | None, bool]:
    """(commit, from_checkout). A vcs_info commit is exact; a file:// source is read live."""
    raw = dist.read_text("direct_url.json")
    if not raw:
        return None, False
    info = json.loads(raw)
    commit = (info.get("vcs_info") or {}).get("commit_id")
    if isinstance(commit, str) and commit:
        return commit[:7], False
    url = info.get("url")
    if isinstance(url, str) and url.startswith("file://"):
        return _git_describe(_file_url_to_path(url)), True
    return None, False


def _valid(commit: str | None) -> str | None:
    return commit if commit and _COMMIT_RE.match(commit) else None


def _baked() -> tuple[str | None, bool]:
    """(COMMIT, DIRTY) from `davinci_axi._build_info`, or (None, False) when it is absent."""
    try:
        info = importlib.import_module("davinci_axi._build_info")
    except ImportError:
        return None, False
    commit = getattr(info, "COMMIT", None)
    return (commit if isinstance(commit, str) else None), bool(getattr(info, "DIRTY", False))


def describe() -> str:
    """`0.1.0`, `0.1.0+a475092` or `0.1.0+a475092.dirty`; never raises.

    Order: the commit baked in at install time (`scripts/reinstall.sh`), then a `vcs_info`
    commit, then a `file://` source read live, then a source checkout. The last two carry a
    trailing note that it is the checkout's HEAD, not the installed code.
    """
    commit: str | None = None
    note = ""
    try:
        baked, dirty = _baked()
        baked = _valid(baked)
        if baked:
            return f"{__version__}+{baked}" + (".dirty" if dirty and ".dirty" not in baked else "")
        try:
            commit, from_checkout = _commit_from_direct_url(distribution("davinci-axi"))
            commit = _valid(commit)
            if commit and from_checkout:
                note = _CHECKOUT_NOTE
        except PackageNotFoundError:
            root = Path(__file__).resolve().parents[2]
            if (root / ".git").exists():
                commit = _valid(_git_describe(root))
                if commit:
                    note = _CHECKOUT_NOTE
    except Exception:  # --version must always answer
        commit, note = None, ""
    return f"{__version__}+{commit}{note}" if commit else __version__
