"""Fake timeline, tracks and timeline items (every phase-1 getter in resolve-api-timeline.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from davinci_axi.fake.world import FakeBase, FakeWorld

if TYPE_CHECKING:
    from davinci_axi.fake.media import FakeMediaPoolItem

TRACK_TYPES = ("video", "audio", "subtitle")
_VIDEO_PROPERTIES: dict[str, Any] = {
    "Pan": 0.0,
    "Tilt": 0.0,
    "ZoomX": 1.0,
    "ZoomY": 1.0,
    "ZoomGang": True,
    "RotationAngle": 0.0,
    "Opacity": 100.0,
    "CompositeMode": 0,
    "CropLeft": 0.0,
    "CropRight": 0.0,
    "CropTop": 0.0,
    "CropBottom": 0.0,
    "FlipX": False,
    "FlipY": False,
    "Scaling": 0,
    "ResizeFilter": 0,
}
_AUDIO_PROPERTIES: dict[str, Any] = {"AudioVolume": 0.0, "AudioPan": 0.0}


class FakeTimelineItem(FakeBase):
    CLASS = "TimelineItem"

    def __init__(
        self,
        world: FakeWorld,
        name: str,
        *,
        track_type: str,
        track_index: int,
        start: int,
        duration: int,
        media: FakeMediaPoolItem | None = None,
        kind: str | None = None,
        color: str = "",
        source_start: int = 0,
    ) -> None:
        super().__init__(world)
        self.name = name
        self.uid = world.new_id("item")
        self.track_type = track_type
        self.track_index = track_index
        self.kind = kind or {"video": "Video", "audio": "Audio", "subtitle": "Subtitle"}[track_type]
        self.start = start
        self.duration = duration
        self.source_start = source_start
        self.media = media
        self.color = color
        self.enabled = True
        self.flags: list[str] = []
        self.markers: dict[int, dict[str, Any]] = {}
        self.linked: list[FakeTimelineItem] = []
        self.takes: list[dict[str, Any]] = []
        self.selected_take = 0
        self.comps: list[str] = []
        self.versions: dict[int, list[str]] = {0: [], 1: []}
        self.properties: dict[str, Any] = dict(
            _AUDIO_PROPERTIES if track_type == "audio" else _VIDEO_PROPERTIES
        )
        self.speed: dict[str, Any] = {"Speed": 100.0, "RetimeProcess": 0, "MotionEstimation": 0}
        self.fades: dict[str, Any] = {"fadeInLength": 0, "fadeOutLength": 0}

    def GetType(self) -> str:
        return self._ret("GetType", self.kind)

    def GetName(self) -> str:
        return self._ret("GetName", self.name)

    def GetUniqueId(self) -> str:
        return self._ret("GetUniqueId", self.uid)

    def GetStart(self, subframePrecision: bool = False) -> float:
        return self._ret("GetStart", float(self.start), subframePrecision)

    def GetEnd(self, subframePrecision: bool = False) -> float:
        return self._ret("GetEnd", float(self.start + self.duration), subframePrecision)

    def GetDuration(self, subframePrecision: bool = False) -> float:
        return self._ret("GetDuration", float(self.duration), subframePrecision)

    def GetLeftOffset(self, subframePrecision: bool = False) -> float:
        return self._ret("GetLeftOffset", float(self.source_start), subframePrecision)

    def GetRightOffset(self, subframePrecision: bool = False) -> float:
        return self._ret("GetRightOffset", 0.0, subframePrecision)

    def GetSourceStartFrame(self) -> int:
        return self._ret("GetSourceStartFrame", self.source_start)

    def GetSourceEndFrame(self) -> int:
        return self._ret("GetSourceEndFrame", self.source_start + self.duration)

    def GetSourceStartTime(self) -> float:
        return self._ret("GetSourceStartTime", self.source_start / 24)

    def GetSourceEndTime(self) -> float:
        return self._ret("GetSourceEndTime", (self.source_start + self.duration) / 24)

    def GetMediaPoolItem(self) -> FakeMediaPoolItem | None:
        return self._ret("GetMediaPoolItem", self.media)

    def GetMarkers(self) -> dict[int, dict[str, Any]]:
        return self._ret("GetMarkers", dict(self.markers))

    def GetMarkerCustomData(self, frameId: int) -> str:
        marker = self.markers.get(frameId, {})
        return self._ret("GetMarkerCustomData", str(marker.get("customData", "")), frameId)

    def GetProperties(self) -> dict[str, Any]:
        return self._ret("GetProperties", dict(self.properties))

    def GetSpeed(self) -> dict[str, Any]:
        return self._ret("GetSpeed", dict(self.speed))

    def GetFlagList(self) -> list[str]:
        return self._ret("GetFlagList", list(self.flags))

    def GetClipColor(self) -> str:
        return self._ret("GetClipColor", self.color)

    def GetClipEnabled(self) -> bool:
        return self._ret("GetClipEnabled", self.enabled)

    def GetCurrentVersion(self) -> dict[str, Any]:
        return self._ret("GetCurrentVersion", {"versionName": "Version 1", "versionType": 0})

    def GetVersionNameList(self, versionType: int) -> list[str]:
        return self._ret(
            "GetVersionNameList", list(self.versions.get(versionType, [])), versionType
        )

    def GetLinkedItems(self) -> list[FakeTimelineItem]:
        return self._ret("GetLinkedItems", list(self.linked))

    def GetTrackTypeAndIndex(self) -> list[Any]:
        return self._ret("GetTrackTypeAndIndex", [self.track_type, self.track_index])

    def GetSelectedTakeIndex(self) -> int:
        return self._ret("GetSelectedTakeIndex", self.selected_take)

    def GetTakesCount(self) -> int:
        return self._ret("GetTakesCount", len(self.takes))

    def GetTakeByIndex(self, idx: int) -> dict[str, Any] | None:
        take = self.takes[idx - 1] if 1 <= idx <= len(self.takes) else None
        return self._ret("GetTakeByIndex", take, idx)

    def GetFades(self) -> dict[str, Any]:
        return self._ret("GetFades", dict(self.fades))

    def GetFusionCompCount(self) -> int:
        return self._ret("GetFusionCompCount", len(self.comps))

    def GetFusionCompNameList(self) -> list[str]:
        return self._ret("GetFusionCompNameList", list(self.comps))

    def GetColorGroup(self) -> Any:
        return self._ret("GetColorGroup", None)

    def GetSourceAudioChannelMapping(self) -> str:
        return self._ret("GetSourceAudioChannelMapping", "")

    def GetIsColorOutputCacheEnabled(self) -> bool:
        return self._ret("GetIsColorOutputCacheEnabled", False)

    def GetVoiceIsolationState(self) -> dict[str, Any]:
        return self._ret("GetVoiceIsolationState", {"isEnabled": False, "amount": 0})

    def GetOutputBlanking(self) -> dict[str, Any]:
        return self._ret("GetOutputBlanking", {})

    def GetUseTimelineForOutputBlanking(self) -> bool:
        return self._ret("GetUseTimelineForOutputBlanking", True)

    def GetStereoConvergenceValues(self) -> dict[int, float]:
        return self._ret("GetStereoConvergenceValues", {})


@dataclass
class FakeTrack:
    name: str
    subtype: str = ""
    enabled: bool = True
    locked: bool = False
    items: list[FakeTimelineItem] = field(default_factory=list)


class FakeTimeline(FakeBase):
    CLASS = "Timeline"

    def __init__(
        self,
        world: FakeWorld,
        name: str,
        *,
        start_frame: int = 86400,
        frames: int = 240,
        start_tc: str = "01:00:00:00",
        frame_rate: float = 24.0,
    ) -> None:
        super().__init__(world)
        self.name = name
        self.uid = world.new_id("timeline")
        self.start_frame = start_frame
        self.end_frame = start_frame + frames
        self.start_tc = start_tc
        self.current_tc = start_tc
        self.tracks: dict[str, list[FakeTrack]] = {t: [] for t in TRACK_TYPES}
        self.markers: dict[int, dict[str, Any]] = {}
        self.settings: dict[str, Any] = {"useCustomSettings": "0", "timelineFrameRate": frame_rate}
        self.selected: list[FakeTimelineItem] = []
        self.current_video_item: FakeTimelineItem | None = None
        self.mark_in_out: dict[str, Any] = {}

    def add_track(self, track_type: str, name: str | None = None, subtype: str = "") -> FakeTrack:
        """Build helper (not an API call): append a track of `track_type`."""
        tracks = self.tracks[track_type]
        label = name or f"{track_type.capitalize()} {len(tracks) + 1}"
        if track_type == "audio" and not subtype:
            subtype = "stereo"
        track = FakeTrack(label, subtype)
        tracks.append(track)
        return track

    def add_item(
        self,
        track_type: str,
        track_index: int,
        name: str,
        start: int,
        duration: int,
        *,
        media: FakeMediaPoolItem | None = None,
        **kwargs: Any,
    ) -> FakeTimelineItem:
        """Build helper (not an API call): place an item on an existing 1-based track."""
        item = FakeTimelineItem(
            self._world,
            name,
            track_type=track_type,
            track_index=track_index,
            start=start,
            duration=duration,
            media=media,
            **kwargs,
        )
        self.tracks[track_type][track_index - 1].items.append(item)
        return item

    def _track(self, track_type: str, index: int) -> FakeTrack | None:
        tracks = self.tracks.get(track_type, [])
        return tracks[index - 1] if 1 <= index <= len(tracks) else None

    def GetName(self) -> str:
        return self._ret("GetName", self.name)

    def GetUniqueId(self) -> str:
        return self._ret("GetUniqueId", self.uid)

    def GetStartFrame(self) -> int:
        return self._ret("GetStartFrame", self.start_frame)

    def GetEndFrame(self) -> int:
        return self._ret("GetEndFrame", self.end_frame)

    def GetStartTimecode(self) -> str:
        return self._ret("GetStartTimecode", self.start_tc)

    def GetCurrentTimecode(self) -> str:
        return self._ret("GetCurrentTimecode", self.current_tc)

    def GetTrackCount(self, trackType: str) -> int:
        return self._ret("GetTrackCount", len(self.tracks.get(trackType, [])), trackType)

    def GetItemListInTrack(self, trackType: str, index: int) -> list[FakeTimelineItem]:
        track = self._track(trackType, index)
        return self._ret("GetItemListInTrack", list(track.items) if track else [], trackType, index)

    def GetSelectedClips(self) -> list[FakeTimelineItem]:
        return self._ret("GetSelectedClips", list(self.selected))

    def GetCurrentVideoItem(self) -> FakeTimelineItem | None:
        return self._ret("GetCurrentVideoItem", self.current_video_item)

    def GetMarkers(self) -> dict[int, dict[str, Any]]:
        return self._ret("GetMarkers", dict(self.markers))

    def GetMarkerCustomData(self, frameId: int) -> str:
        marker = self.markers.get(frameId, {})
        return self._ret("GetMarkerCustomData", str(marker.get("customData", "")), frameId)

    def GetTrackName(self, trackType: str, trackIndex: int) -> str:
        track = self._track(trackType, trackIndex)
        return self._ret("GetTrackName", track.name if track else "", trackType, trackIndex)

    def GetTrackSubType(self, trackType: str, trackIndex: int) -> str:
        track = self._track(trackType, trackIndex)
        return self._ret("GetTrackSubType", track.subtype if track else "", trackType, trackIndex)

    def GetIsTrackEnabled(self, trackType: str, trackIndex: int) -> bool:
        track = self._track(trackType, trackIndex)
        return self._ret(
            "GetIsTrackEnabled", track.enabled if track else False, trackType, trackIndex
        )

    def GetIsTrackLocked(self, trackType: str, trackIndex: int) -> bool:
        track = self._track(trackType, trackIndex)
        return self._ret(
            "GetIsTrackLocked", track.locked if track else False, trackType, trackIndex
        )

    def GetSettings(self) -> dict[str, Any]:
        return self._ret("GetSettings", dict(self.settings))

    def GetMediaPoolItem(self) -> FakeMediaPoolItem | None:
        return self._ret("GetMediaPoolItem", None)

    def GetMarkInOut(self) -> dict[str, Any]:
        return self._ret("GetMarkInOut", dict(self.mark_in_out))

    def GetNormalizeAudioModes(self) -> list[str]:
        return self._ret("GetNormalizeAudioModes", ["Sample Peak", "True Peak", "LUFS"])

    def GetOutputBlanking(self) -> dict[str, Any]:
        return self._ret("GetOutputBlanking", {})

    def Export(self, fileName: str, exportType: Any, exportSubtype: Any = None) -> bool:
        injected, value = self._rec("Export", fileName, exportType, exportSubtype)
        if injected:
            return bool(value)
        with open(fileName, "w", encoding="utf-8") as handle:
            handle.write(f"fake timeline export: {self.name}\n")
        return True
