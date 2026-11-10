"""Where a title or generator insert lands, who it hurts, and placing it through the marks (F24).

`Timeline.Insert*IntoTimeline` and `InsertFusion*IntoTimeline` take no position, track or length.
Live they land on V1 at the playhead (or at the video mark-in with the mark range's length when the
timeline has in/out marks), 150 frames long, and ripple-insert across every unlocked track. This
module predicts that landing from live state, refuses when it would split or shift anything,
sets the video marks around the call when `--at/--frame/--duration` are given, and reads the
landing back. `timeline insert` and `fusion insert` both call it.

Resolve stores the marks relative to the timeline start and a mark pair is inclusive (60-120 is 61
frames), so `Landing` stays absolute and only the mark read and write convert (F32).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, cast

from davinci_axi.commands.item_detail import TRACK_TYPES, Located, track_label, walk_items
from davinci_axi.commands.render_frame import frame_to_timecode
from davinci_axi.commands.timeline_marks import mark_range, to_relative
from davinci_axi.commands.timeline_tracks import TIMECODE
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import MutationPlan
from davinci_axi.resolve_types import Info, TimelineApi
from davinci_axi.resolve_types_edit import TimelineEdit
from davinci_axi.spec import Flag, Invocation

DEFAULT_FRAMES = 150  # Standard Generator Duration as seen live; shown as "about 150"
ABOUT_FRAMES = f"about {DEFAULT_FRAMES}"
RECIPE = (
    "Or build it on an empty scratch timeline and place it yourself: "
    "`timeline create scratch --force`; `<insert command> --force`; "
    "`fusion tool set Template 'StyledText=...' --item <id> --force` (titles); "
    "`timeline compound <id> --name card --force`; `timeline open <edit> --force`; "
    "`timeline append card --track 2 --record-frame <frame> --force`"
)
INSERT_FLAGS: tuple[Flag, ...] = (
    Flag(
        "at",
        "str",
        help="timecode to place it at; sets the video marks for the call, then restores them",
    ),
    Flag("frame", "int", help="absolute timeline frame instead of --at, as `items` prints"),
    Flag(
        "duration",
        "int",
        help="length in frames; default: the mark range or Resolve's Standard Generator Duration",
    ),
    Flag(
        "allow-ripple",
        "bool",
        False,
        help="insert even though items at or after the landing frame will be split or shifted",
    ),
)


class TimelineMarks(TimelineEdit, Protocol):
    """The mark calls the insert needs on top of the read API."""

    def SetMarkInOut(self, markIn: int, markOut: int, markType: str | None = None) -> bool: ...

    def ClearMarkInOut(self, markType: str | None = None) -> bool: ...


@dataclass(frozen=True)
class Landing:
    """Where the insert will land: absolute timeline `frame`, its `tc`, `length` (None: unknown)."""

    frame: int
    tc: str
    length: int | None
    source: str  # "marks" | "playhead" | "flags"


@dataclass(frozen=True)
class Victims:
    """Items the insert would hurt, as `V1:2` labels: cut in two, or pushed later."""

    splits: list[str]
    shifted: list[str]


def timecode_to_frame(tc: str, fps: float) -> int:
    """Absolute frame of a non-drop timecode, counting `round(fps)` frames a second."""
    if not TIMECODE.match(tc):
        raise AxiError(
            "VALIDATION_ERROR",
            f"{tc!r} is not a timecode (HH:MM:SS:FF)",
            help=["Valid timecode: HH:MM:SS:FF, e.g. 01:00:02:00"],
        )
    if ";" in tc:
        raise AxiError(
            "VALIDATION_ERROR",
            "drop-frame timecodes are not supported here",
            help=["Use --frame with the absolute frame number `items` prints"],
        )
    rate = round(fps)
    if rate < 1:
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot convert {tc}: bad frame rate {fps}",
            help=["Check the timeline frame rate with `davinci-axi timeline show`"],
        )
    hh, mm, ss, ff = (int(part) for part in tc.split(":"))
    if ff >= rate:
        raise AxiError(
            "VALIDATION_ERROR",
            f"{tc} has frame {ff}, but the rate is {rate} frames",
            help=[f"Valid example: a frame field from 00 to {rate - 1:02d} at this frame rate"],
        )
    return ((hh * 60 + mm) * 60 + ss) * rate + ff


def check_insert_flags(inv: Invocation) -> None:
    """Validate `--at/--frame/--duration` before any Resolve call."""
    at = cast("str | None", inv.args.get("at"))
    frame = cast("int | None", inv.args.get("frame"))
    duration = cast("int | None", inv.args.get("duration"))
    if at is not None and frame is not None:
        raise AxiError(
            "VALIDATION_ERROR",
            "--at and --frame are mutually exclusive",
            help=["Pass one of them: --at 01:00:02:00 or --frame 86448"],
        )
    if at is not None:
        if not TIMECODE.match(at):
            raise AxiError(
                "VALIDATION_ERROR",
                f"invalid timecode {at}",
                help=["Valid timecode: HH:MM:SS:FF, e.g. 01:00:02:00"],
            )
        if ";" in at:
            raise AxiError(
                "VALIDATION_ERROR",
                "drop-frame timecodes are not supported here",
                help=["Use --frame with the absolute frame number `items` prints"],
            )
    if frame is not None and frame < 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--frame must be 0 or more, got {frame}",
            help=["Valid example: --frame 0"],
        )
    if duration is not None and duration < 1:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--duration must be 1 or more, got {duration}",
            help=["Valid example: --duration 100"],
        )


def read_landing(inv: Invocation, timeline: TimelineApi) -> Landing:
    """Predict the landing from the flags, else the video marks, else the playhead."""
    check_insert_flags(inv)
    at = cast("str | None", inv.args.get("at"))
    frame_flag = cast("int | None", inv.args.get("frame"))
    duration = cast("int | None", inv.args.get("duration"))
    placed = at is not None or frame_flag is not None or duration is not None
    fps = float((timeline.GetSettings() or {}).get("timelineFrameRate") or 24)
    if (at is not None or frame_flag is not None) and ";" in (timeline.GetStartTimecode() or ""):
        raise AxiError(
            "VALIDATION_ERROR",
            "this is a drop-frame timeline: --at and --frame are not supported here",
            help=["Place it with the scratch-timeline route instead", RECIPE],
        )
    start = int(timeline.GetStartFrame())
    marks = mark_range(timeline.GetMarkInOut() or {}, "video", start)
    if not placed and marks is not None:
        return Landing(marks.abs_in, frame_to_timecode(marks.abs_in, fps), marks.frames, "marks")
    if at is not None:
        frame = timecode_to_frame(at, fps)
    elif frame_flag is not None:
        frame = frame_flag
    elif marks is not None:
        frame = marks.abs_in
    else:
        frame = _playhead(timeline, fps)
    if placed:
        to_relative(frame, start)  # VALIDATION_ERROR before any write when it precedes the start
        length = duration or (marks.frames if marks else DEFAULT_FRAMES)
        return Landing(frame, frame_to_timecode(frame, fps), length, "flags")
    return Landing(frame, frame_to_timecode(frame, fps), None, "playhead")


def _playhead(timeline: TimelineApi, fps: float) -> int:
    return timecode_to_frame(timeline.GetCurrentTimecode(), fps)


def find_victims(timeline: TimelineApi, frame: int) -> Victims:
    """Items on unlocked video and audio tracks that a ripple insert at `frame` splits or shifts."""
    splits: list[str] = []
    shifted: list[str] = []
    for track_type in ("video", "audio"):
        for index in range(1, timeline.GetTrackCount(track_type) + 1):
            if timeline.GetIsTrackLocked(track_type, index):
                continue  # a locked track is not rippled
            label = track_label(track_type, index)
            for position, item in enumerate(timeline.GetItemListInTrack(track_type, index) or []):
                start = int(item.GetStart())
                if start < frame < start + int(item.GetDuration()):
                    splits.append(f"{label}:{position + 1}")
                elif start >= frame:
                    shifted.append(f"{label}:{position + 1}")
    return Victims(splits, shifted)


def refuse_ripple(inv: Invocation, landing: Landing, victims: Victims, recipe_cmd: str) -> None:
    """`WRITE_REFUSED` unless `--allow-ripple` when the insert would split or shift anything."""
    if not (victims.splits or victims.shifted) or inv.args.get("allow_ripple"):
        return
    raise AxiError(
        "WRITE_REFUSED",
        f"inserting at {landing.frame} ({landing.tc}) would split "
        f"{', '.join(victims.splits) or 'nothing'} and shift {len(victims.shifted)} items "
        "on every unlocked track",
        help=[
            "Pass --allow-ripple to do it anyway",
            RECIPE.replace("<insert command>", recipe_cmd),
        ],
    )


def landing_warning(landing: Landing) -> str:
    length = landing.length if landing.length is not None else ABOUT_FRAMES
    return (
        f"lands on V1 at {landing.frame} ({landing.tc}), {length} frames (from {landing.source}); "
        "the API takes no track and ripple-inserts across unlocked tracks"
    )


def landing_extra(landing: Landing, victims: Victims, marks: Info) -> dict[str, object]:
    before = mark_range(marks, "video", 0)
    return {
        "landing": f"V1@{landing.frame} ({landing.tc})",
        "frames": landing.length
        if landing.length is not None
        else f"{ABOUT_FRAMES} (Standard Generator Duration)",
        "splits": ", ".join(victims.splits) or "none",
        "shifted": len(victims.shifted),
        "marks_before": f"{before.rel_in}-{before.rel_out} (timeline-relative)"
        if before
        else "none",
    }


@contextmanager
def marks_for(timeline: TimelineApi, landing: Landing, plan: MutationPlan) -> Iterator[None]:
    """Set the video marks to the landing range (relative frames), then put the old marks back."""
    if landing.source != "flags" or landing.length is None:
        yield
        return
    marks = cast(TimelineMarks, timeline)
    before = (timeline.GetMarkInOut() or {}).get("video")  # relative: restored as read
    rel_in = to_relative(landing.frame, int(timeline.GetStartFrame()))
    rel_out = rel_in + landing.length - 1
    if not marks.SetMarkInOut(rel_in, rel_out, "video"):
        raise api_failed("Timeline.SetMarkInOut", "could not set the video marks for the insert")
    plan.extra["marks_set"] = f"{rel_in}-{rel_out} (timeline-relative)"
    try:
        yield
    finally:
        if isinstance(before, dict) and "in" in before and "out" in before:
            restored = marks.SetMarkInOut(int(before["in"]), int(before["out"]), "video")
        else:
            restored = marks.ClearMarkInOut("video")
        plan.extra["marks_restored"] = bool(restored)


def locate(timeline: TimelineApi, uid: str) -> Located | None:
    for loc in walk_items(timeline, TRACK_TYPES):
        if loc.item.GetUniqueId() == uid:
            return loc
    return None


def landing_read(timeline: TimelineApi, uid: str | None) -> str | None:
    """`V1@86415`: where the new item really is, or None when it is gone."""
    loc = locate(timeline, uid) if uid else None
    return f"{loc.track}@{int(loc.item.GetStart())}" if loc else None
