"""Media pool addressing plus the structure verbs: `media folder`, `import`, `move`, `delete`,
`relink` and `unlink` (U13).

Clips are addressed by unique id or by an exact, unique name (KTD20); folders by their path from
the root, such as `Master/Footage`. Several clips are one comma-separated positional (the flag
parser binds a fixed number of positionals); a name that itself contains a comma is tried whole
first, so it still works when it is the only clip. Every verb is a dry run until `--force` and
goes through `mutate.run_mutation`.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, cast

from davinci_axi.commands.item_detail import str_arg
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.output import hint
from davinci_axi.resolve_types import FolderApi, MediaPoolItemApi
from davinci_axi.resolve_types_edit import MediaPoolEdit
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

CLIPS = Positional("clips", help="clip ids or exact names, comma-separated")
FOLDER_PATH = Positional("path", help="folder path from the root, such as Master/Footage")
ONLINE, OFFLINE = "Online", "Offline"


@dataclass
class Ctx:
    """The connected session with the current project's media pool and its root folder."""

    session: Session
    pool: MediaPoolEdit
    root: FolderApi


@dataclass(frozen=True)
class Found:
    folder_path: str
    clip: MediaPoolItemApi


def open_pool(inv: Invocation) -> Ctx:
    """Refuse a bad `--no-snapshot`, connect, and return the project's media pool."""
    check_snapshot_flags(inv)
    session = inv.connect()
    pool = session.require_project().GetMediaPool()
    return Ctx(session, cast(MediaPoolEdit, pool), pool.GetRootFolder())


def walk_folders(root: FolderApi, prefix: str | None = None) -> Iterator[tuple[str, FolderApi]]:
    """Every folder under `root` (itself first) with its path, depth first."""
    path = prefix if prefix is not None else root.GetName()
    yield path, root
    for child in root.GetSubFolderList() or []:
        yield from walk_folders(child, f"{path}/{child.GetName()}")


def walk_clips(root: FolderApi) -> Iterator[Found]:
    for path, folder in walk_folders(root):
        for clip in folder.GetClipList() or []:
            yield Found(path, clip)


def folder_paths(ctx: Ctx) -> str:
    return ", ".join(path for path, _ in walk_folders(ctx.root))


def resolve_folder(ctx: Ctx, ref: str) -> tuple[str, FolderApi]:
    wanted = "/".join(part for part in ref.strip().split("/") if part)
    matches = [(path, folder) for path, folder in walk_folders(ctx.root) if path == wanted]
    if not matches:
        raise not_found("folder", ref, f"Folders in this media pool: {folder_paths(ctx)}")
    if len(matches) > 1:
        raise AxiError(
            "VALIDATION_ERROR",
            f"folder path {wanted} is ambiguous ({len(matches)} folders share it)",
            help=["Rename one of the folders in Resolve so the paths differ"],
        )
    return matches[0]


def current_folder(ctx: Ctx) -> tuple[str, FolderApi]:
    uid = ctx.pool.GetCurrentFolder().GetUniqueId()
    for path, folder in walk_folders(ctx.root):
        if folder.GetUniqueId() == uid:
            return path, folder
    return ctx.root.GetName(), ctx.root


def resolve_clip(ctx: Ctx, ref: str) -> Found:
    by_name: list[Found] = []
    for found in walk_clips(ctx.root):
        if found.clip.GetUniqueId() == ref:
            return found
        if found.clip.GetName() == ref:
            by_name.append(found)
    if len(by_name) == 1:
        return by_name[0]
    if by_name:
        raise AxiError(
            "VALIDATION_ERROR",
            f"clip name {ref} is ambiguous ({len(by_name)} clips share it)",
            help=[
                f"Use the clip id instead: {f.clip.GetUniqueId()} (in {f.folder_path})"
                for f in by_name
            ],
        )
    raise not_found("clip", ref, "Clip ids and names come from `davinci-axi media`")


def resolve_clips(ctx: Ctx, text: str) -> list[Found]:
    """One clip (the whole text first) or several comma-separated ones, without repeats."""
    whole = text.strip()
    try:
        return [resolve_clip(ctx, whole)]
    except AxiError as err:
        if err.code != "NOT_FOUND" or "," not in whole:
            raise
    found: list[Found] = []
    seen: set[str] = set()
    for part in (p.strip() for p in whole.split(",")):
        if not part:
            raise AxiError(
                "VALIDATION_ERROR",
                "an empty clip in the comma-separated list",
                help=["Valid example: A001_beach,A002_city"],
            )
        one = resolve_clip(ctx, part)
        uid = one.clip.GetUniqueId()
        if uid not in seen:
            seen.add(uid)
            found.append(one)
    return found


def where_is(ctx: Ctx, uid: str) -> str | None:
    for found in walk_clips(ctx.root):
        if found.clip.GetUniqueId() == uid:
            return found.folder_path
    return None


TARGET_NAMES = 5


