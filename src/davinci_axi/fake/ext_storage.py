"""Fake Media Storage, clone tool and the extra media pool methods for U21.

`FakeMediaStorage` serves `Resolve.GetMediaStorage()` with one volume `/Volumes/Media` and a small
virtual file tree (`add_storage_file` extends it); a path also counts as present when it exists on
the real disk, so tests can use `tmp_path` files as mattes. The clone tool advances on reads like
the render queue: `Cloning` 0 -> 50 % -> `Complete`, so a polling loop finishes without sleeping.
Imports only from `davinci_axi.fake.core|media|world`, never from the `davinci_axi.fake` package
(KTD17). Each method keeps the `_rec` injection contract: an injected value is returned (as `bool`
unless the real call returns something else), otherwise the side effect happens. `extend()` is
idempotent.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

from davinci_axi.fake.core import FakeResolve
from davinci_axi.fake.media import FakeFolder, FakeMediaPool, FakeMediaPoolItem
from davinci_axi.fake.world import FakeBase, FakeWorld

VOLUME = "/Volumes/Media"
AUDIO_SUFFIXES = (".wav", ".mp3", ".aif", ".aiff", ".flac")
FOLDER_EXPORT = "fake folder export: "
CLONE_KEYS = ("PreserveFolderName", "ChecksumType")
MARK_TYPES = ("video", "audio", "all")
HALF = 50.0
FULL = 100.0
_CHECKSUMS = "NONE FILESIZE CRC32 MD5 SHA256 SHA512 XXH_64"
_ENUM_BASE = 6000.0


def _tree() -> dict[str, dict[str, list[str]]]:
    return {
        VOLUME: {"dirs": [f"{VOLUME}/Shoot1"], "files": [f"{VOLUME}/a.mov", f"{VOLUME}/b.wav"]},
        f"{VOLUME}/Shoot1": {"dirs": [], "files": [f"{VOLUME}/Shoot1/c.mov"]},
    }


class FakeMediaStorage(FakeBase):
    CLASS = "MediaStorage"

    def __init__(self, world: FakeWorld) -> None:
        super().__init__(world)
        self.volumes: list[str] = [VOLUME]
        self.tree = _tree()
        self.clone_status: dict[str, Any] = {"JobStatus": "Complete", "CompletionPercentage": 0.0}
        self.clone_settings: dict[str, Any] = {}
        self.clones: list[tuple[str, list[str], dict[str, Any]]] = []
        self.revealed: list[str] = []

    def known(self, path: str) -> bool:
        """Build helper (not an API call): is `path` in the virtual tree or on the real disk?"""
        in_tree = path in self.tree or any(path in node["files"] for node in self.tree.values())
        return in_tree or os.path.exists(path)

    def _files_under(self, path: str) -> list[str]:
        if path in self.tree:
            node = self.tree[path]
            return list(node["files"]) + [f for d in node["dirs"] for f in self._files_under(d)]
        return [path] if self.known(path) else []

    def GetMountedVolumeList(self) -> list[str]:
        return self._ret("GetMountedVolumeList", list(self.volumes))

    def GetSubFolderList(self, folderPath: str) -> list[str]:
        node = self.tree.get(folderPath, {"dirs": []})
        return self._ret("GetSubFolderList", list(node["dirs"]), folderPath)

    def GetFileList(self, folderPath: str) -> list[str]:
        node = self.tree.get(folderPath, {"files": []})
        return self._ret("GetFileList", list(node["files"]), folderPath)

    def RevealInStorage(self, path: str) -> bool:
        injected, value = self._rec("RevealInStorage", path)
        if injected:
            return bool(value)
        if not self.known(path):
            return False
        self.revealed.append(path)
        return True

    def AddItemListToMediaPool(self, itemInfos: list[dict[str, Any]]) -> list[FakeMediaPoolItem]:
        """Add one clip per file (a folder adds every file in it) to the current pool folder."""
        injected, value = self._rec("AddItemListToMediaPool", itemInfos)
        if injected:
            return list(value) if isinstance(value, list) else []
        project = self._world.resolve.pm.current_project
        if project is None:
            return []
        folder = project.media_pool.current
        made: list[FakeMediaPoolItem] = []
        for info in itemInfos:
            for path in self._files_under(str(info.get("media", ""))):
                stem, suffix = os.path.splitext(os.path.basename(path))
                kind = "Audio" if suffix.lower() in AUDIO_SUFFIXES else "Video + Audio"
                clip = folder.add_clip(stem, path, clip_type=kind)
                if "startFrame" in info:
                    clip.__dict__["storage_range"] = (info["startFrame"], info.get("endFrame"))
                made.append(clip)
        return made

    def AddClipMattesToMediaPool(
        self, mediaPoolItem: FakeMediaPoolItem, paths: list[str], stereoEye: str | None = None
    ) -> bool:
        injected, value = self._rec("AddClipMattesToMediaPool", mediaPoolItem, paths, stereoEye)
        if injected:
            return bool(value)
        if not paths or stereoEye not in (None, "left", "right"):
            return False
        if not all(self.known(p) for p in paths):
            return False
        mattes = mediaPoolItem.__dict__.setdefault("mattes", [])
        mattes.extend(p for p in paths if p not in mattes)
        return True

    def AddTimelineMattesToMediaPool(self, paths: list[str]) -> list[FakeMediaPoolItem]:
        injected, value = self._rec("AddTimelineMattesToMediaPool", paths)
        if injected:
            return list(value) if isinstance(value, list) else []
        project = self._world.resolve.pm.current_project
        if project is None:
            return []
        folder = project.media_pool.current
        made: list[FakeMediaPoolItem] = []
        for path in (p for p in paths if self.known(p)):
            stem = os.path.splitext(os.path.basename(path))[0]
            matte = FakeMediaPoolItem(self._world, stem, path, clip_type="Video")
            folder.__dict__.setdefault("timeline_mattes", []).append(matte)
            made.append(matte)
        return made

    def SetCloneToolSettings(self, cloneToolSettings: dict[str, Any] | None = None) -> bool:
        injected, value = self._rec("SetCloneToolSettings", cloneToolSettings)
        if injected:
            return bool(value)
        settings = cloneToolSettings or {}
        if any(key not in CLONE_KEYS for key in settings):
            return False
        self.clone_settings.update(settings)
        return True

    def StartCloneMedia(self, sourceDir: str, targetDirs: str | list[str]) -> bool:
        injected, value = self._rec("StartCloneMedia", sourceDir, targetDirs)
        if injected:
            return bool(value)
        targets = [targetDirs] if isinstance(targetDirs, str) else list(targetDirs)
        if not sourceDir or not targets or self.clone_status["JobStatus"] == "Cloning":
            return False
        self.clones.append((sourceDir, targets, dict(self.clone_settings)))
        self.clone_status = {"JobStatus": "Cloning", "CompletionPercentage": 0.0}
        return True

    def StopCloneMedia(self) -> bool:
        injected, value = self._rec("StopCloneMedia")
        if injected:
            return bool(value)
        if self.clone_status["JobStatus"] != "Cloning":
            return False
        self.clone_status = {"JobStatus": "Cancelled", "CompletionPercentage": 0.0}
        return True

    def GetCloneStatus(self) -> dict[str, Any]:
        injected, value = self._rec("GetCloneStatus")
        if injected:
            return value  # type: ignore[return-value]
        if self.clone_status["JobStatus"] == "Cloning":
            if float(self.clone_status["CompletionPercentage"]) < HALF:
                self.clone_status["CompletionPercentage"] = HALF
            else:
                self.clone_status = {"JobStatus": "Complete", "CompletionPercentage": FULL}
        return dict(self.clone_status)


def storage_of(world: FakeWorld) -> FakeMediaStorage:
    """The world's Media Storage (created on first use); a build and assertion helper."""
    resolve = world.resolve
    return resolve.__dict__.setdefault("_media_storage", FakeMediaStorage(world))  # type: ignore[no-any-return]


