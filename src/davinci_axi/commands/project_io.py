"""Project lifecycle: `project create|open|save|rename|close|delete|import|export|archive` (U10).

State changes go through `mutate.run_mutation` (dry run unless `--force`, save, snapshot, write,
read-back). `export` and `archive` only write a file or directory, so like `project snapshot`
they need no `--force` and no snapshot; they refuse to overwrite without `--overwrite`.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import cast

from davinci_axi import registry
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    refuse_existing,
    run_mutation,
)
from davinci_axi.output import collapse_home, hint
from davinci_axi.resolve_types_edit import ProjectEdit, ProjectManagerEdit
from davinci_axi.session import Session
from davinci_axi.snapshot import NEVER_SAVED_HINT
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

SAFETY_METHODS = ("ProjectManager.SaveProject", "ProjectManager.ExportProject")


def text_arg(inv: Invocation, key: str) -> str | None:
    raw = inv.args.get(key)
    return raw if isinstance(raw, str) and raw != "" else None


def clean_name(raw: str, what: str) -> str:
    """A non-empty, trimmed name, or VALIDATION_ERROR (exit 2) before any Resolve call."""
    name = raw.strip()
    if not name:
        raise AxiError("VALIDATION_ERROR", f"{what} is empty", help=[f"Pass a non-empty {what}"])
    return name


def edit_manager(session: Session) -> ProjectManagerEdit:
    return cast(ProjectManagerEdit, session.project_manager())


def exists_error(kind: str, name: str, where: str) -> AxiError:
    """A name is already taken. Exit 1 (unit-local code): nothing is ever overwritten."""
    return AxiError(
        "ALREADY_EXISTS",
        f"{kind} {name} already exists in {where}",
        help=["Choose another name; the tool never overwrites an existing one"],
    )


def next_steps(*rows: tuple[str, str]) -> list[str]:
    """Hints for the rows whose top-level command is registered."""
    return [hint(cmd, purpose) for cmd, purpose in rows if cmd.split()[0] in registry.COMMANDS]


def current_name(session: Session) -> str | None:
    project = session.current_project()
    return project.GetName() if project is not None else None


def tree_bytes(path: Path) -> int:
    """Size of a file, or of every file under a directory; 0 when it does not exist."""
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return 0


def run_create(inv: Invocation) -> Result:
    name = clean_name(inv.positionals["name"], "project name")
    media_path = text_arg(inv, "media_path")
    check_snapshot_flags(inv)
    session = inv.connect()
    manager = edit_manager(session)
    if name in manager.GetProjectListInCurrentFolder():
        raise exists_error("project", name, f"folder {manager.GetCurrentFolder()}")
    words = ["project", "create", name]
    if media_path:
        words += ["--media-path", media_path]
    plan = MutationPlan(
        command="project create",
        target=f"project {name}",
        changes=[Change("current project", current_name(session), name)],
        force_cmd=force_cmd(inv, *words),
        warning="opens the new project and makes it current",
        snapshot="if_open",
        next=next_steps(("timelines", "list the timelines of the new project")),
    )

    def apply() -> None:
        created = (
            manager.CreateProject(name, media_path) if media_path else manager.CreateProject(name)
        )
        if created is None:
            raise api_failed("CreateProject", f"could not create project {name}")

    def verify() -> dict[str, object]:
        return {"current project": current_name(session)}

    return run_mutation(inv, session, plan, apply, verify)


def run_open(inv: Invocation) -> Result:
    name = clean_name(inv.positionals["name"], "project name")
    check_snapshot_flags(inv)
    session = inv.connect()
    manager = edit_manager(session)
    names = manager.GetProjectListInCurrentFolder()
    if name not in names:
        listing = ", ".join(names) if names else "(none)"
        raise not_found(
            "project", name, f"Projects in folder {manager.GetCurrentFolder()}: {listing}"
        )
    before = current_name(session)
    already = before == name
    plan = MutationPlan(
        command="project open",
        target=f"project {name}",
        changes=[] if already else [Change("current project", before, name)],
        force_cmd=force_cmd(inv, "project", "open", name),
        warning="already the current project" if already else "changes the current project",
        snapshot="if_open",
        next=next_steps(("timelines", "list the timelines of this project")),
    )

    def apply() -> None:
        if manager.LoadProject(name) is None:
            raise api_failed("LoadProject", f"could not open project {name}")

    def verify() -> dict[str, object]:
        return {"current project": current_name(session)}

    return run_mutation(inv, session, plan, apply, verify)


def run_save(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    session = inv.connect()
    name = session.require_project().GetName()
    manager = edit_manager(session)
    saved = {"ok": False}
    plan = MutationPlan(
        command="project save",
        target=f"project {name}",
        changes=[Change("saved", "(unknown)", True)],
        force_cmd=force_cmd(inv, "project", "save"),
        snapshot="none",
    )

    def apply() -> None:
        saved["ok"] = manager.SaveProject()
        if not saved["ok"]:
            raise api_failed("SaveProject", f"could not save project {name}")

    def verify() -> dict[str, object]:
        return {"saved": saved["ok"]}

    return run_mutation(inv, session, plan, apply, verify)


def run_rename(inv: Invocation) -> Result:
    new = clean_name(text_arg(inv, "to") or "", "new project name")
    check_snapshot_flags(inv)
    session = inv.connect()
    project = cast(ProjectEdit, session.require_project())
    old = project.GetName()
    manager = edit_manager(session)
    if new != old and new in manager.GetProjectListInCurrentFolder():
        raise exists_error("project", new, f"folder {manager.GetCurrentFolder()}")
    plan = MutationPlan(
        command="project rename",
        target=f"project {old}",
        changes=[] if new == old else [Change("name", old, new)],
        force_cmd=force_cmd(inv, "project", "rename", "--to", new),
        warning=f"already named {old}" if new == old else "",
        snapshot="required",
        next=next_steps(("projects", "list the projects in this folder")),
    )

    def apply() -> None:
        if not project.SetName(new):
            raise api_failed("SetName", f"could not rename project {old} to {new}")

    def verify() -> dict[str, object]:
        return {"name": session.require_project().GetName()}

    return run_mutation(inv, session, plan, apply, verify)


def run_close(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    session = inv.connect()
    project = session.require_project()
    name = project.GetName()
    manager = edit_manager(session)
    plan = MutationPlan(
        command="project close",
        target=f"project {name}",
        changes=[Change("current project", name, None)],
        force_cmd=force_cmd(inv, "project", "close"),
        irreversible=True,
        warning="closes the project without a further save; the snapshot holds the saved state",
        snapshot="required",
        next=next_steps(("projects", "list the projects you can open")),
    )

    def apply() -> None:
        if not manager.CloseProject(project):
            raise api_failed("CloseProject", f"could not close project {name}")

    def verify() -> dict[str, object]:
        return {"current project": current_name(session)}

    return run_mutation(inv, session, plan, apply, verify)


def run_delete(inv: Invocation) -> Result:
    name = clean_name(inv.positionals["name"], "project name")
    check_snapshot_flags(inv)
    session = inv.connect()
    manager = edit_manager(session)
    names = manager.GetProjectListInCurrentFolder()
    if name not in names:
        listing = ", ".join(names) if names else "(none)"
        raise not_found(
            "project", name, f"Projects in folder {manager.GetCurrentFolder()}: {listing}"
        )
    if current_name(session) == name:
        raise AxiError(
            "VALIDATION_ERROR",
            f"project {name} is the current project and cannot be deleted",
            help=[
                "Close it first, or open another project",
                *next_steps(("project close", "close the current project")),
            ],
        )
    plan = MutationPlan(
        command="project delete",
        target=f"project {name}",
        changes=[Change("project", name, None)],
        force_cmd=force_cmd(inv, "project", "delete", name),
        irreversible=True,
        warning=(
            f"deletes project {name} and its data; the safety snapshot covers the current "
            "project, not this one"
        ),
        snapshot="if_open",
        next=next_steps(("projects", "list the remaining projects")),
    )

    def apply() -> None:
        if not manager.DeleteProject(name):
            raise api_failed("DeleteProject", f"could not delete project {name}")

    def verify() -> dict[str, object]:
        still = name in manager.GetProjectListInCurrentFolder()
        return {"project": name if still else None}

    return run_mutation(inv, session, plan, apply, verify)


def run_import(inv: Invocation) -> Result:
    file = Path(inv.positionals["file"]).expanduser()
    given = text_arg(inv, "name")
    name = clean_name(given, "project name") if given is not None else None
    if not file.exists():
        raise not_found("file", collapse_home(file), "Check the path of the .drp to import")
    check_snapshot_flags(inv)
    session = inv.connect()
    manager = edit_manager(session)
    before = manager.GetProjectListInCurrentFolder()
    guess = name or file.stem
    if guess in before:
        raise exists_error("project", guess, f"folder {manager.GetCurrentFolder()}")
    words = ["project", "import", str(file)]
    if name:
        words += ["--name", name]
    # Without --name Resolve names the project from the file, which the tool cannot read, so
    # the plan counts projects instead of guessing a name.
    change = (
        Change("project", None, name)
        if name
        else Change("projects in folder", len(before), len(before) + 1)
    )
    plan = MutationPlan(
        command="project import",
        target=f"file {collapse_home(file)}",
        changes=[change],
        force_cmd=force_cmd(inv, *words),
        warning="" if name else "the project is named after the file's own project name",
        snapshot="if_open",
        extra={"name": name or "(from the file)"},
        next=next_steps(("projects", "list the projects in this folder")),
    )

    def apply() -> None:
        ok = manager.ImportProject(str(file), name) if name else manager.ImportProject(str(file))
        if not ok:
            raise api_failed(
                "ImportProject",
                "Resolve refused the import; a project with that name may already exist",
            )
        added = [n for n in manager.GetProjectListInCurrentFolder() if n not in before]
        plan.extra["imported_as"] = ", ".join(added) if added else "(unknown)"

    def verify() -> dict[str, object]:
        now = manager.GetProjectListInCurrentFolder()
        if name:
            return {"project": name if name in now else None}
        return {"projects in folder": len(now)}

    return run_mutation(inv, session, plan, apply, verify)


def run_export(inv: Invocation) -> Result:
    path = Path(inv.positionals["file"]).expanduser()
    refuse_existing(path, bool(inv.args.get("overwrite")))
    with_stills_luts = not inv.args.get("no_stills_luts")
    session = inv.connect()
    name = session.require_project().GetName()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not edit_manager(session).ExportProject(name, str(path), with_stills_luts):
        error = api_failed("ExportProject", f"could not export project {name} to {path.name}")
        error.help.append(NEVER_SAVED_HINT)
        raise error
    data: dict[str, object] = {
        "project": name,
        "path": collapse_home(path),
        "bytes": tree_bytes(path),
        "with_stills_luts": with_stills_luts,
    }
    quoted = shlex.quote(str(path))
    return Result(
        data, next_steps((f"project import {quoted}", "import this file as a new project"))
    )


def _safe(*methods: str) -> tuple[str, ...]:
    return (*SAFETY_METHODS, *methods)


CREATE_SPEC = CommandSpec(
    name="create",
    summary="Create a project in the current folder and open it (dry run until --force)",
    run=run_create,
    positionals=(Positional("name", help="name of the new project"),),
    flags=(
        *MUTATION_FLAGS,
        Flag("media-path", "str", None, help="media location for the new project"),
    ),
    examples=(
        "davinci-axi project create promo-cut",
        "davinci-axi project create promo-cut --force",
        "davinci-axi project create promo-cut --media-path /Volumes/media --force",
    ),
    reads_only=False,
    methods=_safe("ProjectManager.CreateProject"),
)

OPEN_SPEC = CommandSpec(
    name="open",
    summary="Open a project of the current folder (dry run until --force)",
    run=run_open,
    positionals=(Positional("name", help="project to open"),),
    flags=MUTATION_FLAGS,
    examples=(
        "davinci-axi project open sample-project",
        "davinci-axi project open sample-project --force",
    ),
    reads_only=False,
    methods=_safe("ProjectManager.LoadProject"),
)

SAVE_SPEC = CommandSpec(
    name="save",
    summary="Save the current project (dry run until --force)",
    run=run_save,
    flags=MUTATION_FLAGS,
    examples=("davinci-axi project save", "davinci-axi project save --force"),
    reads_only=False,
    methods=("ProjectManager.SaveProject",),
)

RENAME_SPEC = CommandSpec(
    name="rename",
    summary="Rename the current project (dry run until --force)",
    run=run_rename,
    flags=(*MUTATION_FLAGS, Flag("to", "str", None, required=True, help="the new name")),
    examples=(
        "davinci-axi project rename --to promo-v2",
        "davinci-axi project rename --to promo-v2 --force",
    ),
    reads_only=False,
    methods=_safe("Project.SetName"),
)

CLOSE_SPEC = CommandSpec(
    name="close",
    summary="Close the current project without a further save (irreversible)",
    run=run_close,
    flags=MUTATION_FLAGS,
    examples=("davinci-axi project close", "davinci-axi project close --force"),
    reads_only=False,
    methods=_safe("ProjectManager.CloseProject"),
)

DELETE_SPEC = CommandSpec(
    name="delete",
    summary="Delete a project of the current folder (irreversible)",
    run=run_delete,
    positionals=(Positional("name", help="project to delete; it must not be the current one"),),
    flags=MUTATION_FLAGS,
    examples=(
        "davinci-axi project delete scratch",
        "davinci-axi project delete scratch --force",
    ),
    reads_only=False,
    methods=_safe("ProjectManager.DeleteProject"),
)

IMPORT_SPEC = CommandSpec(
    name="import",
    summary="Import a .drp file as a new project; never overwrites a project",
    run=run_import,
    positionals=(Positional("file", help="path of the .drp file"),),
    flags=(
        *MUTATION_FLAGS,
        Flag("name", "str", None, help="name for the imported project (default: from the file)"),
    ),
    examples=(
        "davinci-axi project import ~/exports/promo.drp",
        "davinci-axi project import ~/exports/promo.drp --name promo-copy --force",
    ),
    reads_only=False,
    methods=_safe("ProjectManager.ImportProject"),
)

EXPORT_SPEC = CommandSpec(
    name="export",
    summary="Export the current project to a .drp file (no --force needed; never overwrites)",
    run=run_export,
    positionals=(Positional("file", help="path of the .drp file to write"),),
    flags=(
        Flag("no-stills-luts", "bool", False, help="leave stills and LUTs out of the .drp"),
        Flag("overwrite", "bool", False, help="replace the file if it exists"),
    ),
    examples=(
        "davinci-axi project export ~/exports/promo.drp",
        "davinci-axi project export ~/exports/promo.drp --overwrite --no-stills-luts",
    ),
    reads_only=False,
    methods=("ProjectManager.ExportProject",),
)

LIFECYCLE: dict[str, CommandSpec] = {
    "create": CREATE_SPEC,
    "open": OPEN_SPEC,
    "save": SAVE_SPEC,
    "rename": RENAME_SPEC,
    "close": CLOSE_SPEC,
    "delete": DELETE_SPEC,
    "import": IMPORT_SPEC,
    "export": EXPORT_SPEC,
}
