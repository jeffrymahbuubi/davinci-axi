"""`color version|group|cdl|cache|node-colors`: the colour state that belongs to an item or group.

Versions belong to an item (`versionType` 0 local, 1 `--remote`); groups belong to the project and
share a pre-clip and a post-clip grade. Every change is a dry run until `--force`
(`mutate.run_mutation`); item verbs honour `--timeline` like the phase-2 item verbs. CDL has no
getter, so `cdl set` verifies with the call's return value only (KTD23).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from davinci_axi.commands.color_common import (
    ColorGroupApi,
    ColorProject,
    color_item,
    item_write,
    parent,
    project_write,
    verb,
)
from davinci_axi.commands.item_markers import ITEM_FLAGS, ITEM_ID, open_target
from davinci_axi.commands.items import TIMELINE_FLAG
from davinci_axi.errors import AxiError, not_found
from davinci_axi.mutate import MUTATION_FLAGS, Change, check_snapshot_flags
from davinci_axi.output import empty_state, hint
from davinci_axi.resolve_types import TimelineItemApi
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

REMOTE = Flag("remote", "bool", False, help="remote versions instead of local ones")
VERSION_FLAGS: tuple[Flag, ...] = (*ITEM_FLAGS, REMOTE)
NAME = Positional("name", help="the version name")
GROUP = Positional("name", help="the colour group name")
TO = Flag("to", "str", None, required=True, help="the new name")
SWITCH = "Project.GetCurrentTimeline Project.SetCurrentTimeline"
VERSION_READS = f"TimelineItem.GetVersionNameList TimelineItem.GetCurrentVersion {SWITCH}"
GROUP_READS = "Project.GetColorGroupsList ColorGroup.GetName"


def _vtype(inv: Invocation) -> int:
    return 1 if inv.args.get("remote") else 0


def _kind(vtype: int) -> str:
    return "remote" if vtype else "local"


def _remote_words(vtype: int) -> list[str]:
    return ["--remote"] if vtype else []


def _names(item: TimelineItemApi, vtype: int) -> list[str]:
    return list(item.GetVersionNameList(vtype) or [])


def _clean(text: str, what: str) -> str:
    name = text.strip()
    if not name:
        raise AxiError("VALIDATION_ERROR", f"the {what} name is empty", help=["Pass a name"])
    return name


def _exists(code: str, what: str, name: str) -> AxiError:
    return AxiError(code, f"{what} {name} already exists", help=["Choose another name"])


def _missing_version(name: str, vtype: int, names: list[str]) -> AxiError:
    listing = ", ".join(names) or "(none)"
    return not_found("colour version", name, f"{_kind(vtype)} versions: {listing}")


def run_version_list(inv: Invocation) -> Result:
    vtype = _vtype(inv)
    target = open_target(inv)
    item = color_item(target)
    current = item.GetCurrentVersion() or {}
    live = current.get("versionName") if current.get("versionType") == vtype else None
    rows = [{"name": n, "current": n == live} for n in _names(item, vtype)]
    empty = empty_state("versions", "on this item", {"type": _kind(vtype)})
    return Result({"item": target.label, "type": _kind(vtype), "versions": rows or empty})


def run_version_add(inv: Invocation) -> Result:
    name, vtype = _clean(inv.positionals["name"], "version"), _vtype(inv)
    target = open_target(inv)
    item = color_item(target)
    if name in _names(item, vtype):
        raise _exists("VERSION_EXISTS", "colour version", name)
    return item_write(
        inv,
        target,
        command="color version add",
        words=["color", "version", "add", target.ref, name, *_remote_words(vtype)],
        changes=[Change("version", None, name)],
        call=lambda: item.AddVersion(name, vtype),
        read=lambda: {"version": name if name in _names(item, vtype) else None},
        fail="TimelineItem.AddVersion",
        extra={"type": _kind(vtype)},
    )


def run_version_load(inv: Invocation) -> Result:
    name, vtype = inv.positionals["name"], _vtype(inv)
    target = open_target(inv)
    item = color_item(target)
    if name not in _names(item, vtype):
        raise _missing_version(name, vtype, _names(item, vtype))
    now = item.GetCurrentVersion() or {}
    same = now.get("versionName") == name and now.get("versionType") == vtype
    return item_write(
        inv,
        target,
        command="color version load",
        words=["color", "version", "load", target.ref, name, *_remote_words(vtype)],
        changes=[] if same else [Change("current_version", now.get("versionName"), name)],
        call=lambda: item.LoadVersionByName(name, vtype),
        read=lambda: {"current_version": (item.GetCurrentVersion() or {}).get("versionName")},
        fail="TimelineItem.LoadVersionByName",
        extra={"type": _kind(vtype)},
    )


def run_version_rename(inv: Invocation) -> Result:
    old, vtype = inv.positionals["name"], _vtype(inv)
    new = _clean(str(inv.args.get("to") or ""), "new version")
    target = open_target(inv)
    item = color_item(target)
    names = _names(item, vtype)
    if old not in names:
        raise _missing_version(old, vtype, names)
    if new in names and new != old:
        raise _exists("VERSION_EXISTS", "colour version", new)

    def read() -> dict[str, object]:
        live = _names(item, vtype)
        return {"version": new if new in live and old not in live else old}

    return item_write(
        inv,
        target,
        command="color version rename",
        words=["color", "version", "rename", target.ref, old, "--to", new, *_remote_words(vtype)],
        changes=[Change("version", old, new)] if old != new else [],
        call=lambda: item.RenameVersionByName(old, new, vtype),
        read=read,
        fail="TimelineItem.RenameVersionByName",
        extra={"type": _kind(vtype)},
    )


def run_version_delete(inv: Invocation) -> Result:
    name, vtype = inv.positionals["name"], _vtype(inv)
    target = open_target(inv)
    item = color_item(target)
    if name not in _names(item, vtype):
        raise _missing_version(name, vtype, _names(item, vtype))
    return item_write(
        inv,
        target,
        command="color version delete",
        words=["color", "version", "delete", target.ref, name, *_remote_words(vtype)],
        changes=[Change("version", name, "deleted")],
        call=lambda: item.DeleteVersionByName(name, vtype),
        read=lambda: {"version": "still present" if name in _names(item, vtype) else "deleted"},
        fail="TimelineItem.DeleteVersionByName",
        irreversible=True,
        warning=f"deletes the {_kind(vtype)} colour version {name}; "
        "the snapshot is the only way back",
        extra={"type": _kind(vtype)},
    )


def _groups(session: Session) -> list[ColorGroupApi]:
    return cast(list[ColorGroupApi], session.require_project().GetColorGroupsList() or [])


def _group_names(session: Session) -> list[str]:
    return [g.GetName() for g in _groups(session)]


def _find(groups: list[ColorGroupApi], name: str) -> ColorGroupApi:
    for group in groups:
        if group.GetName() == name:
            return group
    listing = ", ".join(g.GetName() for g in groups) or "(none)"
    raise not_found("color group", name, f"Color groups in this project: {listing}")


def _project_setup(inv: Invocation) -> tuple[Session, ColorProject]:
    check_snapshot_flags(inv)
    session = inv.connect()
    return session, cast(ColorProject, session.require_project())


def run_group_list(inv: Invocation) -> Result:
    session = inv.connect()
    name = inv.args.get("timeline")
    timeline = session.require_timeline(name if isinstance(name, str) and name else None)
    rows = [
        {"name": g.GetName(), "clips": len(g.GetClipsInTimeline(timeline) or [])}
        for g in _groups(session)
    ]
    empty = empty_state("color groups", "in this project", {})
    return Result({"timeline": timeline.GetName(), "count": len(rows), "groups": rows or empty})


def run_group_add(inv: Invocation) -> Result:
    name = _clean(inv.positionals["name"], "group")
    session, project = _project_setup(inv)
    if name in _group_names(session):
        raise _exists("GROUP_EXISTS", "color group", name)
    return project_write(
        inv,
        session,
        command="color group add",
        words=["color", "group", "add", name],
        changes=[Change("group", None, name)],
        call=lambda: project.AddColorGroup(name) is not None,
        read=lambda: {"group": name if name in _group_names(session) else None},
        fail="Project.AddColorGroup",
        next_lines=[hint("color group list", "list the colour groups")],
    )


def run_group_rename(inv: Invocation) -> Result:
    old, new = inv.positionals["name"], _clean(str(inv.args.get("to") or ""), "new group")
    session, _ = _project_setup(inv)
    group = _find(_groups(session), old)
    if new != old and new in _group_names(session):
        raise _exists("GROUP_EXISTS", "color group", new)
    return project_write(
        inv,
        session,
        command="color group rename",
        words=["color", "group", "rename", old, "--to", new],
        changes=[Change("group", old, new)] if old != new else [],
        call=lambda: group.SetName(new),
        read=lambda: {"group": group.GetName()},
        fail="ColorGroup.SetName",
    )


def run_group_delete(inv: Invocation) -> Result:
    name = inv.positionals["name"]
    session, project = _project_setup(inv)
    group = _find(_groups(session), name)
    return project_write(
        inv,
        session,
        command="color group delete",
        words=["color", "group", "delete", name],
        changes=[Change("group", name, "deleted")],
        call=lambda: project.DeleteColorGroup(group),
        read=lambda: {"group": "still present" if name in _group_names(session) else "deleted"},
        fail="Project.DeleteColorGroup",
        irreversible=True,
        warning=f"deletes color group {name} and leaves its clips ungrouped; "
        "the snapshot is the only way back",
        next_lines=[hint("color group list", "list the remaining colour groups")],
    )


def _group_name(item: Any) -> str | None:
    group = item.GetColorGroup()
    return None if group is None else str(group.GetName())


def run_group_assign(inv: Invocation) -> Result:
    name = inv.positionals["name"]
    target = open_target(inv)
    item = color_item(target)
    group = _find(_groups(target.session), name)
    before = _group_name(item)
    return item_write(
        inv,
        target,
        command="color group assign",
        words=["color", "group", "assign", target.ref, name],
        changes=[Change("group", before, name)] if before != name else [],
        call=lambda: item.AssignToColorGroup(group),
        read=lambda: {"group": _group_name(item)},
        fail="TimelineItem.AssignToColorGroup",
    )


def run_group_unassign(inv: Invocation) -> Result:
    target = open_target(inv)
    item = color_item(target)
    before = _group_name(item)
    return item_write(
        inv,
        target,
        command="color group unassign",
        words=["color", "group", "unassign", target.ref],
        changes=[Change("group", before, None)] if before is not None else [],
        call=item.RemoveFromColorGroup,
        read=lambda: {"group": _group_name(item)},
        fail="TimelineItem.RemoveFromColorGroup",
    )


def _version(
    name: str, summary: str, run: Callable[[Invocation], Result], write: str, *extra: Any
) -> CommandSpec:
    read = name == "list"
    methods = VERSION_READS if read else f"{VERSION_READS} TimelineItem.{write}"
    positionals = (ITEM_ID,) if read else (ITEM_ID, NAME)
    flags = (TIMELINE_FLAG, REMOTE) if read else (*VERSION_FLAGS, *extra)
    example = f"davinci-axi color version {name} V1:1" + ("" if read else " alt")
    return verb(name, summary, run, positionals, flags, example, methods, read=read)


VERSION_SPEC = parent(
    "version",
    "List, add, load, rename or delete the colour versions of an item",
    {
        "list": _version(
            "list", "List the colour versions of an item (read-only)", run_version_list, ""
        ),
        "add": _version("add", "Add a colour version to an item", run_version_add, "AddVersion"),
        "load": _version("load", "Make a version current", run_version_load, "LoadVersionByName"),
        "rename": _version(
            "rename", "Rename a colour version", run_version_rename, "RenameVersionByName", TO
        ),
        "delete": _version(
            "delete",
            "Delete a colour version (irreversible except through a snapshot)",
            run_version_delete,
            "DeleteVersionByName",
        ),
    },
    "davinci-axi color version list V1:1|davinci-axi color version add V1:1 alt --force",
)


def _group(
    name: str,
    summary: str,
    run: Callable[[Invocation], Result],
    positionals: tuple[Positional, ...],
    flags: tuple[Flag, ...],
    methods: str,
) -> CommandSpec:
    read = name == "list"
    words = "".join(" Hero" if p is GROUP else " V1:1" for p in positionals)
    example = f"davinci-axi color group {name}{words}"
    return verb(
        name, summary, run, positionals, flags, example, f"{GROUP_READS} {methods}", read=read
    )


GROUP_SPEC = parent(
    "group",
    "List, add, rename or delete colour groups, and assign items to them",
    {
        "list": _group(
            "list",
            "List the colour groups (read-only)",
            run_group_list,
            (),
            (TIMELINE_FLAG,),
            "ColorGroup.GetClipsInTimeline",
        ),
        "add": _group(
            "add",
            "Add a colour group",
            run_group_add,
            (GROUP,),
            MUTATION_FLAGS,
            "Project.AddColorGroup",
        ),
        "rename": _group(
            "rename",
            "Rename a colour group",
            run_group_rename,
            (GROUP,),
            (*MUTATION_FLAGS, TO),
            "ColorGroup.SetName",
        ),
        "delete": _group(
            "delete",
            "Delete a colour group (irreversible except through a snapshot)",
            run_group_delete,
            (GROUP,),
            MUTATION_FLAGS,
            "Project.DeleteColorGroup",
        ),
        "assign": _group(
            "assign",
            "Assign an item to a colour group",
            run_group_assign,
            (ITEM_ID, GROUP),
            ITEM_FLAGS,
            f"{SWITCH} TimelineItem.GetColorGroup TimelineItem.AssignToColorGroup",
        ),
        "unassign": _group(
            "unassign",
            "Remove an item from its colour group",
            run_group_unassign,
            (ITEM_ID,),
            ITEM_FLAGS,
            f"{SWITCH} TimelineItem.GetColorGroup TimelineItem.RemoveFromColorGroup",
        ),
    },
    "davinci-axi color group list|davinci-axi color group assign V1:1 Hero --force",
)
SUBCOMMANDS: dict[str, CommandSpec] = {"version": VERSION_SPEC, "group": GROUP_SPEC}