def clips_target(found: list[Found]) -> str:
    if len(found) == 1:
        return f"clip {found[0].clip.GetName()} ({found[0].clip.GetUniqueId()})"
    names = [f.clip.GetName() for f in found]
    if len(names) > TARGET_NAMES:
        more = len(names) - TARGET_NAMES
        return f"{len(found)} clips ({', '.join(names[:TARGET_NAMES])} and {more} more)"
    return f"{len(found)} clips ({', '.join(names)})"


def field_names(found: list[Found], suffix: str = "") -> list[str]:
    """One change field per clip: its name, or name#id when names repeat in the selection."""
    names = [f.clip.GetName() for f in found]
    return [
        f"{f.clip.GetName()}#{f.clip.GetUniqueId()}{suffix}"
        if names.count(f.clip.GetName()) > 1
        else f"{f.clip.GetName()}{suffix}"
        for f in found
    ]


def show_hint(found: list[Found]) -> str:
    return hint(f"media show {found[0].clip.GetUniqueId()}", "show the first clip in detail")


def props(clip: MediaPoolItemApi) -> dict[str, Any]:
    live = clip.GetClipProperty()
    return live if isinstance(live, dict) else {}


def abs_path(text: str) -> str:
    return os.path.abspath(os.path.expanduser(text))


def missing(path: str, what: str) -> AxiError:
    return AxiError("NOT_FOUND", f"{what} {path} does not exist", help=["Check the path and retry"])


def folder_hint(path: str, purpose: str) -> str:
    return hint(f"media --folder {shlex.quote(path)}", purpose)


# ---- folders ------------------------------------------------------------------------------


def _folder_exists(name: str, path: str) -> AxiError:
    return AxiError(
        "FOLDER_EXISTS",
        f"folder {path} already exists",
        help=[f"Choose another name than {name}; this command never merges folders"],
    )


def _counts(root: FolderApi, path: str) -> tuple[int, int]:
    folders = list(walk_folders(root, path))
    return sum(len(f.GetClipList() or []) for _, f in folders), len(folders) - 1


def run_folder_add(inv: Invocation) -> Result:
    name = inv.positionals["name"].strip()
    if not name or "/" in name:
        raise AxiError(
            "VALIDATION_ERROR",
            "the folder name is empty or contains a /",
            help=["Valid example: davinci-axi media folder add Dailies"],
        )
    parent_ref = str_arg(inv, "parent")
    ctx = open_pool(inv)
    parent_path, parent = (
        resolve_folder(ctx, parent_ref) if parent_ref else (ctx.root.GetName(), ctx.root)
    )
    path = f"{parent_path}/{name}"
    if any(child.GetName() == name for child in parent.GetSubFolderList() or []):
        raise _folder_exists(name, path)
    words = ["media", "folder", "add", name, *(["--parent", parent_ref] if parent_ref else [])]
    plan = MutationPlan(
        command="media folder add",
        target=f"media pool folder {parent_path}",
        changes=[Change("folder", None, path)],
        force_cmd=force_cmd(inv, *words),
        next=[folder_hint(path, "list the new folder")],
    )

    def apply() -> None:
        previous = ctx.pool.GetCurrentFolder()
        if ctx.pool.AddSubFolder(parent, name) is None:
            raise api_failed("MediaPool.AddSubFolder", f"could not create folder {path}")
        # Resolve 21.1.1 makes the new folder current; put the previous one back (like import).
        if ctx.pool.GetCurrentFolder().GetUniqueId() != previous.GetUniqueId():
            plan.extra["current_folder_restored"] = bool(ctx.pool.SetCurrentFolder(previous))

    def verify() -> dict[str, object]:
        listed = [c.GetName() for c in parent.GetSubFolderList() or []]
        return {"folder": path if name in listed else None}

    return run_mutation(inv, ctx.session, plan, apply, verify)


def run_folder_delete(inv: Invocation) -> Result:
    ref = inv.positionals["path"]
    ctx = open_pool(inv)
    path, folder = resolve_folder(ctx, ref)
    if folder.GetUniqueId() == ctx.root.GetUniqueId():
        raise AxiError(
            "WRITE_REFUSED",
            f"the root folder {path} cannot be deleted",
            help=["Delete a sub folder instead, e.g. davinci-axi media folder delete Master/old"],
        )
    clips, subfolders = _counts(folder, path)
    plan = MutationPlan(
        command="media folder delete",
        target=f"media pool folder {path}",
        changes=[Change("folder", path, "deleted")],
        force_cmd=force_cmd(inv, "media", "folder", "delete", ref),
        irreversible=True,
        warning=(
            f"deletes folder {path} with {clips} clip(s) and {subfolders} sub folder(s) from "
            "the media pool; the snapshot is the only way back"
        ),
        next=[hint("media", "list what is left in the media pool")],
    )

    def apply() -> None:
        inside = {p for p, _ in walk_folders(folder, path)}
        if current_folder(ctx)[0] in inside:
            parent = path.rpartition("/")[0]
            ctx.pool.SetCurrentFolder(resolve_folder(ctx, parent)[1])
        if not ctx.pool.DeleteFolders([folder]):
            raise api_failed("MediaPool.DeleteFolders", f"folder {path} was not deleted")

    def verify() -> dict[str, object]:
        gone = path not in {p for p, _ in walk_folders(ctx.root)}
        return {"folder": "deleted" if gone else "still present"}

    return run_mutation(inv, ctx.session, plan, apply, verify)


