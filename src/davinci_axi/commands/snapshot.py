"""The `snapshot` command group: list, show, restore and delete saved project snapshots (R31, F4).

`list` and `show` only read the snapshot directories on disk and never connect to Resolve.
`restore` imports a snapshot's `.drp` as a NEW project (never overwriting one, R31) after the
usual save and safety snapshot. `delete` removes a snapshot directory from disk; it is the one
irreversible verb here and refuses any directory that is not inside the snapshot root.
"""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from davinci_axi.commands.project_io import clean_name, edit_manager, exists_error, next_steps
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.output import apply_limit, collapse_home, empty_state, hint, select_fields
from davinci_axi.snapshot import snapshot_root
from davinci_axi.snapshot_index import SnapshotMeta, counts, find_snapshot, list_snapshots
from davinci_axi.spec import LIST_FLAGS, CommandSpec, Flag, Invocation, Positional, Result

DEFAULT_FIELDS = ("id", "project", "created", "reason", "drp")
SAFETY_METHODS = ("ProjectManager.SaveProject", "ProjectManager.ExportProject")
# Reads of `dump.dump_project_state`, which `take_snapshot` runs before every restore's import.
DUMP_METHODS = (
    "Project.GetName",
    "Project.GetUniqueId",
    "Project.GetSettings",
    "Project.GetCurrentTimeline",
    "Project.GetTimelineCount",
    "Project.GetTimelineByIndex",
    "Timeline.GetName",
    "Timeline.GetUniqueId",
    "Timeline.GetStartFrame",
    "Timeline.GetEndFrame",
    "Timeline.GetStartTimecode",
    "Timeline.GetSettings",
    "Timeline.GetMarkers",
    "Timeline.GetTrackCount",
    "Timeline.GetTrackName",
    "Timeline.GetIsTrackEnabled",
    "Timeline.GetIsTrackLocked",
    "Timeline.GetTrackSubType",
    "Timeline.GetItemListInTrack",
    "TimelineItem.GetUniqueId",
    "TimelineItem.GetName",
    "TimelineItem.GetType",
    "TimelineItem.GetStart",
    "TimelineItem.GetEnd",
    "TimelineItem.GetDuration",
    "TimelineItem.GetSourceStartFrame",
    "TimelineItem.GetSourceEndFrame",
    "TimelineItem.GetClipEnabled",
    "TimelineItem.GetClipColor",
    "TimelineItem.GetFlagList",
    "TimelineItem.GetMarkers",
    "TimelineItem.GetMediaPoolItem",
    "MediaPoolItem.GetName",
    "MediaPoolItem.GetUniqueId",
    "MediaPoolItem.GetClipProperty",
)
RESTORE_METHODS = (
    *SAFETY_METHODS,
    "ProjectManager.ImportProject",
    "ProjectManager.GetCurrentFolder",
    "ProjectManager.GetProjectListInCurrentFolder",
    *DUMP_METHODS,
)


def _requested(inv: Invocation) -> list[str] | None:
    raw = inv.args.get("fields")
    return [str(f) for f in raw] if isinstance(raw, list) else None


def _limit(inv: Invocation) -> int:
    value = inv.args.get("limit")
    return value if isinstance(value, int) else 100


def _row(meta: SnapshotMeta) -> dict[str, object]:
    return {
        "id": meta.id,
        "project": meta.project,
        "created": meta.created,
        "reason": meta.reason,
        "drp": meta.drp is not None,
        "resolve_version": meta.resolve_version,
        "dir": collapse_home(meta.dir),
    }


