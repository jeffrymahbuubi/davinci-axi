"""Fake timeline and media-pool methods for U25: append, build, compound, insert, AAF import,
mark in/out, blanking, stereo, Dolby Vision, clip links and custom marker data (KTD17).

The item-side methods (transitions, takes, item blanking, stereo reads, sidecar) live in
`ext_edit_extra_item.py`; `extend()` here installs both. Imports only from
`davinci_axi.fake.core|media|timeline|ext_writes_core`, never from the `davinci_axi.fake`
package, because the package is still mid-import when `EXTENSIONS` run. Each write keeps the
`_rec` injection contract: an injected value is returned (as `bool`, or as the object for the
calls that return one), otherwise the side effect happens. `extend()` is idempotent.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from davinci_axi.fake import ext_edit_extra_item
from davinci_axi.fake.core import FakeResolve
from davinci_axi.fake.ext_edit_extra_item import FPS, frames_of
from davinci_axi.fake.ext_writes_core import owner_project
from davinci_axi.fake.insert_place import place_insert
from davinci_axi.fake.media import FakeMediaPool, FakeMediaPoolItem
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem

GENERATOR_FRAMES = 120
AAF_KEYS = frozenset(
    {
        "autoImportSourceClipsIntoMediaPool",
        "ignoreFileExtensionsWhenMatching",
        "linkToSourceCameraFiles",
        "useSizingInfo",
        "importMultiChannelAudioTracksAsLinkedGroups",
        "insertAdditionalTracks",
        "insertWithOffset",
        "sourceClipsPath",
        "sourceClipsFolders",
    }
)
BLANKING_KEYS = ("Top", "Bottom", "Left", "Right")
DLB_BLEND_SHOTS = 9001.0
CONSTANTS: dict[str, float] = {"DLB_BLEND_SHOTS": DLB_BLEND_SHOTS}
MARK_TYPES = ("video", "audio", "all")


def _kind_of(clip: FakeMediaPoolItem) -> str:
    return "audio" if clip.properties.get("Type") == "Audio" else "video"


def _track_end(timeline: FakeTimeline, kind: str, index: int) -> int:
    track = timeline._track(kind, index)
    ends = [i.start + i.duration for i in (track.items if track else [])]
    return max(ends, default=timeline.start_frame)


def _grow(timeline: FakeTimeline, end: int) -> None:
    timeline.end_frame = max(timeline.end_frame, end)


def _clips_of(infos: list[dict[str, Any]]) -> list[FakeMediaPoolItem] | None:
    clips: list[FakeMediaPoolItem] = []
    for info in infos:
        clip = info.get("mediaPoolItem")
        if not isinstance(clip, FakeMediaPoolItem):
            return None
        clips.append(clip)
    return clips or None


def _append_to_timeline(self: FakeMediaPool, clipInfos: list[dict[str, Any]]) -> list[Any]:
    injected, value = self._rec("AppendToTimeline", list(clipInfos))
    if injected:
        return list(value) if isinstance(value, list) else []
    timeline = owner_project(self).current_timeline
    clips = _clips_of(clipInfos)
    if timeline is None or clips is None:
        return []
    for info, clip in zip(clipInfos, clips, strict=True):
        if timeline._track(_kind_of(clip), int(info.get("trackIndex") or 1)) is None:
            return []
    made: list[FakeTimelineItem] = []
    for info, clip in zip(clipInfos, clips, strict=True):
        kind, index = _kind_of(clip), int(info.get("trackIndex") or 1)
        record = info.get("recordFrame")
        start = int(record) if record is not None else _track_end(timeline, kind, index)
        # Like Resolve 21.1.1: startFrame/endFrame trim the source and endFrame is exclusive.
        first = int(info.get("startFrame") or 0)
        last = int(info.get("endFrame") or frames_of(clip))
        item = timeline.add_item(
            kind, index, clip.name, start, last - first, media=clip, source_start=first
        )
        _grow(timeline, start + item.duration)
        made.append(item)
    return made


def _create_timeline_from_clips(
    self: FakeMediaPool, name: str, clipInfos: list[dict[str, Any]]
) -> FakeTimeline | None:
    injected, value = self._rec("CreateTimelineFromClips", name, list(clipInfos))
    if injected:
        return value if isinstance(value, FakeTimeline) else None
    project = owner_project(self)
    clips = _clips_of(clipInfos)
    if not name or clips is None:
        return None
    if any(t.name == name for t in project.timelines):
        return None
    timeline = project.add_timeline(name, frames=0)
    timeline.add_track("video")
    timeline.add_track("audio")
    for clip in clips:
        kind = _kind_of(clip)
        start = _track_end(timeline, kind, 1)
        timeline.add_item(kind, 1, clip.name, start, frames_of(clip), media=clip)
        _grow(timeline, start + frames_of(clip))
    return timeline


def _locked(timeline: FakeTimeline, item: FakeTimelineItem) -> bool:
    track = timeline._track(item.track_type, item.track_index)
    return track is None or track.locked or item not in track.items


def _create_compound_clip(
    self: FakeTimeline, timelineItems: list[Any], clipInfo: dict[str, Any] | None = None
) -> FakeTimelineItem | None:
    injected, value = self._rec("CreateCompoundClip", list(timelineItems), clipInfo)
    if injected:
        return value if isinstance(value, FakeTimelineItem) else None
    if not timelineItems or not all(isinstance(i, FakeTimelineItem) for i in timelineItems):
        return None
    if any(_locked(self, item) for item in timelineItems):
        return None
    first = timelineItems[0]
    start = min(i.start for i in timelineItems)
    end = max(i.start + i.duration for i in timelineItems)
    for item in timelineItems:
        track = self._track(item.track_type, item.track_index)
        if track is not None:
            track.items.remove(item)
    name = str((clipInfo or {}).get("name") or "Compound Clip 1")
    return self.add_item(
        first.track_type,
        first.track_index,
        name,
        start,
        end - start,
        media=_compound_clip(self, name, end - start),
        kind="Compound",
    )


def _compound_clip(timeline: FakeTimeline, name: str, frames: int) -> FakeMediaPoolItem | None:
    """Live the compound also lands in the media pool's current folder; the fake adds it too."""
    project = timeline._world.resolve.pm.current_project
    if project is None or timeline not in project.timelines:
        return None
    seconds, rest = divmod(frames, FPS)  # frames_of reads `Duration` back at 24 fps
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return project.media_pool.current.add_clip(
        name,
        "",
        duration=f"{hours:02d}:{minutes:02d}:{seconds:02d}:{rest:02d}",
        fps=str(int(timeline.settings.get("timelineFrameRate") or FPS)),
        clip_type="Compound",
        frames=str(frames),
    )