def add_storage_file(world: FakeWorld, path: str, folder: str = VOLUME) -> None:
    """Build helper (not an API call): list `path` as a file of the storage folder `folder`."""
    node = storage_of(world).tree.setdefault(folder, {"dirs": [], "files": []})
    if path not in node["files"]:
        node["files"].append(path)


def _get_media_storage(self: FakeResolve) -> FakeMediaStorage | None:
    injected, value = self._rec("GetMediaStorage")
    if injected:
        return value if isinstance(value, FakeMediaStorage) else None
    return storage_of(self._world)


# ---- media pool ---------------------------------------------------------------------------


def _folders(folder: FakeFolder) -> Iterator[FakeFolder]:
    yield folder
    for child in folder.subfolders:
        yield from _folders(child)


def _all_clips(pool: FakeMediaPool) -> list[FakeMediaPoolItem]:
    return [clip for folder in _folders(pool.root) for clip in folder.clips]


def _holder(pool: FakeMediaPool, clip: FakeMediaPoolItem) -> FakeFolder | None:
    return next((f for f in _folders(pool.root) if clip in f.clips), None)


def _refresh_folders(self: FakeMediaPool) -> bool:
    injected, value = self._rec("RefreshFolders")
    return bool(value) if injected else True


def _set_selected_clip(self: FakeMediaPool, mediaPoolItem: FakeMediaPoolItem) -> bool:
    injected, value = self._rec("SetSelectedClip", mediaPoolItem)
    if injected:
        return bool(value)
    if mediaPoolItem not in _all_clips(self):
        return False
    self.selected = [mediaPoolItem]
    return True


