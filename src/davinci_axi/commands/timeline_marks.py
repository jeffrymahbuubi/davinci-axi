"""Timeline in/out marks, which Resolve stores relative to the timeline start (F32)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from davinci_axi.commands.timeline_tracks import TIMELINE_FLAG, target_timeline
from davinci_axi.errors import AxiError
from davinci_axi.resolve_types import Info, TimelineApi
from davinci_axi.spec import CommandSpec, Invocation, Result


@dataclass(frozen=True)
class MarkRange:
    """One mark pair: `rel_*` as Resolve returns them, `abs_*` as `items` prints them."""

    rel_in: int
    rel_out: int
    start: int  # Timeline.GetStartFrame()

    @property
    def abs_in(self) -> int:
        return self.start + self.rel_in

    @property
    def abs_out(self) -> int:
        return self.start + self.rel_out

    @property
    def frames(self) -> int:
        return self.rel_out - self.rel_in + 1  # inclusive (live: 60-120 -> 61)

    @property
    def text(self) -> str:
        return f"{self.rel_in}-{self.rel_out} (absolute {self.abs_in}-{self.abs_out})"


def mark_range(marks: Info, kind: str, start: int) -> MarkRange | None:
    """`marks[kind]` as a MarkRange, or None when that pair is unset or malformed."""
    pair = marks.get(kind) if isinstance(marks, dict) else None
    if not isinstance(pair, dict):
        return None
    try:
        rel_in, rel_out = int(pair["in"]), int(pair["out"])
    except (KeyError, TypeError, ValueError):
        return None
    if rel_out < rel_in:
        return None
    return MarkRange(rel_in, rel_out, start)


def video_marks(timeline: TimelineApi) -> MarkRange | None:
    """The timeline's video marks, or None when unset."""
    tl = cast(Any, timeline)
    return mark_range(tl.GetMarkInOut() or {}, "video", int(tl.GetStartFrame()))


def to_relative(frame: int, start: int) -> int:
    """`frame - start`; VALIDATION_ERROR when the frame is before the timeline start."""
    if frame < start:
        raise AxiError(
            "VALIDATION_ERROR",
            f"frame {frame} is before the timeline start {start}",
            help=["Pass an absolute frame at or after the timeline start, as `items` prints"],
        )
    return frame - start


def _pair(marks: MarkRange | None) -> dict[str, int] | None:
    if marks is None:
        return None
    return {
        "in": marks.rel_in,
        "out": marks.rel_out,
        "abs_in": marks.abs_in,
        "abs_out": marks.abs_out,
        "frames": marks.frames,
    }


def run_mark_show(inv: Invocation) -> Result:
    """Both frame systems for the video and audio marks (read only)."""
    session = inv.connect()
    timeline = target_timeline(session, inv)
    start = int(timeline.GetStartFrame())
    marks = timeline.GetMarkInOut() or {}
    data: dict[str, object] = {
        "timeline": timeline.GetName(),
        "start_frame": start,
        "video": _pair(mark_range(marks, "video", start)),
        "audio": _pair(mark_range(marks, "audio", start)),
        "note": "frames are relative to the timeline start; absolute = frame + start_frame",
    }
    return Result(data)


MARK_SHOW_SPEC = CommandSpec(
    name="show",
    summary="Show the mark in/out in relative and absolute frames",
    run=run_mark_show,
    flags=(TIMELINE_FLAG,),
    examples=("davinci-axi timeline mark show",),
    methods=("Timeline.GetMarkInOut", "Timeline.GetStartFrame"),
)