def run_list(inv: Invocation) -> Result:
    requested = _requested(inv)
    limit = _limit(inv)
    apply_limit([], limit)  # reject a bad --limit first
    project = inv.args.get("project")
    wanted = project if isinstance(project, str) and project else None
    root = snapshot_root()
    warnings: list[str] = []
    snapshots = [m for m in list_snapshots(root, warnings) if wanted is None or m.project == wanted]
    page, total = apply_limit([_row(m) for m in snapshots], limit)
    data: dict[str, object] = {"total": total}
    if page:
        data["snapshots"] = select_fields(page, DEFAULT_FIELDS, requested)
    else:
        scope = f"under {collapse_home(root)}"
        data["snapshots"] = empty_state("snapshots", scope, {"project": wanted})
    if warnings:
        data["warnings"] = warnings
    help_lines: list[str] = []
    if total > len(page):
        help_lines.append(hint(f"snapshot list --limit {total}", f"list all {total} snapshots"))
    if snapshots:
        newest = snapshots[0].id
        help_lines.append(hint(f"snapshot show {newest}", "see what the newest snapshot holds"))
        help_lines.append(
            hint(f"snapshot restore {newest}", "preview restoring it as a new project")
        )
    else:
        help_lines.append(hint("project snapshot", "save the current project as a snapshot"))
    return Result(data, help_lines[:3])


def run_show(inv: Invocation) -> Result:
    meta = find_snapshot(snapshot_root(), inv.positionals["id"])
    timelines, items = counts(meta)
    data: dict[str, object] = {
        "id": meta.id,
        "dir": collapse_home(meta.dir),
        "drp": collapse_home(meta.drp) if meta.drp else None,
        "state": collapse_home(meta.state) if meta.state else None,
        "project": meta.project,
        "created": meta.created,
        "reason": meta.reason,
        "resolve_version": meta.resolve_version,
        "timelines": timelines,
        "items": items,
    }
    help_lines = [hint(f"snapshot restore {meta.id}", "preview restoring it as a new project")]
    help_lines.append(hint("snapshot list", "list every snapshot"))
    return Result(data, help_lines)


def _incomplete(meta: SnapshotMeta) -> AxiError:
    state = collapse_home(meta.state) if meta.state else f"{collapse_home(meta.dir)}/state.json"
    return AxiError(
        "SNAPSHOT_INCOMPLETE",
        f"snapshot {meta.id} has no .drp file to import",
        help=[
            f"The JSON state dump may still be readable at {state}",
            "Pick another snapshot with `davinci-axi snapshot list`",
        ],
        detail={"dir": str(meta.dir)},
    )


def run_restore(inv: Invocation) -> Result:
    meta = find_snapshot(snapshot_root(), inv.positionals["id"])
    given = inv.args.get("name")
    name = clean_name(given, "project name") if isinstance(given, str) else None
    if meta.drp is None:
        raise _incomplete(meta)
    name = name or f"{meta.project} (restored {meta.id[:15]})"
    check_snapshot_flags(inv)
    session = inv.connect()
    manager = edit_manager(session)
    if name in manager.GetProjectListInCurrentFolder():
        raise exists_error("project", name, f"folder {manager.GetCurrentFolder()}")
    drp = meta.drp
    words = ["snapshot", "restore", meta.id]
    if isinstance(given, str):
        words += ["--name", name]
    plan = MutationPlan(
        command="snapshot restore",
        target=f"snapshot {meta.id}",
        changes=[Change("project", None, name)],
        force_cmd=force_cmd(inv, *words),
        snapshot="if_open",
        extra={"restored_as": name, "from_project": meta.project},
        next=next_steps(
            (
                f"project open {shlex.quote(name)} --force",
                "open the restored project (it is not open yet)",
            )
        ),
    )

    def apply() -> None:
        if not manager.ImportProject(str(drp), name):
            raise api_failed("ImportProject", f"could not import {drp.name} as {name}")

    def verify() -> dict[str, object]:
        listed = name in manager.GetProjectListInCurrentFolder()
        return {"project": name if listed else None}

    return run_mutation(inv, session, plan, apply, verify)


def _inside_root(meta: SnapshotMeta, root: Path) -> bool:
    """True only for a real directory whose resolved path sits under the resolved root."""
    if meta.dir.is_symlink():
        return False
    return meta.dir.resolve().is_relative_to(root.resolve())


