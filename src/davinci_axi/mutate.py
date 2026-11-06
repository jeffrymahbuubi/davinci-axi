"""The one mutation flow every phase-2 command uses (KTD12).

A command builds a `MutationPlan` and hands `apply` and `verify` closures to `run_mutation`:
no `--force` is a dry run (no write call, no snapshot); `--force` saves the project, takes a
snapshot, applies the change and reads it back (R27-R29). Irreversible changes say so (R32).
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from davinci_axi import registry
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.output import collapse_home, hint
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.resolve_types_edit import ProjectEdit, ProjectManagerEdit, ResolveEdit
from davinci_axi.session import Session
from davinci_axi.snapshot import take_snapshot
from davinci_axi.spec import Flag, Invocation, Result

ALLOW_ENV = "DAVINCI_AXI_ALLOW_UNSNAPSHOTTED"
EPSILON = 1e-6

MUTATION_FLAGS: tuple[Flag, ...] = (
    Flag("force", "bool", False, help="apply the change (without it the command is a dry run)"),
    Flag(
        "no-snapshot",
        "bool",
        False,
        help=f"skip the safety snapshot (refused unless {ALLOW_ENV}=1 is set)",
    ),
    Flag("snapshot-out", "str", None, help="write the safety snapshot into this directory"),
)

SnapshotPolicy = Literal["required", "if_open", "none"]


@dataclass(frozen=True)
class Change:
    """One field of a planned change. Values are primitives; anything else is shown with str()."""

    field: str
    before: object
    after: object


@dataclass
class MutationPlan:
    command: str
    target: str
    changes: list[Change]
    force_cmd: str
    irreversible: bool = False
    warning: str = ""
    snapshot: SnapshotPolicy = "required"
    extra: dict[str, object] = field(default_factory=dict)
    next: list[str] = field(default_factory=list)


def force_cmd(inv: Invocation, *words: str) -> str:
    """`davinci-axi <quoted words> --force`, the line a dry run tells the agent to run."""
    return " ".join([inv.bin_name, *(shlex.quote(w) for w in words), "--force"])


def check_snapshot_flags(inv: Invocation) -> None:
    """Refuse `--no-snapshot` without the env opt-in. Call before `inv.connect()`."""
    if inv.args.get("no_snapshot") and os.environ.get(ALLOW_ENV) != "1":
        raise AxiError(
            "WRITE_REFUSED",
            "--no-snapshot is refused: a change without a snapshot cannot be undone",
            help=[
                f"Set {ALLOW_ENV}=1 in the environment to allow writes without a snapshot",
                "Or drop --no-snapshot so the project is snapshotted first",
            ],
        )
    out = inv.args.get("snapshot_out")
    if out is not None and (not isinstance(out, str) or out == ""):
        raise AxiError(
            "VALIDATION_ERROR",
            "flag --snapshot-out needs a directory path",
            help=["Valid example: --snapshot-out ~/snapshots/before-edit"],
        )
    if out is not None and inv.args.get("no_snapshot"):
        raise AxiError(
            "VALIDATION_ERROR",
            "--snapshot-out and --no-snapshot contradict each other",
            help=["Pass one of them, not both"],
        )


def _bad_assignments(message: str) -> AxiError:
    return AxiError(
        "VALIDATION_ERROR",
        message,
        help=["Valid example: Opacity=50,ZoomX=1.2 (comma-separated key=value pairs)"],
    )


def parse_assignments(text: str) -> dict[str, str]:
    """`a=1,b=x y` -> `{"a": "1", "b": "x y"}`. Malformed input is VALIDATION_ERROR."""
    if text.strip() == "":
        raise _bad_assignments("assignments are empty")
    pairs: dict[str, str] = {}
    for part in text.split(","):
        key, eq, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if not eq or not key:
            raise _bad_assignments(f"malformed assignment {part.strip() or '(empty)'}")
        if key in pairs:
            raise _bad_assignments(f"{key} is assigned twice")
        pairs[key] = value
    return pairs


def _bad_value(value: str, kind: str) -> AxiError:
    return AxiError(
        "VALIDATION_ERROR",
        f"cannot read {value} as {kind}",
        help=[f"Pass {kind}, like the value Resolve reports today"],
    )


def coerce_like(value: str, like: object) -> object:
    """Convert `value` to the type of the live value `like` (bool, int, float or str)."""
    if isinstance(like, bool):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise _bad_value(value, "true or false")
    try:
        if isinstance(like, int):
            return int(value)
        if isinstance(like, float):
            return float(value)
    except ValueError:
        kind = "a whole number" if isinstance(like, int) else "a number"
        raise _bad_value(value, kind) from None
    return value


def refuse_existing(path: Path, overwrite: bool) -> None:
    """FILE_EXISTS (exit 1) when `path` exists and `--overwrite` was not given."""
    if path.exists() and not overwrite:
        raise AxiError(
            "FILE_EXISTS",
            f"{collapse_home(path)} already exists",
            help=["Pass --overwrite to replace it, or choose another path"],
        )


def _primitive(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _changes_rows(changes: list[Change]) -> list[dict[str, object]]:
    return [
        {"field": c.field, "before": _primitive(c.before), "after": _primitive(c.after)}
        for c in changes
    ]


def _same(expected: object, actual: object) -> bool:
    if expected == actual:
        return True
    numeric = (int, float)
    if isinstance(expected, numeric) and isinstance(actual, numeric):
        return abs(float(expected) - float(actual)) <= EPSILON
    return str(expected) == str(actual)


def _header(plan: MutationPlan, applied: bool) -> dict[str, object]:
    data: dict[str, object] = {"command": plan.command, "target": plan.target, "applied": applied}
    if plan.irreversible:
        data["irreversible"] = True
    if plan.warning:
        data["warning"] = plan.warning
    return data


@dataclass
class _Taken:
    """What the safety step produced: a label for the output and, if one was written, a dir."""

    label: str
    directory: Path | None = None
    custom_out: bool = False


def _restore_hint(taken: _Taken) -> str | None:
    if taken.directory is None or taken.custom_out:
        return None
    if "snapshot" not in registry.COMMANDS:
        return None
    return hint(f"snapshot restore {taken.label}", "restore this snapshot as a new project")


def _safety_step(inv: Invocation, session: Session | None, plan: MutationPlan) -> _Taken:
    """Save then snapshot, per the plan's policy. Raises SNAPSHOT_FAILED before any write."""
    if plan.snapshot == "none":
        return _Taken("none")
    if inv.args.get("no_snapshot"):
        return _Taken("skipped")
    if session is None:
        raise ValueError("run_mutation needs a session unless the snapshot policy is none")
    if plan.snapshot == "if_open" and session.current_project() is None:
        return _Taken("none")
    project = session.require_project()
    if _unsaved_default(session, project.GetName()):
        if plan.snapshot == "if_open":
            return _Taken("none")
        raise AxiError(
            "SNAPSHOT_FAILED",
            f"the current project {project.GetName()} is Resolve's unsaved default project, "
            "so there is nothing to snapshot",
            help=[
                "No change was made",
                "Create or open a named project first (`davinci-axi project create <name>`)",
            ],
        )
    if not cast(ProjectManagerEdit, session.project_manager()).SaveProject():
        raise AxiError(
            "SNAPSHOT_FAILED",
            f"Resolve could not save project {project.GetName()} before the snapshot",
            help=[
                "No change was made",
                "Check that Resolve is idle (no modal dialog), then retry",
            ],
        )
    out_text = inv.args.get("snapshot_out")
    out = Path(out_text).expanduser() if isinstance(out_text, str) and out_text else None
    snapshot = take_snapshot(session, reason=plan.command, out=out)
    return _Taken(snapshot.id, snapshot.dir, custom_out=out is not None)


