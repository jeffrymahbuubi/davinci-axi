"""`storage mattes` and `storage timeline-mattes`: clip mattes and timeline mattes (U21).

Clip mattes belong to one media pool clip (`GetClipMatteList` returns their file paths); timeline
mattes are media pool items of a folder. Listing only reads; adding changes the project (dry run
until `--force`); deleting a clip matte is irreversible except through the snapshot (R32).
"""

from __future__ import annotations

import shlex
from typing import cast

from davinci_axi.commands.item_detail import str_arg
from davinci_axi.commands.media_edit import CLIP
from davinci_axi.commands.media_folders import (
    Ctx,
    Found,
    clips_target,
    current_folder,
    folder_hint,
    open_pool,
    resolve_clip,
    resolve_folder,
    show_hint,
    verb_spec,
)
from davinci_axi.commands.storage_common import (
    STORAGE,
    StoragePool,
    names_of,
    split_paths,
    storage_of,
)
from davinci_axi.errors import api_failed, not_found
from davinci_axi.mutate import Change, MutationPlan, force_cmd, run_mutation
from davinci_axi.output import empty_state, hint
from davinci_axi.resolve_types import FolderApi
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

POOL = "MediaPool."
PATHS = Positional("paths", help="absolute matte file paths, comma-separated")
FOLDER_FLAG = Flag("folder", "str", None, help="media pool folder path (default: current folder)")
EYES = ("left", "right")


def _open(inv: Invocation) -> tuple[Ctx, Found, StoragePool]:
    ctx = open_pool(inv)
    return ctx, resolve_clip(ctx, inv.positionals["clip"]), cast(StoragePool, ctx.pool)


def _mattes(pool: StoragePool, found: Found) -> list[str]:
    return [str(p) for p in pool.GetClipMatteList(found.clip) or []]


def run_list(inv: Invocation) -> Result:
    _, found, pool = _open(inv)
    paths = _mattes(pool, found)
    name = found.clip.GetName()
    data: dict[str, object] = {"clip": name, "id": found.clip.GetUniqueId()}
    data["mattes"] = (
        [{"path": p} for p in paths] if paths else empty_state("mattes", f"on clip {name}", {})
    )
    return Result(data, [show_hint([found])])


def run_add(inv: Invocation) -> Result:
    paths = split_paths(inv.positionals["paths"], "matte paths")
    eye = str_arg(inv, "eye")
    ctx, found, pool = _open(inv)
    storage = storage_of(ctx.session)
    before = _mattes(pool, found)
    new = [p for p in paths if p not in before]
    words = ["storage", "mattes", "add", found.clip.GetName(), inv.positionals["paths"]]
    plan = MutationPlan(
        command="storage mattes add",
        target=clips_target([found]),
        changes=[Change("mattes", len(before), len(before) + len(new))] if new else [],
        force_cmd=force_cmd(inv, *words, *(["--eye", eye] if eye else [])),
        extra={"adding": ", ".join(new)} if new else {},
        next=[hint(f"storage mattes list {shlex.quote(found.clip.GetName())}", "list the mattes")],
    )

    def apply() -> None:
        ok = (
            storage.AddClipMattesToMediaPool(found.clip, new)
            if eye is None
            else storage.AddClipMattesToMediaPool(found.clip, new, eye)
        )
        if not ok:
            raise api_failed(
                "MediaStorage.AddClipMattesToMediaPool",
                "Resolve added no matte; check the files are visible in Media Storage",
            )

    return run_mutation(
        inv, ctx.session, plan, apply, lambda: {"mattes": len(_mattes(pool, found))}
    )


def run_delete(inv: Invocation) -> Result:
    paths = split_paths(inv.positionals["paths"], "matte paths")
    ctx, found, pool = _open(inv)
    before = _mattes(pool, found)
    unknown = [p for p in paths if p not in before]
    if unknown:
        listing = ", ".join(before) if before else "(none)"
        raise not_found("matte", ", ".join(unknown), f"Mattes on this clip: {listing}")
    plan = MutationPlan(
        command="storage mattes delete",
        target=clips_target([found]),
        changes=[Change("mattes", len(before), len(before) - len(paths))],
        force_cmd=force_cmd(
            inv, "storage", "mattes", "delete", found.clip.GetName(), inv.positionals["paths"]
        ),
        irreversible=True,
        warning="removes the mattes from the clip; the snapshot is the only way back",
        extra={"deleting": ", ".join(paths)},
        next=[
            hint(f"storage mattes list {shlex.quote(found.clip.GetName())}", "list what is left")
        ],
    )

    def apply() -> None:
        if not pool.DeleteClipMattes(found.clip, paths):
            raise api_failed("MediaPool.DeleteClipMattes", "Resolve deleted no matte")

    return run_mutation(
        inv, ctx.session, plan, apply, lambda: {"mattes": len(_mattes(pool, found))}
    )


def _folder(ctx: Ctx, inv: Invocation) -> tuple[str, FolderApi, str | None]:
    ref = str_arg(inv, "folder")
    path, folder = resolve_folder(ctx, ref) if ref else current_folder(ctx)
    return path, folder, ref


def run_timeline_list(inv: Invocation) -> Result:
    ctx = open_pool(inv)
    path, folder, _ = _folder(ctx, inv)
    items = list(cast(StoragePool, ctx.pool).GetTimelineMatteList(folder) or [])
    data: dict[str, object] = {"folder": path}
    data["mattes"] = (
        [{"id": i.GetUniqueId(), "name": i.GetName()} for i in items]
        if items
        else empty_state("timeline mattes", f"in folder {path}", {})
    )
    return Result(data, [folder_hint(path, "list the clips in that folder")])


