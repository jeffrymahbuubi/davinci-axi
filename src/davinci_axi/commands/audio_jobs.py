"""`audio sync|classify|speech` and the `audio` job specs (U18).

Every verb is a mutation built on `mutate.run_mutation`: a dry run until `--force`, then save,
snapshot, write and read-back (R27-R29). Calls with no getter (sync, classify) follow KTD23: the
call's return value is the check, and the summary says "not read back". A False from the AI calls
means Studio or AI Extras is missing, which is the coded error `NOT_AVAILABLE` (KTD29).
Transcription lives in `audio_transcribe`; `JOB_SPECS` here lists every job verb.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

from davinci_axi.commands.audio_common import (
    FOLDER_FLAG,
    MEDIA_READS,
    SCOPE_ARGS,
    UNTESTED,
    AudioPool,
    AudioProject,
    enum,
    flag_words,
    not_available,
    pick_scope,
    verb,
)
from davinci_axi.commands.audio_transcribe import SPECS as TRANSCRIBE_SPECS
from davinci_axi.commands.item_detail import str_arg
from davinci_axi.commands.media_folders import (
    CLIPS,
    abs_path,
    clips_target,
    open_pool,
    resolve_clips,
    show_hint,
    where_is,
)
from davinci_axi.commands.render_common import force_words, methods_of
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import Change, MutationPlan, run_mutation
from davinci_axi.output import hint
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

SPEECH_MAX_CHARS = 350
SPEECH_RANGES = {"speed": (-10.0, 10.0), "variation": (0.0, 1.0), "pitch": (-2.0, 2.0)}
SYNC_MODES = ("waveform", "timecode", "in", "out", "marker")


def run_sync(inv: Invocation) -> Result:
    mode, channel = str_arg(inv, "mode"), str_arg(inv, "channel")
    if channel is not None and mode != "waveform":
        raise AxiError(
            "VALIDATION_ERROR",
            "--channel only applies with --mode waveform",
            help=["Valid example: --mode waveform --channel automatic"],
        )
    number: int | None = None
    if channel is not None and channel not in ("automatic", "mix"):
        if not channel.isdigit() or int(channel) < 1:
            raise AxiError(
                "VALIDATION_ERROR",
                f"cannot read {channel} as a channel",
                help=["Pass a channel number from 1, or automatic, or mix"],
            )
        number = int(channel)
    ctx = open_pool(inv)
    found = resolve_clips(ctx, inv.positionals["clips"])
    if len(found) < 2:
        raise AxiError(
            "VALIDATION_ERROR",
            "audio sync needs at least one video clip and one audio clip",
            help=["Valid example: davinci-axi audio sync A001_beach,music_bed --mode waveform"],
        )
    settings: dict[str, object] = {}
    if mode is not None:
        settings["syncMode"] = enum(ctx.session, f"AUDIO_SYNC_{mode.upper()}")
    if channel is not None:
        settings["channelNumber"] = (
            number
            if number is not None
            else enum(ctx.session, f"AUDIO_SYNC_CHANNEL_{channel.upper()}")
        )
    for flag, key in (
        ("keep_embedded", "retainEmbeddedAudio"),
        ("keep_video_metadata", "retainVideoMetadata"),
    ):
        if inv.args.get(flag):
            settings[key] = True
    words = ["audio", "sync", inv.positionals["clips"], *flag_words(inv, ("mode", "channel"))]
    plan = MutationPlan(
        command="audio sync",
        target=clips_target(found),
        changes=[Change("called", False, True)],
        force_cmd=force_words(inv, words, ("keep-embedded", "keep-video-metadata")),
        extra={"settings": ", ".join(f"{k}={v}" for k, v in settings.items()) or "(defaults)"},
        warning=f"links audio to video clips in the media pool; {UNTESTED}",
        next=[show_hint(found)],
    )
    done = {"ok": False}

    def apply() -> None:
        pool = cast(AudioPool, ctx.pool)
        done["ok"] = bool(pool.AutoSyncAudio([f.clip for f in found], dict(settings)))
        if not done["ok"]:
            raise api_failed(
                "MediaPool.AutoSyncAudio", "the list needs at least one video and one audio clip"
            )

    return run_mutation(inv, ctx.session, plan, apply, lambda: {"called": done["ok"]})


def _classify(inv: Invocation, clear: bool, method: str, warning: str) -> Result:
    base = ["audio", "classify", *(["clear"] if clear else [])]
    scope = pick_scope(inv, base)
    plan = MutationPlan(
        command=f"audio classify{' clear' if clear else ''}",
        target=scope.target,
        changes=[Change("called", False, True)],
        force_cmd=force_words(inv, scope.words),
        irreversible=clear,
        warning=f"{warning}; {UNTESTED}",
    )
    done = {"ok": False}

    def apply() -> None:
        done["ok"] = all(getattr(target, method)() for target in scope.objects)
        if not done["ok"]:
            raise not_available(method)

    return run_mutation(inv, scope.ctx.session, plan, apply, lambda: {"called": done["ok"]})


def run_classify(inv: Invocation) -> Result:
    return _classify(
        inv, False, "PerformAudioClassification", "needs AI Extras; adds audio categories"
    )


def run_classify_clear(inv: Invocation) -> Result:
    return _classify(inv, True, "ClearAudioClassification", "removes the audio classification")


# ---- speech ---------------------------------------------------------------------------------


def run_speech(inv: Invocation) -> Result:
    text = inv.positionals["text"]
    if not text.strip() or len(text) > SPEECH_MAX_CHARS:
        raise AxiError(
            "VALIDATION_ERROR",
            f"the text must be 1 to {SPEECH_MAX_CHARS} characters, got {len(text)}",
            help=["Split longer text into several `audio speech` runs"],
        )
    settings: dict[str, object] = {"TextInput": text}
    for key, (low, high) in SPEECH_RANGES.items():
        value = inv.args.get(key)
        if isinstance(value, float):
            if not low <= value <= high:
                raise AxiError(
                    "VALIDATION_ERROR",
                    f"--{key} must be between {low:g} and {high:g}, got {value:g}",
                    help=[f"Valid example: --{key} {(low + high) / 2:g}"],
                )
            settings[key.capitalize()] = value
    voice, voice_file = str_arg(inv, "voice"), str_arg(inv, "voice_file")
    if voice_file is not None:
        path = abs_path(voice_file)
        if not Path(path).is_file():
            raise not_found("voice file", voice_file, "Pass the path of an existing voice file")
        if voice is not None and voice != "Custom Voice":
            raise AxiError(
                "VALIDATION_ERROR",
                "--voice-file needs the voice Custom Voice",
                help=["Drop --voice, or pass --voice 'Custom Voice'"],
            )
        voice, settings["CustomVoiceFile"] = "Custom Voice", path
    if voice is not None:
        settings["VoiceModel"] = voice
    ctx = open_pool(inv)
    project = cast(AudioProject, ctx.session.require_project())
    names = ("voice", "voice-file", "speed", "variation", "pitch")
    words = ["audio", "speech", text, *flag_words(inv, names)]
    plan = MutationPlan(
        command="audio speech",
        target=f"project {project.GetName()}",
        changes=[Change("clip", None, "(new clip)")],
        force_cmd=force_words(inv, words),
        warning="needs the Speech Generator (Studio with AI Extras); adds a clip to the media pool",
    )
    created: dict[str, str] = {}

    def apply() -> None:
        clip = project.GenerateSpeech(dict(settings))
        if clip is None:
            raise not_available("GenerateSpeech")
        created["uid"], created["name"] = clip.GetUniqueId(), clip.GetName()
        plan.changes[0] = Change("clip", None, created["name"])
        plan.extra["clip"] = created["name"]
        plan.next.append(hint(f"media show {created['uid']}", "show the new clip"))

    def verify() -> dict[str, object]:
        return {"clip": created["name"] if where_is(ctx, created["uid"]) else None}

    return run_mutation(inv, ctx.session, plan, apply, verify)


# ---- specs ----------------------------------------------------------------------------------

SYNC_SPEC = verb(
    "sync",
    "Sync audio clips to video clips in the media pool (not read back)",
    run_sync,
    positionals=(CLIPS,),
    flags=(
        Flag("mode", "str", None, choices=SYNC_MODES, help="how to match the clips"),
        Flag("channel", "str", None, help="waveform channel: a number, automatic or mix"),
        Flag("keep-embedded", "bool", False, help="keep the original embedded audio"),
        Flag("keep-video-metadata", "bool", False, help="keep the video clip metadata"),
    ),
    examples=(
        "davinci-axi audio sync A001_beach,music_bed --mode waveform",
        "davinci-axi audio sync A001_beach,music_bed --mode waveform --channel mix --force",
    ),
    methods=methods_of(MediaPool="AutoSyncAudio") + MEDIA_READS,
)
CLASSIFY_CLEAR_SPEC = verb(
    "clear",
    "Clear the audio classification of clips or a folder (not read back)",
    run_classify_clear,
    positionals=SCOPE_ARGS,
    flags=(FOLDER_FLAG,),
    examples=(
        "davinci-axi audio classify clear A001_beach",
        "davinci-axi audio classify clear --folder Master/Footage --force",
    ),
    methods=methods_of(MediaPoolItem="ClearAudioClassification", Folder="ClearAudioClassification")
    + MEDIA_READS,
)
CLASSIFY_SPEC = verb(
    "classify",
    "Classify the audio of clips or a folder (not read back)",
    run_classify,
    positionals=SCOPE_ARGS,
    flags=(FOLDER_FLAG,),
    examples=(
        "davinci-axi audio classify A001_beach",
        "davinci-axi audio classify --folder Master/Footage --force",
    ),
    methods=methods_of(
        MediaPoolItem="PerformAudioClassification", Folder="PerformAudioClassification"
    )
    + MEDIA_READS,
)
CLASSIFY_SPEC = replace(
    CLASSIFY_SPEC,
    subcommands={"clear": CLASSIFY_CLEAR_SPEC},
    methods=CLASSIFY_SPEC.methods + CLASSIFY_CLEAR_SPEC.methods,
)
SPEECH_SPEC = verb(
    "speech",
    "Generate speech from text into a new media pool clip (needs AI Extras)",
    run_speech,
    positionals=(Positional("text", help="text to speak (up to 350 characters)"),),
    flags=(
        Flag("voice", "str", None, help="voice model, such as 'Female 1' or 'Male 1'"),
        Flag("voice-file", "str", None, help="a custom voice file (uses the Custom Voice model)"),
        Flag("speed", "float", None, help="-10 to 10"),
        Flag("variation", "float", None, help="0 to 1"),
        Flag("pitch", "float", None, help="-2 to 2"),
    ),
    examples=(
        "davinci-axi audio speech 'Welcome to the show' --voice 'Female 1'",
        "davinci-axi audio speech 'Hello' --speed 1.5 --force",
    ),
    methods=methods_of(Project="GenerateSpeech GetName") + MEDIA_READS,
)

JOB_SPECS: dict[str, CommandSpec] = {
    "sync": SYNC_SPEC,
    **TRANSCRIBE_SPECS,
    "classify": CLASSIFY_SPEC,
    "speech": SPEECH_SPEC,
}
