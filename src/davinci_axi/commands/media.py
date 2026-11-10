"""`media`: list and show media pool clips, import, move, delete, relink and unlink them (U13).

`SPEC` is the `media` command: its own `run` (`media_list`) is the read view (R24, never writes),
`media show <clip>` (`media_show`) the detail view, and the subcommands are the guarded mutations.
Addressing and the folder verbs live in `media_folders`, the clip attribute verbs in `media_edit`,
import in `media_import`; every mutation is a dry run until `--force` and goes through
`mutate.run_mutation`.
"""

from __future__ import annotations

import os

from davinci_axi.commands.media_edit import (
    COLOR_SPEC,
    FLAG_SPEC,
    MARKER_SPEC,
    METADATA_SPEC,
    PROXY_SPEC,
    RENAME_SPEC,
)
from davinci_axi.commands.media_folders import (
    CLIPS,
    FOLDER_SPEC,
    OFFLINE,
    ONLINE,
    POOL,
    Found,
    abs_path,
    clips_target,
    field_names,
    folder_hint,
    missing,
    open_pool,
    props,
    resolve_clips,
    resolve_folder,
    show_hint,
    verb_spec,
    where_is,
)
from davinci_axi.commands.media_import import IMPORT_SPEC
from davinci_axi.commands.media_list import LIST_VIEW_FLAGS, run
from davinci_axi.commands.media_show import SHOW_SPEC
from davinci_axi.errors import api_failed
from davinci_axi.mutate import Change, MutationPlan, force_cmd, run_mutation
from davinci_axi.output import hint
from davinci_axi.spec import LIST_FLAGS, CommandSpec, Flag, Invocation, Result

# ---- move, delete, relink, unlink ---------------------------------------------------------


def run_move(inv: Invocation) -> Result:
    to_ref = str(inv.args.get("to") or "")
    ctx = open_pool(inv)
    found = resolve_clips(ctx, inv.positionals["clips"])
    to_path, to_folder = resolve_folder(ctx, to_ref)
    moving = [f for f in found if f.folder_path != to_path]
    fields = field_names(moving)
    plan = MutationPlan(
        command="media move",
        target=clips_target(found),
        changes=[Change(n, f.folder_path, to_path) for n, f in zip(fields, moving, strict=True)],
        force_cmd=force_cmd(inv, "media", "move", inv.positionals["clips"], "--to", to_ref),
        next=[folder_hint(to_path, "list the clips in that folder")],
    )

    def apply() -> None:
        if not ctx.pool.MoveClips([f.clip for f in moving], to_folder):
            raise api_failed("MediaPool.MoveClips", f"could not move the clips to {to_path}")

    def verify() -> dict[str, object]:
        return {n: where_is(ctx, f.clip.GetUniqueId()) for n, f in zip(fields, moving, strict=True)}

    return run_mutation(inv, ctx.session, plan, apply, verify)


def run_delete(inv: Invocation) -> Result:
    ctx = open_pool(inv)
    found = resolve_clips(ctx, inv.positionals["clips"])
    fields = field_names(found)
    plan = MutationPlan(
        command="media delete",
        target=clips_target(found),
        changes=[Change(n, f.folder_path, "deleted") for n, f in zip(fields, found, strict=True)],
        force_cmd=force_cmd(inv, "media", "delete", inv.positionals["clips"]),
        irreversible=True,
        warning=(
            f"deletes {clips_target(found)} from the media pool (the source files on disk are "
            "untouched); the snapshot is the only way back"
        ),
        next=[hint("media", "list the remaining clips")],
    )

    def apply() -> None:
        if not ctx.pool.DeleteClips([f.clip for f in found]):
            raise api_failed("MediaPool.DeleteClips", "the clips were not deleted")

    def verify() -> dict[str, object]:
        return {
            n: "still present" if where_is(ctx, f.clip.GetUniqueId()) else "deleted"
            for n, f in zip(fields, found, strict=True)
        }

    return run_mutation(inv, ctx.session, plan, apply, verify)


def run_relink(inv: Invocation) -> Result:
    folder = str(inv.args.get("path") or "")
    target_dir = abs_path(folder).rstrip("/") or "/"
    if not os.path.isdir(target_dir):
        raise missing(folder, "folder")
    ctx = open_pool(inv)
    found = resolve_clips(ctx, inv.positionals["clips"])
    todo: list[tuple[str, Found, str, str]] = []
    for name, one in zip(field_names(found, " path"), found, strict=True):
        before = str(props(one.clip).get("File Path") or "")
        after = os.path.join(target_dir, os.path.basename(before)) if before else target_dir
        if before != after:
            todo.append((name, one, before, after))
    plan = MutationPlan(
        command="media relink",
        target=clips_target(found),
        changes=[Change(name, before, after) for name, _, before, after in todo],
        force_cmd=force_cmd(inv, "media", "relink", inv.positionals["clips"], "--path", folder),
        next=[show_hint(found)],
    )

    def apply() -> None:
        if not ctx.pool.RelinkClips([one.clip for _, one, _, _ in todo], target_dir):
            raise api_failed("MediaPool.RelinkClips", f"could not relink the clips to {target_dir}")

    def verify() -> dict[str, object]:
        live: dict[str, object] = {}
        for name, one, _, after in todo:
            now = str(props(one.clip).get("File Path") or "")
            live[name] = after if now.startswith(target_dir) else now
        return live

    return run_mutation(inv, ctx.session, plan, apply, verify)