def run_timeline_add(inv: Invocation) -> Result:
    paths = split_paths(inv.positionals["paths"], "matte paths")
    ctx = open_pool(inv)
    pool = cast(StoragePool, ctx.pool)
    storage = storage_of(ctx.session)
    path, folder, ref = _folder(ctx, inv)
    plan = MutationPlan(
        command="storage timeline-mattes add",
        target=f"{len(paths)} matte file(s) into folder {path}",
        changes=[Change("mattes", None, "added")],
        force_cmd=force_cmd(
            inv,
            "storage",
            "timeline-mattes",
            "add",
            inv.positionals["paths"],
            *(["--folder", ref] if ref else []),
        ),
        extra={"folder": path},
        next=[hint("storage timeline-mattes list", "list the timeline mattes")],
    )
    added: list[str] = []

    def apply() -> None:
        previous = ctx.pool.GetCurrentFolder()
        switched = previous.GetUniqueId() != folder.GetUniqueId()
        if switched and not ctx.pool.SetCurrentFolder(folder):
            raise api_failed("MediaPool.SetCurrentFolder", f"could not open folder {path}")
        try:
            got = storage.AddTimelineMattesToMediaPool(paths)
        finally:
            if switched:
                ctx.pool.SetCurrentFolder(previous)
        if not got:
            raise api_failed("MediaStorage.AddTimelineMattesToMediaPool", "no matte was added")
        added.extend(i.GetUniqueId() for i in got)
        plan.extra["added"] = names_of(list(got))
        if switched:
            plan.extra["folder_restored"] = True

    def verify() -> dict[str, object]:
        present = {i.GetUniqueId() for i in pool.GetTimelineMatteList(folder) or []}
        missing = [uid for uid in added if uid not in present]
        return {"mattes": "added" if added and not missing else f"{len(missing)} not in folder"}

    return run_mutation(inv, ctx.session, plan, apply, verify)


CLIP_FLAG_EYE = Flag("eye", "str", None, choices=EYES, help="stereo eye for a stereo clip")

LIST_SPEC = CommandSpec(
    name="list",
    summary="List the mattes of one media pool clip",
    run=run_list,
    positionals=(CLIP,),
    examples=("davinci-axi storage mattes list A001_beach",),
    methods=(POOL + "GetClipMatteList",),
)
ADD_SPEC = verb_spec(
    "add",
    "Add matte files to a clip",
    run_add,
    (CLIP, PATHS),
    (CLIP_FLAG_EYE,),
    (
        "davinci-axi storage mattes add A001_beach /Volumes/Media/m.png",
        "davinci-axi storage mattes add A001_beach /Volumes/Media/m.png --eye left --force",
    ),
    ("Resolve.GetMediaStorage", STORAGE + "AddClipMattesToMediaPool", POOL + "GetClipMatteList"),
)
DELETE_SPEC = verb_spec(
    "delete",
    "Delete matte files from a clip (irreversible except through a snapshot)",
    run_delete,
    (CLIP, PATHS),
    (),
    (
        "davinci-axi storage mattes delete A001_beach /Volumes/Media/m.png",
        "davinci-axi storage mattes delete A001_beach /Volumes/Media/m.png --force",
    ),
    (POOL + "GetClipMatteList", POOL + "DeleteClipMattes"),
)
MATTES_SPEC = CommandSpec(
    name="mattes",
    summary="List, add or delete the mattes of a clip",
    subcommands={"list": LIST_SPEC, "add": ADD_SPEC, "delete": DELETE_SPEC},
    examples=("davinci-axi storage mattes list A001_beach",),
    reads_only=False,
    methods=tuple(dict.fromkeys(m for s in (LIST_SPEC, ADD_SPEC, DELETE_SPEC) for m in s.methods)),
)

TIMELINE_LIST_SPEC = CommandSpec(
    name="list",
    summary="List the timeline mattes of a media pool folder",
    run=run_timeline_list,
    flags=(FOLDER_FLAG,),
    examples=(
        "davinci-axi storage timeline-mattes list",
        "davinci-axi storage timeline-mattes list --folder Master/Footage",
    ),
    methods=(POOL + "GetTimelineMatteList", POOL + "GetCurrentFolder"),
)
TIMELINE_ADD_SPEC = verb_spec(
    "add",
    "Add media files as timeline mattes (dry run until --force)",
    run_timeline_add,
    (PATHS,),
    (FOLDER_FLAG,),
    (
        "davinci-axi storage timeline-mattes add /Volumes/Media/matte.mov",
        "davinci-axi storage timeline-mattes add /Volumes/Media/matte.mov --force",
    ),
    (
        "Resolve.GetMediaStorage",
        STORAGE + "AddTimelineMattesToMediaPool",
        POOL + "GetTimelineMatteList",
        POOL + "GetCurrentFolder",
        POOL + "SetCurrentFolder",
    ),
)
TIMELINE_MATTES_SPEC = CommandSpec(
    name="timeline-mattes",
    summary="List or add the timeline mattes of a folder",
    subcommands={"list": TIMELINE_LIST_SPEC, "add": TIMELINE_ADD_SPEC},
    examples=("davinci-axi storage timeline-mattes list",),
    reads_only=False,
    methods=tuple(
        dict.fromkeys(m for s in (TIMELINE_LIST_SPEC, TIMELINE_ADD_SPEC) for m in s.methods)
    ),
)
