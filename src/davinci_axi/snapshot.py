"""Project snapshots: a `.drp` export plus a JSON state dump in one directory (KTD7).

A snapshot writes only to disk. It never calls SaveProject and never changes Resolve state.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from davinci_axi import paths
from davinci_axi.dump import dump_project_state
from davinci_axi.errors import AxiError
from davinci_axi.output import collapse_home
from davinci_axi.session import Session

# Live finding (Resolve 21.1.1): ExportProject returns False for a project never saved since
# it was created; one SaveProject fixes it.
NEVER_SAVED_HINT = (
    "If the project was never saved, it cannot be exported: run `davinci-axi project save --force`"
)


def resolve_out(text: str) -> Path:
    """`--out`/`--snapshot-out` text to an absolute Path (expanduser + abspath, no symlinks).

    Resolve writes the `.drp` relative to its own working directory, so a relative path must
    be made absolute against the CLI's before it reaches `ExportProject`.
    """
    return Path(os.path.abspath(os.path.expanduser(text)))


@dataclass(frozen=True)
class Snapshot:
    id: str
    dir: Path
    drp: Path | None
    state: Path
    project: str
    created: str
    reason: str
    manual: bool = False


def snapshot_root() -> Path:
    """`$DAVINCI_AXI_HOME/snapshots` (default `~/.davinci-axi/snapshots`)."""
    return paths.axi_home() / "snapshots"


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "project"


def _unique_dir(root: Path, snapshot_id: str) -> tuple[str, Path]:
    candidate, number = snapshot_id, 1
    while (root / candidate).exists():
        number += 1
        candidate = f"{snapshot_id}-{number}"
    return candidate, root / candidate


def _write_json(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )


def take_snapshot(
    session: Session,
    *,
    reason: str,
    out: Path | None = None,
    with_stills_luts: bool = True,
    manual: bool = False,
) -> Snapshot:
    """Write `meta.json`, `state.json` and `project.drp` for the current project.

    `manual` marks a snapshot the user asked for (`project snapshot`); `snapshot prune` keeps
    those unless `--all`. `out` overrides the snapshot directory. If the `.drp` export fails the
    directory and `state.json` are kept and SNAPSHOT_FAILED names them in `detail`.
    """
    project = session.require_project()
    name = project.GetName()
    now = datetime.now().astimezone()
    base_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{_slug(name)}"
    if out is not None:
        snapshot_id, directory = base_id, Path(os.path.abspath(out))
    else:
        snapshot_id, directory = _unique_dir(snapshot_root(), base_id)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        state_path = directory / "state.json"
        drp_path = directory / "project.drp"
        _write_json(state_path, dump_project_state(project))
        _write_json(
            directory / "meta.json",
            {
                "id": snapshot_id,
                "project": name,
                "created": now.isoformat(timespec="seconds"),
                "reason": reason,
                "resolve_version": ".".join(str(n) for n in session.version),
                "with_stills_luts": with_stills_luts,
                "manual": manual,
            },
        )
    except OSError as exc:
        raise AxiError(
            "SNAPSHOT_FAILED",
            f"could not write the snapshot directory: {exc}",
            help=["Check that the snapshot location is writable, or choose another with --out"],
            detail={"dir": str(directory)},
        ) from exc
    exported = session.project_manager().ExportProject(name, str(drp_path), with_stills_luts)
    if not exported:
        raise AxiError(
            "SNAPSHOT_FAILED",
            f"Resolve could not export project {name} to {drp_path.name}",
            help=[
                f"The state dump was kept at {collapse_home(state_path)}",
                f"Resolve was asked to write {drp_path}: the directory must exist and be "
                "writable by Resolve (davinci-axi passes an absolute path; "
                "check the disk and permissions)",
                "Check disk space and that the project is still open, then retry",
                NEVER_SAVED_HINT,
            ],
            detail={"state": str(state_path), "dir": str(directory), "drp": str(drp_path)},
        )
    return Snapshot(
        id=snapshot_id,
        dir=directory,
        drp=drp_path,
        state=state_path,
        project=name,
        created=now.isoformat(timespec="seconds"),
        reason=reason,
        manual=manual,
    )
