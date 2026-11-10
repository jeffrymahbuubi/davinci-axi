"""The `project` command group: snapshot, lifecycle, folders and settings.

`project snapshot` (R23) exports the current project to disk with `ExportProject` and writes
files under the snapshot root; it never calls SaveProject and never changes Resolve state. The
lifecycle verbs live in `project_io.py`; folders and settings are here. Every state change goes
through `mutate.run_mutation`: a dry run unless `--force`, with a snapshot first.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from davinci_axi import registry
from davinci_axi.commands.project_io import (
    LIFECYCLE,
    SAFETY_METHODS,
    clean_name,
    edit_manager,
    exists_error,
    next_steps,
    text_arg,
    tree_bytes,
)
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    coerce_like,
    force_cmd,
    parse_assignments,
    refuse_existing,
    run_mutation,
)
from davinci_axi.output import collapse_home, hint
from davinci_axi.resolve_types_edit import ProjectEdit
from davinci_axi.snapshot import Snapshot, resolve_out, take_snapshot
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

DEFAULT_REASON = "manual"
# ASSUMED (live check): GetCurrentFolder() names the root folder "Root".
ROOT_FOLDER = "Root"
SHOWN_KEYS = 25


def _counts(snapshot: Snapshot) -> tuple[int, int]:
    """(timelines, items) read back from the `state.json` the snapshot just wrote."""
    try:
        state = json.loads(snapshot.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, 0
    timelines = state.get("timelines") or []
    items = sum(len(track.get("items") or []) for tl in timelines for track in tl.get("tracks", []))
    return len(timelines), items


def run_snapshot(inv: Invocation) -> Result:
    out_text = text_arg(inv, "out")
    if inv.args.get("out") is not None and out_text is None:
        raise AxiError(
            "VALIDATION_ERROR",
            "flag --out needs a directory path",
            help=["Valid example: --out ~/snapshots/before-grade"],
        )
    out = resolve_out(out_text) if out_text else None
    reason = text_arg(inv, "reason") or DEFAULT_REASON
    with_stills_luts = not inv.args.get("no_stills_luts")
    snapshot = take_snapshot(
        inv.connect(), reason=reason, out=out, with_stills_luts=with_stills_luts, manual=True
    )
    timelines, items = _counts(snapshot)
    data: dict[str, object] = {
        "snapshot": snapshot.id,
        "dir": collapse_home(snapshot.dir),
        "drp": collapse_home(snapshot.drp) if snapshot.drp else None,
        "state": collapse_home(snapshot.state),
        "project": snapshot.project,
        "reason": snapshot.reason,
        "timelines": timelines,
        "items": items,
    }
    if inv.args.get("force"):
        data["force"] = "ignored (snapshot is disk-only and always runs)"
    help_lines: list[str] = []
    if "timelines" in registry.COMMANDS:
        help_lines.append(hint("timelines", "list the timelines in the live project"))
    if "items" in registry.COMMANDS:
        help_lines.append(hint("items", "list the timeline items in the live project"))
    if "snapshot" in registry.COMMANDS:
        help_lines.append(hint("snapshot list", "list snapshots and restore one"))
    else:
        help_lines.append(
            "Restore by importing the .drp from Resolve's Project Manager; "
            "snapshot list and restore commands are coming in a later release"
        )
    return Result(data, help_lines)


SNAPSHOT_SPEC = CommandSpec(
    name="snapshot",
    summary="Export the current project to a .drp plus a JSON state dump on disk",
    run=run_snapshot,
    flags=(
        Flag("out", "str", None, help="write the snapshot into this directory"),
        Flag("no-stills-luts", "bool", False, help="leave stills and LUTs out of the .drp"),
        Flag("reason", "str", None, help=f"note stored in meta.json (default {DEFAULT_REASON})"),
        Flag(
            "force",
            "bool",
            False,
            help="accepted and ignored: a snapshot is disk-only and always runs",
        ),
    ),
    notes=(
        "No dry run and no --force gate: a snapshot never changes the project, it only writes "
        "<dir>/<id>/ on disk; --force is accepted so recipes can keep it",
    ),
    examples=(
        "davinci-axi project snapshot",
        "davinci-axi project snapshot --reason before-regrade",
        "davinci-axi project snapshot --out ~/snapshots/v1 --no-stills-luts",
    ),
    reads_only=False,
    methods=("ProjectManager.ExportProject",),
)


def run_archive(inv: Invocation) -> Result:
    """Disk-only like `project snapshot`: no `--force`, no snapshot, never overwrites."""
    path = Path(inv.positionals["dir"]).expanduser()
    refuse_existing(path, bool(inv.args.get("overwrite")))
    media = bool(inv.args.get("media"))
    render_cache = bool(inv.args.get("render_cache"))
    proxy = bool(inv.args.get("proxy"))
    session = inv.connect()
    name = session.require_project().GetName()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not edit_manager(session).ArchiveProject(name, str(path), media, render_cache, proxy):
        raise api_failed("ArchiveProject", f"could not archive project {name} to {path.name}")
    data: dict[str, object] = {
        "project": name,
        "path": collapse_home(path),
        "bytes": tree_bytes(path),
        "media": media,
        "render_cache": render_cache,
        "proxy": proxy,
    }
    return Result(data, [])


def run_folder_create(inv: Invocation) -> Result:
    name = clean_name(inv.positionals["name"], "folder name")
    check_snapshot_flags(inv)
    session = inv.connect()
    session.require_project()
    manager = edit_manager(session)
    where = manager.GetCurrentFolder()
    if name in manager.GetFolderListInCurrentFolder():
        raise exists_error("folder", name, f"folder {where}")
    plan = MutationPlan(
        command="project folder create",
        target=f"folder {name}",
        changes=[Change("folder", None, name)],
        force_cmd=force_cmd(inv, "project", "folder", "create", name),
        snapshot="required",
        extra={"in": where},
    )

    def apply() -> None:
        if not manager.CreateFolder(name):
            raise api_failed("CreateFolder", f"could not create folder {name}")

    def verify() -> dict[str, object]:
        listed = name in manager.GetFolderListInCurrentFolder()
        return {"folder": name if listed else None}

    return run_mutation(inv, session, plan, apply, verify)


def run_folder_delete(inv: Invocation) -> Result:
    name = clean_name(inv.positionals["name"], "folder name")
    check_snapshot_flags(inv)
    session = inv.connect()
    session.require_project()
    manager = edit_manager(session)
    folders = manager.GetFolderListInCurrentFolder()
    if name not in folders:
        listing = ", ".join(folders) if folders else "(none)"
        raise not_found("folder", name, f"Folders in {manager.GetCurrentFolder()}: {listing}")
    plan = MutationPlan(
        command="project folder delete",
        target=f"folder {name}",
        changes=[Change("folder", name, None)],
        force_cmd=force_cmd(inv, "project", "folder", "delete", name),
        irreversible=True,
        warning=(
            f"deletes folder {name} and every project in it; the API cannot say whether it "
            "is empty, and the safety snapshot covers only the current project"
        ),
        snapshot="required",
    )

    def apply() -> None:
        if not manager.DeleteFolder(name):
            raise api_failed("DeleteFolder", f"could not delete folder {name}")

    def verify() -> dict[str, object]:
        listed = name in manager.GetFolderListInCurrentFolder()
        return {"folder": name if listed else None}

    return run_mutation(inv, session, plan, apply, verify)


def run_folder_open(inv: Invocation) -> Result:
    name = clean_name(inv.positionals["name"], "folder name")
    check_snapshot_flags(inv)
    session = inv.connect()
    manager = edit_manager(session)
    here = manager.GetCurrentFolder()
    folders = manager.GetFolderListInCurrentFolder()
    if name not in folders:
        listing = ", ".join(folders) if folders else "(none)"
        raise not_found("folder", name, f"Folders in {here}: {listing}")
    plan = MutationPlan(
        command="project folder open",
        target=f"folder {name}",
        changes=[Change("folder", here, name)],
        force_cmd=force_cmd(inv, "project", "folder", "open", name),
        snapshot="none",
        next=next_steps(("projects", "list the projects in this folder")),
    )

    def apply() -> None:
        if not manager.OpenFolder(name):
            raise api_failed("OpenFolder", f"could not open folder {name}")

    def verify() -> dict[str, object]:
        return {"folder": manager.GetCurrentFolder()}

    return run_mutation(inv, session, plan, apply, verify)


def run_folder_up(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    session = inv.connect()
    manager = edit_manager(session)
    here = manager.GetCurrentFolder()
    at_root = here == ROOT_FOLDER
    plan = MutationPlan(
        command="project folder up",
        target=f"folder {here}",
        changes=[] if at_root else [Change("folder", here, "(parent folder)")],
        force_cmd=force_cmd(inv, "project", "folder", "up"),
        warning="already at the root folder" if at_root else "",
        snapshot="none",
        next=next_steps(("projects", "list the projects in this folder")),
    )

    def apply() -> None:
        if not manager.GotoParentFolder():
            raise api_failed("GotoParentFolder", "could not go up one folder")
        plan.extra["now_in"] = manager.GetCurrentFolder()

    def verify() -> dict[str, object]:
        moved = manager.GetCurrentFolder() != here
        return {"folder": "(parent folder)" if moved else here}

    return run_mutation(inv, session, plan, apply, verify)


def run_folder_root(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    session = inv.connect()
    manager = edit_manager(session)
    here = manager.GetCurrentFolder()
    at_root = here == ROOT_FOLDER
    plan = MutationPlan(
        command="project folder root",
        target=f"folder {here}",
        changes=[] if at_root else [Change("folder", here, ROOT_FOLDER)],
        force_cmd=force_cmd(inv, "project", "folder", "root"),
        warning="already at the root folder" if at_root else "",
        snapshot="none",
        next=next_steps(("projects", "list the projects in this folder")),
    )

    def apply() -> None:
        if not manager.GotoRootFolder():
            raise api_failed("GotoRootFolder", "could not go to the root folder")

    def verify() -> dict[str, object]:
        return {"folder": manager.GetCurrentFolder()}

    return run_mutation(inv, session, plan, apply, verify)


def _unknown_settings(unknown: list[str], live: dict[str, object]) -> AxiError:
    keys = sorted(live)
    shown = ", ".join(keys[:SHOWN_KEYS]) + (" ..." if len(keys) > SHOWN_KEYS else "")
    return AxiError(
        "VALIDATION_ERROR",
        f"unknown project setting {', '.join(unknown)}",
        help=[f"Valid settings ({len(keys)}): {shown}"],
    )


def run_settings_set(inv: Invocation) -> Result:
    text = inv.positionals["assignments"]
    pairs = parse_assignments(text)
    check_snapshot_flags(inv)
    session = inv.connect()
    project = cast(ProjectEdit, session.require_project())
    live = dict(project.GetSettings())
    unknown = [key for key in pairs if key not in live]
    if unknown:
        raise _unknown_settings(unknown, live)
    wanted = {key: coerce_like(value, live[key]) for key, value in pairs.items()}
    changed = [key for key in pairs if wanted[key] != live[key]]
    plan = MutationPlan(
        command="project settings set",
        target=f"project {project.GetName()}",
        changes=[Change(key, live[key], wanted[key]) for key in changed],
        force_cmd=force_cmd(inv, "project", "settings", "set", text),
        warning="applies keys one by one; on failure earlier keys stay set",
        snapshot="required",
    )

    def apply() -> None:
        # Resolve takes setting values as strings, so the user's text is sent as typed.
        if not project.SetSettings({key: pairs[key] for key in changed}):
            raise api_failed("SetSettings", "Resolve rejected a setting; earlier keys may be set")

    def verify() -> dict[str, object]:
        now = session.require_project().GetSettings()
        return {key: now.get(key) for key in changed}

    return run_mutation(inv, session, plan, apply, verify)


FOLDER_CREATE_SPEC = CommandSpec(
    name="create",
    summary="Create a project folder inside the current folder (dry run until --force)",
    run=run_folder_create,
    positionals=(Positional("name", help="name of the new folder"),),
    flags=MUTATION_FLAGS,
    examples=(
        "davinci-axi project folder create clients",
        "davinci-axi project folder create clients --force",
    ),
    reads_only=False,
    methods=(*SAFETY_METHODS, "ProjectManager.CreateFolder"),
)
FOLDER_DELETE_SPEC = CommandSpec(
    name="delete",
    summary="Delete a project folder and every project in it (irreversible)",
    run=run_folder_delete,
    positionals=(Positional("name", help="folder to delete"),),
    flags=MUTATION_FLAGS,
    examples=(
        "davinci-axi project folder delete clients",
        "davinci-axi project folder delete clients --force",
    ),
    reads_only=False,
    methods=(*SAFETY_METHODS, "ProjectManager.DeleteFolder"),
)
FOLDER_OPEN_SPEC = CommandSpec(
    name="open",
    summary="Enter a project folder of the current folder (no snapshot)",
    run=run_folder_open,
    positionals=(Positional("name", help="folder to enter"),),
    flags=MUTATION_FLAGS,
    examples=("davinci-axi project folder open clients --force",),
    reads_only=False,
    methods=("ProjectManager.OpenFolder",),
)
FOLDER_UP_SPEC = CommandSpec(
    name="up",
    summary="Go to the parent project folder (no snapshot)",
    run=run_folder_up,
    flags=MUTATION_FLAGS,
    examples=("davinci-axi project folder up --force",),
    reads_only=False,
    methods=("ProjectManager.GotoParentFolder",),
)
FOLDER_ROOT_SPEC = CommandSpec(
    name="root",
    summary="Go to the root project folder (no snapshot)",
    run=run_folder_root,
    flags=MUTATION_FLAGS,
    examples=("davinci-axi project folder root --force",),
    reads_only=False,
    methods=("ProjectManager.GotoRootFolder",),
)
_FOLDER_SUBCOMMANDS: dict[str, CommandSpec] = {
    "create": FOLDER_CREATE_SPEC,
    "delete": FOLDER_DELETE_SPEC,
    "open": FOLDER_OPEN_SPEC,
    "up": FOLDER_UP_SPEC,
    "root": FOLDER_ROOT_SPEC,
}
FOLDER_SPEC = CommandSpec(
    name="folder",
    summary="Create, delete and navigate project folders",
    subcommands=_FOLDER_SUBCOMMANDS,
    examples=("davinci-axi project folder create clients", "davinci-axi project folder root"),
    reads_only=False,
    methods=tuple(dict.fromkeys(m for s in _FOLDER_SUBCOMMANDS.values() for m in s.methods)),
)

SETTINGS_SET_SPEC = CommandSpec(
    name="set",
    summary="Change project settings, e.g. timelineFrameRate=25 (dry run until --force)",
    run=run_settings_set,
    positionals=(Positional("assignments", help="comma-separated key=value pairs, e.g. a=1,b=x"),),
    flags=MUTATION_FLAGS,
    examples=(
        "davinci-axi project settings set timelineFrameRate=25",
        "davinci-axi project settings set timelineFrameRate=25 --force",
    ),
    reads_only=False,
    methods=(*SAFETY_METHODS, "Project.SetSettings"),
)
SETTINGS_SPEC = CommandSpec(
    name="settings",
    summary="Change project settings",
    subcommands={"set": SETTINGS_SET_SPEC},
    examples=("davinci-axi project settings set timelineFrameRate=25",),
    reads_only=False,
    methods=SETTINGS_SET_SPEC.methods,
)

ARCHIVE_SPEC = CommandSpec(
    name="archive",
    summary="Archive the current project to a .dra directory (no --force needed)",
    run=run_archive,
    positionals=(Positional("dir", help="path of the archive directory, usually ending .dra"),),
    flags=(
        Flag("media", "bool", False, help="include the source media"),
        Flag("render-cache", "bool", False, help="include the render cache"),
        Flag("proxy", "bool", False, help="include proxy media"),
        Flag("overwrite", "bool", False, help="replace the directory if it exists"),
    ),
    examples=(
        "davinci-axi project archive ~/archives/promo.dra",
        "davinci-axi project archive ~/archives/promo.dra --media --render-cache",
    ),
    reads_only=False,
    methods=("ProjectManager.ArchiveProject",),
)

_SUBCOMMANDS: dict[str, CommandSpec] = {
    "snapshot": SNAPSHOT_SPEC,
    **LIFECYCLE,
    "archive": ARCHIVE_SPEC,
    "folder": FOLDER_SPEC,
    "settings": SETTINGS_SPEC,
}

SPEC = CommandSpec(
    name="project",
    summary="Manage projects: lifecycle, folders, settings; `snapshot` saves a restorable copy",
    subcommands=_SUBCOMMANDS,
    examples=(
        "davinci-axi project snapshot",
        "davinci-axi project rename --to promo-v2",
        "davinci-axi project open sample-project --force",
    ),
    reads_only=False,
    methods=tuple(dict.fromkeys(m for s in _SUBCOMMANDS.values() for m in s.methods)),
)
