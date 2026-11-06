"""`media`: list and show media pool clips, import, move, delete, relink and unlink them (U13).

`SPEC` is the `media` command: its own `run` is the read view (R24, never writes), `media show
<clip>` the detail view, and the subcommands are the guarded mutations. Addressing and the folder
verbs live in `media_folders`, the clip attribute verbs in `media_edit`; every mutation is a dry
run until `--force` and goes through `mutate.run_mutation`.
"""

from __future__ import annotations

import os
import shlex

from davinci_axi.commands.item_detail import _properties as truncated_block
from davinci_axi.commands.item_detail import str_arg
from davinci_axi.commands.media_edit import (
    CLIP,
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
    current_folder,
    field_names,
    folder_hint,
    missing,
    open_pool,
    props,
    resolve_clip,
    resolve_clips,
    resolve_folder,
    show_hint,
    verb_spec,
    walk_folders,
    where_is,
)
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import Change, MutationPlan, force_cmd, run_mutation
from davinci_axi.output import apply_limit, empty_state, hint, select_fields
from davinci_axi.resolve_types import MediaPoolItemApi
from davinci_axi.spec import (
    DEFAULT_LIMIT,
    LIST_FLAGS,
    CommandSpec,
    Flag,
    Invocation,
    Positional,
    Result,
)

DEFAULT_FIELDS = ("id", "name", "type", "duration", "folder")
EXTRA_FIELDS = ("fps", "path", "start_tc", "color", "flags")
FOLDER_FLAG = Flag("folder", "str", None, help="target folder path (default: the current folder)")


def _row(found: Found, extras: set[str]) -> dict[str, object]:
    clip, live = found.clip, props(found.clip)
    row: dict[str, object] = {
        "id": clip.GetUniqueId(),
        "name": clip.GetName(),
        "type": live.get("Type"),
        "duration": live.get("Duration"),
        "folder": found.folder_path,
    }
    if "fps" in extras:
        row["fps"] = live.get("FPS")
    if "path" in extras:
        row["path"] = live.get("File Path") or None
    if "start_tc" in extras:
        row["start_tc"] = live.get("Start TC")
    if "color" in extras:
        row["color"] = clip.GetClipColor() or None
    if "flags" in extras:
        row["flags"] = list(clip.GetFlagList() or [])
    return row


def run(inv: Invocation) -> Result:
    raw = inv.args.get("fields")
    requested = [str(f) for f in raw] if isinstance(raw, list) else None
    limit = inv.args.get("limit", DEFAULT_LIMIT)
    limit = limit if isinstance(limit, int) else DEFAULT_LIMIT
    select_fields([dict.fromkeys((*DEFAULT_FIELDS, *EXTRA_FIELDS))], DEFAULT_FIELDS, requested)
    apply_limit([], limit)
    folder_ref, recursive = str_arg(inv, "folder"), bool(inv.args.get("recursive"))
    extras = {f for f in requested or [] if f in EXTRA_FIELDS}
    ctx = open_pool(inv)
    path, folder = resolve_folder(ctx, folder_ref) if folder_ref else (ctx.root.GetName(), ctx.root)
    scope = walk_folders(folder, path) if recursive else iter([(path, folder)])
    found = [Found(p, c) for p, f in scope for c in f.GetClipList() or []]
    shown, total = apply_limit(found, limit)
    children = [
        {
            "path": f"{path}/{child.GetName()}",
            "clips": len(child.GetClipList() or []),
            "subfolders": len(child.GetSubFolderList() or []),
        }
        for child in folder.GetSubFolderList() or []
    ]
    data: dict[str, object] = {
        "folder": path,
        "folder_stale": bool(folder.GetIsFolderStale()),
        "folders": children,
        "total": total,
    }
    if shown:
        data["clips"] = select_fields([_row(f, extras) for f in shown], DEFAULT_FIELDS, requested)
    else:
        scope_text = f"in folder {path}"
        data["clips"] = empty_state("clips", scope_text, {} if recursive else {"recursive": "no"})
    lines: list[str] = []
    if len(shown) < total:
        lines.append(hint(f"media --limit {total}", f"list all {total} clips"))
    if shown:
        lines.append(show_hint(shown[:1]))
    if children:
        lines.append(folder_hint(str(children[0]["path"]), "list one sub folder"))
    return Result(data, lines)


def _timeline_name(clip: MediaPoolItemApi) -> str | None:
    """The timeline a timeline-type clip stands for, else `None`."""
    try:
        timeline = clip.GetTimeline()
    except Exception:  # OQ23: may raise on a clip that is not a timeline
        return None
    return timeline.GetName() if timeline is not None else None


