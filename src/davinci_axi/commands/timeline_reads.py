"""`timeline show`: one timeline in detail (U26, R36). Read-only; never switches timelines (R24).

`Timeline.GetCurrentVideoItem` is the item under the playhead, which only means something for the
current timeline (KTD34), so a non-current target answers `current_video_item: null` without the
call.
"""

from __future__ import annotations

import shlex

from davinci_axi import registry
from davinci_axi.commands.item_detail import str_arg, track_label, walk_items
from davinci_axi.commands.items import TIMELINE_FLAG
from davinci_axi.output import hint
from davinci_axi.resolve_types import TimelineApi, TimelineItemApi
from davinci_axi.spec import CommandSpec, Invocation, Result


def _item_block(item: TimelineItemApi) -> dict[str, object]:
    kind, index = item.GetTrackTypeAndIndex()[:2]
    return {
        "id": item.GetUniqueId(),
        "name": item.GetName(),
        "track": track_label(str(kind), int(index)),
    }


def _pool_clip(timeline: TimelineApi) -> dict[str, object] | None:
    clip = timeline.GetMediaPoolItem()
    if clip is None:
        return None
    return {"id": clip.GetUniqueId(), "name": clip.GetName()}


def _position(timeline: TimelineApi, uid: str) -> str | None:
    """`V1:2` for the video item with this unique id, or `None` when it is not on a video track."""
    for located in walk_items(timeline, ("video",)):
        if located.item.GetUniqueId() == uid:
            return f"{located.track}:{located.position}"
    return None


def run_show(inv: Invocation) -> Result:
    session = inv.connect()
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    current = session.current_timeline()
    name = timeline.GetName()
    is_current = current is not None and current.GetName() == name
    start, end = timeline.GetStartFrame(), timeline.GetEndFrame()
    video_item = timeline.GetCurrentVideoItem() if is_current else None
    video_block = _item_block(video_item) if video_item is not None else None
    data: dict[str, object] = {
        "name": name,
        "id": timeline.GetUniqueId(),
        "current": is_current,
        "frames": end - start,
        "start_frame": start,
        "end_frame": end,
        "start_tc": timeline.GetStartTimecode(),
        "current_tc": timeline.GetCurrentTimecode(),
        "fps": (timeline.GetSettings() or {}).get("timelineFrameRate"),
        "video": timeline.GetTrackCount("video"),
        "audio": timeline.GetTrackCount("audio"),
        "subtitle": timeline.GetTrackCount("subtitle"),
        "markers": len(timeline.GetMarkers() or {}),
        "pool_clip": _pool_clip(timeline),
        "current_video_item": video_block,
    }
    lines: list[str] = []
    if "items" in registry.COMMANDS:
        lines.append(
            hint(f"items --timeline {shlex.quote(name)}", "list the items of this timeline")
        )
    if data["markers"] and "timeline" in registry.COMMANDS:
        lines.append(hint("timeline marker list", "read the markers' names, notes and colours"))
    if video_block is not None and video_item is not None and "item" in registry.COMMANDS:
        ref = _position(timeline, str(video_block["id"])) or str(video_block["id"])
        lines.append(hint(f"item {ref}", "show the current video item in detail"))
    return Result(data, lines)


SHOW_SPEC = CommandSpec(
    name="show",
    summary=(
        "Show one timeline in detail: frames, timecode, tracks, markers, its media pool clip "
        "and the current video item"
    ),
    run=run_show,
    flags=(TIMELINE_FLAG,),
    examples=("davinci-axi timeline show", "davinci-axi timeline show --timeline 'Edit v2'"),
    methods=(
        "Resolve.GetProjectManager",
        "ProjectManager.GetCurrentProject",
        "Project.GetName",
        "Project.GetCurrentTimeline",
        "Project.GetTimelineCount",
        "Project.GetTimelineByIndex",
        "Timeline.GetName",
        "Timeline.GetUniqueId",
        "Timeline.GetStartFrame",
        "Timeline.GetEndFrame",
        "Timeline.GetStartTimecode",
        "Timeline.GetCurrentTimecode",
        "Timeline.GetSettings",
        "Timeline.GetTrackCount",
        "Timeline.GetMarkers",
        "Timeline.GetMediaPoolItem",
        "Timeline.GetCurrentVideoItem",
        "Timeline.GetItemListInTrack",
        "TimelineItem.GetUniqueId",
        "TimelineItem.GetName",
        "TimelineItem.GetTrackTypeAndIndex",
        "MediaPoolItem.GetUniqueId",
        "MediaPoolItem.GetName",
    ),
)
