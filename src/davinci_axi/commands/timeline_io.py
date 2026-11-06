"""`timeline export` and `timeline import`: move timelines to and from files (U15).

`export` writes a file with `Timeline.Export`; it changes no Resolve state, so like
`project snapshot` it has no `--force` and no snapshot, but never overwrites without
`--overwrite`. `import` creates a timeline with `MediaPool.ImportTimelineFromFile` and is a
mutation (`mutate.run_mutation`): a dry run until `--force`, then save, snapshot, import, read back.

`SPEC` wraps `timeline.SPEC` with the two verbs (KTD15); the registry points `timeline` here.
"""

from __future__ import annotations

import shlex
from dataclasses import replace
from pathlib import Path
from typing import cast

from davinci_axi.commands import timeline
from davinci_axi.commands.project_io import tree_bytes
from davinci_axi.commands.timeline_tracks import (
    TIMELINE_FLAG,
    context_words,
    text_arg,
    timelines_hint,
)
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    refuse_existing,
    run_mutation,
)
from davinci_axi.output import collapse_home, hint
from davinci_axi.resolve_types_edit import MediaPoolEdit
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

# format -> (Resolve export type attribute, export subtype attribute or None)
FORMATS: dict[str, tuple[str, str | None]] = {
    "otio": ("EXPORT_OTIO", None),
    "fcpxml": ("EXPORT_FCPXML_1_10", None),
    "drt": ("EXPORT_DRT", None),
    "edl": ("EXPORT_EDL", "EXPORT_NONE"),
    "aaf": ("EXPORT_AAF", "EXPORT_AAF_NEW"),
    "csv": ("EXPORT_TEXT_CSV", None),
    "tab": ("EXPORT_TEXT_TAB", None),
    "ale": ("EXPORT_ALE", None),
}
EXTENSION_FORMAT: dict[str, str] = {
    ".otio": "otio",
    ".fcpxml": "fcpxml",
    ".xml": "fcpxml",
    ".drt": "drt",
    ".edl": "edl",
    ".aaf": "aaf",
    ".csv": "csv",
    ".tab": "tab",
    ".txt": "tab",
    ".ale": "ale",
}
IMPORT_EXTENSIONS: tuple[str, ...] = (".otio", ".otioz", ".fcpxml", ".xml", ".drt", ".edl", ".aaf")
FORMAT_NOTES: dict[str, str] = {
    "edl": "an EDL carries one video track; use --format fcpxml or otio for several tracks",
    "aaf": "an AAF can drop effects Resolve cannot express after edits; check it before delivery",
}


def _path(inv: Invocation) -> Path:
    """The `file` positional as an absolute path (Resolve's working directory is not ours)."""
    return Path(inv.positionals["file"]).expanduser().resolve()


def _format(inv: Invocation, path: Path) -> str:
    chosen = text_arg(inv, "format")
    if chosen is not None:
        if chosen not in FORMATS:
            raise _unknown_format(f"unknown format {chosen}")
        return chosen
    found = EXTENSION_FORMAT.get(path.suffix.lower())
    if found is None:
        shown = path.suffix or "(none)"
        raise _unknown_format(f"cannot tell the format from the extension {shown}")
    return found


def _unknown_format(message: str) -> AxiError:
    return AxiError(
        "VALIDATION_ERROR",
        message,
        help=[
            f"Valid formats: {', '.join(FORMATS)}",
            "Valid example: davinci-axi timeline export ~/edit.otio --format otio",
        ],
    )


def _enum(session: Session, name: str) -> object:
    value = getattr(session.resolve, name, None)
    if value is None:
        raise AxiError(
            "API_FAILED",
            f"this Resolve has no {name}",
            help=["Choose another --format, or update Resolve to a version that offers it"],
        )
    return value


def run_export(inv: Invocation) -> Result:
    path = _path(inv)
    fmt = _format(inv, path)
    if path.is_dir():
        raise AxiError(
            "VALIDATION_ERROR",
            f"{collapse_home(path)} is a directory",
            help=["Pass a file path such as ~/edit.otio"],
        )
    refuse_existing(path, bool(inv.args.get("overwrite")))
    session = inv.connect()
    target = session.require_timeline(text_arg(inv, "timeline"))
    type_name, subtype_name = FORMATS[fmt]
    export_type = _enum(session, type_name)
    export_subtype = _enum(session, subtype_name) if subtype_name else None
    path.parent.mkdir(parents=True, exist_ok=True)
    name = target.GetName()
    if not target.Export(str(path), export_type, export_subtype):
        raise api_failed("Timeline.Export", f"could not export timeline {name} as {fmt}")
    if not path.exists():
        raise api_failed("Timeline.Export", f"Resolve reported success but wrote no {path.name}")
    data: dict[str, object] = {
        "timeline": name,
        "format": fmt,
        "path": collapse_home(path),
        "bytes": tree_bytes(path),
    }
    help_lines: list[str] = []
    if path.suffix.lower() in IMPORT_EXTENSIONS:
        shown = shlex.quote(collapse_home(path))
        help_lines.append(hint(f"timeline import {shown}", "bring the file back as a new timeline"))
    if fmt in FORMAT_NOTES:
        help_lines.append(FORMAT_NOTES[fmt])
    return Result(data, help_lines)


