"""File verbs of the media pool (U21): `media metadata export`, `media folder import|export`.

The two exports only write a file and change no Resolve state, so like `timeline export` they
have no `--force` and no snapshot, but never overwrite without `--overwrite`. `folder import`
adds a folder (a DRB file) to the media pool, so it is a mutation: a dry run until `--force`.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import cast

from davinci_axi.commands.item_detail import str_arg
from davinci_axi.commands.media_extra_common import FOLDER, POOL, FolderExtra, pool_of
from davinci_axi.commands.media_folders import (
    FOLDER_PATH,
    open_pool,
    resolve_clips,
    resolve_folder,
    verb_spec,
    walk_clips,
    walk_folders,
)
from davinci_axi.commands.project_io import tree_bytes
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import Change, MutationPlan, force_cmd, refuse_existing, run_mutation
from davinci_axi.output import collapse_home, hint
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

OVERWRITE = Flag("overwrite", "bool", False, help="replace the file if it exists")
FILE = Positional("file", help="path of the file to write")


def _target_file(text: str) -> Path:
    path = Path(text).expanduser().resolve()
    if path.is_dir():
        raise AxiError(
            "VALIDATION_ERROR",
            f"{collapse_home(path)} is a directory",
            help=["Pass a file path such as ~/metadata.csv"],
        )
    return path


def _written(path: Path, what: str, method: str) -> None:
    if not path.exists():
        raise api_failed(method, f"Resolve reported success but wrote no {path.name} for {what}")


def run_metadata_export(inv: Invocation) -> Result:
    path = _target_file(inv.positionals["file"])
    refuse_existing(path, bool(inv.args.get("overwrite")))
    clips_text = str_arg(inv, "clips")
    ctx = open_pool(inv)
    chosen = [f.clip for f in resolve_clips(ctx, clips_text)] if clips_text else None
    path.parent.mkdir(parents=True, exist_ok=True)
    pool = pool_of(ctx)
    ok = pool.ExportMetadata(str(path), chosen) if chosen else pool.ExportMetadata(str(path))
    if not ok:
        raise api_failed("MediaPool.ExportMetadata", "could not export the clip metadata")
    _written(path, "the metadata", "MediaPool.ExportMetadata")
    count = len(chosen) if chosen else sum(1 for _ in walk_clips(ctx.root))
    data: dict[str, object] = {
        "path": collapse_home(path),
        "clips": count,
        "bytes": tree_bytes(path),
    }
    return Result(data, [hint("media", "list the clips that were exported")])


def run_folder_export(inv: Invocation) -> Result:
    ref = inv.positionals["path"]
    path = _target_file(inv.positionals["file"])
    refuse_existing(path, bool(inv.args.get("overwrite")))
    ctx = open_pool(inv)
    folder_path, folder = resolve_folder(ctx, ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cast(FolderExtra, folder).Export(str(path)):
        raise api_failed("Folder.Export", f"could not export folder {folder_path}")
    _written(path, f"folder {folder_path}", "Folder.Export")
    data: dict[str, object] = {
        "folder": folder_path,
        "path": collapse_home(path),
        "bytes": tree_bytes(path),
    }
    shown = shlex.quote(collapse_home(path))
    return Result(data, [hint(f"media folder import {shown}", "bring the folder back in")])


def run_folder_import(inv: Invocation) -> Result:
    given = inv.positionals["file"]
    path = Path(given).expanduser().resolve()
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path; nothing was changed")
    source = str_arg(inv, "source_path")
    ctx = open_pool(inv)
    pool = pool_of(ctx)

    def snapshot_of_pool() -> tuple[set[str], int]:
        return {p for p, _ in walk_folders(ctx.root)}, sum(1 for _ in walk_clips(ctx.root))

    before_folders, before_clips = snapshot_of_pool()
    plan = MutationPlan(
        command="media folder import",
        target="the media pool",
        changes=[Change("folders", None, "imported")],
        force_cmd=force_cmd(
            inv,
            "media",
            "folder",
            "import",
            given,
            *(["--source-path", source] if source else []),
        ),
        extra={"file": collapse_home(path)},
        next=[hint("media", "list the media pool")],
    )

    def apply() -> None:
        ok = (
            pool.ImportFolderFromFile(str(path), source)
            if source
            else pool.ImportFolderFromFile(str(path))
        )
        if not ok:
            raise api_failed("MediaPool.ImportFolderFromFile", f"could not import {path.name}")
        now_folders, _ = snapshot_of_pool()
        plan.extra["new_folders"] = ", ".join(sorted(now_folders - before_folders)) or "(merged)"

    def verify() -> dict[str, object]:
        now_folders, now_clips = snapshot_of_pool()
        grew = bool(now_folders - before_folders) or now_clips > before_clips
        return {"folders": "imported" if grew else "nothing new in the media pool"}

    return run_mutation(inv, ctx.session, plan, apply, verify)


METADATA_EXPORT_SPEC = CommandSpec(
    name="export",
    summary="Export clip metadata to a CSV file (never overwrites without --overwrite)",
    run=run_metadata_export,
    positionals=(FILE,),
    flags=(
        Flag("clips", "str", None, help="clip ids or names, comma-separated (default: all clips)"),
        OVERWRITE,
    ),
    examples=(
        "davinci-axi media metadata export ~/metadata.csv",
        "davinci-axi media metadata export ~/m.csv --clips A001_beach,A002_city --overwrite",
    ),
    methods=(POOL + "GetRootFolder", POOL + "ExportMetadata"),
)
FOLDER_EXPORT_SPEC = CommandSpec(
    name="export",
    summary="Export a media pool folder as a DRB file (never overwrites without --overwrite)",
    run=run_folder_export,
    positionals=(FOLDER_PATH, FILE),
    flags=(OVERWRITE,),
    examples=(
        "davinci-axi media folder export Master/Footage ~/footage.drb",
        "davinci-axi media folder export Master/Footage ~/footage.drb --overwrite",
    ),
    methods=(FOLDER + "Export",),
)
FOLDER_IMPORT_SPEC = verb_spec(
    "import",
    "Import a DRB file as a folder of the media pool (dry run until --force)",
    run_folder_import,
    (Positional("file", help="the DRB file to import"),),
    (Flag("source-path", "str", None, help="folder that holds the source clips"),),
    (
        "davinci-axi media folder import ~/footage.drb",
        "davinci-axi media folder import ~/footage.drb --source-path /Volumes/Media --force",
    ),
    (POOL + "ImportFolderFromFile",),
)
