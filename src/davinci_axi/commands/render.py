"""`render`: presets and queue reads (R22), format and mode switches, frame export (R33).

The queue verbs live in `render_jobs`, the preset verbs in `render_presets`; this module builds the
parent `SPEC`. `format set` and `mode set` change which format, codec and output mode the next
job uses: UI-level state with getters, so they are verified but take no snapshot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from davinci_axi.commands.project_io import tree_bytes
from davinci_axi.commands.render_common import render_project
from davinci_axi.commands.render_jobs import JOB_SPECS
from davinci_axi.commands.render_presets import PRESET_SPECS
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    refuse_existing,
    run_mutation,
    with_page,
)
from davinci_axi.output import apply_limit, collapse_home, empty_state, hint, select_fields
from davinci_axi.resolve_types import ProjectApi
from davinci_axi.spec import LIST_FLAGS, CommandSpec, Flag, Invocation, Positional, Result

SCOPE = "current project"
RENDER_MODES = {0: "individual clips", 1: "single clip"}
MODE_WORDS = {"individual": 0, "single": 1}
STATUS_FIELDS = ("id", "name", "timeline", "status", "percent")
EXTRA_FIELDS: dict[str, str] = {
    "target_dir": "TargetDir",
    "file": "OutputFilename",
    "format": "VideoFormat",
    "codec": "VideoCodec",
    "width": "FormatWidth",
    "height": "FormatHeight",
}


def _names(noun: str, names: list[str], project: ProjectApi) -> object:
    """The names as a list, or the empty-state sentence when there are none."""
    return list(names) if names else empty_state(noun, f"in project {project.GetName()}", {})


def run_presets(inv: Invocation) -> Result:
    project = inv.connect().require_project()
    data: dict[str, object] = {
        "scope": SCOPE,
        "presets": _names("presets", project.GetRenderPresetList() or [], project),
        "quick_export": _names(
            "quick export presets", project.GetQuickExportRenderPresets() or [], project
        ),
    }
    current = project.GetCurrentRenderFormatAndCodec() or {}
    data["current_format"] = current.get("format")
    data["current_codec"] = current.get("codec")
    mode = project.GetCurrentRenderMode()
    data["render_mode"] = RENDER_MODES.get(mode, f"unknown ({mode})")
    return Result(data, [hint("render status", "list the render queue and job progress")])


def _job_row(project: ProjectApi, job: dict[str, Any]) -> dict[str, object]:
    job_id = str(job.get("JobId", ""))
    status = project.GetRenderJobStatus(job_id) or {}
    row: dict[str, object] = {
        "id": job_id,
        "name": job.get("RenderJobName"),
        "timeline": job.get("TimelineName"),
        "status": status.get("JobStatus") or "unknown",
        "percent": status.get("CompletionPercentage"),
    }
    for field, key in EXTRA_FIELDS.items():
        row[field] = job.get(key)
    return row


def run_status(inv: Invocation) -> Result:
    project = inv.connect().require_project()
    rows = [_job_row(project, job) for job in project.GetRenderJobList() or []]
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        counts[status] = counts.get(status, 0) + 1
    data: dict[str, object] = {
        "scope": SCOPE,
        "rendering": bool(project.IsRenderingInProgress()),
        "total": len(rows),
    }
    next_step = hint("render presets", "list the render presets")
    if not rows:
        data["jobs"] = empty_state("jobs", f"in project {project.GetName()}", {})
        return Result(data, [next_step])
    data["by_status"] = [{"status": s, "count": n} for s, n in counts.items()]
    requested = cast("list[str] | None", inv.args.get("fields"))
    page, total = apply_limit(rows, cast(int, inv.args.get("limit")))
    data["jobs"] = select_fields(page, STATUS_FIELDS, requested)
    help_lines = [next_step]
    if total > len(page):
        help_lines.insert(0, f"Showing {len(page)} of {total} jobs; raise --limit to see more")
    return Result(data, help_lines)


def _pairs(table: dict[str, Any], first: str, second: str) -> list[dict[str, object]]:
    return [{first: name, second: value} for name, value in table.items()]


def run_formats(inv: Invocation) -> Result:
    project = inv.connect().require_project()
    audio = bool(inv.args.get("audio"))
    kind = "audio" if audio else "video"
    formats: dict[str, Any] = (
        project.GetAudioRenderFormats() if audio else project.GetRenderFormats()
    ) or {}
    data: dict[str, object] = {"scope": SCOPE, "kind": kind}
    data["formats"] = (
        _pairs(formats, "name", "extension")
        if formats
        else empty_state(f"{kind} formats", f"in project {project.GetName()}", {})
    )
    extension = inv.args.get("codecs")
    help_lines: list[str] = []
    if isinstance(extension, str) and extension:
        codecs: dict[str, Any] = (
            project.GetAudioRenderCodecs(extension) if audio else project.GetRenderCodecs(extension)
        ) or {}
        if not codecs:
            known = ", ".join(str(e) for e in formats.values()) or "(none)"
            raise not_found("render format extension", extension, f"Extensions: {known}")
        data["codecs_for"] = extension
        data["codecs"] = _pairs(codecs, "name", "codec")
        help_lines.append(hint(f"render format set {extension} <codec>", "select a codec"))
    if inv.args.get("resolutions"):
        found = project.GetRenderResolutions(extension if isinstance(extension, str) else None)
        data["resolutions"] = [
            {"width": r.get("Width"), "height": r.get("Height")} for r in found or []
        ] or empty_state("resolutions", f"in project {project.GetName()}", {})
    return Result(data, help_lines or [hint("render presets", "list the render presets")])


def _lookup(table: dict[str, Any], given: str) -> str | None:
    """`given` as a value of `table`; a key (name) is accepted too and mapped to its value."""
    if given in table.values():
        return given
    value = table.get(given)
    return str(value) if value is not None else None


def run_format_set(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    session = inv.connect()
    project = render_project(session)
    formats: dict[str, Any] = project.GetRenderFormats() or {}
    given = inv.positionals["format"]
    extension = _lookup(formats, given)
    if extension is None:
        known = ", ".join(str(e) for e in formats.values()) or "(none)"
        raise not_found("render format", given, f"Format extensions: {known}")
    codecs: dict[str, Any] = project.GetRenderCodecs(extension) or {}
    wanted = _lookup(codecs, inv.positionals["codec"])
    if wanted is None:
        known = ", ".join(str(c) for c in codecs.values()) or "(none)"
        raise not_found("codec", inv.positionals["codec"], f"Codecs for {extension}: {known}")
    current = project.GetCurrentRenderFormatAndCodec() or {}
    changes = [
        Change(field, current.get(field), value)
        for field, value in (("format", extension), ("codec", wanted))
        if current.get(field) != value
    ]
    plan = MutationPlan(
        command="render format set",
        target=f"project {project.GetName()}",
        changes=changes,
        force_cmd=force_cmd(inv, "render", "format", "set", given, inv.positionals["codec"]),
        snapshot="none",
        next=[hint("render presets", "check the current format and codec")],
    )

    def apply() -> None:
        if not project.SetCurrentRenderFormatAndCodec(extension, wanted):
            raise api_failed("SetCurrentRenderFormatAndCodec", f"could not select {extension}")

    def verify() -> dict[str, object]:
        now = project.GetCurrentRenderFormatAndCodec() or {}
        return {"format": now.get("format"), "codec": now.get("codec")}

    return run_mutation(inv, session, plan, apply, verify)


def run_mode_set(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    word = inv.positionals["mode"]
    if word not in MODE_WORDS:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown render mode {word}",
            help=[f"Valid modes: {', '.join(MODE_WORDS)}"],
        )
    session = inv.connect()
    project = render_project(session)
    wanted = RENDER_MODES[MODE_WORDS[word]]
    now = RENDER_MODES.get(project.GetCurrentRenderMode(), "unknown")
    plan = MutationPlan(
        command="render mode set",
        target=f"project {project.GetName()}",
        changes=[Change("mode", now, wanted)] if now != wanted else [],
        force_cmd=force_cmd(inv, "render", "mode", "set", word),
        snapshot="none",
        next=[hint("render presets", "check the current render mode")],
    )

    def apply() -> None:
        if not project.SetCurrentRenderMode(MODE_WORDS[word]):
            raise api_failed("SetCurrentRenderMode", f"could not select {wanted}")

    def verify() -> dict[str, object]:
        mode = project.GetCurrentRenderMode()
        return {"mode": RENDER_MODES.get(mode, f"unknown ({mode})")}

    return run_mutation(inv, session, plan, apply, verify)


def run_frame(inv: Invocation) -> Result:
    path = Path(inv.positionals["file"]).expanduser().resolve()
    if path.is_dir():
        raise AxiError(
            "VALIDATION_ERROR",
            f"{collapse_home(path)} is a directory",
            help=["Pass a file path such as ~/frame.png"],
        )
    refuse_existing(path, bool(inv.args.get("overwrite")))
    session = inv.connect()
    project = render_project(session)
    timeline = session.require_timeline(None)
    path.parent.mkdir(parents=True, exist_ok=True)
    with with_page(session, "color") as switched:
        exported = project.ExportCurrentFrameAsStill(str(path))
    if not exported:
        raise api_failed("ExportCurrentFrameAsStill", "could not export the current frame")
    if not path.exists():
        raise api_failed(
            "ExportCurrentFrameAsStill", f"Resolve reported success but wrote no {path.name}"
        )
    data: dict[str, object] = {
        "timeline": timeline.GetName(),
        "path": collapse_home(path),
        "bytes": tree_bytes(path),
    }
    if switched:
        data["page_restored"] = True
    return Result(data)


PRESETS_SPEC = CommandSpec(
    name="presets",
    summary="List render presets, quick export presets and the current format and codec",
    run=run_presets,
    examples=("davinci-axi render presets", "davinci-axi render presets --json"),
    methods=(
        "Project.GetName",
        "Project.GetRenderPresetList",
        "Project.GetQuickExportRenderPresets",
        "Project.GetCurrentRenderFormatAndCodec",
        "Project.GetCurrentRenderMode",
    ),
)

STATUS_SPEC = CommandSpec(
    name="status",
    summary="List render queue jobs with status and completion",
    run=run_status,
    flags=LIST_FLAGS,
    examples=(
        "davinci-axi render status",
        "davinci-axi render status --fields id,status,target_dir,file",
        "davinci-axi render status --limit 5",
    ),
    methods=(
        "Project.GetName",
        "Project.GetRenderJobList",
        "Project.GetRenderJobStatus",
        "Project.IsRenderingInProgress",
    ),
)

FORMATS_SPEC = CommandSpec(
    name="formats",
    summary="List render formats, the codecs of one format and the resolutions",
    run=run_formats,
    flags=(
        Flag("codecs", "str", None, help="list the codecs of this format extension, e.g. mov"),
        Flag("audio", "bool", False, help="use the audio formats and codecs"),
        Flag("resolutions", "bool", False, help="also list the resolutions Resolve offers"),
    ),
    examples=(
        "davinci-axi render formats",
        "davinci-axi render formats --codecs mov",
        "davinci-axi render formats --audio --codecs wav --resolutions",
    ),
    methods=(
        "Project.GetName",
        "Project.GetRenderFormats",
        "Project.GetAudioRenderFormats",
        "Project.GetRenderCodecs",
        "Project.GetAudioRenderCodecs",
        "Project.GetRenderResolutions",
    ),
)

FRAME_SPEC = CommandSpec(
    name="frame",
    summary="Export the current frame of the current timeline as a still file",
    run=run_frame,
    positionals=(Positional("file", help="output image file"),),
    flags=(Flag("overwrite", "bool", False, help="replace the file if it exists"),),
    examples=(
        "davinci-axi render frame ~/frame.png",
        "davinci-axi render frame ~/f.png --overwrite",
    ),
    reads_only=False,
    methods=("Resolve.GetCurrentPage", "Resolve.OpenPage", "Project.ExportCurrentFrameAsStill"),
)

FORMAT_SET_SPEC = CommandSpec(
    name="set",
    summary="Select the render format and codec for the next job",
    run=run_format_set,
    positionals=(
        Positional("format", help="format extension or name (see `render formats`)"),
        Positional("codec", help="codec name or description (see `render formats --codecs`)"),
    ),
    flags=MUTATION_FLAGS,
    examples=(
        "davinci-axi render format set mp4 H265",
        "davinci-axi render format set mov H264 --force",
    ),
    reads_only=False,
    methods=(
        "Project.GetRenderFormats",
        "Project.GetRenderCodecs",
        "Project.GetCurrentRenderFormatAndCodec",
        "Project.SetCurrentRenderFormatAndCodec",
    ),
)

MODE_SET_SPEC = CommandSpec(
    name="set",
    summary="Select the render mode: individual clips or a single clip",
    run=run_mode_set,
    positionals=(Positional("mode", help="individual or single"),),
    flags=MUTATION_FLAGS,
    examples=(
        "davinci-axi render mode set individual",
        "davinci-axi render mode set single --force",
    ),
    reads_only=False,
    methods=("Project.GetCurrentRenderMode", "Project.SetCurrentRenderMode"),
)

SUBCOMMANDS: dict[str, CommandSpec] = {
    "presets": PRESETS_SPEC,
    "status": STATUS_SPEC,
    "formats": FORMATS_SPEC,
    **JOB_SPECS,
    "quick-export": PRESET_SPECS["quick-export"],
    "frame": FRAME_SPEC,
    "format": CommandSpec(
        name="format",
        summary="Select the render format and codec",
        subcommands={"set": FORMAT_SET_SPEC},
        examples=("davinci-axi render format set mp4 H265",),
        reads_only=False,
        methods=FORMAT_SET_SPEC.methods,
    ),
    "mode": CommandSpec(
        name="mode",
        summary="Select the render mode",
        subcommands={"set": MODE_SET_SPEC},
        examples=("davinci-axi render mode set individual",),
        reads_only=False,
        methods=MODE_SET_SPEC.methods,
    ),
    "preset": PRESET_SPECS["preset"],
}

SPEC = CommandSpec(
    name="render",
    summary="Inspect and control render presets, the render queue and render jobs",
    subcommands=SUBCOMMANDS,
    examples=(
        "davinci-axi render status",
        "davinci-axi render add --target-dir ~/renders --mkdir",
        "davinci-axi render start job-2 --force",
    ),
    reads_only=False,
    methods=tuple(dict.fromkeys(m for spec in SUBCOMMANDS.values() for m in spec.methods)),
)