def _insert(self: FakeTimeline, method: str, kind: str, name: str) -> FakeTimelineItem | None:
    injected, value = self._rec(method, name)
    if injected:
        return value if isinstance(value, FakeTimelineItem) else None
    return place_insert(self, name, kind)


def _insert_generator(self: FakeTimeline, generatorName: str) -> FakeTimelineItem | None:
    return _insert(self, "InsertGeneratorIntoTimeline", "Generator", generatorName)


def _insert_ofx_generator(self: FakeTimeline, generatorName: str) -> FakeTimelineItem | None:
    return _insert(self, "InsertOFXGeneratorIntoTimeline", "Generator", generatorName)


def _insert_title(self: FakeTimeline, titleName: str) -> FakeTimelineItem | None:
    return _insert(self, "InsertTitleIntoTimeline", "Title", titleName)


def _import_into_timeline(
    self: FakeTimeline, filePath: str, importOptions: dict[str, Any] | None = None
) -> bool:
    injected, value = self._rec("ImportIntoTimeline", filePath, importOptions)
    if injected:
        return bool(value)
    path = Path(filePath)
    if path.suffix.lower() != ".aaf" or not path.is_file():
        return False
    if any(key not in AAF_KEYS for key in (importOptions or {})):
        return False
    if not self.tracks["video"]:
        self.add_track("video")
    start = _track_end(self, "video", 1)
    self.add_item("video", 1, f"{path.stem} (aaf)", start, GENERATOR_FRAMES)
    _grow(self, start + GENERATOR_FRAMES)
    return True


def _set_mark_in_out(
    self: FakeTimeline, markIn: int, markOut: int, markType: str | None = None
) -> bool:
    injected, value = self._rec("SetMarkInOut", markIn, markOut, markType)
    if injected:
        return bool(value)
    kind = markType or "all"
    if kind not in MARK_TYPES or markIn < 0 or markOut < markIn:
        return False
    for key in ("video", "audio") if kind == "all" else (kind,):
        self.mark_in_out = {**self.mark_in_out, key: {"in": markIn, "out": markOut}}
    return True


def _clear_mark_in_out(self: FakeTimeline, markType: str | None = None) -> bool:
    injected, value = self._rec("ClearMarkInOut", markType)
    if injected:
        return bool(value)
    kind = markType or "all"
    if kind not in MARK_TYPES:
        return False
    for key in ("video", "audio") if kind == "all" else (kind,):
        self.mark_in_out = {k: v for k, v in self.mark_in_out.items() if k != key}
    return True


def _get_output_blanking(self: FakeTimeline) -> dict[str, Any]:
    stored: dict[str, Any] = self.__dict__.setdefault("blanking", {})
    return self._ret("GetOutputBlanking", dict(stored))


