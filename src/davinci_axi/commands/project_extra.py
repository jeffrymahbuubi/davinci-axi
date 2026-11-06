"""Project extras: settings presets, `restore`, `db` and `cloud` (U22, R32, R35).

`SPEC` wraps `project.SPEC` with `replace()` (KTD15); the registry points `project` here.
Settings presets reuse the `presets.py` engine. The cloud verbs need a Blackmagic Cloud account
and are untested live. Every state change goes through `mutate.run_mutation` with the
`if_open` policy: a dry run unless `--force`, a project snapshot first when one is open.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol, cast

from davinci_axi.commands import project
from davinci_axi.commands.presets import Kind, build_kind_spec
from davinci_axi.commands.project_io import (
    SAFETY_METHODS,
    clean_name,
    current_name,
    edit_manager,
    exists_error,
    next_steps,
    text_arg,
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
    run_mutation,
)
from davinci_axi.output import collapse_home, empty_state
from davinci_axi.resolve_types_edit import ProjectManagerEdit
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

FOLDER_READS = ("ProjectManager.GetProjectListInCurrentFolder", "ProjectManager.GetCurrentFolder")
# `CloudSettings` keys from the `.pyi`; `syncMode` is a word mapped to `resolve.CLOUD_SYNC_*`.
CLOUD_KEYS: tuple[str, ...] = (
    "projectName",
    "projectMediaPath",
    "isCollab",
    "isCameraAccess",
    "syncMode",
)
SYNC_MODES: dict[str, str] = {
    "none": "CLOUD_SYNC_NONE",
    "proxy-only": "CLOUD_SYNC_PROXY_ONLY",
    "proxy-and-orig": "CLOUD_SYNC_PROXY_AND_ORIG",
}
CLOUD_NOTE = "needs Blackmagic Cloud; untested live"
SETTINGS_PRESETS = Kind(
    key="settings",
    noun="project settings preset",
    words=("project", "settings", "preset"),
    owner="Project",
    list_method="GetProjectSettingsPresetList",
    verbs={
        "apply": ("load", "SetProjectSettingsPreset"),
        "save": ("save", "SaveCurrentProjectSettingsAsNewPreset"),
        "update": ("update", "UpdateProjectSettingsPreset"),
        "delete": ("delete", "DeleteProjectSettingsPreset"),
        "import": ("import", "ImportProjectSettingsPreset"),
        "export": ("export", "ExportProjectSettingsPreset"),
    },
    sample="base",
    rows=True,
    load_policy="required",
)


class ProjectManagerExtra(ProjectManagerEdit, Protocol):
    def RestoreProject(self, filePath: str, projectName: str | None = None) -> bool: ...
    def SetCurrentDatabase(self, dbInfo: dict[str, Any]) -> bool: ...
    def LoadCloudProject(self, cloudSettings: dict[str, Any]) -> Any: ...
    def CreateCloudProject(self, cloudSettings: dict[str, Any]) -> Any: ...
    def ImportCloudProject(self, filePath: str, cloudSettings: dict[str, Any]) -> bool: ...
    def RestoreCloudProject(self, folderPath: str, cloudSettings: dict[str, Any]) -> bool: ...


def _manager(session: Session) -> ProjectManagerExtra:
    return cast(ProjectManagerExtra, edit_manager(session))


def run_restore(inv: Invocation) -> Result:
    raw_file = inv.positionals["file"]
    path = Path(raw_file).expanduser().resolve()
    given = text_arg(inv, "name")
    name = clean_name(given, "project name") if given is not None else None
    if not path.exists():
        raise not_found("file", collapse_home(path), "Check the path of the project backup")
    check_snapshot_flags(inv)
    session = inv.connect()
    manager = _manager(session)
    before = manager.GetProjectListInCurrentFolder()
    if (name or path.stem) in before:
        raise exists_error("project", name or path.stem, f"folder {manager.GetCurrentFolder()}")
    count = len(before)
    change = (
        Change("project", None, name) if name else Change("projects in folder", count, count + 1)
    )
    plan = MutationPlan(
        command="project restore",
        target=f"file {collapse_home(path)}",
        changes=[change],
        force_cmd=force_cmd(
            inv, "project", "restore", raw_file, *(["--name", name] if name else [])
        ),
        warning="" if name else "the project is named after the backup's own project name",
        snapshot="if_open",
        extra={"name": name or "(from the file)"},
        next=next_steps(("projects", "list the projects in this folder")),
    )

    def apply() -> None:
        ok = manager.RestoreProject(str(path), name) if name else manager.RestoreProject(str(path))
        if not ok:
            raise api_failed("RestoreProject", "Resolve refused the backup; the name may be taken")
        added = [n for n in manager.GetProjectListInCurrentFolder() if n not in before]
        plan.extra["restored_as"] = ", ".join(added) if added else "(unknown)"

    def verify() -> dict[str, object]:
        now = manager.GetProjectListInCurrentFolder()
        return (
            {"project": name if name in now else None} if name else {"projects in folder": len(now)}
        )

    return run_mutation(inv, session, plan, apply, verify)


def _databases(manager: ProjectManagerExtra) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return dict(manager.GetCurrentDatabase() or {}), [
        dict(d) for d in manager.GetDatabaseList() or []
    ]


def run_db_list(inv: Invocation) -> Result:
    current, listed = _databases(_manager(inv.connect()))
    rows = [
        {
            "name": d.get("DbName"),
            "type": d.get("DbType"),
            "ip": d.get("IpAddress"),
            "current": d.get("DbName") == current.get("DbName")
            and d.get("DbType") == current.get("DbType"),
        }
        for d in listed
    ]
    data: dict[str, object] = {
        "current": current.get("DbName"),
        "databases": rows or empty_state("databases", "in Resolve", {}),
    }
    return Result(data, next_steps(("project db use --help", "see how to switch databases")))


def run_db_use(inv: Invocation) -> Result:
    name = clean_name(inv.positionals["name"], "database name")
    db_type = text_arg(inv, "type")
    check_snapshot_flags(inv)
    session = inv.connect()
    manager = _manager(session)
    current, listed = _databases(manager)
    found = [d for d in listed if d.get("DbName") == name and db_type in (None, d.get("DbType"))]
    if not found:
        names = ", ".join(f"{d.get('DbName')} ({d.get('DbType')})" for d in listed) or "(none)"
        raise not_found("database", name, f"Databases in this Resolve: {names}")
    if len(found) > 1:
        kinds = ", ".join(str(d.get("DbType")) for d in found)
        raise AxiError(
            "VALIDATION_ERROR",
            f"more than one database is named {name}",
            help=[f"Pick one with --type ({kinds})"],
        )
    info = found[0]
    same = info.get("DbName") == current.get("DbName") and info.get("DbType") == current.get(
        "DbType"
    )
    open_name = current_name(session)
    plan = MutationPlan(
        command="project db use",
        target=f"database {name}",
        changes=[] if same else [Change("database", current.get("DbName"), name)],
        force_cmd=force_cmd(
            inv, "project", "db", "use", name, *(["--type", db_type] if db_type else [])
        ),
        warning="already the current database"
        if same
        else "closes the current project (snapshotted first) and swaps every project list",
        snapshot="if_open",
        extra={"closes_project": open_name},
        next=next_steps(("projects", "list the projects in the new database")),
    )

    def apply() -> None:
        if not manager.SetCurrentDatabase(info):
            raise api_failed("SetCurrentDatabase", f"could not switch to database {name}")

    def verify() -> dict[str, object]:
        return {"database": (manager.GetCurrentDatabase() or {}).get("DbName")}

    return run_mutation(inv, session, plan, apply, verify)


def _cloud_settings(inv: Invocation) -> dict[str, object]:
    """`--set k=v,...` checked against `CLOUD_KEYS`; `projectName` is required."""
    pairs = parse_assignments(text_arg(inv, "set") or "")
    unknown = [k for k in pairs if k not in CLOUD_KEYS]
    if unknown:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown cloud setting {', '.join(unknown)}",
            help=[f"Valid settings: {', '.join(CLOUD_KEYS)}"],
        )
    if not pairs.get("projectName"):
        raise AxiError(
            "VALIDATION_ERROR",
            "the cloud project needs a projectName",
            help=["Valid example: --set projectName=promo,isCollab=true"],
        )
    settings: dict[str, object] = {}
    for key, value in pairs.items():
        if key in ("isCollab", "isCameraAccess"):
            settings[key] = coerce_like(value, True)
        elif key == "syncMode":
            if value not in SYNC_MODES:
                raise AxiError(
                    "VALIDATION_ERROR",
                    f"unknown syncMode {value}",
                    help=[f"Valid values: {', '.join(SYNC_MODES)}"],
                )
            settings[key] = value
        else:
            settings[key] = value
    return settings


def _api_settings(session: Session, settings: dict[str, object]) -> dict[str, Any]:
    """The settings as the API takes them: `syncMode` becomes a `resolve.CLOUD_SYNC_*` value."""
    api: dict[str, Any] = dict(settings)
    if "syncMode" in api:
        constant = SYNC_MODES[str(api["syncMode"])]
        value = getattr(session.resolve, constant, None)
        if value is None:
            raise api_failed("syncMode", f"this Resolve has no {constant}")
        api["syncMode"] = value
    return api


def _run_cloud(verb: str, call: str, path_kind: str | None) -> Callable[[Invocation], Result]:
    opens = path_kind is None

    def run(inv: Invocation) -> Result:
        settings = _cloud_settings(inv)
        raw_path = inv.positionals.get("path")
        path = Path(raw_path).expanduser().resolve() if raw_path else None
        if path is not None and not (path.is_file() if path_kind == "file" else path.is_dir()):
            raise not_found(path_kind or "path", collapse_home(path), f"Check the {path_kind} path")
        check_snapshot_flags(inv)
        session = inv.connect()
        manager = _manager(session)
        name = str(settings["projectName"])
        words = ["project", "cloud", verb, *([raw_path] if raw_path else [])]
        words += ["--set", cast(str, inv.args["set"])]
        change = (
            Change("current project", current_name(session), name)
            if opens
            else Change("called", False, True)
        )
        plan = MutationPlan(
            command=f"project cloud {verb}",
            target=f"cloud project {name}",
            changes=[change],
            force_cmd=force_cmd(inv, *words),
            warning=f"{CLOUD_NOTE}" + ("; opening it closes the current project" if opens else ""),
            snapshot="if_open",
            extra={"settings": settings, **({"path": collapse_home(path)} if path else {})},
        )
        done = {"ok": False}

        def apply() -> None:
            api = _api_settings(session, settings)
            args = (str(path), api) if path else (api,)
            result = getattr(manager, call)(*args)
            if not result:
                raise api_failed(call, f"could not {verb} cloud project {name} ({CLOUD_NOTE})")
            done["ok"] = True

        def verify() -> dict[str, object]:
            if opens:
                return {"current project": current_name(session)}
            return {"called": done["ok"]}

        return run_mutation(inv, session, plan, apply, verify)

    return run


def _cloud_spec(verb: str, call: str, path_kind: str | None, summary: str) -> CommandSpec:
    positionals = (Positional("path", help=f"the {path_kind} to {verb}"),) if path_kind else ()
    set_flag = Flag(
        "set", "str", None, required=True, help=f"cloud settings, keys: {', '.join(CLOUD_KEYS)}"
    )
    sample = " ".join(
        filter(
            None,
            [
                "davinci-axi project cloud",
                verb,
                "~/promo.drp" if path_kind == "file" else "~/promo-folder" if path_kind else "",
            ],
        )
    )
    return CommandSpec(
        name=verb,
        summary=f"{summary} ({CLOUD_NOTE})",
        run=_run_cloud(verb, call, path_kind),
        positionals=positionals,
        flags=(*MUTATION_FLAGS, set_flag),
        examples=(
            f"{sample} --set projectName=promo",
            f"{sample} --set projectName=promo,isCollab=true --force",
        ),
        reads_only=False,
        methods=(*SAFETY_METHODS, f"ProjectManager.{call}"),
    )


RESTORE_SPEC = CommandSpec(
    name="restore",
    summary="Restore a project from a backup file as a new project (dry run until --force)",
    run=run_restore,
    positionals=(Positional("file", help="path of the project backup file"),),
    flags=(*MUTATION_FLAGS, Flag("name", "str", None, help="name for the restored project")),
    examples=(
        "davinci-axi project restore ~/backups/promo.drp",
        "davinci-axi project restore ~/backups/promo.drp --name promo-copy --force",
    ),
    reads_only=False,
    methods=(*SAFETY_METHODS, "ProjectManager.RestoreProject", *FOLDER_READS),
)

DB_LIST_SPEC = CommandSpec(
    name="list",
    summary="List the databases Resolve knows and mark the current one",
    run=run_db_list,
    examples=("davinci-axi project db list", "davinci-axi project db list --json"),
    methods=("ProjectManager.GetDatabaseList", "ProjectManager.GetCurrentDatabase"),
)
DB_USE_SPEC = CommandSpec(
    name="use",
    summary="Switch the current database; closes the open project (dry run until --force)",
    run=run_db_use,
    positionals=(Positional("name", help="database name from `project db list`"),),
    flags=(
        *MUTATION_FLAGS,
        Flag("type", "str", None, choices=("Disk", "PostgreSQL"), help="needed if names repeat"),
    ),
    examples=(
        "davinci-axi project db use Studio-DB",
        "davinci-axi project db use Studio-DB --force",
    ),
    reads_only=False,
    methods=(
        *SAFETY_METHODS,
        "ProjectManager.SetCurrentDatabase",
        "ProjectManager.GetDatabaseList",
        "ProjectManager.GetCurrentDatabase",
    ),
)
DB_SPEC = CommandSpec(
    name="db",
    summary="List and switch Resolve databases",
    subcommands={"list": DB_LIST_SPEC, "use": DB_USE_SPEC},
    examples=("davinci-axi project db list", "davinci-axi project db use Studio-DB --force"),
    reads_only=False,
    methods=tuple(dict.fromkeys((*DB_LIST_SPEC.methods, *DB_USE_SPEC.methods))),
)

_CLOUD = {
    "open": _cloud_spec("open", "LoadCloudProject", None, "Open a cloud project"),
    "create": _cloud_spec("create", "CreateCloudProject", None, "Create a cloud project"),
    "import": _cloud_spec("import", "ImportCloudProject", "file", "Import a cloud project file"),
    "restore": _cloud_spec("restore", "RestoreCloudProject", "folder", "Restore a cloud project"),
}
CLOUD_SPEC = CommandSpec(
    name="cloud",
    summary=f"Open, create, import and restore cloud projects ({CLOUD_NOTE})",
    subcommands=_CLOUD,
    examples=("davinci-axi project cloud create --set projectName=promo",),
    reads_only=False,
    methods=tuple(dict.fromkeys(m for s in _CLOUD.values() for m in s.methods)),
)

_PRESET_SPEC = build_kind_spec(SETTINGS_PRESETS, "preset")
SETTINGS_SPEC = replace(
    project.SETTINGS_SPEC,
    subcommands={**project.SETTINGS_SPEC.subcommands, "preset": _PRESET_SPEC},
    methods=tuple(dict.fromkeys((*project.SETTINGS_SPEC.methods, *_PRESET_SPEC.methods))),
)
_EXTRA = {"settings": SETTINGS_SPEC, "restore": RESTORE_SPEC, "db": DB_SPEC, "cloud": CLOUD_SPEC}

SPEC = replace(
    project.SPEC,
    subcommands={**project.SPEC.subcommands, **_EXTRA},
    methods=tuple(
        dict.fromkeys((*project.SPEC.methods, *(m for s in _EXTRA.values() for m in s.methods)))
    ),
)
