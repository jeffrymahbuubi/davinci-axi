"""`ai scene-cuts` and `ai subtitles`: analysis jobs that write into a timeline (U20, R35).

Both run on the current timeline, or on `--timeline <name>` kept current while the job runs
(`--no-wait` refuses another timeline, because the timeline would switch back mid job). They poll
the project's status getter with `poll.ANALYSIS` and are verified by status (KTD23); the number
of markers or subtitle tracks added is reported as a read-back fact. Caption options are words
mapped to `resolve.AUTO_CAPTION_*` at run time (KTD24).
"""

from __future__ import annotations

from typing import cast

from davinci_axi.commands.ai_status import (
    SCENE_CUTS,
    SUBTITLES,
    AiTimeline,
    refuse_no_wait_switch,
    require_ok,
    run_job,
    timeline_scope,
)
from davinci_axi.commands.audio_common import enum, flag_words, verb
from davinci_axi.commands.item_detail import str_arg
from davinci_axi.commands.render_common import force_words, methods_of, words
from davinci_axi.commands.timeline_tracks import TIMELINE_FLAG
from davinci_axi.errors import AxiError
from davinci_axi.mutate import MutationPlan, check_snapshot_flags
from davinci_axi.output import hint
from davinci_axi.poll import POLL_FLAGS, poll_args
from davinci_axi.session import Session
from davinci_axi.spec import Flag, Invocation, Result

LANGUAGES = words(
    "auto mandarin-simplified dutch english finnish french german hindi indonesian italian "
    "japanese korean malay norwegian polish portuguese romanian russian spanish swedish turkish "
    "vietnamese tamil thai danish mandarin-traditional"
)
PRESETS = {
    "default": "AUTO_CAPTION_SUBTITLE_DEFAULT",
    "teletext": "AUTO_CAPTION_TELETEXT",
    "netflix": "AUTO_CAPTION_NETFLIX",
}
LINE_BREAKS = {"single": "AUTO_CAPTION_LINE_SINGLE", "double": "AUTO_CAPTION_LINE_DOUBLE"}
LANGUAGE_PACK = "Extended AI Transcription Language Support 2"
CHARS_RANGE = (1, 60)
GAP_RANGE = (0, 10)
TIMELINE_READS = methods_of(
    Timeline="GetName GetUniqueId GetMarkers GetTrackCount",
    Project="GetCurrentTimeline SetCurrentTimeline GetName GetTimelineCount GetTimelineByIndex",
)


def _open(inv: Invocation) -> tuple[Session, AiTimeline]:
    check_snapshot_flags(inv)
    session = inv.connect()
    return session, cast(AiTimeline, session.require_timeline(str_arg(inv, "timeline")))


def _in_range(inv: Invocation, flag: str, low: int, high: int) -> int | None:
    value = inv.args.get(flag.replace("-", "_"))
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if not low <= value <= high:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--{flag} must be between {low} and {high}, got {value}",
            help=[f"Valid example: --{flag} {(low + high) // 2}"],
        )
    return value


def run_scene_cuts(inv: Invocation) -> Result:
    wait, _, _ = poll_args(inv)
    session, timeline = _open(inv)
    refuse_no_wait_switch(session, timeline, wait)
    plan = MutationPlan(
        command="ai scene-cuts",
        target=f"timeline {timeline.GetName()}",
        changes=[],
        force_cmd=force_words(inv, ["ai", "scene-cuts"]),
        warning="adds cuts and markers along the current timeline; needs Studio",
        next=[hint("timelines", "list the timelines"), hint("ai status", "check the job")],
    )
    markers_before = len(timeline.GetMarkers() or {})

    def start() -> None:
        require_ok(timeline.DetectSceneCuts(), "Timeline.DetectSceneCuts")

    def after(waited: bool) -> None:
        if waited:
            plan.extra["markers_added"] = len(timeline.GetMarkers() or {}) - markers_before

    return run_job(
        inv,
        session,
        plan,
        SCENE_CUTS,
        start,
        scope=lambda: timeline_scope(session, timeline, plan),
        after=after,
    )


