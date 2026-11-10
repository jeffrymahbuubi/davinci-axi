"""`media import`: import files into a media pool folder (dry run until `--force`)."""

from __future__ import annotations

import os

from davinci_axi.commands.item_detail import str_arg
from davinci_axi.commands.media_folders import (
    POOL,
    Ctx,
    abs_path,
    current_folder,
    folder_hint,
    missing,
    open_pool,
    props,
    resolve_folder,
    verb_spec,
    walk_clips,
)
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import Change, MutationPlan, force_cmd, run_mutation
from davinci_axi.output import hint
from davinci_axi.resolve_types import MediaPoolItemApi
from davinci_axi.spec import Flag, Invocation, Positional, Result

FOLDER_FLAG = Flag(
    "folder",
    "str",
    None,
    help="target folder path (default: the pool's current folder, whatever is open in Resolve, "
    "e.g. Master/Titles)",
)
ALLOW_DUPLICATES = Flag(
    "allow-duplicates",
    "bool",
    False,
    help="import files already in the media pool (matched on File Path) instead of skipping them",
)
PARTIAL = "IMPORT_PARTIAL"
SAMPLE = 3  # basenames shown in the TOON dry run


def _tail(inv: Invocation) -> list[str]:
    folder = str_arg(inv, "folder")
    tail = ["--folder", folder] if folder else []
    return tail + (["--allow-duplicates"] if inv.args.get("allow_duplicates") else [])


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


def _sample(files: list[str]) -> str:
    shown = [os.path.basename(f) for f in files[:SAMPLE]]
    more = len(files) - SAMPLE
    return ", ".join(shown) + (f", +{more} more" if more > 0 else "")


def _duplicates(ctx: Ctx, files: list[str]) -> dict[str, tuple[str, MediaPoolItemApi]]:
    """`{abs file path: (pool folder path, clip)}` for each of `files` already in the media pool."""
    wanted = set(files)
    found: dict[str, tuple[str, MediaPoolItemApi]] = {}
    for hit in walk_clips(ctx.root):
        raw = props(hit.clip).get("File Path")
        if not raw:
            continue
        key = abs_path(str(raw))
        if key in wanted and key not in found:
            found[key] = (hit.folder_path, hit.clip)
    return found


def _failed(files: list[str], got: list[MediaPoolItemApi]) -> list[str]:
    """Basenames of `files` that no imported clip accounts for (by File Path, else by stem)."""
    paths = {os.path.abspath(str(props(c).get("File Path") or "")) for c in got}
    stems = {c.GetName() for c in got}
    return [
        os.path.basename(f)
        for f in files
        if f not in paths and os.path.splitext(os.path.basename(f))[0] not in stems
    ]


def run_import(inv: Invocation) -> Result:
    files = _import_files(inv.positionals["paths"])
    folder_ref = str_arg(inv, "folder")
    ctx = open_pool(inv)
    path, folder = resolve_folder(ctx, folder_ref) if folder_ref else current_folder(ctx)
    dupes = _duplicates(ctx, files)
    allow = bool(inv.args.get("allow_duplicates"))
    to_import = files if allow else [f for f in files if f not in dupes]
    before = len(folder.GetClipList() or [])
    extra: dict[str, object] = {"folder": path, "files": len(to_import)}
    if inv.opts.json:
        extra["files_list"] = [os.path.basename(f) for f in to_import]
    else:
        extra["sample"] = _sample(to_import)
    notes: list[str] = []
    if dupes:
        extra["duplicates"] = len(dupes)
        verdict = "imported again" if allow else "skipped"
        notes = [
            f"already in media pool: {clip.GetName()} (id {clip.GetUniqueId()}) \u2014 {verdict}"
            for _, clip in dupes.values()
        ]
        if not allow:
            extra["skipped"] = len(dupes)
        if inv.opts.json:
            extra["duplicates_list"] = [
                {"name": clip.GetName(), "id": clip.GetUniqueId(), "folder": where}
                for where, clip in dupes.values()
            ]
    if not to_import:
        extra["note"] = (
            "every file is already in the media pool; pass --allow-duplicates to import them again"
        )
    plan = MutationPlan(
        command="media import",
        target=f"{len(to_import)} file(s) into folder {path}",
        changes=[Change("clips", before, before + len(to_import))] if to_import else [],
        force_cmd=force_cmd(inv, "media", "import", inv.positionals["paths"], *_tail(inv)),
        extra=extra,
        next=[folder_hint(path, "list the clips in that folder")],
    )

    def apply() -> None:
        previous = ctx.pool.GetCurrentFolder()
        switched = previous.GetUniqueId() != folder.GetUniqueId()
        if switched and not ctx.pool.SetCurrentFolder(folder):
            raise api_failed("MediaPool.SetCurrentFolder", f"could not open folder {path}")
        try:
            # Resolve 21.1.1 imports nothing for {"FilePath": f} dicts; plain paths work
            got = ctx.pool.ImportMedia(list(to_import))
        finally:
            if switched:
                ctx.pool.SetCurrentFolder(previous)
        if not got:
            raise api_failed("MediaPool.ImportMedia", "no clip was imported")
        failed = _failed(to_import, got)
        if failed:
            raise AxiError(
                PARTIAL,
                f"imported {len(got)} of {len(to_import)} files",
                help=[
                    f"The imported clips stay in folder {path}; fix or drop the failed files "
                    "and import them again",
                    "Resolve imports nothing for unsupported or unreadable files",
                ],
                detail={"imported": f"{len(got)}/{len(to_import)}", "failed": failed},
            )
        plan.extra["imported"] = f"{len(got)}/{len(to_import)}"
        if inv.opts.json:
            plan.extra["imported_names"] = [c.GetName() for c in got]
        if switched:
            plan.extra["folder_restored"] = True

    def verify() -> dict[str, object]:
        return {"clips": len(folder.GetClipList() or [])}

    result = run_mutation(inv, ctx.session, plan, apply, verify)
    result.help[:0] = notes
    if not folder_ref:
        result.help.append(
            hint(
                "media import <paths> --folder Master",
                f"choose the folder (this run used the current folder {path})",
            )
        )
    return result


IMPORT_SPEC = verb_spec(
    "import",
    "Import media files into a folder of the media pool (comma-separated paths)",
    run_import,
    (Positional("paths", help="file paths, comma-separated"),),
    (FOLDER_FLAG, ALLOW_DUPLICATES),
    (
        "davinci-axi media import /media/a.mov",
        "davinci-axi media import /media/a.mov,/media/b.wav --folder Master/Footage --force",
    ),
    (POOL + "ImportMedia", POOL + "GetCurrentFolder", POOL + "SetCurrentFolder"),
    notes=(
        "Without --folder the files land in the media pool's current folder, which is whatever "
        "folder is open in Resolve (often a sub folder); `media folders` lists them, "
        "`--folder Master` is the root",
    ),
)
