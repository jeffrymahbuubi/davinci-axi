"""Fake media pool: root folder, sub folders and clips with metadata and properties."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from davinci_axi.fake.world import FakeBase, FakeWorld

if TYPE_CHECKING:
    from davinci_axi.fake.timeline import FakeTimeline


class FakeMediaPoolItem(FakeBase):
    CLASS = "MediaPoolItem"

    def __init__(
        self,
        world: FakeWorld,
        name: str,
        path: str,
        *,
        duration: str = "00:00:10:00",
        fps: str = "24",
        start_tc: str = "01:00:00:00",
        clip_type: str = "Video + Audio",
        resolution: str = "1920x1080",
        video_codec: str = "H.264",
        frames: str | None = None,
        date_created: str = "Sat Nov 1 2025 10:00:00",
        audio_ch: str = "2",
    ) -> None:
        super().__init__(world)
        self.name = name
        self.uid = world.new_id("clip")
        self.media_id = world.new_id("media")
        self.metadata: dict[str, Any] = {"Clip Name": name, "Comments": ""}
        self.third_party: dict[str, Any] = {}
        self.markers: dict[int, dict[str, Any]] = {}
        self.flags: list[str] = []
        self.color = ""
        self.properties: dict[str, Any] = {
            "File Path": path,
            "Clip Name": name,
            "Type": clip_type,
            "Duration": duration,
            "FPS": fps,
            "Start TC": start_tc,
            "Resolution": resolution,
            "Video Codec": video_codec,
            "Frames": ("" if clip_type == "Audio" else "240") if frames is None else frames,
            "Date Created": date_created,
            "Audio Ch": audio_ch,
            "Super Scale": 1,
            "Scene": "",
            "Shot": "",
            "Take": "",
            "Camera #": "",
        }
        self.mark_in_out: dict[str, Any] = {}

    def GetName(self) -> str:
        return self._ret("GetName", self.name)

    def GetMetadata(self, metadataType: str | None = None) -> Any:
        if metadataType is None:
            return self._ret("GetMetadata", dict(self.metadata), metadataType)
        return self._ret("GetMetadata", self.metadata.get(metadataType, ""), metadataType)

    def GetThirdPartyMetadata(self, metadataType: str | None = None) -> Any:
        if metadataType is None:
            return self._ret("GetThirdPartyMetadata", dict(self.third_party), metadataType)
        return self._ret(
            "GetThirdPartyMetadata", self.third_party.get(metadataType, ""), metadataType
        )

    def GetMediaId(self) -> str:
        return self._ret("GetMediaId", self.media_id)

    def GetUniqueId(self) -> str:
        return self._ret("GetUniqueId", self.uid)

    def GetMarkers(self) -> dict[int, dict[str, Any]]:
        return self._ret("GetMarkers", dict(self.markers))

    def GetFlagList(self) -> list[str]:
        return self._ret("GetFlagList", list(self.flags))

    def GetClipColor(self) -> str:
        return self._ret("GetClipColor", self.color)

    def GetClipProperty(self, propertyName: str | None = None) -> Any:
        if propertyName is None:
            return self._ret("GetClipProperty", dict(self.properties), propertyName)
        return self._ret("GetClipProperty", self.properties.get(propertyName, ""), propertyName)

    def GetAudioMapping(self) -> str:
        return self._ret("GetAudioMapping", "")

    def GetMarkInOut(self) -> dict[str, Any]:
        return self._ret("GetMarkInOut", dict(self.mark_in_out))

    def GetTimeline(self) -> FakeTimeline | None:
        return self._ret("GetTimeline", None)


class FakeFolder(FakeBase):
    CLASS = "Folder"

    def __init__(self, world: FakeWorld, name: str) -> None:
        super().__init__(world)
        self.name = name
        self.uid = world.new_id("folder")
        self.subfolders: list[FakeFolder] = []
        self.clips: list[FakeMediaPoolItem] = []

    def GetName(self) -> str:
        return self._ret("GetName", self.name)

    def GetSubFolderList(self) -> list[FakeFolder]:
        return self._ret("GetSubFolderList", list(self.subfolders))

    def GetClipList(self) -> list[FakeMediaPoolItem]:
        return self._ret("GetClipList", list(self.clips))

    def GetIsFolderStale(self) -> bool:
        return self._ret("GetIsFolderStale", False)

    def GetUniqueId(self) -> str:
        return self._ret("GetUniqueId", self.uid)

    def Export(self, filePath: str) -> bool:
        injected, value = self._rec("Export", filePath)
        if injected:
            return bool(value)
        with open(filePath, "w", encoding="utf-8") as handle:
            handle.write(f"fake folder export: {self.name}\n")
        return True

    def add_clip(self, name: str, path: str, **kwargs: Any) -> FakeMediaPoolItem:
        """Build helper (not an API call): add a clip to this folder."""
        clip = FakeMediaPoolItem(self._world, name, path, **kwargs)
        self.clips.append(clip)
        return clip

    def add_folder(self, name: str) -> FakeFolder:
        """Build helper (not an API call): add a sub folder."""
        folder = FakeFolder(self._world, name)
        self.subfolders.append(folder)
        return folder


class FakeMediaPool(FakeBase):
    CLASS = "MediaPool"

    def __init__(self, world: FakeWorld) -> None:
        super().__init__(world)
        self.root = FakeFolder(world, "Master")
        self.current = self.root
        self.uid = world.new_id("pool")
        self.selected: list[FakeMediaPoolItem] = []

    def GetCurrentFolder(self) -> FakeFolder:
        return self._ret("GetCurrentFolder", self.current)

    def GetRootFolder(self) -> FakeFolder:
        return self._ret("GetRootFolder", self.root)

    def GetUniqueId(self) -> str:
        return self._ret("GetUniqueId", self.uid)

    def GetSelectedClips(self) -> list[FakeMediaPoolItem]:
        return self._ret("GetSelectedClips", list(self.selected))

    def GetClipMatteList(self, mediaPoolItem: FakeMediaPoolItem) -> list[str]:
        return self._ret("GetClipMatteList", [], mediaPoolItem)
