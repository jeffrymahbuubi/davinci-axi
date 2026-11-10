"""Remembered last error of each AI verb (F11): `~/.davinci-axi/ai-errors.json`, keyed by command.

`ai status` shows it, because a failed AI call leaves nothing in Resolve to read back. Every I/O
or parse problem is swallowed: an unwritable state file must never mask the real error.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from davinci_axi.errors import AxiError
from davinci_axi.paths import axi_home

STATE_FILE = "ai-errors.json"


@dataclass(frozen=True)
class LastError:
    command: str
    code: str
    message: str
    at: str
    project: str | None


def _path() -> Path:
    return axi_home() / STATE_FILE


def _read() -> dict[str, dict[str, object]]:
    """The raw JSON object, or {} when the file is absent, unreadable or not an object."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}


def _write(entries: dict[str, dict[str, object]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{STATE_FILE}.{os.getpid()}.tmp")
    try:
        temp.write_text(json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def record(command: str, err: AxiError, project: str | None) -> None:
    """Remember `err` as the last error of `command`, replacing the earlier one."""
    try:
        entries = _read()
        entries[command] = {
            "code": err.code,
            "message": err.message,
            "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "project": project,
        }
        _write(entries)
    except (OSError, ValueError):
        pass


def forget(command: str) -> None:
    """Drop the remembered error of `command` (a later success clears it)."""
    try:
        entries = _read()
        if command in entries:
            del entries[command]
            _write(entries)
    except (OSError, ValueError):
        pass


def load() -> dict[str, LastError]:
    """Every remembered error by command; {} when the file is absent or unreadable."""
    result: dict[str, LastError] = {}
    for command, raw in _read().items():
        project = raw.get("project")
        result[command] = LastError(
            command=command,
            code=str(raw.get("code") or ""),
            message=str(raw.get("message") or ""),
            at=str(raw.get("at") or ""),
            project=str(project) if project is not None else None,
        )
    return result


def clear() -> int:
    """Delete the state file; returns how many entries it held."""
    count = len(_read())
    with contextlib.suppress(OSError):
        _path().unlink(missing_ok=True)
    return count


def label(err: LastError) -> str:
    return f"{err.code} at {err.at}"