def _export_metadata(
    self: FakeMediaPool, fileName: str, clips: list[FakeMediaPoolItem] | None = None
) -> bool:
    injected, value = self._rec("ExportMetadata", fileName, clips)
    if injected:
        return bool(value)
    chosen = _all_clips(self) if clips is None else clips
    if not chosen:
        return False
    rows = ["Clip Name,File Path"] + [
        f"{c.name},{c.properties.get('File Path', '')}" for c in chosen
    ]
    with open(fileName, "w", encoding="utf-8") as handle:
        handle.write("\n".join(rows) + "\n")
    return True


def _import_folder_from_file(
    self: FakeMediaPool, filePath: str, sourceClipsPath: str | None = None
) -> bool:
    """Add an empty folder named from the DRB placeholder; a name already taken gets ` N`."""
    injected, value = self._rec("ImportFolderFromFile", filePath, sourceClipsPath)
    if injected:
        return bool(value)
    if not os.path.isfile(filePath):
        return False
    with open(filePath, encoding="utf-8") as handle:
        first = handle.readline().strip()
    if not first.startswith(FOLDER_EXPORT):
        return False
    base = first[len(FOLDER_EXPORT) :] or "Imported"
    taken = {f.name for f in self.current.subfolders}
    name, number = base, 0
    while name in taken:
        number += 1
        name = f"{base} {number}"
    self.current.add_folder(name)
    return True


def _create_stereo_clip(
    self: FakeMediaPool, leftMediaPoolItem: FakeMediaPoolItem, rightMediaPoolItem: FakeMediaPoolItem
) -> FakeMediaPoolItem | None:
    """Replace the two clips with one `Stereo N` clip in the left clip's folder."""
    injected, value = self._rec("CreateStereoClip", leftMediaPoolItem, rightMediaPoolItem)
    if injected:
        return value if isinstance(value, FakeMediaPoolItem) else None
    left, right = leftMediaPoolItem, rightMediaPoolItem
    holder = _holder(self, left)
    if holder is None or left is right or _holder(self, right) is None:
        return None
    count = sum(1 for c in _all_clips(self) if c.name.startswith("Stereo ")) + 1
    path = str(left.properties.get("File Path", ""))
    for clip in (left, right):
        owner = _holder(self, clip)
        assert owner is not None
        owner.clips.remove(clip)
        if clip in self.selected:
            self.selected.remove(clip)
    return holder.add_clip(f"Stereo {count}", path, clip_type="Video + Audio")


