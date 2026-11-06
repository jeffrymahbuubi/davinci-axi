"""Shared plumbing of the `storage` command group (U21): Protocols and path parsing.

`Resolve.GetMediaStorage()` is the one entry point; the Protocols below are declared locally
(KTD5) because `resolve_types*` does not cover the Media Storage class. Disk paths are taken as
given (made absolute): a Media Storage volume is not necessarily visible to this process, so
nothing here checks that a path exists; Resolve answers False or an empty list for a bad path.
"""

from __future__ import annotations

from typing import Any, Protocol, cast

from davinci_axi.commands.media_folders import abs_path
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.resolve_types import FolderApi, Info, MediaPoolItemApi, ResolveApp
from davinci_axi.resolve_types_edit import MediaPoolEdit
from davinci_axi.session import Session

STORAGE = "MediaStorage."


class MediaStorageApi(Protocol):
    def GetMountedVolumeList(self) -> list[str]: ...
    def GetSubFolderList(self, folderPath: str) -> list[str]: ...
    def GetFileList(self, folderPath: str) -> list[str]: ...
    def RevealInStorage(self, path: str) -> bool: ...
    def AddItemListToMediaPool(self, itemInfos: list[Info]) -> list[MediaPoolItemApi]: ...
    def AddClipMattesToMediaPool(
        self, mediaPoolItem: MediaPoolItemApi, paths: list[str], stereoEye: str | None = None
    ) -> bool: ...
    def AddTimelineMattesToMediaPool(self, paths: list[str]) -> list[MediaPoolItemApi]: ...
    def StartCloneMedia(self, sourceDir: str, targetDirs: str | list[str]) -> bool: ...
    def SetCloneToolSettings(self, cloneToolSettings: Info | None = None) -> bool: ...
    def StopCloneMedia(self) -> bool: ...
    def GetCloneStatus(self) -> Info: ...


class StorageResolve(ResolveApp, Protocol):
    def GetMediaStorage(self) -> MediaStorageApi: ...


class StoragePool(MediaPoolEdit, Protocol):
    def GetTimelineMatteList(self, folder: FolderApi) -> list[MediaPoolItemApi]: ...
    def DeleteClipMattes(self, mediaPoolItem: MediaPoolItemApi, paths: list[str]) -> bool: ...


def storage_of(session: Session) -> MediaStorageApi:
    """The Media Storage object of the connected Resolve."""
    storage = cast(StorageResolve, session.resolve).GetMediaStorage()
    if storage is None:
        raise api_failed("Resolve.GetMediaStorage", "Resolve returned no Media Storage")
    return storage


def split_paths(text: str, what: str = "paths") -> list[str]:
    """`a,b` -> absolute paths without repeats. A comma inside a path is not supported."""
    paths: list[str] = []
    for raw in (part.strip() for part in text.split(",")):
        if not raw:
            raise AxiError(
                "VALIDATION_ERROR",
                f"an empty entry in the comma-separated {what}",
                help=[f"Valid example: /Volumes/Media/a.mov,/Volumes/Media/b.mov ({what})"],
            )
        path = abs_path(raw)
        if path not in paths:
            paths.append(path)
    return paths


def names_of(items: list[Any]) -> str:
    return ", ".join(str(i.GetName()) for i in items)
