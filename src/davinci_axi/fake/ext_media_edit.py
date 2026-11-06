"""Fake media pool write methods for U13: folders, clips, metadata, flags, markers and proxies.

Imports only from `davinci_axi.fake.media|world`, never from the `davinci_axi.fake` package
(KTD17). Each method keeps the `_rec` injection contract: an injected value is returned as `bool`
(or as the object or list for `AddSubFolder` and `ImportMedia`), otherwise the side effect
happens. `CreateEmptyTimeline`, `DeleteTimelines` and `ImportTimelineFromFile` belong to other
units. `extend()` is idempotent.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

from davinci_axi.fake.media import FakeFolder, FakeMediaPool, FakeMediaPoolItem

PROXY_KEY = "Proxy Media Path"
AUDIO_SUFFIXES = (".wav", ".mp3", ".aif", ".aiff", ".flac")


def _folders(
    folder: FakeFolder, parent: FakeFolder | None = None
) -> Iterator[tuple[FakeFolder | None, FakeFolder]]:
    """Every folder with its parent (None for the root), depth first."""
    yield parent, folder
    for child in folder.subfolders:
        yield from _folders(child, folder)


def _all(pool: FakeMediaPool) -> list[tuple[FakeFolder | None, FakeFolder]]:
    return list(_folders(pool.root))


def _parent_of(pool: FakeMediaPool, folder: FakeFolder) -> FakeFolder | None:
    return next((p for p, f in _all(pool) if f is folder), None)


def _in_tree(pool: FakeMediaPool, folder: object) -> bool:
    return any(f is folder for _, f in _all(pool))


def _contains(folder: FakeFolder, other: FakeFolder) -> bool:
    return folder is other or any(_contains(child, other) for child in folder.subfolders)


def _holder(pool: FakeMediaPool, clip: FakeMediaPoolItem) -> FakeFolder | None:
    return next((f for _, f in _all(pool) if clip in f.clips), None)


def _add_sub_folder(self: FakeMediaPool, folder: FakeFolder, name: str) -> FakeFolder | None:
    injected, value = self._rec("AddSubFolder", folder, name)
    if injected:
        return value if isinstance(value, FakeFolder) else None
    if not name or not _in_tree(self, folder) or any(c.name == name for c in folder.subfolders):
        return None
    return folder.add_folder(name)


def _set_current_folder(self: FakeMediaPool, folder: FakeFolder) -> bool:
    injected, value = self._rec("SetCurrentFolder", folder)
    if injected:
        return bool(value)
    if not _in_tree(self, folder):
        return False
    self.current = folder
    return True


def _delete_clips(self: FakeMediaPool, clips: list[FakeMediaPoolItem]) -> bool:
    injected, value = self._rec("DeleteClips", clips)
    if injected:
        return bool(value)
    holders = [_holder(self, clip) for clip in clips]
    if not clips or any(h is None for h in holders):
        return False
    for clip, holder in zip(clips, holders, strict=True):
        assert holder is not None
        holder.clips.remove(clip)
        if clip in self.selected:
            self.selected.remove(clip)
    return True


def _delete_folders(self: FakeMediaPool, subfolders: list[FakeFolder]) -> bool:
    injected, value = self._rec("DeleteFolders", subfolders)
    if injected:
        return bool(value)
    if not subfolders or any(f is self.root or not _in_tree(self, f) for f in subfolders):
        return False
    for folder in subfolders:
        parent = _parent_of(self, folder)
        if parent is None:
            continue
        if _contains(folder, self.current):
            self.current = self.root
        parent.subfolders.remove(folder)
    return True


def _move_clips(
    self: FakeMediaPool, clips: list[FakeMediaPoolItem], targetFolder: FakeFolder
) -> bool:
    injected, value = self._rec("MoveClips", clips, targetFolder)
    if injected:
        return bool(value)
    holders = [_holder(self, clip) for clip in clips]
    if not clips or not _in_tree(self, targetFolder) or any(h is None for h in holders):
        return False
    for clip, holder in zip(clips, holders, strict=True):
        assert holder is not None
        holder.clips.remove(clip)
        targetFolder.clips.append(clip)
    return True


def _move_folders(self: FakeMediaPool, folders: list[FakeFolder], targetFolder: FakeFolder) -> bool:
    injected, value = self._rec("MoveFolders", folders, targetFolder)
    if injected:
        return bool(value)
    if not folders or not _in_tree(self, targetFolder):
        return False
    for folder in folders:
        parent = _parent_of(self, folder)
        if parent is None or _contains(folder, targetFolder):
            return False
        if any(c.name == folder.name for c in targetFolder.subfolders):
            return False
    for folder in folders:
        parent = _parent_of(self, folder)
        assert parent is not None
        parent.subfolders.remove(folder)
        targetFolder.subfolders.append(folder)
    return True


def _relink_clips(self: FakeMediaPool, clips: list[FakeMediaPoolItem], folderPath: str) -> bool:
    injected, value = self._rec("RelinkClips", clips, folderPath)
    if injected:
        return bool(value)
    if not clips or not os.path.isdir(folderPath):
        return False
    for clip in clips:
        name = os.path.basename(str(clip.properties.get("File Path", ""))) or clip.name
        clip.properties["File Path"] = os.path.join(folderPath, name)
        clip.properties["Online Status"] = "Online"
    return True


def _unlink_clips(self: FakeMediaPool, clips: list[FakeMediaPoolItem]) -> bool:
    injected, value = self._rec("UnlinkClips", clips)
    if injected:
        return bool(value)
    if not clips:
        return False
    for clip in clips:
        clip.properties["Online Status"] = "Offline"
    return True


def _import_media(self: FakeMediaPool, clipInfos: list[Any]) -> list[FakeMediaPoolItem]:
    """Add one clip per existing file to the current folder, named by the file stem.

    Like Resolve 21.1.1, a plain path string imports, but a {"FilePath"} dict without
    StartIndex and EndIndex (the image-sequence form) imports nothing.
    """
    injected, value = self._rec("ImportMedia", clipInfos)
    if injected:
        return list(value) if isinstance(value, list) else []
    made: list[FakeMediaPoolItem] = []
    for info in clipInfos:
        if isinstance(info, dict):
            if "StartIndex" not in info or "EndIndex" not in info:
                continue
            path = str(info.get("FilePath", ""))
        else:
            path = str(info)
        if not os.path.isfile(path):
            continue
        stem, suffix = os.path.splitext(os.path.basename(path))
        kind = "Audio" if suffix.lower() in AUDIO_SUFFIXES else "Video + Audio"
        made.append(self.current.add_clip(stem, path, clip_type=kind))
    return made


def _set_name(self: FakeMediaPoolItem, name: str) -> bool:
    injected, value = self._rec("SetName", name)
    if injected:
        return bool(value)
    if not name:
        return False
    self.name = name
    self.properties["Clip Name"] = name
    return True


def _set_metadata(self: FakeMediaPoolItem, metadata: dict[str, Any]) -> bool:
    injected, value = self._rec("SetMetadata", metadata)
    if injected:
        return bool(value)
    if not metadata:
        return False
    self.metadata.update(metadata)
    return True


def _set_clip_property(self: FakeMediaPoolItem, propertyName: str, propertyValue: str) -> bool:
    injected, value = self._rec("SetClipProperty", propertyName, propertyValue)
    if injected:
        return bool(value)
    if not propertyName:
        return False
    self.properties[propertyName] = propertyValue
    return True


def _add_marker(
    self: FakeMediaPoolItem,
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
    if frameId in self.markers or not color:
        return False
    self.markers[frameId] = {
        "color": color,
        "duration": duration,
        "note": note,
        "name": name,
        "customData": customData or "",
    }
    return True


def _delete_marker_at(self: FakeMediaPoolItem, frameNum: int) -> bool:
    injected, value = self._rec("DeleteMarkerAtFrame", frameNum)
    if injected:
        return bool(value)
    return self.markers.pop(frameNum, None) is not None


def _drop_markers(self: FakeMediaPoolItem, doomed: list[int]) -> bool:
    for frame in doomed:
        del self.markers[frame]
    return bool(doomed)


def _delete_markers_by_color(self: FakeMediaPoolItem, color: str) -> bool:
    injected, value = self._rec("DeleteMarkersByColor", color)
    if injected:
        return bool(value)
    return _drop_markers(self, [f for f, m in self.markers.items() if color in ("All", m["color"])])


def _delete_marker_by_custom(self: FakeMediaPoolItem, customData: str) -> bool:
    injected, value = self._rec("DeleteMarkerByCustomData", customData)
    if injected:
        return bool(value)
    return _drop_markers(
        self, [f for f, m in self.markers.items() if m["customData"] == customData]
    )


def _add_flag(self: FakeMediaPoolItem, color: str) -> bool:
    injected, value = self._rec("AddFlag", color)
    if injected:
        return bool(value)
    if not color:
        return False
    if color not in self.flags:
        self.flags.append(color)
    return True


def _clear_flags(self: FakeMediaPoolItem, color: str) -> bool:
    injected, value = self._rec("ClearFlags", color)
    if injected:
        return bool(value)
    kept = [] if color == "All" else [flag for flag in self.flags if flag != color]
    changed = len(kept) != len(self.flags)
    self.flags = kept
    return changed


def _set_clip_color(self: FakeMediaPoolItem, colorName: str) -> bool:
    injected, value = self._rec("SetClipColor", colorName)
    if injected:
        return bool(value)
    if not colorName:
        return False
    self.color = colorName
    return True


def _clear_clip_color(self: FakeMediaPoolItem) -> bool:
    injected, value = self._rec("ClearClipColor")
    if injected:
        return bool(value)
    self.color = ""
    return True


def _link_proxy_media(self: FakeMediaPoolItem, proxyMediaFilePath: str) -> bool:
    injected, value = self._rec("LinkProxyMedia", proxyMediaFilePath)
    if injected:
        return bool(value)
    if not proxyMediaFilePath:
        return False
    self.properties[PROXY_KEY] = proxyMediaFilePath
    return True


def _unlink_proxy_media(self: FakeMediaPoolItem) -> bool:
    injected, value = self._rec("UnlinkProxyMedia")
    if injected:
        return bool(value)
    return bool(self.properties.pop(PROXY_KEY, ""))


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeMediaPool, "AddSubFolder"): _add_sub_folder,
    (FakeMediaPool, "SetCurrentFolder"): _set_current_folder,
    (FakeMediaPool, "DeleteClips"): _delete_clips,
    (FakeMediaPool, "DeleteFolders"): _delete_folders,
    (FakeMediaPool, "MoveClips"): _move_clips,
    (FakeMediaPool, "MoveFolders"): _move_folders,
    (FakeMediaPool, "RelinkClips"): _relink_clips,
    (FakeMediaPool, "UnlinkClips"): _unlink_clips,
    (FakeMediaPool, "ImportMedia"): _import_media,
    (FakeMediaPoolItem, "SetName"): _set_name,
    (FakeMediaPoolItem, "SetMetadata"): _set_metadata,
    (FakeMediaPoolItem, "SetClipProperty"): _set_clip_property,
    (FakeMediaPoolItem, "AddMarker"): _add_marker,
    (FakeMediaPoolItem, "DeleteMarkerAtFrame"): _delete_marker_at,
    (FakeMediaPoolItem, "DeleteMarkersByColor"): _delete_markers_by_color,
    (FakeMediaPoolItem, "DeleteMarkerByCustomData"): _delete_marker_by_custom,
    (FakeMediaPoolItem, "AddFlag"): _add_flag,
    (FakeMediaPoolItem, "ClearFlags"): _clear_flags,
    (FakeMediaPoolItem, "SetClipColor"): _set_clip_color,
    (FakeMediaPoolItem, "ClearClipColor"): _clear_clip_color,
    (FakeMediaPoolItem, "LinkProxyMedia"): _link_proxy_media,
    (FakeMediaPoolItem, "UnlinkProxyMedia"): _unlink_proxy_media,
}


def extend() -> None:
    """Add the MediaPool and MediaPoolItem write methods."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)


__all__ = ["extend"]
