"""Fake timeline write methods for U11: names, timecodes, markers, tracks, duplicate, settings.

Imports only from `davinci_axi.fake.core|media|timeline|world|ext_writes_core`, never from the
`davinci_axi.fake` package (KTD17). Each method keeps the `_rec` injection contract: an injected
value is returned as `bool` (or as the object for `DuplicateTimeline`, `CreateEmptyTimeline`),
otherwise the side effect happens. `extend()` is idempotent.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import Any

from davinci_axi.fake.core import FakeProject, FakeProjectFolder
from davinci_axi.fake.ext_writes_core import owner_project
from davinci_axi.fake.media import FakeMediaPool
from davinci_axi.fake.timeline import TRACK_TYPES, FakeTimeline, FakeTimelineItem, FakeTrack

_TIMECODE = re.compile(r"^\d{2}:\d{2}:\d{2}[:;]\d{2}$")


def _walk(folder: FakeProjectFolder) -> Iterator[FakeProject]:
    yield from folder.projects.values()
    for child in folder.children.values():
        yield from _walk(child)


def _project_of(timeline: FakeTimeline) -> FakeProject | None:
    for project in _walk(timeline._world.resolve.pm.root):
        if timeline in project.timelines:
            return project
    return None


def _length(timeline: FakeTimeline) -> int:
    return timeline.end_frame - timeline.start_frame


def _timeline_set_name(self: FakeTimeline, timelineName: str) -> bool:
    injected, value = self._rec("SetName", timelineName)
    if injected:
        return bool(value)
    project = _project_of(self)
    if not timelineName or project is None:
        return False
    if any(t.name == timelineName and t is not self for t in project.timelines):
        return False
    self.name = timelineName
    return True


def _set_current_timecode(self: FakeTimeline, timecode: str) -> bool:
    injected, value = self._rec("SetCurrentTimecode", timecode)
    if injected:
        return bool(value)
    if not _TIMECODE.match(timecode):
        return False
    self.current_tc = timecode
    return True


def _set_start_timecode(self: FakeTimeline, timecode: str) -> bool:
    injected, value = self._rec("SetStartTimecode", timecode)
    if injected:
        return bool(value)
    if not _TIMECODE.match(timecode):
        return False
    self.start_tc = timecode
    return True


def _add_marker(
    self: FakeTimeline,
    frameId: int,
    color: str,
    name: str,
    note: str,
    duration: int,
    customData: str | None = None,
) -> bool:
    injected, value = self._rec("AddMarker", frameId, color, name, note, duration, customData)
    if injected:
        return bool(value)
    if frameId < 0 or frameId > _length(self) or frameId in self.markers or duration < 1:
        return False
    self.markers[frameId] = {
        "color": color,
        "duration": duration,
        "note": note,
        "name": name,
        "customData": customData or "",
    }
    return True


def _delete_marker_at_frame(self: FakeTimeline, frameNum: int) -> bool:
    injected, value = self._rec("DeleteMarkerAtFrame", frameNum)
    if injected:
        return bool(value)
    return self.markers.pop(frameNum, None) is not None


def _delete_markers_by_color(self: FakeTimeline, color: str) -> bool:
    injected, value = self._rec("DeleteMarkersByColor", color)
    if injected:
        return bool(value)
    gone = [f for f, m in self.markers.items() if color == "All" or m.get("color") == color]
    for frame in gone:
        del self.markers[frame]
    return bool(gone)


def _delete_marker_by_custom_data(self: FakeTimeline, customData: str) -> bool:
    injected, value = self._rec("DeleteMarkerByCustomData", customData)
    if injected:
        return bool(value)
    gone = [f for f, m in self.markers.items() if m.get("customData") == customData]
    for frame in gone:
        del self.markers[frame]
    return bool(gone)


def _add_track(self: FakeTimeline, trackType: str, subTrackType: str | None = None) -> bool:
    injected, value = self._rec("AddTrack", trackType, subTrackType)
    if injected:
        return bool(value)
    if trackType not in TRACK_TYPES:
        return False
    self.add_track(trackType, subtype=subTrackType or "")
    return True


def _delete_track(self: FakeTimeline, trackType: str, trackIndex: int) -> bool:
    injected, value = self._rec("DeleteTrack", trackType, trackIndex)
    if injected:
        return bool(value)
    tracks = self.tracks.get(trackType)
    if tracks is None or not 1 <= trackIndex <= len(tracks):
        return False
    del tracks[trackIndex - 1]
    for number, track in enumerate(tracks, start=1):
        for item in track.items:
            item.track_index = number
    return True


def _set_track_enable(self: FakeTimeline, trackType: str, trackIndex: int, enabled: bool) -> bool:
    injected, value = self._rec("SetTrackEnable", trackType, trackIndex, enabled)
    if injected:
        return bool(value)
    track = self._track(trackType, trackIndex)
    if track is None:
        return False
    track.enabled = bool(enabled)
    return True


def _set_track_lock(self: FakeTimeline, trackType: str, trackIndex: int, locked: bool) -> bool:
    injected, value = self._rec("SetTrackLock", trackType, trackIndex, locked)
    if injected:
        return bool(value)
    track = self._track(trackType, trackIndex)
    if track is None:
        return False
    track.locked = bool(locked)
    return True


def _set_track_name(self: FakeTimeline, trackType: str, trackIndex: int, name: str) -> bool:
    injected, value = self._rec("SetTrackName", trackType, trackIndex, name)
    if injected:
        return bool(value)
    track = self._track(trackType, trackIndex)
    if track is None or not name:
        return False
    track.name = name
    return True


def _clone_item(source: FakeTimelineItem) -> FakeTimelineItem:
    clone = FakeTimelineItem(
        source._world,
        source.name,
        track_type=source.track_type,
        track_index=source.track_index,
        start=source.start,
        duration=source.duration,
        media=source.media,
        kind=source.kind,
        color=source.color,
        source_start=source.source_start,
    )
    clone.enabled = source.enabled
    clone.flags = list(source.flags)
    clone.markers = {frame: dict(marker) for frame, marker in source.markers.items()}
    clone.takes = [dict(take) for take in source.takes]
    clone.selected_take = source.selected_take
    clone.comps = list(source.comps)
    clone.versions = {key: list(names) for key, names in source.versions.items()}
    clone.properties = dict(source.properties)
    clone.speed = dict(source.speed)
    clone.fades = dict(source.fades)
    return clone


def _clone_timeline(source: FakeTimeline, name: str) -> FakeTimeline:
    clone = FakeTimeline(
        source._world,
        name,
        start_frame=source.start_frame,
        frames=_length(source),
        start_tc=source.start_tc,
        frame_rate=float(source.settings.get("timelineFrameRate", 24.0)),
    )
    clone.current_tc = source.current_tc
    clone.settings = dict(source.settings)
    clone.markers = {frame: dict(marker) for frame, marker in source.markers.items()}
    pairs: list[tuple[FakeTimelineItem, FakeTimelineItem]] = []
    for kind, tracks in source.tracks.items():
        for track in tracks:
            copied = FakeTrack(track.name, track.subtype, track.enabled, track.locked)
            for item in track.items:
                twin = _clone_item(item)
                pairs.append((item, twin))
                copied.items.append(twin)
            clone.tracks[kind].append(copied)
    mapping = {id(item): twin for item, twin in pairs}
    for item, twin in pairs:
        twin.linked = [mapping[id(other)] for other in item.linked if id(other) in mapping]
    return clone


def _duplicate_timeline(self: FakeTimeline, timelineName: str) -> FakeTimeline | None:
    injected, value = self._rec("DuplicateTimeline", timelineName)
    if injected:
        return value if isinstance(value, FakeTimeline) else None
    project = _project_of(self)
    if project is None or not timelineName:
        return None
    if any(t.name == timelineName for t in project.timelines):
        return None
    clone = _clone_timeline(self, timelineName)
    project.timelines.append(clone)
    return clone


def _set_settings(self: FakeTimeline, settings: dict[str, Any]) -> bool:
    injected, value = self._rec("SetSettings", dict(settings))
    if injected:
        return bool(value)
    for key, new in settings.items():
        if key not in self.settings:
            return False
        custom = str(self.settings.get("useCustomSettings", "0")) in ("1", "true", "True")
        if key != "useCustomSettings" and not custom:
            return False
        old = self.settings[key]
        try:
            self.settings[key] = float(new) if isinstance(old, float) else new
        except (TypeError, ValueError):
            return False
    return True


def _create_empty_timeline(self: FakeMediaPool, name: str) -> FakeTimeline | None:
    injected, value = self._rec("CreateEmptyTimeline", name)
    if injected:
        return value if isinstance(value, FakeTimeline) else None
    project = owner_project(self)
    if not name or any(t.name == name for t in project.timelines):
        return None
    timeline = project.add_timeline(name, current=True, frames=0)
    timeline.add_track("video")
    timeline.add_track("audio")
    return timeline


def _delete_timelines(self: FakeMediaPool, timelines: list[Any]) -> bool:
    injected, value = self._rec("DeleteTimelines", list(timelines))
    if injected:
        return bool(value)
    project = owner_project(self)
    if not timelines or any(t not in project.timelines for t in timelines):
        return False
    for timeline in timelines:
        project.timelines.remove(timeline)
        if project.current_timeline is timeline:
            project.current_timeline = None
    return True


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeTimeline, "SetName"): _timeline_set_name,
    (FakeTimeline, "SetCurrentTimecode"): _set_current_timecode,
    (FakeTimeline, "SetStartTimecode"): _set_start_timecode,
    (FakeTimeline, "AddMarker"): _add_marker,
    (FakeTimeline, "DeleteMarkerAtFrame"): _delete_marker_at_frame,
    (FakeTimeline, "DeleteMarkersByColor"): _delete_markers_by_color,
    (FakeTimeline, "DeleteMarkerByCustomData"): _delete_marker_by_custom_data,
    (FakeTimeline, "AddTrack"): _add_track,
    (FakeTimeline, "DeleteTrack"): _delete_track,
    (FakeTimeline, "SetTrackEnable"): _set_track_enable,
    (FakeTimeline, "SetTrackLock"): _set_track_lock,
    (FakeTimeline, "SetTrackName"): _set_track_name,
    (FakeTimeline, "DuplicateTimeline"): _duplicate_timeline,
    (FakeTimeline, "SetSettings"): _set_settings,
    (FakeMediaPool, "CreateEmptyTimeline"): _create_empty_timeline,
    (FakeMediaPool, "DeleteTimelines"): _delete_timelines,
}


def extend() -> None:
    """Add the Timeline write methods and MediaPool.CreateEmptyTimeline / DeleteTimelines."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)


__all__ = ["extend"]