def run_unlink(inv: Invocation) -> Result:
    ctx = open_pool(inv)
    found = resolve_clips(ctx, inv.positionals["clips"])
    fields = field_names(found, " status")
    todo = [
        (n, f, str(props(f.clip).get("Online Status") or ONLINE))
        for n, f in zip(fields, found, strict=True)
    ]
    todo = [t for t in todo if t[2] != OFFLINE]
    plan = MutationPlan(
        command="media unlink",
        target=clips_target(found),
        changes=[Change(n, before, OFFLINE) for n, _, before in todo],
        force_cmd=force_cmd(inv, "media", "unlink", inv.positionals["clips"]),
        warning="unlinked clips go offline until you run `media relink` on them",
        next=[show_hint(found)],
    )

    def apply() -> None:
        if not ctx.pool.UnlinkClips([f.clip for _, f, _ in todo]):
            raise api_failed("MediaPool.UnlinkClips", "the clips were not unlinked")

    def verify() -> dict[str, object]:
        return {n: str(props(f.clip).get("Online Status") or ONLINE) for n, f, _ in todo}

    return run_mutation(inv, ctx.session, plan, apply, verify)


MOVE_SPEC = verb_spec(
    "move",
    "Move clips to another media pool folder",
    run_move,
    (CLIPS,),
    (Flag("to", "str", None, required=True, help="target folder path"),),
    (
        "davinci-axi media move A001_beach --to Master",
        "davinci-axi media move A001_beach,A002_city --to Master/Selects --force",
    ),
    (POOL + "MoveClips",),
)
DELETE_SPEC = verb_spec(
    "delete",
    "Delete clips from the media pool (irreversible except through a snapshot)",
    run_delete,
    (CLIPS,),
    (),
    ("davinci-axi media delete music_bed", "davinci-axi media delete music_bed --force"),
    (POOL + "DeleteClips",),
)
RELINK_SPEC = verb_spec(
    "relink",
    "Point clips at the same file names inside another folder on disk",
    run_relink,
    (CLIPS,),
    (Flag("path", "str", None, required=True, help="folder on disk that holds the files"),),
    (
        "davinci-axi media relink A001_beach --path /Volumes/new/footage",
        "davinci-axi media relink A001_beach,A002_city --path /Volumes/new --force",
    ),
    (POOL + "RelinkClips",),
)
UNLINK_SPEC = verb_spec(
    "unlink",
    "Unlink clips from their files on disk (they go offline; relink restores them)",
    run_unlink,
    (CLIPS,),
    (),
    ("davinci-axi media unlink A001_beach", "davinci-axi media unlink A001_beach --force"),
    (POOL + "UnlinkClips",),
)


SUBCOMMANDS: dict[str, CommandSpec] = {
    "show": SHOW_SPEC,
    "import": IMPORT_SPEC,
    "rename": RENAME_SPEC,
    "move": MOVE_SPEC,
    "delete": DELETE_SPEC,
    "relink": RELINK_SPEC,
    "unlink": UNLINK_SPEC,
    "metadata": METADATA_SPEC,
    "color": COLOR_SPEC,
    "flag": FLAG_SPEC,
    "marker": MARKER_SPEC,
    "proxy": PROXY_SPEC,
    "folder": FOLDER_SPEC,
}


def _all_methods() -> tuple[str, ...]:
    found: dict[str, None] = dict.fromkeys(
        (
            "Project.GetMediaPool",
            "MediaPool.GetRootFolder",
            "MediaPool.GetCurrentFolder",
            "Folder.GetClipList",
            "Folder.GetSubFolderList",
            "Folder.GetIsFolderStale",
            "Folder.GetName",
            "Folder.GetUniqueId",
            "MediaPoolItem.GetName",
            "MediaPoolItem.GetUniqueId",
        )
    )
    stack = list(SUBCOMMANDS.values())
    while stack:
        spec = stack.pop(0)
        found.update(dict.fromkeys(spec.methods))
        stack.extend(spec.subcommands.values())
    return tuple(found)


SPEC = CommandSpec(
    name="media",
    summary="List and show media pool clips, or change them (dry run until --force)",
    run=run,
    flags=(*LIST_VIEW_FLAGS, *LIST_FLAGS),
    examples=(
        "davinci-axi media",
        "davinci-axi media --folder Master/Footage --no-recursive --fields name,fps,path",
        "davinci-axi media show A001_beach",
        "davinci-axi media --fields name,resolution,frames,date_created --sort date_created",
        "davinci-axi media rename A001_beach --to beach_wide --force",
    ),
    subcommands=SUBCOMMANDS,
    reads_only=False,
    methods=_all_methods(),
)
