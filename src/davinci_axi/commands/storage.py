"""`storage`: browse Media Storage, add files to the media pool, mattes and the clone tool (U21).

`volumes`, `ls`, `mattes list`, `timeline-mattes list` and `clone status` only read (R24). `add`,
`mattes add|delete` and `timeline-mattes add` change the project, so they are dry runs until
`--force` and go through `mutate.run_mutation`. `reveal` and `clone start|stop` act on the app or
on the disk, not on the project, so their snapshot policy is `none`. The mattes verbs live in
`storage_mattes`, the clone tool in `storage_clone`.
"""

from __future__ import annotations

import shlex
from typing import Any

from davinci_axi.commands.item_detail import str_arg
from davinci_axi.commands.media_folders import (
    abs_path,
    current_folder,
    folder_hint,
    open_pool,
    resolve_folder,
)
from davinci_axi.commands.storage_clone import CLONE_SPEC
from davinci_axi.commands.storage_common import STORAGE, split_paths, storage_of
from davinci_axi.commands.storage_mattes import MATTES_SPEC, TIMELINE_MATTES_SPEC
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.output import apply_limit, empty_state, hint
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

POOL = "MediaPool."
FOLDER_FLAG = Flag("folder", "str", None, help="media pool folder path (default: current folder)")


def _limit(inv: Invocation) -> int:
    limit = inv.args.get("limit", DEFAULT_LIMIT)
    return limit if isinstance(limit, int) else DEFAULT_LIMIT


def run_volumes(inv: Invocation) -> Result:
    volumes = list(storage_of(inv.connect()).GetMountedVolumeList() or [])
    data: dict[str, object] = {
        "volumes": [{"path": v} for v in volumes]
        if volumes
        else empty_state("volumes", "in Media Storage", {})
    }
    lines = (
        [hint(f"storage ls {shlex.quote(volumes[0])}", "list the first volume")] if volumes else []
    )
    return Result(data, lines)


def _listing(kind: str, paths: list[str], limit: int, lines: list[str], base: str) -> object:
    shown, total = apply_limit(paths, limit)
    if not shown:
        return empty_state(kind, f"in {base}", {})
    if len(shown) < total:
        lines.append(hint(f"storage ls {shlex.quote(base)} --limit {total}", f"list all {total}"))
    return [{"path": p} for p in shown]


def run_ls(inv: Invocation) -> Result:
    path = abs_path(inv.positionals["path"])
    only_files, only_dirs = bool(inv.args.get("files")), bool(inv.args.get("dirs"))
    limit = _limit(inv)
    apply_limit([], limit)
    storage = storage_of(inv.connect())
    lines: list[str] = []
    data: dict[str, object] = {"path": path}
    if not only_files or only_dirs:
        dirs = [str(d) for d in storage.GetSubFolderList(path) or []]
        data["dirs"] = _listing("folders", dirs, limit, lines, path)
        if dirs:
            lines.append(hint(f"storage ls {shlex.quote(dirs[0])}", "list the first folder"))
    if not only_dirs or only_files:
        files = [str(f) for f in storage.GetFileList(path) or []]
        data["files"] = _listing("files", files, limit, lines, path)
    return Result(data, lines)


def _frame(inv: Invocation, key: str) -> int | None:
    raw = inv.args.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--{key.replace('_', '-')} needs a frame number of 0 or more",
            help=["Valid example: --in-frame 0 --out-frame 100"],
        )
    return raw


def _item_infos(inv: Invocation, paths: list[str]) -> list[dict[str, Any]]:
    start, end = _frame(inv, "in_frame"), _frame(inv, "out_frame")
    if (start is None) != (end is None):
        raise AxiError(
            "VALIDATION_ERROR",
            "--in-frame and --out-frame go together",
            help=["Valid example: --in-frame 0 --out-frame 100"],
        )
    if start is not None and end is not None and start > end:
        raise AxiError("VALIDATION_ERROR", f"--in-frame {start} is after --out-frame {end}")
    infos: list[dict[str, Any]] = []
    for path in paths:
        info: dict[str, Any] = {"media": path}
        if start is not None and end is not None:
            info["startFrame"], info["endFrame"] = start, end
        infos.append(info)
    return infos


def run_add(inv: Invocation) -> Result:
    paths = split_paths(inv.positionals["paths"])
    infos = _item_infos(inv, paths)
    folder_ref = str_arg(inv, "folder")
    ctx = open_pool(inv)
    storage = storage_of(ctx.session)
    path, folder = resolve_folder(ctx, folder_ref) if folder_ref else current_folder(ctx)
    words = [
        "storage",
        "add",
        inv.positionals["paths"],
        *(["--folder", folder_ref] if folder_ref else []),
    ]
    for key in ("in_frame", "out_frame"):
        if inv.args.get(key) is not None:
            words += [f"--{key.replace('_', '-')}", str(inv.args[key])]
    plan = MutationPlan(
        command="storage add",
        target=f"{len(paths)} path(s) into folder {path}",
        changes=[Change("clips", None, "added")],
        force_cmd=force_cmd(inv, *words),
        extra={"folder": path, "paths": ", ".join(paths)},
        next=[folder_hint(path, "list the clips in that folder")],
    )
    added: list[MediaPoolItemApi] = []

    def apply() -> None:
        previous = ctx.pool.GetCurrentFolder()
        switched = previous.GetUniqueId() != folder.GetUniqueId()
        if switched and not ctx.pool.SetCurrentFolder(folder):
            raise api_failed("MediaPool.SetCurrentFolder", f"could not open folder {path}")
        try:
            got = storage.AddItemListToMediaPool(infos)
        finally:
            if switched:
                ctx.pool.SetCurrentFolder(previous)
        if not got:
            raise api_failed(
                "MediaStorage.AddItemListToMediaPool",
                "no clip was added; check that the paths are visible in Media Storage",
            )
        added.extend(got)
        plan.extra["added"] = ", ".join(c.GetName() for c in got)
        if switched:
            plan.extra["folder_restored"] = True

    def verify() -> dict[str, object]:
        present = {c.GetUniqueId() for c in folder.GetClipList() or []}
        missing = [c for c in added if c.GetUniqueId() not in present]
        return {"clips": "added" if added and not missing else f"{len(missing)} not in folder"}

    return run_mutation(inv, ctx.session, plan, apply, verify)


