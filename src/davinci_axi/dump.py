"""JSON-ready dump of a project's state: settings, timelines, tracks, items and markers.

Read-only: it calls getters only, so it is safe to run before any mutation.
"""

from __future__ import annotations

from typing import Any

from davinci_axi.resolve_types import MediaPoolItemApi, ProjectApi, TimelineApi, TimelineItemApi

TRACK_TYPES = ("video", "audio", "subtitle")


def _markers(markers: dict[int, dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Marker dicts are keyed by frame offset; flatten to rows so the frame is a field."""
    return [{"frame": frame, **info} for frame, info in sorted((markers or {}).items())]


def _media(item: MediaPoolItemApi | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "name": item.GetName(),
        "id": item.GetUniqueId(),
        "path": item.GetClipProperty("File Path"),
    }


def _item(item: TimelineItemApi) -> dict[str, Any]:
    return {
        "id": item.GetUniqueId(),
        "name": item.GetName(),
        "type": item.GetType(),
        "start": item.GetStart(),
        "end": item.GetEnd(),
        "duration": item.GetDuration(),
        "source_start": item.GetSourceStartFrame(),
        "source_end": item.GetSourceEndFrame(),
        "enabled": item.GetClipEnabled(),
        "color": item.GetClipColor(),
        "flags": item.GetFlagList(),
        "markers": _markers(item.GetMarkers()),
        "media": _media(item.GetMediaPoolItem()),
    }


def _tracks(timeline: TimelineApi) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for track_type in TRACK_TYPES:
        for index in range(1, timeline.GetTrackCount(track_type) + 1):
            tracks.append(
                {
                    "type": track_type,
                    "index": index,
                    "name": timeline.GetTrackName(track_type, index),
                    "enabled": timeline.GetIsTrackEnabled(track_type, index),
                    "locked": timeline.GetIsTrackLocked(track_type, index),
                    "subtype": timeline.GetTrackSubType(track_type, index),
                    "items": [_item(i) for i in timeline.GetItemListInTrack(track_type, index)],
                }
            )
    return tracks


def _timeline(timeline: TimelineApi, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "name": timeline.GetName(),
        "id": timeline.GetUniqueId(),
        "start_frame": timeline.GetStartFrame(),
        "end_frame": timeline.GetEndFrame(),
        "start_timecode": timeline.GetStartTimecode(),
        "settings": timeline.GetSettings(),
        "markers": _markers(timeline.GetMarkers()),
        "tracks": _tracks(timeline),
    }


def dump_project_state(project: ProjectApi) -> dict[str, object]:
    """Settings, timelines, tracks, items and markers of `project` as plain data."""
    timelines: list[dict[str, Any]] = []
    for index in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is not None:
            timelines.append(_timeline(timeline, index))
    current = project.GetCurrentTimeline()
    return {
        "project": project.GetName(),
        "id": project.GetUniqueId(),
        "settings": project.GetSettings(),
        "current_timeline": current.GetName() if current is not None else None,
        "timelines": timelines,
    }