def run_folder_move(inv: Invocation) -> Result:
    ref, to_ref = inv.positionals["path"], str(inv.args.get("to") or "")
    ctx = open_pool(inv)
    path, folder = resolve_folder(ctx, ref)
    to_path, to_folder = resolve_folder(ctx, to_ref)
    if folder.GetUniqueId() == ctx.root.GetUniqueId():
        raise AxiError(
            "WRITE_REFUSED",
            f"the root folder {path} cannot be moved",
            help=[
                "Name a folder below the root, for example:",
                "davinci-axi media folder move Master/Day1 --to Archive",
            ],
        )
    if to_path == path or to_path.startswith(path + "/"):
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot move folder {path} into itself ({to_path})",
            help=["Choose a target outside the folder"],
        )
    name = folder.GetName()
    new_path = f"{to_path}/{name}"
    parent_path = path.rpartition("/")[0]
    if parent_path != to_path and any(
        c.GetName() == name for c in to_folder.GetSubFolderList() or []
    ):
        raise _folder_exists(name, new_path)
    uid = folder.GetUniqueId()
    plan = MutationPlan(
        command="media folder move",
        target=f"media pool folder {path}",
        changes=[Change("folder", parent_path, to_path)] if parent_path != to_path else [],
        force_cmd=force_cmd(inv, "media", "folder", "move", ref, "--to", to_ref),
        next=[folder_hint(new_path, "list the moved folder")],
    )

    def apply() -> None:
        if not ctx.pool.MoveFolders([folder], to_folder):
            raise api_failed("MediaPool.MoveFolders", f"could not move folder {path}")

    def verify() -> dict[str, object]:
        for found_path, candidate in walk_folders(ctx.root):
            if candidate.GetUniqueId() == uid:
                return {"folder": found_path.rpartition("/")[0]}
        return {"folder": None}

    return run_mutation(inv, ctx.session, plan, apply, verify)


# ---- specs --------------------------------------------------------------------------------

POOL = "MediaPool."


def verb_spec(
    name: str,
    summary: str,
    run: Callable[[Invocation], Result],
    positionals: tuple[Positional, ...],
    flags: tuple[Flag, ...],
    examples: tuple[str, ...],
    methods: tuple[str, ...],
    notes: tuple[str, ...] = (),
) -> CommandSpec:
    return CommandSpec(
        name=name,
        summary=summary,
        run=run,
        positionals=positionals,
        flags=(*flags, *MUTATION_FLAGS),
        examples=examples,
        reads_only=False,
        methods=methods,
        notes=notes,
    )


FOLDER_ADD_SPEC = verb_spec(
    "add",
    "Create a folder in the media pool",
    run_folder_add,
    (Positional("name", help="name of the new folder"),),
    (
        Flag(
            "parent",
            "str",
            None,
            help="parent folder path (default: the root folder)",
            renamed_from="in",
        ),
    ),
    (
        "davinci-axi media folder add Dailies",
        "davinci-axi media folder add Day2 --parent Master --force",
    ),
    (POOL + "AddSubFolder", POOL + "GetCurrentFolder", POOL + "SetCurrentFolder"),
    (
        "Resolve 21.1.1 makes the new folder current; the command puts the previous folder "
        "back (current_folder_restored)",
    ),
)
FOLDER_DELETE_SPEC = verb_spec(
    "delete",
    "Delete a folder and everything in it (irreversible except through a snapshot)",
    run_folder_delete,
    (FOLDER_PATH,),
    (),
    (
        "davinci-axi media folder delete Master/Dailies",
        "davinci-axi media folder delete Master/Dailies --force",
    ),
    (POOL + "DeleteFolders", POOL + "SetCurrentFolder"),
)
FOLDER_MOVE_SPEC = verb_spec(
    "move",
    "Move a folder under another folder",
    run_folder_move,
    (FOLDER_PATH,),
    (Flag("to", "str", None, required=True, help="target folder path"),),
    (
        "davinci-axi media folder move Master/Dailies --to Master/Footage",
        "davinci-axi media folder move Master/Dailies --to Master/Footage --force",
    ),
    (POOL + "MoveFolders",),
)
FOLDER_SPEC = CommandSpec(
    name="folder",
    summary="Add, delete or move media pool folders",
    subcommands={"add": FOLDER_ADD_SPEC, "delete": FOLDER_DELETE_SPEC, "move": FOLDER_MOVE_SPEC},
    reads_only=False,
)