def run_reveal(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    path = abs_path(inv.positionals["path"])
    session = inv.connect()
    storage = storage_of(session)
    plan = MutationPlan(
        command="storage reveal",
        target=f"Media Storage path {path}",
        changes=[Change("called", False, True)],
        force_cmd=force_cmd(inv, "storage", "reveal", inv.positionals["path"]),
        snapshot="none",
        extra={"note": "not read back: Resolve has no getter for the Media Storage view"},
    )

    def apply() -> None:
        if not storage.RevealInStorage(path):
            raise api_failed("MediaStorage.RevealInStorage", f"could not reveal {path}")

    return run_mutation(inv, session, plan, apply, lambda: {"called": True})


VOLUMES_SPEC = CommandSpec(
    name="volumes",
    summary="List the volumes mounted in Media Storage",
    run=run_volumes,
    examples=("davinci-axi storage volumes", "davinci-axi storage volumes --json"),
    methods=("Resolve.GetMediaStorage", STORAGE + "GetMountedVolumeList"),
)
LS_SPEC = CommandSpec(
    name="ls",
    summary="List the folders and files of one Media Storage path",
    run=run_ls,
    positionals=(Positional("path", help="absolute folder path, such as /Volumes/Media"),),
    flags=(
        Flag("files", "bool", False, help="list files only"),
        Flag("dirs", "bool", False, help="list folders only"),
        *LIST_FLAGS[1:],
    ),
    examples=(
        "davinci-axi storage ls /Volumes/Media",
        "davinci-axi storage ls /Volumes/Media --files --limit 20",
    ),
    methods=("Resolve.GetMediaStorage", STORAGE + "GetSubFolderList", STORAGE + "GetFileList"),
)
ADD_SPEC = CommandSpec(
    name="add",
    summary="Add files or folders from Media Storage to the media pool (dry run until --force)",
    run=run_add,
    positionals=(Positional("paths", help="absolute paths, comma-separated"),),
    flags=(
        FOLDER_FLAG,
        Flag("in-frame", "int", None, help="first frame to use (with --out-frame)"),
        Flag("out-frame", "int", None, help="last frame to use (with --in-frame)"),
        *MUTATION_FLAGS,
    ),
    examples=(
        "davinci-axi storage add /Volumes/Media/a.mov --folder Master/Footage",
        "davinci-axi storage add /Volumes/Media/a.mov,/Volumes/Media/b.wav --force",
    ),
    reads_only=False,
    methods=(
        "Resolve.GetMediaStorage",
        STORAGE + "AddItemListToMediaPool",
        POOL + "GetCurrentFolder",
        POOL + "SetCurrentFolder",
    ),
)
REVEAL_SPEC = CommandSpec(
    name="reveal",
    summary="Show a path in Resolve's Media Storage panel (not read back; no snapshot)",
    run=run_reveal,
    positionals=(Positional("path", help="absolute file or folder path"),),
    flags=MUTATION_FLAGS,
    examples=(
        "davinci-axi storage reveal /Volumes/Media/a.mov",
        "davinci-axi storage reveal /Volumes/Media/a.mov --force",
    ),
    reads_only=False,
    methods=("Resolve.GetMediaStorage", STORAGE + "RevealInStorage"),
)

SUBCOMMANDS: dict[str, CommandSpec] = {
    "volumes": VOLUMES_SPEC,
    "ls": LS_SPEC,
    "add": ADD_SPEC,
    "reveal": REVEAL_SPEC,
    "mattes": MATTES_SPEC,
    "timeline-mattes": TIMELINE_MATTES_SPEC,
    "clone": CLONE_SPEC,
}


def _all_methods() -> tuple[str, ...]:
    found: dict[str, None] = {}
    stack = list(SUBCOMMANDS.values())
    while stack:
        spec = stack.pop(0)
        found.update(dict.fromkeys(spec.methods))
        stack.extend(spec.subcommands.values())
    return tuple(found)


SPEC = CommandSpec(
    name="storage",
    summary="Browse Media Storage, add media, manage mattes, clone media (dry run until --force)",
    examples=(
        "davinci-axi storage volumes",
        "davinci-axi storage ls /Volumes/Media",
        "davinci-axi storage add /Volumes/Media/a.mov --force",
        "davinci-axi storage clone status",
    ),
    subcommands=SUBCOMMANDS,
    reads_only=False,
    methods=_all_methods(),
)