def _unsaved_default(session: Session, name: str) -> bool:
    """Resolve's startup project: named `Untitled Project*` and absent from the project list.

    Saving it can raise a modal save dialog, which hangs the scripting call, so the safety step
    never calls SaveProject on it.
    """
    if not name.startswith("Untitled Project"):
        return False
    return name not in session.project_manager().GetProjectListInCurrentFolder()


def _snapshot_data(taken: _Taken, data: dict[str, object]) -> None:
    data["snapshot"] = taken.label
    if taken.custom_out and taken.directory is not None:
        data["snapshot_dir"] = collapse_home(taken.directory)


def _verify_failed(plan: MutationPlan, live: dict[str, object], taken: _Taken) -> AxiError | None:
    missing = object()
    bad: list[tuple[str, object, object]] = []
    for change in plan.changes:
        actual = live.get(change.field, missing)
        if actual is missing:
            bad.append((change.field, change.after, "(not read back)"))
        elif not _same(change.after, actual):
            bad.append((change.field, change.after, actual))
    if not bad:
        return None
    field_name, expected, actual = bad[0]
    message = "; ".join(f"{f} is {a!r}, expected {e!r}" for f, e, a in bad)
    help_lines = ["Inspect the live state, then retry or undo the change"]
    restore = _restore_hint(taken)
    if restore:
        help_lines.append(restore)
    elif taken.directory is not None:
        help_lines.append(f"The snapshot is at {collapse_home(taken.directory)}")
    detail: dict[str, object] = {
        "field": field_name,
        "expected": _primitive(expected),
        "actual": _primitive(actual),
    }
    if taken.directory is not None:
        detail["snapshot"] = taken.label
    return AxiError(
        "VERIFY_FAILED", f"read-back differs after the write: {message}", help_lines, detail
    )


