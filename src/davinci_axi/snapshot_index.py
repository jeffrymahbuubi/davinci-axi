"""Read the snapshot directories written by `take_snapshot`: `meta.json`, `state.json`, `.drp`.

Pure disk reads, never a Resolve call. A directory without `meta.json` is not a snapshot and is
ignored; an unreadable `meta.json` is skipped with a warning so one bad directory cannot hide
the rest. Snapshots are addressed by directory name, never by prefix (KTD20).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from davinci_axi.errors import AxiError
from davinci_axi.output import collapse_home

META = "meta.json"
STATE = "state.json"
DRP = "project.drp"
NEWEST_SHOWN = 5


@dataclass(frozen=True)
class SnapshotMeta:
    id: str
    dir: Path
    project: str
    created: str
    reason: str
    resolve_version: str
    drp: Path | None
    state: Path | None
    manual: bool | None = None  # None: written before the key existed (legacy)


def _text(meta: dict[str, object], key: str) -> str:
    value = meta.get(key)
    return value if isinstance(value, str) else ""


def _read(directory: Path) -> SnapshotMeta:
    """Parse one snapshot directory. Raises OSError or ValueError when `meta.json` is unusable."""
    raw = json.loads((directory / META).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("meta.json is not a JSON object")
    drp, state = directory / DRP, directory / STATE
    return SnapshotMeta(
        id=directory.name,
        dir=directory,
        project=_text(raw, "project"),
        created=_text(raw, "created"),
        reason=_text(raw, "reason"),
        resolve_version=_text(raw, "resolve_version"),
        drp=drp if drp.is_file() else None,
        state=state if state.is_file() else None,
        manual=raw["manual"] if isinstance(raw.get("manual"), bool) else None,
    )


def list_snapshots(root: Path, warnings: list[str] | None = None) -> list[SnapshotMeta]:
    """Every snapshot under `root`, newest first. Unreadable ones add a line to `warnings`."""
    if not root.is_dir():
        return []
    found: list[SnapshotMeta] = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or not (directory / META).is_file():
            continue
        try:
            found.append(_read(directory))
        except (OSError, ValueError) as exc:
            if warnings is not None:
                warnings.append(f"skipped {directory.name}: {META} is unreadable ({exc})")
    found.sort(key=lambda meta: (meta.created, meta.id), reverse=True)
    return found


def find_snapshot(root: Path, snapshot_id: str) -> SnapshotMeta:
    """The snapshot whose directory name is exactly `snapshot_id`, else NOT_FOUND."""
    snapshots = list_snapshots(root)
    for meta in snapshots:
        if meta.id == snapshot_id:
            return meta
    if snapshots:
        newest = ", ".join(meta.id for meta in snapshots[:NEWEST_SHOWN])
        hint_line = f"Newest snapshots: {newest}"
    else:
        hint_line = f"No snapshots under {collapse_home(root)}; run `davinci-axi project snapshot`"
    raise AxiError(
        "NOT_FOUND",
        f"snapshot {snapshot_id} not found",
        help=[hint_line, "Run `davinci-axi snapshot list` to see every snapshot"],
    )


def counts(meta: SnapshotMeta) -> tuple[int, int]:
    """(timelines, items) read from `state.json`; (0, 0) when it is missing or unreadable."""
    if meta.state is None:
        return 0, 0
    try:
        state = json.loads(meta.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, 0
    if not isinstance(state, dict):
        return 0, 0
    timelines = state.get("timelines") or []
    items = sum(len(track.get("items") or []) for tl in timelines for track in tl.get("tracks", []))
    return len(timelines), items
