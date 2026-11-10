"""`render add` and the timeline's in/out marks: Resolve renders the marked range (F24 U4)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from davinci_axi.commands.insert_place import TimelineMarks
from davinci_axi.commands.timeline_marks import video_marks
from davinci_axi.mutate import MutationPlan
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.spec import Flag

RANGE_KEYS = ("MarkIn", "MarkOut", "SelectAllFrames")
MARK_NOTE = (
    "MarkIn/MarkOut set the timeline's in/out marks for the job; the previous marks are put "
    "back after AddRenderJob (marks_restored is read back from the timeline; marks_before says "
    "what was there) unless --keep-marks; the frames are absolute, "
    "Resolve stores them relative to the timeline start"
)
KEEP_MARKS_FLAG = Flag(
    "keep-marks",
    "bool",
    False,
    help="leave the timeline's in/out marks as --set MarkIn/MarkOut set them "
    "(default: put the previous marks back after the job is queued)",
)
SETTINGS_NOTE = (
    "TargetDir, CustomName and every --set key stay as the project's render settings after the "
    "job (even once it is deleted); the API has no getter or reset: put them back with "
    "`render settings set TargetDir=<old>,CustomName=<old> --force`, or pass --preset to load a "
    "preset over them"
)
ADD_NOTES = (
    "VideoQuality is a bit rate in kbps (20000 = 20 Mbps) or Least|Low|Medium|High|Best; "
    "0 = automatic",
    "SelectAllFrames=true renders the whole timeline; it makes MarkIn/MarkOut ignored",
    "MarkIn/MarkOut are absolute timeline frames; Resolve stores them as the timeline's "
    "in/out marks, which read back relative to the timeline start (timeline mark show)",
    SETTINGS_NOTE,
    "With in/out marks set on the timeline Resolve renders only that range: the dry run warns; "
    "pass --all-frames, or --set MarkIn=..,MarkOut=.. (the previous marks are put back after "
    "the job is queued; --keep-marks leaves the new ones)",
    "Settings that Resolve's job info does not echo (VideoQuality, SelectAllFrames, ...) are "
    "listed as applied_settings and cannot be read back",
    "AddRenderJob opens the Deliver page; the page that was open is put back (page_restored)",
)


def note_range(plan: MutationPlan, timeline: TimelineApi, wanted: dict[str, object]) -> None:
    """Warn when the marks will silently limit the render; note when `--set MarkIn` writes them."""
    if "MarkIn" in wanted:
        plan.extra["note"] = MARK_NOTE
    marks = video_marks(timeline)
    if marks is None or any(key in wanted for key in RANGE_KEYS):
        return
    message = (
        f"the timeline has in/out marks {marks.text}: Resolve renders that range, not the whole "
        "timeline; pass --all-frames or --set MarkIn=..,MarkOut=.. "
        "(--set marks are put back after the job unless --keep-marks)"
    )
    plan.warning = f"{plan.warning}; {message}" if plan.warning else message
    plan.extra["timeline_marks"] = f"{marks.rel_in}-{marks.rel_out}"
    plan.extra["timeline_marks_abs"] = f"{marks.abs_in}-{marks.abs_out}"


@contextmanager
def restore_marks(
    timeline: TimelineApi, wanted: dict[str, object], keep: bool, plan: MutationPlan
) -> Iterator[None]:
    """Put the timeline's video marks back after `--set MarkIn/MarkOut` set them for a job."""
    if keep or not any(key in wanted for key in ("MarkIn", "MarkOut")):
        yield
        return
    marks = cast(TimelineMarks, timeline)
    pair: Any = (marks.GetMarkInOut() or {}).get("video")
    plan.extra["marks_before"] = f"{pair['in']}-{pair['out']}" if _is_pair(pair) else "none"
    try:
        yield
    finally:
        try:
            if _is_pair(pair):
                marks.SetMarkInOut(int(pair["in"]), int(pair["out"]), "video")
            else:
                marks.ClearMarkInOut("video")
            after = (marks.GetMarkInOut() or {}).get("video")
            plan.extra["marks_restored"] = _same_marks(pair, after)
        except Exception:  # a failed restore must not hide the job's own result or error
            plan.extra["marks_restored"] = False


def _is_pair(pair: object) -> bool:
    return isinstance(pair, dict) and "in" in pair and "out" in pair


def _same_marks(before: Any, after: Any) -> bool:
    """Two non-pairs are equal; two pairs compare by integer in/out."""
    if not _is_pair(before) or not _is_pair(after):
        return not _is_pair(before) and not _is_pair(after)
    try:
        return (int(before["in"]), int(before["out"])) == (int(after["in"]), int(after["out"]))
    except (TypeError, ValueError):
        return False
