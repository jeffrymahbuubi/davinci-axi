"""`projects`: list the projects of the current project-manager folder (R18). Read-only."""

from __future__ import annotations

from davinci_axi import registry
from davinci_axi.errors import AxiError
from davinci_axi.output import apply_limit, empty_state, hint, page_meta, select_fields
from davinci_axi.spec import LIST_FLAGS, CommandSpec, Invocation, Result

DEFAULT_FIELDS = ("name", "modified", "current")
EXTRA_FIELDS = ("created", "notes", "collab", "modified_epoch")
NEXT_STEPS = (
    ("timelines", "list the timelines of the current project"),
    ("project snapshot", "save a restorable snapshot of the current project"),
)


def _requested(inv: Invocation) -> list[str] | None:
    raw = inv.args.get("fields")
    if not isinstance(raw, list):
        return None
    fields = [str(f) for f in raw]
    valid = (*DEFAULT_FIELDS, *EXTRA_FIELDS)
    unknown = [f for f in fields if f not in valid]
    if unknown:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown field {', '.join(unknown)}",
            help=[f"Valid fields: {', '.join(valid)}"],
        )
    return fields


def _limit(inv: Invocation) -> int:
    value = inv.args.get("limit")
    return value if isinstance(value, int) else 100


def run(inv: Invocation) -> Result:
    requested = _requested(inv)
    limit = _limit(inv)
    apply_limit([], limit)  # reject a bad --limit before any Resolve call
    session = inv.connect()
    manager = session.project_manager()
    database = manager.GetCurrentDatabase()
    folder = manager.GetCurrentFolder()
    current_project = manager.GetCurrentProject()
    current = current_project.GetName() if current_project is not None else None
    attributes = manager.GetProjectAttributesInCurrentFolder()
    want_epoch = requested is not None and "modified_epoch" in requested
    rows: list[dict[str, object]] = [
        {
            "name": name,
            "modified": info.get("lastModifiedDate"),
            "current": name == current,
            "created": info.get("creationDate"),
            "notes": info.get("notes"),
            "collab": info.get("liveCollaborationMode"),
            "modified_epoch": manager.GetProjectLastModifiedTime(name) if want_epoch else None,
        }
        for name, info in attributes.items()
    ]
    page, total = apply_limit(rows, limit)
    db_name = database.get("DbName")
    data: dict[str, object] = {
        "database": db_name,
        "db_type": database.get("DbType"),
        "folder": folder,
        "folders": list(manager.GetFolderListInCurrentFolder()),
        **page_meta(len(page), total),
    }
    if page:
        data["projects"] = select_fields(page, DEFAULT_FIELDS, requested)
    else:
        data["projects"] = empty_state("projects", f"in folder {folder} of {db_name}", {})
    help_lines = [
        hint(cmd, purpose) for cmd, purpose in NEXT_STEPS if cmd.split()[0] in registry.COMMANDS
    ]
    if total > len(page):
        help_lines.insert(0, hint(f"projects --limit {total}", f"list all {total} projects"))
    return Result(data, help_lines[:3])


SPEC = CommandSpec(
    name="projects",
    summary="List the projects in the current project-manager folder",
    run=run,
    flags=LIST_FLAGS,
    examples=(
        "davinci-axi projects",
        "davinci-axi projects --fields name,created,notes",
        "davinci-axi projects --fields name,modified_epoch",
        "davinci-axi projects --limit 5 --json",
    ),
    methods=(
        "Resolve.GetProjectManager",
        "ProjectManager.GetCurrentDatabase",
        "ProjectManager.GetCurrentFolder",
        "ProjectManager.GetCurrentProject",
        "ProjectManager.GetFolderListInCurrentFolder",
        "ProjectManager.GetProjectAttributesInCurrentFolder",
        "ProjectManager.GetProjectLastModifiedTime",
        "Project.GetName",
    ),
)