def _import_file(inv: Invocation) -> Path:
    path = _path(inv)
    if path.suffix.lower() not in IMPORT_EXTENSIONS:
        shown = path.suffix or "(none)"
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot import a {shown} file",
            help=[f"Supported extensions: {' '.join(IMPORT_EXTENSIONS)}"],
        )
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path; nothing was changed")
    return path


def _import_name(inv: Invocation, path: Path) -> str | None:
    raw = inv.args.get("name")
    if raw is None:
        return None
    name = str(raw).strip()
    if not name:
        raise AxiError(
            "VALIDATION_ERROR",
            "the timeline name is empty",
            help=['Valid example: davinci-axi timeline import ~/edit.otio --name "Edit v4"'],
        )
    if path.suffix.lower() == ".drt":
        raise AxiError(
            "VALIDATION_ERROR",
            "--name is not valid for a .drt import; Resolve names the timeline from the file",
            help=["Import without --name, then run `davinci-axi timeline rename` to rename it"],
        )
    return name


def run_import(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    path = _import_file(inv)
    name = _import_name(inv, path)
    session = inv.connect()
    project = session.require_project()
    if name is not None and session.find_timeline(name) is not None:
        raise AxiError(
            "TIMELINE_EXISTS",
            f"a timeline named {name} already exists",
            help=["Choose another --name; this command never overwrites a timeline"],
        )
    warnings = [
        "imports the file's source clips into the media pool",
        "the new timeline may become the current timeline",
    ]
    if name is None:
        warnings.insert(0, "Resolve names the timeline from the file; the name shown is a guess")
    if path.suffix.lower() == ".otioz":
        warnings.append(".otioz unpacks media next to the file")
    words = ["timeline", "import", inv.positionals["file"]]
    if name is not None:
        words.extend(["--name", name])
    plan = MutationPlan(
        command="timeline import",
        target=f"project {project.GetName()}",
        changes=[Change("timeline", None, name or path.stem)],
        force_cmd=force_cmd(inv, *words, *context_words(inv)),
        warning="; ".join(warnings),
        extra={"file": collapse_home(path)},
        next=timelines_hint(),
    )
    pool = cast(MediaPoolEdit, project.GetMediaPool())
    created: dict[str, str] = {}

    def apply() -> None:
        options: dict[str, object] = {"timelineName": name} if name else {}
        imported = pool.ImportTimelineFromFile(str(path), options)
        if imported is None:
            raise api_failed("ImportTimelineFromFile", f"could not import {path.name}")
        created["name"] = imported.GetName()
        if name is None:
            plan.changes[:] = [Change("timeline", None, created["name"])]

    def verify() -> dict[str, object]:
        found = session.find_timeline(created["name"]) is not None
        return {"timeline": created["name"] if found else None}

    return run_mutation(inv, session, plan, apply, verify)


EXPORT_SPEC = CommandSpec(
    name="export",
    summary="Export a timeline to a file (otio, fcpxml, drt, edl, aaf, csv, tab, ale)",
    run=run_export,
    positionals=(Positional("file", help="output file; the extension picks the format"),),
    flags=(
        TIMELINE_FLAG,
        Flag(
            "format", "str", None, choices=tuple(FORMATS), help="format (default: from extension)"
        ),
        Flag("overwrite", "bool", False, help="replace the file if it exists"),
    ),
    examples=(
        "davinci-axi timeline export ~/edit.otio",
        'davinci-axi timeline export ~/cut.edl --timeline "Edit v2"',
        "davinci-axi timeline export ~/cut.aaf --format aaf --overwrite",
    ),
    reads_only=False,
    methods=("Timeline.Export",),
)

IMPORT_SPEC = CommandSpec(
    name="import",
    summary="Import a timeline from an otio, otioz, fcpxml, xml, drt, edl or aaf file",
    run=run_import,
    positionals=(Positional("file", help="file to import"),),
    flags=(
        Flag("name", "str", None, help="name for the new timeline (not valid for .drt)"),
        *MUTATION_FLAGS,
    ),
    examples=(
        "davinci-axi timeline import ~/edit.otio",
        'davinci-axi timeline import ~/edit.otio --name "Edit v4" --force',
    ),
    reads_only=False,
    methods=("MediaPool.ImportTimelineFromFile",),
)

SPEC = replace(
    timeline.SPEC,
    subcommands={**timeline.SPEC.subcommands, "export": EXPORT_SPEC, "import": IMPORT_SPEC},
    methods=tuple(
        dict.fromkeys((*timeline.SPEC.methods, *EXPORT_SPEC.methods, *IMPORT_SPEC.methods))
    ),
)