def _get_clip_matte_list(self: FakeMediaPool, mediaPoolItem: FakeMediaPoolItem) -> list[str]:
    injected, value = self._rec("GetClipMatteList", mediaPoolItem)
    if injected:
        return list(value) if isinstance(value, list) else []
    return list(mediaPoolItem.__dict__.get("mattes", []))


def _get_timeline_matte_list(self: FakeMediaPool, folder: FakeFolder) -> list[FakeMediaPoolItem]:
    injected, value = self._rec("GetTimelineMatteList", folder)
    if injected:
        return list(value) if isinstance(value, list) else []
    return list(folder.__dict__.get("timeline_mattes", []))


def _delete_clip_mattes(
    self: FakeMediaPool, mediaPoolItem: FakeMediaPoolItem, paths: list[str]
) -> bool:
    injected, value = self._rec("DeleteClipMattes", mediaPoolItem, paths)
    if injected:
        return bool(value)
    mattes: list[str] = mediaPoolItem.__dict__.get("mattes", [])
    if not paths or any(p not in mattes for p in paths):
        return False
    mattes[:] = [m for m in mattes if m not in paths]
    return True


# ---- media pool items ---------------------------------------------------------------------


def _set_third_party_metadata(self: FakeMediaPoolItem, metadata: dict[str, Any]) -> bool:
    injected, value = self._rec("SetThirdPartyMetadata", metadata)
    if injected:
        return bool(value)
    if not metadata:
        return False
    self.third_party.update(metadata)
    return True


def _replace(self: FakeMediaPoolItem, method: str, filePath: str, keep: bool) -> bool:
    injected, value = self._rec(method, filePath)
    if injected:
        return bool(value)
    if not os.path.isfile(filePath):
        return False
    self.properties["File Path"] = filePath
    self.__dict__["subclip_preserved"] = keep
    return True


def _replace_clip(self: FakeMediaPoolItem, filePath: str) -> bool:
    return _replace(self, "ReplaceClip", filePath, False)


def _replace_clip_preserve(self: FakeMediaPoolItem, filePath: str) -> bool:
    return _replace(self, "ReplaceClipPreserveSubClip", filePath, True)


def _link_full_resolution_media(self: FakeMediaPoolItem, fullResMediaPath: str) -> bool:
    injected, value = self._rec("LinkFullResolutionMedia", fullResMediaPath)
    if injected:
        return bool(value)
    if not os.path.exists(fullResMediaPath):
        return False
    self.__dict__["full_resolution"] = fullResMediaPath
    return True


def _monitor_growing_file(self: FakeMediaPoolItem) -> bool:
    injected, value = self._rec("MonitorGrowingFile")
    if injected:
        return bool(value)
    self.__dict__["monitoring"] = True
    return True


def _kinds(clip: FakeMediaPoolItem, kind: str) -> list[str]:
    """The mark kinds that exist on `clip`: video marks need video, audio marks need audio."""
    kind_text = str(clip.properties.get("Type", ""))
    present = [k for k, word in (("video", "Video"), ("audio", "Audio")) if word in kind_text]
    return present if kind == "all" else [k for k in present if k == kind]