def run_show(inv: Invocation) -> Result:
    ref, full = inv.positionals["clip"], bool(inv.args.get("full"))
    ctx = open_pool(inv)
    found = resolve_clip(ctx, ref)
    clip = found.clip
    full_cmd = f"{inv.bin_name} media show {shlex.quote(ref)} --full"
    metadata = clip.GetMetadata()
    properties, properties_note = truncated_block(props(clip), full, full_cmd)
    meta_block, metadata_note = truncated_block(
        metadata if isinstance(metadata, dict) else {}, full, full_cmd
    )
    data: dict[str, object] = {
        "id": clip.GetUniqueId(),
        "name": clip.GetName(),
        "media_id": clip.GetMediaId(),
        "folder": found.folder_path,
        "timeline": _timeline_name(clip),
        "color": clip.GetClipColor() or None,
        "flags": list(clip.GetFlagList() or []),
        "properties": properties,
    }
    if properties_note:
        data["properties_note"] = properties_note
    data["metadata"] = meta_block
    if metadata_note:
        data["metadata_note"] = metadata_note
    data["markers"] = [
        {"frame": f, "color": m.get("color"), "name": m.get("name"), "duration": m.get("duration")}
        for f, m in sorted((clip.GetMarkers() or {}).items())
    ]
    return Result(data, [folder_hint(found.folder_path, "list the clips next to it")])


def _tail(inv: Invocation) -> list[str]:
    folder = str_arg(inv, "folder")
    return ["--folder", folder] if folder else []


# ---- import -------------------------------------------------------------------------------


def _import_files(text: str) -> list[str]:
    whole = abs_path(text.strip())
    candidates = [text.strip()] if os.path.exists(whole) else text.split(",")
    files: list[str] = []
    for raw in (c.strip() for c in candidates):
        path = abs_path(raw)
        if not raw or not os.path.exists(path):
            raise missing(raw or "(empty)", "file")
        if not os.path.isfile(path):
            raise AxiError(
                "VALIDATION_ERROR",
                f"{raw} is a folder; media import takes files",
                help=["Pass the files inside the folder, or import the folder in Resolve"],
            )
        if path not in files:
            files.append(path)
    return files


def run_import(inv: Invocation) -> Result:
    files = _import_files(inv.positionals["paths"])
    folder_ref = str_arg(inv, "folder")
    ctx = open_pool(inv)
    path, folder = resolve_folder(ctx, folder_ref) if folder_ref else current_folder(ctx)
    names = ", ".join(os.path.basename(f) for f in files)
    plan = MutationPlan(
        command="media import",
        target=f"{len(files)} file(s) into folder {path}",
        changes=[Change("clips", 0, len(files))],
        force_cmd=force_cmd(inv, "media", "import", inv.positionals["paths"], *_tail(inv)),
        extra={"folder": path, "files": names},
        next=[folder_hint(path, "list the clips in that folder")],
    )
    imported: list[MediaPoolItemApi] = []

    def apply() -> None:
        previous = ctx.pool.GetCurrentFolder()
        switched = previous.GetUniqueId() != folder.GetUniqueId()
        if switched and not ctx.pool.SetCurrentFolder(folder):
            raise api_failed("MediaPool.SetCurrentFolder", f"could not open folder {path}")
        try:
            # Resolve 21.1.1 imports nothing for {"FilePath": f} dicts; plain paths work
            got = ctx.pool.ImportMedia(list(files))
        finally:
            if switched:
                ctx.pool.SetCurrentFolder(previous)
        if not got:
            raise api_failed("MediaPool.ImportMedia", "no clip was imported")
        imported.extend(got)
        plan.extra["imported"] = ", ".join(c.GetName() for c in got)
        if switched:
            plan.extra["folder_restored"] = True

    def verify() -> dict[str, object]:
        present = {c.GetUniqueId() for c in folder.GetClipList() or []}
        return {"clips": sum(1 for c in imported if c.GetUniqueId() in present)}

    return run_mutation(inv, ctx.session, plan, apply, verify)


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


IMPORT_SPEC = verb_spec(
    "import",
    "Import media files into a folder of the media pool (comma-separated paths)",
    run_import,
    (Positional("paths", help="file paths, comma-separated"),),
    (FOLDER_FLAG,),
    (
        "davinci-axi media import /media/a.mov",
        "davinci-axi media import /media/a.mov,/media/b.wav --folder Master/Footage --force",
    ),
    (POOL + "ImportMedia", POOL + "GetCurrentFolder", POOL + "SetCurrentFolder"),
)
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


SHOW_SPEC = CommandSpec(
    name="show",
    summary="Show one media pool clip: properties, metadata, flags and markers",
    run=run_show,
    positionals=(CLIP,),
    flags=(Flag("full", "bool", False, help="do not truncate long property blocks"),),
    examples=("davinci-axi media show A001_beach", "davinci-axi media show clip-1 --full --json"),
    methods=(
        "MediaPoolItem.GetClipProperty",
        "MediaPoolItem.GetMetadata",
        "MediaPoolItem.GetMediaId",
        "MediaPoolItem.GetTimeline",
        "Timeline.GetName",
    ),
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
    flags=(
        Flag(
            "folder",
            "str",
            None,
            help="folder path to list, such as Master/Footage (default: the root folder)",
        ),
        Flag("recursive", "bool", True, help="include sub folders (--no-recursive for one folder)"),
        *LIST_FLAGS,
    ),
    examples=(
        "davinci-axi media",
        "davinci-axi media --folder Master/Footage --no-recursive --fields name,fps,path",
        "davinci-axi media show A001_beach",
        "davinci-axi media rename A001_beach --to beach_wide --force",
    ),
    subcommands=SUBCOMMANDS,
    reads_only=False,
    methods=_all_methods(),
)
