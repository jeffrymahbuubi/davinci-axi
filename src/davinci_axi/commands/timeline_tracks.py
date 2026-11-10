"""`timeline track` and `timeline marker` verbs, plus the helpers `timeline.py` shares (U11).

Every verb is a mutation built on `mutate.run_mutation`: a dry run without `--force`, then
save, snapshot, write and read-back with `--force`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from typing import cast

from davinci_axi import markers, registry
from davinci_axi.commands.timeline_track_lock import switch_spec
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.output import hint
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.resolve_types_edit import TimelineEdit
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

TIMECODE = re.compile(r"^\d{2}:\d{2}:\d{2}[:;]\d{2}$")
TRACK_KINDS = ("video", "audio", "subtitle")
TIMELINE_FLAG = Flag("timeline", "str", None, help="timeline name (default: the current timeline)")


def text_arg(inv: Invocation, key: str) -> str | None:
    value = inv.args.get(key)
    return value if isinstance(value, str) and value != "" else None


def context_words(inv: Invocation) -> list[str]:
    """`--timeline` and `--snapshot-out`, repeated in the force command of a dry run."""
    words: list[str] = []
    for flag, key in (("--timeline", "timeline"), ("--snapshot-out", "snapshot_out")):
        value = text_arg(inv, key)
        if value is not None:
            words.extend([flag, value])
    return words


def target_timeline(session: Session, inv: Invocation) -> TimelineEdit:
    """The `--timeline` named timeline, or the current one."""
    return cast(TimelineEdit, session.require_timeline(text_arg(inv, "timeline")))


def timelines_hint() -> list[str]:
    if "timelines" not in registry.COMMANDS:
        return []
    return [hint("timelines", "list the timelines and their track counts")]


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def count_items(timeline: TimelineApi) -> int:
    total = 0
    for kind in TRACK_KINDS:
        for index in range(1, timeline.GetTrackCount(kind) + 1):
            total += len(timeline.GetItemListInTrack(kind, index) or [])
    return total


def same_value(expected: object, actual: object) -> bool:
    if expected == actual or str(expected) == str(actual):
        return True
    try:
        return abs(float(str(expected)) - float(str(actual))) <= 1e-6
    except ValueError:
        return False


def wire(value: object) -> object:
    """Settings travel as strings (`timelineFrameRate` is read as a number but set as text)."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:g}"
    return value if isinstance(value, str) else str(value)


def is_custom(value: object) -> bool:
    return str(value).strip().lower() in ("1", "true")


def timecode_arg(inv: Invocation) -> str:
    code = inv.positionals["tc"]
    if not TIMECODE.match(code):
        raise AxiError(
            "VALIDATION_ERROR",
            f"invalid timecode {code}",
            help=["Valid timecode: HH:MM:SS:FF or HH:MM:SS;FF (drop frame), e.g. 01:00:02:00"],
        )
    return code


def nominal_fps(rate: object) -> int:
    """The whole-number frame count per second: 29.97 -> 30, "23.976" -> 24, 25.0 -> 25."""
    try:
        value = float(str(rate))
    except ValueError:
        return 24
    nearest = round(value)
    return nearest if abs(value - nearest) < 0.01 else math.ceil(value)


def check_timecode_fields(code: str, settings: Mapping[str, object]) -> None:
    """VALIDATION_ERROR when a field of `code` is out of range for the timeline's rate, or the
    frame is a dropped frame on a drop-frame timeline. `code` already matches TIMECODE."""
    hh, mm, ss, ff = (int(part) for part in re.split(r"[:;]", code))
    rate = settings.get("timelineFrameRate", 24)
    fps = nominal_fps(rate)
    if ff >= fps:
        raise AxiError(
            "VALIDATION_ERROR",
            f"frame field {ff:02d} is out of range for a {rate} fps timeline (00-{fps - 1})",
            help=["Valid example: 01:00:11:29 at 30 fps"],
        )
    for name, number, limit in (("hour", hh, 24), ("minute", mm, 60), ("second", ss, 60)):
        if number >= limit:
            raise AxiError(
                "VALIDATION_ERROR",
                f"{name} field {number:02d} is out of range",
                help=["Valid example: 01:00:11:29 (hour 00-23, minute and second 00-59)"],
            )
    flag = str(settings.get("timelineDropFrameTimecode", "0")).strip()
    drop = ";" in code or flag in ("1", "true", "True")
    if drop and fps in (30, 60) and ss == 0 and mm % 10 != 0 and ff < fps // 15:
        raise AxiError(
            "VALIDATION_ERROR",
            f"{code} is a dropped frame on this drop-frame timeline",
            help=[
                "Drop-frame timelines skip frames 00-01 (29.97) or 00-03 (59.94) at the start of "
                "each minute except every tenth"
            ],
        )


def finish(
    inv: Invocation,
    session: Session,
    plan: MutationPlan,
    method: str,
    call: Callable[[], bool],
    verify: Callable[[], dict[str, object]],
    detail: str = "",
) -> Result:
    """Run `plan`; a False return from `call` becomes API_FAILED naming `method`."""

    def apply() -> None:
        if not call():
            raise api_failed(method, detail)

    return run_mutation(inv, session, plan, apply, verify)