def _set_mark_in_out(
    self: FakeMediaPoolItem, markIn: int, markOut: int, markType: str | None = None
) -> bool:
    injected, value = self._rec("SetMarkInOut", markIn, markOut, markType)
    if injected:
        return bool(value)
    kind = markType or "all"
    if kind not in MARK_TYPES or markIn < 0 or markIn > markOut:
        return False
    kinds = _kinds(self, kind)
    for each in kinds:
        self.mark_in_out[each] = {"in": markIn, "out": markOut}
    return bool(kinds)


def _clear_mark_in_out(self: FakeMediaPoolItem, markType: str | None = None) -> bool:
    injected, value = self._rec("ClearMarkInOut", markType)
    if injected:
        return bool(value)
    kind = markType or "all"
    if kind not in MARK_TYPES:
        return False
    cleared = [k for k in ("video", "audio") if (kind in ("all", k)) and k in self.mark_in_out]
    for each in cleared:
        del self.mark_in_out[each]
    return bool(cleared)


def _update_marker_custom_data(self: FakeMediaPoolItem, frameId: int, customData: str) -> bool:
    injected, value = self._rec("UpdateMarkerCustomData", frameId, customData)
    if injected:
        return bool(value)
    if frameId not in self.markers:
        return False
    self.markers[frameId]["customData"] = customData
    return True


def _get_marker_custom_data(self: FakeMediaPoolItem, frameId: int) -> str:
    injected, value = self._rec("GetMarkerCustomData", frameId)
    if injected:
        return str(value)
    return str(self.markers.get(frameId, {}).get("customData", ""))


def _get_marker_by_custom_data(self: FakeMediaPoolItem, customData: str) -> dict[str, Any]:
    injected, value = self._rec("GetMarkerByCustomData", customData)
    if injected:
        return value if isinstance(value, dict) else {}
    for _, marker in sorted(self.markers.items()):
        if customData and marker.get("customData") == customData:
            return dict(marker)
    return {}


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeResolve, "GetMediaStorage"): _get_media_storage,
    (FakeMediaPool, "RefreshFolders"): _refresh_folders,
    (FakeMediaPool, "SetSelectedClip"): _set_selected_clip,
    (FakeMediaPool, "ExportMetadata"): _export_metadata,
    (FakeMediaPool, "ImportFolderFromFile"): _import_folder_from_file,
    (FakeMediaPool, "CreateStereoClip"): _create_stereo_clip,
    (FakeMediaPool, "GetClipMatteList"): _get_clip_matte_list,
    (FakeMediaPool, "GetTimelineMatteList"): _get_timeline_matte_list,
    (FakeMediaPool, "DeleteClipMattes"): _delete_clip_mattes,
    (FakeMediaPoolItem, "SetThirdPartyMetadata"): _set_third_party_metadata,
    (FakeMediaPoolItem, "ReplaceClip"): _replace_clip,
    (FakeMediaPoolItem, "ReplaceClipPreserveSubClip"): _replace_clip_preserve,
    (FakeMediaPoolItem, "LinkFullResolutionMedia"): _link_full_resolution_media,
    (FakeMediaPoolItem, "MonitorGrowingFile"): _monitor_growing_file,
    (FakeMediaPoolItem, "SetMarkInOut"): _set_mark_in_out,
    (FakeMediaPoolItem, "ClearMarkInOut"): _clear_mark_in_out,
    (FakeMediaPoolItem, "UpdateMarkerCustomData"): _update_marker_custom_data,
    (FakeMediaPoolItem, "GetMarkerCustomData"): _get_marker_custom_data,
    (FakeMediaPoolItem, "GetMarkerByCustomData"): _get_marker_by_custom_data,
}


def extend() -> None:
    """Add Media Storage, the extra pool methods and the KTD24 clone enums (idempotent)."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)
    number = _ENUM_BASE
    for name in _CHECKSUMS.split():
        number += 1.0
        setattr(FakeResolve, f"CLONE_CHECKSUM_TYPE_{name}", number)


__all__ = ["FakeMediaStorage", "add_storage_file", "extend", "storage_of"]