def run_delete(inv: Invocation) -> Result:
    root = snapshot_root()
    meta = find_snapshot(root, inv.positionals["id"])
    if not _inside_root(meta, root):
        raise AxiError(
            "WRITE_REFUSED",
            f"snapshot {meta.id} is not inside the snapshot root {collapse_home(root)}",
            help=["Only directories inside the snapshot root are deleted; remove this one by hand"],
            detail={"dir": str(meta.dir)},
        )
    plan = MutationPlan(
        command="snapshot delete",
        target=f"snapshot {meta.id}",
        changes=[Change("snapshot", meta.id, None)],
        force_cmd=force_cmd(inv, "snapshot", "delete", meta.id),
        irreversible=True,
        warning=f"removes {collapse_home(meta.dir)} from disk; the .drp inside cannot be recovered",
        snapshot="none",
        next=next_steps(("snapshot list", "list the remaining snapshots")),
    )

    def apply() -> None:
        try:
            shutil.rmtree(meta.dir)
        except OSError as exc:
            raise AxiError(
                "DELETE_FAILED",
                f"could not remove {collapse_home(meta.dir)}: {exc}",
                help=["Check the directory permissions, then retry"],
            ) from exc

    def verify() -> dict[str, object]:
        return {"snapshot": meta.id if meta.dir.exists() else None}

    return run_mutation(inv, None, plan, apply, verify)


LIST_SPEC = CommandSpec(
    name="list",
    summary="List saved snapshots, newest first (reads disk only)",
    run=run_list,
    flags=(Flag("project", "str", None, help="only snapshots of this project name"), *LIST_FLAGS),
    examples=(
        "davinci-axi snapshot list",
        "davinci-axi snapshot list --project sample-project --limit 5",
        "davinci-axi snapshot list --fields id,reason,created",
    ),
)
SHOW_SPEC = CommandSpec(
    name="show",
    summary="Show one snapshot: where it lives, what it holds and why it was taken",
    run=run_show,
    positionals=(Positional("id", help="snapshot id from `snapshot list`"),),
    examples=("davinci-axi snapshot show 20261027-013000-sample-project",),
)
RESTORE_SPEC = CommandSpec(
    name="restore",
    summary="Import a snapshot as a NEW project; never overwrites (dry run until --force)",
    run=run_restore,
    positionals=(Positional("id", help="snapshot id from `snapshot list`"),),
    flags=(
        Flag(
            "name",
            "str",
            None,
            help="name of the restored project (default: <project> (restored <id>))",
        ),
        *MUTATION_FLAGS,
    ),
    examples=(
        "davinci-axi snapshot restore 20261027-013000-sample-project",
        "davinci-axi snapshot restore 20261027-013000-sample-project --name sample-v1 --force",
    ),
    reads_only=False,
    methods=RESTORE_METHODS,
)
DELETE_SPEC = CommandSpec(
    name="delete",
    summary="Delete a snapshot directory from disk (irreversible, dry run until --force)",
    run=run_delete,
    positionals=(Positional("id", help="snapshot id from `snapshot list`"),),
    flags=(
        Flag(
            "force", "bool", False, help="delete the snapshot (without it the command is a dry run)"
        ),
    ),
    examples=(
        "davinci-axi snapshot delete 20261027-013000-sample-project",
        "davinci-axi snapshot delete 20261027-013000-sample-project --force",
    ),
    reads_only=False,
)

SPEC = CommandSpec(
    name="snapshot",
    summary="List, show, restore and delete project snapshots; restore creates a new project",
    subcommands={
        "list": LIST_SPEC,
        "show": SHOW_SPEC,
        "restore": RESTORE_SPEC,
        "delete": DELETE_SPEC,
    },
    examples=(
        "davinci-axi snapshot list",
        "davinci-axi snapshot show 20261027-013000-sample-project",
        "davinci-axi snapshot restore 20261027-013000-sample-project --force",
    ),
    reads_only=False,
    methods=RESTORE_METHODS,
)
