"""`render`: presets and queue reads (R22), format and mode switches, frame export (R33).

The queue verbs live in `render_jobs`, the preset verbs in `render_presets`; this module builds the
parent `SPEC`. `format set` and `mode set` change which format, codec and output mode the next
job uses: UI-level state with getters, so they are verified but take no snapshot.
"""

from __future__ import annotations

from typing import Any

from davinci_axi.commands.render_common import render_project
from davinci_axi.commands.render_deliver import DELIVER_SPEC
from davinci_axi.commands.render_frame import FRAME_SPEC
from davinci_axi.commands.render_jobs import JOB_SPECS
from davinci_axi.commands.render_master import MASTER_SPEC
from davinci_axi.commands.render_presets import PRESET_SPECS
from davinci_axi.commands.render_settings import SETTINGS_SPEC
from davinci_axi.commands.render_sheet import SHEET_SPEC
from davinci_axi.commands.render_start import START_SPEC
from davinci_axi.commands.render_status import SCOPE, STATUS_SPEC
from davinci_axi.commands.render_verify import VERIFY_SPEC
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.output import empty_state, hint
from davinci_axi.resolve_types import ProjectApi
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

RENDER_MODES = {0: "individual clips", 1: "single clip"}
MODE_WORDS = {"individual": 0, "single": 1}


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
    extension = inv.args.get("codecs")
    wants_codecs = isinstance(extension, str) and bool(extension)
    if not wants_codecs:
        data["formats"] = (
            _pairs(formats, "name", "extension")
            if formats
            else empty_state(f"{kind} formats", f"in project {project.GetName()}", {})
        )
    help_lines: list[str] = []
    if isinstance(extension, str) and extension:
        codecs: dict[str, Any] = (
            project.GetAudioRenderCodecs(extension) if audio else project.GetRenderCodecs(extension)
        ) or {}
        if not codecs:
            known = ", ".join(str(e) for e in formats.values()) or "(none)"
            raise not_found("render format extension", extension, f"Extensions: {known}")
        named = next((name for name, ext in formats.items() if ext == extension), None)
        if named is not None:
            data["format"] = named
        data["extension"] = extension
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
        extra={} if changes else {"note": "already selected"},
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

FORMATS_SPEC = CommandSpec(
    name="formats",
    summary="List render formats, or the codecs of one format, and the resolutions",
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
    notes=("--codecs prints only that format's codecs; leave it out to list every format",),
    methods=(
        "Project.GetName",
        "Project.GetRenderFormats",
        "Project.GetAudioRenderFormats",
        "Project.GetRenderCodecs",
        "Project.GetAudioRenderCodecs",
        "Project.GetRenderResolutions",
    ),
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
    notes=(
        "If the format and codec are already selected the result says `note: already selected`",
    ),
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
    "add": JOB_SPECS["add"],
    "start": START_SPEC,
    "stop": JOB_SPECS["stop"],
    "delete": JOB_SPECS["delete"],
    "deliver": DELIVER_SPEC,
    "verify": VERIFY_SPEC,
    "master": MASTER_SPEC,
    "sheet": SHEET_SPEC,
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
    "settings": SETTINGS_SPEC,
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
        "davinci-axi render verify ~/out/edit.mp4 --max-mb 50 --lufs -14",
        "davinci-axi render start job-2 --force --verify --max-mb 6",
        "davinci-axi render master ~/out/edit.mp4 --lufs -14",
        "davinci-axi render sheet ~/out/edit.mp4 --every 10",
    ),
    reads_only=False,
    methods=tuple(dict.fromkeys(m for spec in SUBCOMMANDS.values() for m in spec.methods)),
)
