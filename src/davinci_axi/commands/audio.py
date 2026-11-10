"""`audio`: Fairlight and audio verbs on timeline items, tracks and clips (U18, R35).

This module holds the item, track and project verbs (normalize, align, voice isolation, audio
mapping, Fairlight presets, insert); the media-pool jobs (sync, transcribe, classify, speech) live
in `audio_jobs` and `audio_transcribe`. Every write is a dry run until `--force`, then save,
snapshot, write and read-back through `mutate.run_mutation`. Calls with no getter (normalize,
align, preset apply) follow KTD23: the call's return value is the check, and the summary says
"not read back". Verbs on timeline items run inside `with_current_timeline` and say so.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from davinci_axi.commands.audio_common import (
    TIMELINE_READS,
    UNTESTED,
    AudioProject,
    Picked,
    enum,
    flag_words,
    not_available,
    open_items,
    timeline_step,
    verb,
)
from davinci_axi.commands.audio_gain import GAIN_SPEC, ITEMS, ref_of, volume_text, volumes
from davinci_axi.commands.audio_gaps import GAPS_SPEC
from davinci_axi.commands.audio_jobs import JOB_SPECS
from davinci_axi.commands.audio_loudness import LOUDNESS_SPEC
from davinci_axi.commands.audio_mapping import SPECS as MAPPING_SPECS
from davinci_axi.commands.item_detail import str_arg, walk_items
from davinci_axi.commands.media_folders import abs_path
from davinci_axi.commands.render_common import force_words, methods_of
from davinci_axi.commands.timeline_tracks import TIMELINE_FLAG
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    Change,
    MutationPlan,
    check_snapshot_flags,
    run_mutation,
    with_page,
)
from davinci_axi.output import empty_state, hint
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

ITEM_BOOLS = ("independent",)


# ---- normalize and align --------------------------------------------------------------------


DEFAULT_MODE = "Sample Peak Program"


def _list_modes(inv: Invocation) -> Result:
    session = inv.connect()
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    modes = list(timeline.GetNormalizeAudioModes() or [])
    if not modes:
        scope = f"in timeline {timeline.GetName()}"
        return Result({"modes": empty_state("normalization modes", scope, {})})
    data: dict[str, object] = {
        "timeline": timeline.GetName(),
        "modes": modes,
        "default": DEFAULT_MODE,
    }
    return Result(data, [hint("audio normalize A1:1 --mode <mode> --force", "use one")])


def _normalize_options(inv: Invocation, sel: Picked) -> dict[str, object]:
    mode = str_arg(inv, "mode")
    if mode is not None:
        modes = list(sel.timeline.GetNormalizeAudioModes() or [])
        if mode not in modes:
            raise AxiError(
                "VALIDATION_ERROR",
                f"unknown normalization mode {mode}",
                help=[f"Valid modes: {', '.join(modes) if modes else '(none reported)'}"],
            )
    options: dict[str, object] = {}
    if mode is not None:
        options["normalizationMode"] = mode
    for flag, key in (("level", "targetLevel"), ("loudness", "targetLoudness")):
        value = inv.args.get(flag)
        if isinstance(value, float):
            options[key] = value
    if inv.args.get("independent"):
        options["setLevelMode"] = enum(sel.session, "NORMALIZE_AUDIO_SET_LEVEL_INDEPENDENT")
    return options


def _options_text(options: dict[str, object]) -> str:
    parts = []
    for key, value in options.items():
        if key == "setLevelMode" and isinstance(value, float):
            parts.append(f"{key}=INDEPENDENT ({value:g})")
        else:
            parts.append(f"{key}={value}")
    return ", ".join(parts) or "(defaults)"


def run_normalize(inv: Invocation) -> Result:
    if inv.args.get("list_modes"):
        return _list_modes(inv)
    if not inv.positionals.get("items"):
        raise AxiError(
            "VALIDATION_ERROR",
            "pass the items to normalize, or --list-modes",
            help=[
                "Valid example: davinci-axi audio normalize A1:1,A1:2 --loudness -24",
                "Valid example: davinci-axi audio normalize --list-modes",
            ],
        )
    sel = open_items(inv)
    options = _normalize_options(inv, sel)
    words = ["audio", "normalize", inv.positionals["items"]]
    words += flag_words(inv, ("mode", "level", "loudness"))
    independent = bool(inv.args.get("independent"))
    before = volumes(sel)
    plan = MutationPlan(
        command="audio normalize",
        target=sel.target,
        changes=[Change("called", False, True)],
        force_cmd=force_words(inv, words, ITEM_BOOLS),
        extra={
            "options": _options_text(options),
            "measures": "each clip's own audio (clip level, before track effects such as "
            "voice isolation and before the bus/mix)",
            "scope": "independent: each item to the target"
            if independent
            else "relative: one gain for all items, keeping their balance",
            "readback": "AudioVolume per item (the API has no loudness meter)",
            "volume_before": volume_text(before),
        },
        warning="changes the clip volume of the items; read back as AudioVolume; " + UNTESTED,
        next=[hint(f"item {ref_of(sel.located[0])}", "show the first item")],
    )
    done = {"ok": False}

    def apply() -> None:
        with timeline_step(sel.session, sel.timeline, plan):
            done["ok"] = bool(sel.timeline.NormalizeAudioLevel(sel.items, dict(options) or None))
            after = volumes(sel)
        if not done["ok"]:
            raise not_available("Timeline.NormalizeAudioLevel", "DaVinci Resolve Studio")
        plan.extra["volume_after"] = volume_text(after)
        unreadable = [r for r in after if after[r] is None or before[r] is None]
        unchanged = [r for r in after if r not in unreadable and after[r] == before[r]]
        if unreadable:
            plan.extra["not_readable"] = unreadable
            plan.next.insert(
                0,
                "AudioVolume is not readable on some items, so the result cannot be read back; "
                "measure the render offline (see --help)",
            )
        if unchanged:
            plan.extra["unchanged"] = unchanged
        if len(unchanged) == len(after):
            plan.next.insert(
                0,
                "No clip volume changed: Resolve may apply normalize elsewhere, or the items "
                "were already at the target; measure the render offline (see --help)",
            )

    return run_mutation(inv, sel.session, plan, apply, lambda: {"called": done["ok"]})


def _align_options(inv: Invocation) -> tuple[str | None, str | None]:
    using, track = str_arg(inv, "using"), str_arg(inv, "track")
    if track is not None and using != "waveform":
        raise AxiError(
            "VALIDATION_ERROR",
            "--track only applies with --using waveform",
            help=["Valid example: --using waveform --track mix"],
        )
    if track is not None and track not in ("mix", "auto") and not track.isdigit():
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot read {track} as a track",
            help=["Pass a track number from 1, or mix, or auto"],
        )
    return using, track


def run_align(inv: Invocation) -> Result:
    using, track = _align_options(inv)
    sel = open_items(inv)
    options: dict[str, object] = {}
    if using is not None:
        options["SyncUsing"] = enum(sel.session, f"AUTO_ALIGN_CLIPS_USING_{using.upper()}")
    if track is not None:
        if track.isdigit():
            options["UseTrack"] = int(track)
        else:
            name = "AUTOMATIC" if track == "auto" else "MIX"
            options["UseTrack"] = enum(sel.session, f"AUTO_ALIGN_CLIPS_WAVEFORM_TRACK_{name}")
    words = ["audio", "align", inv.positionals["items"], *flag_words(inv, ("using", "track"))]
    plan = MutationPlan(
        command="audio align",
        target=sel.target,
        changes=[Change("called", False, True)],
        force_cmd=force_words(inv, words),
        extra={"options": ", ".join(f"{k}={v}" for k, v in options.items()) or "(defaults)"},
        warning=f"moves the items in the timeline to line them up; {UNTESTED}",
    )
    done = {"ok": False}

    def apply() -> None:
        with timeline_step(sel.session, sel.timeline, plan):
            done["ok"] = bool(sel.timeline.AutoAlignClips(sel.items, dict(options) or None))
        if not done["ok"]:
            raise not_available("Timeline.AutoAlignClips", "DaVinci Resolve Studio")

    return run_mutation(inv, sel.session, plan, apply, lambda: {"called": done["ok"]})


# ---- Fairlight presets and insert -----------------------------------------------------------


def _presets(session: Session) -> list[str]:
    return list(session.resolve.GetFairlightPresets() or [])


def run_preset_list(inv: Invocation) -> Result:
    names = _presets(inv.connect())
    data: dict[str, object] = {
        "presets": names or empty_state("Fairlight presets", "in this Resolve", {})
    }
    return Result(data, [hint("audio preset apply <name>", "apply one to the current timeline")])


def run_preset_apply(inv: Invocation) -> Result:
    name = inv.positionals["name"]
    check_snapshot_flags(inv)
    session = inv.connect()
    names = _presets(session)
    if name not in names:
        listing = ", ".join(names) if names else "(none)"
        raise not_found("Fairlight preset", name, f"Fairlight presets: {listing}")
    project = cast(AudioProject, session.require_project())
    timeline = session.require_timeline()
    plan = MutationPlan(
        command="audio preset apply",
        target=f"current timeline {timeline.GetName()}",
        changes=[Change("called", False, True)],
        force_cmd=force_words(inv, ["audio", "preset", "apply", name]),
        warning=f"applies the preset to the whole current timeline; {UNTESTED}",
    )
    done = {"ok": False}

    def apply() -> None:
        done["ok"] = bool(project.ApplyFairlightPresetToCurrentTimeline(name))
        if not done["ok"]:
            raise api_failed("ApplyFairlightPresetToCurrentTimeline", f"could not apply {name}")

    return run_mutation(inv, session, plan, apply, lambda: {"called": done["ok"]})


def _audio_items(timeline: Any) -> int:
    return sum(1 for _ in walk_items(timeline, ("audio",)))


def _samples(inv: Invocation, key: str) -> int:
    value = inv.args.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def run_insert(inv: Invocation) -> Result:
    path = Path(abs_path(inv.positionals["file"]))
    if not path.is_file():
        raise not_found("audio file", str(path), "Pass the path of an existing audio file")
    offset, duration = _samples(inv, "offset_samples"), _samples(inv, "duration_samples")
    if offset < 0 or duration < 0:
        raise AxiError(
            "VALIDATION_ERROR", "sample counts cannot be negative", help=["Valid: 48000"]
        )
    check_snapshot_flags(inv)
    session = inv.connect()
    project = cast(AudioProject, session.require_project())
    timeline = session.require_timeline()
    before = _audio_items(timeline)
    words = ["audio", "insert", inv.positionals["file"]]
    words += flag_words(inv, ("offset-samples", "duration-samples"))
    plan = MutationPlan(
        command="audio insert",
        target=f"timeline {timeline.GetName()}",
        changes=[Change("audio items", before, before + 1)],
        force_cmd=force_words(inv, words),
        warning="lands on the current Fairlight track at the playhead (needs a selected track)",
        next=[hint("items --track A1", "list the audio items")],
    )

    def apply() -> None:
        with with_page(session, "fairlight") as switched:
            if switched:
                plan.extra["page_restored"] = True
            ok = project.InsertAudioToCurrentTrackAtPlayhead(str(path), offset, duration)
        if not ok:
            raise api_failed(
                "InsertAudioToCurrentTrackAtPlayhead", "select an audio track on Fairlight first"
            )

    return run_mutation(inv, session, plan, apply, lambda: {"audio items": _audio_items(timeline)})


# ---- specs ----------------------------------------------------------------------------------

NORMALIZE_SPEC = verb(
    "normalize",
    "Normalize the audio level of timeline items (read back as AudioVolume)",
    run_normalize,
    positionals=(Positional("items", required=False, help=ITEMS.help),),
    flags=(
        TIMELINE_FLAG,
        Flag("list-modes", "bool", False, help="print the timeline's normalization modes and exit"),
        Flag("mode", "str", None, help="a mode from the timeline's list (default: Resolve's)"),
        Flag("level", "float", None, help="target level in dBFS, such as -9"),
        Flag("loudness", "float", None, help="target loudness in LKFS, such as -24"),
        Flag(
            "independent",
            "bool",
            False,
            help="set each item's level on its own (setLevelMode INDEPENDENT); default RELATIVE "
            "moves all items by one gain so their balance is kept",
        ),
    ),
    examples=(
        "davinci-axi audio normalize --list-modes",
        "davinci-axi audio normalize A1:1,A1:2 --loudness -24",
        "davinci-axi audio normalize A1:1 --mode 'True Peak' --level -3 --force",
    ),
    notes=(
        "--list-modes prints the modes Resolve offers (Sample Peak Program is the default); "
        "--level is dBFS for peak modes, --loudness is LKFS/LUFS for loudness modes",
        "The result is read back as each item's AudioVolume property; `item A1:1` shows it. "
        "Where Resolve reports no AudioVolume for an item it is listed as not readable",
        "Normalize works per clip on the clip's own audio, before track effects (voice isolation, "
        "EQ, dynamics) and before the bus. Without --independent it applies ONE common gain to "
        "every item (live: every A1 item moved by the same -1.1 dB); --independent normalises "
        "each clip to the target separately. Neither sets the loudness of the mix",
        "To hit a mix target such as -14 LUFS (YouTube): normalize, render, measure the file "
        "offline (ffmpeg -i out.mp4 -af ebur128=peak=true -f null - prints integrated LUFS and "
        "true peak; ffmpeg is optional and not part of davinci-axi), then "
        "`audio gain <all A1 items> --db <target - measured>` and render again; keep true peak "
        "under -1 dBTP",
    ),
    methods=methods_of(Timeline="GetNormalizeAudioModes NormalizeAudioLevel")
    + TIMELINE_READS
    + methods_of(TimelineItem="GetProperties"),
)
ALIGN_SPEC = verb(
    "align",
    "Align timeline items by waveform or timecode (not read back)",
    run_align,
    positionals=(ITEMS,),
    flags=(
        TIMELINE_FLAG,
        Flag("using", "str", None, choices=("waveform", "timecode"), help="how to line up"),
        Flag("track", "str", None, help="waveform track: a number, mix or auto"),
    ),
    examples=(
        "davinci-axi audio align V1:1,V1:2 --using waveform --track mix",
        "davinci-axi audio align V1:1,V1:2 --using timecode --force",
    ),
    methods=methods_of(Timeline="AutoAlignClips") + TIMELINE_READS,
)
PRESET_LIST_SPEC = CommandSpec(
    name="list",
    summary="List the Fairlight presets",
    run=run_preset_list,
    examples=("davinci-axi audio preset list",),
    methods=methods_of(Resolve="GetFairlightPresets"),
)
PRESET_APPLY_SPEC = verb(
    "apply",
    "Apply a Fairlight preset to the current timeline (not read back)",
    run_preset_apply,
    positionals=(Positional("name", help="preset name (see `audio preset list`)"),),
    examples=(
        "davinci-axi audio preset apply Dialogue",
        "davinci-axi audio preset apply Dialogue --force",
    ),
    methods=methods_of(
        Resolve="GetFairlightPresets", Project="ApplyFairlightPresetToCurrentTimeline"
    ),
)
INSERT_SPEC = verb(
    "insert",
    "Insert an audio file on the current Fairlight track at the playhead",
    run_insert,
    positionals=(Positional("file", help="path of the audio file"),),
    flags=(
        Flag("offset-samples", "int", 0, help="start offset into the file, in samples"),
        Flag("duration-samples", "int", 0, help="length to insert, in samples"),
    ),
    examples=(
        "davinci-axi audio insert ~/sfx/door.wav --duration-samples 96000",
        "davinci-axi audio insert ~/sfx/door.wav --offset-samples 4800 --force",
    ),
    methods=methods_of(
        Project="InsertAudioToCurrentTrackAtPlayhead", Resolve="OpenPage GetCurrentPage"
    )
    + TIMELINE_READS,
)

SUBCOMMANDS: dict[str, CommandSpec] = {
    "normalize": NORMALIZE_SPEC,
    "gain": GAIN_SPEC,
    "loudness": LOUDNESS_SPEC,
    "gaps": GAPS_SPEC,
    "align": ALIGN_SPEC,
    **MAPPING_SPECS,
    "preset": CommandSpec(
        name="preset",
        summary="List or apply Fairlight presets",
        subcommands={"list": PRESET_LIST_SPEC, "apply": PRESET_APPLY_SPEC},
        reads_only=False,
        methods=PRESET_LIST_SPEC.methods + PRESET_APPLY_SPEC.methods,
    ),
    "insert": INSERT_SPEC,
    **JOB_SPECS,
}

SPEC = CommandSpec(
    name="audio",
    summary="Fairlight and audio: levels, alignment, voice isolation, mappings, speech",
    subcommands=SUBCOMMANDS,
    examples=(
        "davinci-axi audio normalize A1:1,A1:2 --loudness -24",
        "davinci-axi audio voice-isolation set A1:1 --on --amount 60 --force",
        "davinci-axi audio transcribe A001_beach --force",
        "davinci-axi audio loudness --target -14",
        "davinci-axi audio gaps --min-s 2",
    ),
    reads_only=False,
    methods=tuple(dict.fromkeys(m for s in SUBCOMMANDS.values() for m in s.methods)),
)
