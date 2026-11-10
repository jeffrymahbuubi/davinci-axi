"""Fake model of the live `Insert*IntoTimeline` calls (F24, KTD17).

The live calls take no position or track: the item lands on V1 at the video mark-in (marks are
relative to the timeline start; the range is inclusive) or at the playhead (150 frames), and every
unlocked video and audio track is ripple-inserted: the item spanning the landing frame is cut in
two and everything at or after it moves later by the item's length.
"""

from __future__ import annotations

from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem

STANDARD_FRAMES = 150
FRAME_RATE = 24


def _timecode_frame(tc: str) -> int:
    hh, mm, ss, ff = (int(part) for part in tc.replace(";", ":").split(":"))
    return ((hh * 60 + mm) * 60 + ss) * FRAME_RATE + ff


def playhead_frame(timeline: FakeTimeline) -> int:
    """The absolute frame of `current_tc`, counted from the timeline's start timecode."""
    offset = _timecode_frame(timeline.current_tc) - _timecode_frame(timeline.start_tc)
    return timeline.start_frame + offset


def place_insert(timeline: FakeTimeline, name: str, kind: str) -> FakeTimelineItem | None:
    """Insert like Resolve does; None when there is no name, no V1 or V1 is locked."""
    track = timeline._track("video", 1)
    if not name or track is None or track.locked:
        return None
    marks = timeline.mark_in_out.get("video")
    if marks:
        frame = timeline.start_frame + int(marks["in"])  # marks are relative to the start
        length = int(marks["out"]) - int(marks["in"]) + 1  # and inclusive
    else:
        frame, length = playhead_frame(timeline), STANDARD_FRAMES
    for track_type in ("video", "audio"):
        for index, each in enumerate(timeline.tracks[track_type], start=1):
            if each.locked:
                continue
            for item in list(each.items):
                if item.start < frame < item.start + item.duration:
                    tail = timeline.add_item(
                        track_type,
                        index,
                        item.name,
                        frame + length,
                        item.start + item.duration - frame,
                        media=item.media,
                        kind=item.kind,
                        color=item.color,
                        source_start=item.source_start + frame - item.start,
                    )
                    tail.enabled = item.enabled
                    item.duration = frame - item.start
                elif item.start >= frame:
                    item.start += length
    item = timeline.add_item("video", 1, name, frame, length, kind=kind)
    timeline.end_frame += length
    return item