def _set_output_blanking(self: FakeTimeline, outputBlanking: dict[str, Any]) -> bool:
    injected, value = self._rec("SetOutputBlanking", dict(outputBlanking))
    if injected:
        return bool(value)
    if not outputBlanking or any(k not in BLANKING_KEYS for k in outputBlanking):
        return False
    if any(not isinstance(v, int) or v < 0 for v in outputBlanking.values()):
        return False
    self.__dict__["blanking"] = {**self.__dict__.get("blanking", {}), **outputBlanking}
    return True


def _convert_to_stereo(self: FakeTimeline) -> bool:
    injected, value = self._rec("ConvertTimelineToStereo")
    if injected:
        return bool(value)
    self.__dict__["stereo"] = True
    return True


def _analyze_dolby_vision(self: FakeTimeline, timelineItems: list[Any], analysisType: Any) -> bool:
    injected, value = self._rec("AnalyzeDolbyVision", list(timelineItems), analysisType)
    if injected:
        return bool(value)
    if not timelineItems or analysisType != DLB_BLEND_SHOTS:
        return False
    self.__dict__.setdefault("dolby", set()).update(i.uid for i in timelineItems)
    return True


def _set_clips_linked(self: FakeTimeline, timelineItems: list[Any], linked: bool) -> bool:
    injected, value = self._rec("SetClipsLinked", list(timelineItems), linked)
    if injected:
        return bool(value)
    items = list(timelineItems)
    if not items or not all(isinstance(i, FakeTimelineItem) for i in items):
        return False
    if linked and len(items) < 2:
        return False
    for item in items:
        others = [o for o in items if o is not item]
        if linked:
            item.linked.extend(o for o in others if o not in item.linked)
        else:
            item.linked = [o for o in item.linked if o not in others]
            if len(items) == 1:
                for peer in list(item.linked):
                    peer.linked = [o for o in peer.linked if o is not item]
                item.linked = []
    return True


def _update_marker_custom_data(self: Any, frameId: int, customData: str) -> bool:
    injected, value = self._rec("UpdateMarkerCustomData", frameId, customData)
    if injected:
        return bool(value)
    marker = self.markers.get(frameId)
    if marker is None:
        return False
    marker["customData"] = customData
    return True


def _get_marker_by_custom_data(self: Any, customData: str) -> dict[str, Any]:
    matches = [dict(m) for m in self.markers.values() if m.get("customData") == customData]
    found: dict[str, Any] = matches[0] if matches else {}
    return cast(dict[str, Any], self._ret("GetMarkerByCustomData", found, customData))


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeMediaPool, "AppendToTimeline"): _append_to_timeline,
    (FakeMediaPool, "CreateTimelineFromClips"): _create_timeline_from_clips,
    (FakeTimeline, "CreateCompoundClip"): _create_compound_clip,
    (FakeTimeline, "InsertGeneratorIntoTimeline"): _insert_generator,
    (FakeTimeline, "InsertOFXGeneratorIntoTimeline"): _insert_ofx_generator,
    (FakeTimeline, "InsertTitleIntoTimeline"): _insert_title,
    (FakeTimeline, "ImportIntoTimeline"): _import_into_timeline,
    (FakeTimeline, "SetMarkInOut"): _set_mark_in_out,
    (FakeTimeline, "ClearMarkInOut"): _clear_mark_in_out,
    (FakeTimeline, "GetOutputBlanking"): _get_output_blanking,
    (FakeTimeline, "SetOutputBlanking"): _set_output_blanking,
    (FakeTimeline, "ConvertTimelineToStereo"): _convert_to_stereo,
    (FakeTimeline, "AnalyzeDolbyVision"): _analyze_dolby_vision,
    (FakeTimeline, "SetClipsLinked"): _set_clips_linked,
    (FakeTimeline, "UpdateMarkerCustomData"): _update_marker_custom_data,
    (FakeTimeline, "GetMarkerByCustomData"): _get_marker_by_custom_data,
    (FakeTimelineItem, "UpdateMarkerCustomData"): _update_marker_custom_data,
    (FakeTimelineItem, "GetMarkerByCustomData"): _get_marker_by_custom_data,
}


def is_stereo(timeline: FakeTimeline) -> bool:
    """Test helper (not an API call): whether `ConvertTimelineToStereo` ran."""
    return bool(timeline.__dict__.get("stereo", False))


def dolby_analyzed(timeline: FakeTimeline) -> set[str]:
    """Test helper (not an API call): ids of the items `AnalyzeDolbyVision` ran on."""
    return set(timeline.__dict__.get("dolby", set()))


def extend() -> None:
    """Add the U25 timeline, media-pool and item methods and the `DLB_BLEND_SHOTS` constant."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)
    for name, number in CONSTANTS.items():
        setattr(FakeResolve, name, number)
    ext_edit_extra_item.extend()


__all__ = ["dolby_analyzed", "extend", "frames_of", "is_stereo"]