def run_mutation(
    inv: Invocation,
    session: Session | None,
    plan: MutationPlan,
    apply: Callable[[], None],
    verify: Callable[[], dict[str, object]],
) -> Result:
    """Dry run without `--force`; otherwise save, snapshot, `apply()`, then compare `verify()`.

    `verify()` returns `{Change.field: live value}`; any field whose live value differs from
    the plan's `after` raises VERIFY_FAILED with the snapshot id in `detail`. A plan with no
    changes is a no-op: `applied: false`, nothing written, nothing snapshotted.
    """
    check_snapshot_flags(inv)
    if not plan.changes:
        data = _header(plan, False)
        data["changes"] = []
        data.update(plan.extra)
        return Result(data, list(plan.next))
    if not inv.args.get("force"):
        data = _header(plan, False)
        data["changes"] = _changes_rows(plan.changes)
        data.update(plan.extra)
        return Result(data, [f"Run `{plan.force_cmd}` to apply this change"])
    taken = _safety_step(inv, session, plan)
    try:
        apply()
    except AxiError as err:
        restore = _restore_hint(taken)
        if taken.directory is not None:
            err.detail.setdefault("snapshot", taken.label)
        if restore:
            err.help.append(restore)
        raise
    failure = _verify_failed(plan, verify(), taken)
    if failure is not None:
        raise failure
    data = _header(plan, True)
    data["changes"] = _changes_rows(plan.changes)
    data.update(plan.extra)
    _snapshot_data(taken, data)
    help_lines = list(plan.next)
    restore = _restore_hint(taken)
    if restore:
        help_lines.append(restore)
    return Result(data, help_lines)


@contextmanager
def with_page(session: Session, page: str) -> Iterator[bool]:
    """Open `page` for the block and restore the previous page; yields whether it switched."""
    app = cast(ResolveEdit, session.resolve)
    current = app.GetCurrentPage()
    if current == page:
        yield False
        return
    if not app.OpenPage(page):
        raise api_failed("OpenPage", f"could not switch to the {page} page")
    try:
        yield True
    finally:
        if current:
            app.OpenPage(current)


@contextmanager
def with_current_timeline(session: Session, timeline: TimelineApi) -> Iterator[bool]:
    """Make `timeline` current for the block, then restore; yields whether it switched."""
    project = cast(ProjectEdit, session.require_project())
    previous = project.GetCurrentTimeline()
    if previous is not None and previous.GetUniqueId() == timeline.GetUniqueId():
        yield False
        return
    if not project.SetCurrentTimeline(timeline):
        raise api_failed("SetCurrentTimeline", f"could not open timeline {timeline.GetName()}")
    try:
        yield True
    finally:
        if previous is not None:
            project.SetCurrentTimeline(previous)