def run_subtitles(inv: Invocation) -> Result:
    wait, _, _ = poll_args(inv)
    language, preset = str_arg(inv, "language"), str_arg(inv, "preset")
    line_break = str_arg(inv, "line_break")
    if language is not None and language.lower() not in LANGUAGES:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown subtitle language {language}",
            help=[f"Valid languages: {', '.join(LANGUAGES)}"],
        )
    chars = _in_range(inv, "chars-per-line", *CHARS_RANGE)
    gap = _in_range(inv, "gap", *GAP_RANGE)
    session, timeline = _open(inv)
    refuse_no_wait_switch(session, timeline, wait)
    settings: dict[str, object] = {}
    if language is not None:
        settings["language"] = enum(
            session, f"AUTO_CAPTION_{language.lower().replace('-', '_').upper()}"
        )
    if preset is not None:
        settings["captionPreset"] = enum(session, PRESETS[preset])
    if chars is not None:
        settings["charsPerLine"] = chars
    if line_break is not None:
        settings["lineBreak"] = enum(session, LINE_BREAKS[line_break])
    if gap is not None:
        settings["gap"] = gap
    shown = [
        f"{key}={value}"
        for key, value in (
            ("language", language),
            ("captionPreset", preset),
            ("charsPerLine", chars),
            ("lineBreak", line_break),
            ("gap", gap),
        )
        if value is not None
    ]
    words = ["ai", "subtitles"]
    words += flag_words(inv, ("language", "preset", "chars-per-line", "line-break", "gap"))
    plan = MutationPlan(
        command="ai subtitles",
        target=f"timeline {timeline.GetName()}",
        changes=[],
        force_cmd=force_words(inv, words),
        extra={"settings": ", ".join(shown) or "(defaults)"},
        warning=(
            "adds a subtitle track to the timeline from its audio; needs Studio; non-built-in "
            f"languages need the Extras package {LANGUAGE_PACK}"
        ),
        next=[hint("ai status", "check the job")],
    )
    tracks_before = timeline.GetTrackCount("subtitle")

    def start() -> None:
        require_ok(
            timeline.CreateSubtitlesFromAudio(dict(settings) or None),
            "Timeline.CreateSubtitlesFromAudio",
        )

    def after(waited: bool) -> None:
        if waited:
            plan.extra["subtitle_tracks_added"] = timeline.GetTrackCount("subtitle") - tracks_before

    return run_job(
        inv,
        session,
        plan,
        SUBTITLES,
        start,
        scope=lambda: timeline_scope(session, timeline, plan),
        after=after,
        package=LANGUAGE_PACK,
        advisory=True,
    )


SCENE_CUTS_SPEC = verb(
    "scene-cuts",
    "Detect scene cuts along a timeline and wait for the job (progress on stderr)",
    run_scene_cuts,
    flags=(TIMELINE_FLAG, *POLL_FLAGS),
    examples=(
        "davinci-axi ai scene-cuts",
        "davinci-axi ai scene-cuts --force",
        "davinci-axi ai scene-cuts --force --no-wait",
    ),
    methods=methods_of(Timeline="DetectSceneCuts", Project="GetDetectSceneCutsStatus")
    + TIMELINE_READS,
)
SUBTITLES_SPEC = verb(
    "subtitles",
    "Create subtitles from the timeline audio and wait for the job (progress on stderr)",
    run_subtitles,
    flags=(
        Flag("language", "str", None, help="spoken language word, such as english or german"),
        Flag("preset", "str", None, choices=tuple(PRESETS), help="caption preset"),
        Flag("chars-per-line", "int", None, help="maximum characters per line, 1 to 60"),
        Flag("line-break", "str", None, choices=tuple(LINE_BREAKS), help="single or double line"),
        Flag("gap", "int", None, help="gap between subtitles in frames, 0 to 10"),
        TIMELINE_FLAG,
        *POLL_FLAGS,
    ),
    examples=(
        "davinci-axi ai subtitles --language english --preset netflix",
        "davinci-axi ai subtitles --chars-per-line 32 --line-break double --force",
        "davinci-axi ai subtitles --force --no-wait",
    ),
    methods=methods_of(
        Timeline="CreateSubtitlesFromAudio", Project="GetCreateSubtitlesFromAudioStatus"
    )
    + TIMELINE_READS,
)