def track_kind(inv: Invocation) -> str:
    kind = inv.positionals["type"]
    if kind not in TRACK_KINDS:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown track type {kind}",
            help=[f"Valid track types: {', '.join(TRACK_KINDS)}"],
        )
    return kind


def track_index(inv: Invocation) -> int:
    raw = inv.positionals["n"]
    try:
        index = int(raw)
    except ValueError:
        index = 0
    if index < 1:
        raise AxiError(
            "VALIDATION_ERROR",
            f"track number must be 1 or more, got {raw}",
            help=["Valid example: davinci-axi timeline track enable video 2"],
        )
    return index


def existing_track(timeline: TimelineApi, kind: str, index: int) -> None:
    count = timeline.GetTrackCount(kind)
    if index > count:
        raise AxiError(
            "NOT_FOUND",
            f"{kind} track {index} not found in timeline {timeline.GetName()}",
            help=[f"The timeline has {plural(count, kind + ' track')}"],
        )


def run_add(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    kind, subtype = track_kind(inv), text_arg(inv, "subtype")
    if subtype is not None and kind != "audio":
        raise AxiError(
            "VALIDATION_ERROR",
            "--subtype applies to audio tracks only",
            help=["Valid example: davinci-axi timeline track add audio --subtype mono"],
        )
    session = inv.connect()
    timeline = target_timeline(session, inv)
    before = timeline.GetTrackCount(kind)
    words = ["timeline", "track", "add", kind]
    if subtype is not None:
        words.extend(["--subtype", subtype])
    plan = MutationPlan(
        command="timeline track add",
        target=f"timeline {timeline.GetName()}",
        changes=[Change(f"{kind}_tracks", before, before + 1)],
        force_cmd=force_cmd(inv, *words, *context_words(inv)),
        next=timelines_hint(),
    )

    def call() -> bool:
        return timeline.AddTrack(kind, subtype) if subtype else timeline.AddTrack(kind)

    return finish(
        inv,
        session,
        plan,
        "AddTrack",
        call,
        lambda: {f"{kind}_tracks": timeline.GetTrackCount(kind)},
    )


def run_delete(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    kind, index = track_kind(inv), track_index(inv)
    session = inv.connect()
    timeline = target_timeline(session, inv)
    existing_track(timeline, kind, index)
    before = timeline.GetTrackCount(kind)
    on_track = len(timeline.GetItemListInTrack(kind, index) or [])
    plan = MutationPlan(
        command="timeline track delete",
        target=f"timeline {timeline.GetName()}",
        changes=[Change(f"{kind}_tracks", before, before - 1)],
        force_cmd=force_cmd(
            inv, "timeline", "track", "delete", kind, str(index), *context_words(inv)
        ),
        irreversible=True,
        warning=(
            f"deletes {kind} track {index} ({timeline.GetTrackName(kind, index)}) "
            f"and its {plural(on_track, 'item')}; the snapshot holds the saved project"
        ),
        next=timelines_hint(),
    )
    return finish(
        inv,
        session,
        plan,
        "DeleteTrack",
        lambda: timeline.DeleteTrack(kind, index),
        lambda: {f"{kind}_tracks": timeline.GetTrackCount(kind)},
    )


def run_rename(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    kind, index = track_kind(inv), track_index(inv)
    new = text_arg(inv, "to")
    if new is None:
        raise AxiError(
            "VALIDATION_ERROR",
            "--to needs a track name",
            help=["Valid example: davinci-axi timeline track rename video 1 --to Main"],
        )
    session = inv.connect()
    timeline = target_timeline(session, inv)
    existing_track(timeline, kind, index)
    before = timeline.GetTrackName(kind, index)
    plan = MutationPlan(
        command="timeline track rename",
        target=f"timeline {timeline.GetName()}, {kind} track {index}",
        changes=[] if before == new else [Change("track_name", before, new)],
        force_cmd=force_cmd(
            inv, "timeline", "track", "rename", kind, str(index), "--to", new, *context_words(inv)
        ),
        next=timelines_hint(),
    )
    return finish(
        inv,
        session,
        plan,
        "SetTrackName",
        lambda: timeline.SetTrackName(kind, index, new),
        lambda: {"track_name": timeline.GetTrackName(kind, index)},
    )


def _frame_check(timeline: TimelineApi, frame: int) -> None:
    length = timeline.GetEndFrame() - timeline.GetStartFrame()
    if frame > length:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--frame {frame} is past the end of timeline {timeline.GetName()} ({length} frames)",
            help=["Marker frames are offsets from the start of the timeline"],
        )


def run_marker_add(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    markers.check_add_flags(inv)
    session = inv.connect()
    timeline = target_timeline(session, inv)
    frame = cast(int, inv.args["frame"])
    _frame_check(timeline, frame)
    words = ["timeline", "marker", "add", *context_words(inv)]
    plan, apply, verify = markers.add_plan(inv, timeline, f"timeline {timeline.GetName()}", words)
    plan.extra["absolute_frame"] = timeline.GetStartFrame() + frame
    return run_mutation(inv, session, plan, apply, verify)


def run_marker_delete(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    markers.check_delete_flags(inv)
    session = inv.connect()
    timeline = target_timeline(session, inv)
    words = ["timeline", "marker", "delete", *context_words(inv)]
    plan, apply, verify = markers.delete_plan(
        inv, timeline, f"timeline {timeline.GetName()}", words
    )
    plan.irreversible = True
    plan.warning = "deleted markers cannot be brought back; the snapshot holds the saved project"
    frame = inv.args.get("frame")
    if isinstance(frame, int):
        plan.extra["absolute_frame"] = timeline.GetStartFrame() + frame
    return run_mutation(inv, session, plan, apply, verify)


_TRACK_ARGS = (
    Positional("type", help="track type: video, audio or subtitle"),
    Positional("n", help="1-based track number"),
)

TRACK_SPEC = CommandSpec(
    name="track",
    summary="Add, delete, rename, enable or lock timeline tracks",
    subcommands={
        "add": CommandSpec(
            name="add",
            summary="Add a track to a timeline",
            run=run_add,
            positionals=(Positional("type", help="track type: video, audio or subtitle"),),
            flags=(
                Flag("subtype", "str", None, help="audio track format, e.g. mono or stereo"),
                TIMELINE_FLAG,
                *MUTATION_FLAGS,
            ),
            examples=(
                "davinci-axi timeline track add audio",
                "davinci-axi timeline track add audio --subtype mono --force",
            ),
            reads_only=False,
            methods=("Timeline.AddTrack", "Timeline.GetTrackCount"),
        ),
        "delete": CommandSpec(
            name="delete",
            summary="Delete a track and every item on it (irreversible)",
            run=run_delete,
            positionals=_TRACK_ARGS,
            flags=(TIMELINE_FLAG, *MUTATION_FLAGS),
            examples=(
                "davinci-axi timeline track delete video 2",
                "davinci-axi timeline track delete video 2 --force",
            ),
            reads_only=False,
            methods=("Timeline.DeleteTrack", "Timeline.GetTrackCount"),
        ),
        "rename": CommandSpec(
            name="rename",
            summary="Rename a track",
            run=run_rename,
            positionals=_TRACK_ARGS,
            flags=(
                Flag("to", "str", None, required=True, help="new track name"),
                TIMELINE_FLAG,
                *MUTATION_FLAGS,
            ),
            examples=(
                "davinci-axi timeline track rename video 1 --to Main",
                'davinci-axi timeline track rename audio 1 --to "Dialogue" --force',
            ),
            reads_only=False,
            methods=("Timeline.SetTrackName", "Timeline.GetTrackName"),
        ),
        "enable": switch_spec("enable", "enabled", True, "SetTrackEnable", "GetIsTrackEnabled"),
        "disable": switch_spec("disable", "enabled", False, "SetTrackEnable", "GetIsTrackEnabled"),
        "lock": switch_spec("lock", "locked", True, "SetTrackLock", "GetIsTrackLocked"),
        "unlock": switch_spec("unlock", "locked", False, "SetTrackLock", "GetIsTrackLocked"),
    },
    reads_only=False,
    methods=(
        "Timeline.AddTrack",
        "Timeline.DeleteTrack",
        "Timeline.SetTrackName",
        "Timeline.SetTrackEnable",
        "Timeline.SetTrackLock",
    ),
)

MARKER_SPEC = CommandSpec(
    name="marker",
    summary="Add or delete timeline markers",
    subcommands={
        "add": CommandSpec(
            name="add",
            summary="Add a marker at a frame offset from the timeline start",
            run=run_marker_add,
            flags=(*markers.MARKER_ADD_FLAGS, TIMELINE_FLAG, *MUTATION_FLAGS),
            examples=(
                "davinci-axi timeline marker add --frame 10 --color Green --name Pick",
                "davinci-axi timeline marker add --frame 10 --name Pick --force",
            ),
            reads_only=False,
            methods=("Timeline.AddMarker", "Timeline.GetMarkers"),
        ),
        "delete": CommandSpec(
            name="delete",
            summary="Delete markers by frame, colour or custom data (irreversible)",
            run=run_marker_delete,
            flags=(*markers.MARKER_DELETE_FLAGS, TIMELINE_FLAG, *MUTATION_FLAGS),
            examples=(
                "davinci-axi timeline marker delete --frame 10",
                "davinci-axi timeline marker delete --color Green --force",
            ),
            reads_only=False,
            methods=(
                "Timeline.DeleteMarkerAtFrame",
                "Timeline.DeleteMarkersByColor",
                "Timeline.DeleteMarkerByCustomData",
                "Timeline.GetMarkers",
            ),
        ),
    },
    reads_only=False,
    methods=(
        "Timeline.AddMarker",
        "Timeline.DeleteMarkerAtFrame",
        "Timeline.DeleteMarkersByColor",
        "Timeline.DeleteMarkerByCustomData",
    ),
)
