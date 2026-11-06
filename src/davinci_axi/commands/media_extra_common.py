"""Shared plumbing of the extra `media` verbs (U21): local write Protocols and the clip plan helper.

`media.py` is at its size limit, so the extra verbs live in `media_extra*` modules and `SPEC` in
`media_extra` wraps `media.SPEC` (KTD15). The Protocols below declare the Media Pool calls that
`resolve_types_edit` does not cover (KTD5).
"""

from __future__ import annotations

from typing import Any, Protocol, cast

from davinci_axi.commands.media_folders import (
    Ctx,
    Found,
    clips_target,
    open_pool,
    resolve_clip,
    show_hint,
)
from davinci_axi.mutate import Change, MutationPlan, SnapshotPolicy, force_cmd
from davinci_axi.resolve_types import FolderApi, Info, MediaPoolItemApi
from davinci_axi.resolve_types_edit import MediaPoolEdit, MediaPoolItemEdit
from davinci_axi.spec import Invocation

POOL = "MediaPool."
ITEM = "MediaPoolItem."
FOLDER = "Folder."


class PoolExtra(MediaPoolEdit, Protocol):
    def RefreshFolders(self) -> bool: ...
    def SetSelectedClip(self, mediaPoolItem: MediaPoolItemApi) -> bool: ...
    def ExportMetadata(self, fileName: str, clips: list[Any] | None = None) -> bool: ...
    def ImportFolderFromFile(self, filePath: str, sourceClipsPath: str | None = None) -> bool: ...
    def CreateStereoClip(
        self, leftMediaPoolItem: MediaPoolItemApi, rightMediaPoolItem: MediaPoolItemApi
    ) -> MediaPoolItemApi | None: ...


class ClipExtra(MediaPoolItemEdit, Protocol):
    def SetThirdPartyMetadata(self, metadata: Info) -> bool: ...
    def ReplaceClip(self, filePath: str) -> bool: ...
    def ReplaceClipPreserveSubClip(self, filePath: str) -> bool: ...
    def LinkFullResolutionMedia(self, fullResMediaPath: str) -> bool: ...
    def MonitorGrowingFile(self) -> bool: ...
    def SetMarkInOut(self, markIn: int, markOut: int, markType: str | None = None) -> bool: ...
    def ClearMarkInOut(self, markType: str | None = None) -> bool: ...
    def UpdateMarkerCustomData(self, frameId: int, customData: str) -> bool: ...
    def GetMarkerCustomData(self, frameId: int) -> str: ...
    def GetMarkerByCustomData(self, customData: str) -> Info: ...


class FolderExtra(FolderApi, Protocol):
    def Export(self, filePath: str) -> bool: ...


def pool_of(ctx: Ctx) -> PoolExtra:
    return cast(PoolExtra, ctx.pool)


def open_clip(inv: Invocation, key: str = "clip") -> tuple[Ctx, Found, ClipExtra]:
    """Refuse a bad `--no-snapshot`, connect and resolve the clip named by positional `key`."""
    ctx = open_pool(inv)
    found = resolve_clip(ctx, inv.positionals[key])
    return ctx, found, cast(ClipExtra, found.clip)


def clip_plan(
    inv: Invocation,
    found: Found,
    command: str,
    changes: list[Change],
    *words: str,
    snapshot: SnapshotPolicy = "required",
    warning: str = "",
    extra: dict[str, object] | None = None,
) -> MutationPlan:
    """A plan for one clip; `command` is `media <words>` and the clip name follows it."""
    return MutationPlan(
        command=command,
        target=clips_target([found]),
        changes=changes,
        force_cmd=force_cmd(inv, "media", *command.split()[1:], found.clip.GetName(), *words),
        warning=warning,
        snapshot=snapshot,
        extra=dict(extra or {}),
        next=[show_hint([found])],
    )
