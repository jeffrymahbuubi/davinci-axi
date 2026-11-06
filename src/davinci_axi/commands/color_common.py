"""Plumbing shared by every `color` verb (U19): the typed boundary and the two write helpers.

`item_write` and `project_write` build a `MutationPlan` and hand it to `mutate.run_mutation`
(dry run until `--force`, then save, snapshot, apply, read back). `called_write` is the KTD23
variant for calls with no getter: the plan is `Change("called", False, True)`, `apply` raises
when Resolve returns False, and the output says the result was not read back. The caller owns the
`changes` list and the `extra` dict it passes in and may update them inside its `call`
(the plan keeps the same objects).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, cast

from davinci_axi.commands.item_detail import Located, resolve_item
from davinci_axi.commands.item_markers import Target, show_hint, tl_words
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    Change,
    MutationPlan,
    SnapshotPolicy,
    force_cmd,
    run_mutation,
    with_current_timeline,
)
from davinci_axi.resolve_types import TimelineApi, TimelineItemApi
from davinci_axi.resolve_types_edit import ProjectEdit
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

CALLED_NOTE = "call result only: Resolve has no getter for this, so it is not read back"


class GraphApi(Protocol):
    def GetNumNodes(self) -> int: ...
    def SetLUT(self, nodeIndex: int, lutPath: str) -> bool: ...
    def GetLUT(self, nodeIndex: int) -> str: ...
    def SetNodeCacheMode(self, nodeIndex: int, cacheValue: Any) -> bool: ...
    def GetNodeCacheMode(self, nodeIndex: int) -> int: ...
    def GetNodeLabel(self, nodeIndex: int) -> str: ...
    def GetToolsInNode(self, nodeIndex: int) -> list[str]: ...
    def SetNodeEnabled(self, nodeIndex: int, isEnabled: bool) -> bool: ...
    def ApplyArriCdlLut(self) -> bool: ...
    def ApplyGradeFromDRX(
        self, path: str, gradeMode: int, applyToAllLayers: bool = False
    ) -> bool: ...
    def ResetAllGrades(self) -> bool: ...


class ColorGroupApi(Protocol):
    def GetName(self) -> str: ...
    def SetName(self, groupName: str) -> bool: ...
    def GetClipsInTimeline(self, timeline: TimelineApi | None = None) -> list[Any]: ...
    def GetPreClipNodeGraph(self) -> GraphApi: ...
    def GetPostClipNodeGraph(self) -> GraphApi: ...


class ColorItem(TimelineItemApi, Protocol):
    def GetNodeGraph(self, layerIdx: int | None = None) -> GraphApi | None: ...
    def SetCDL(self, CDL: dict[str, Any]) -> bool: ...
    def CopyGrades(self, tgtTimelineItems: list[Any]) -> bool: ...
    def AddVersion(self, versionName: str, versionType: int) -> bool: ...
    def LoadVersionByName(self, versionName: str, versionType: int) -> bool: ...
    def RenameVersionByName(self, oldName: str, newName: str, versionType: int) -> bool: ...
    def DeleteVersionByName(self, versionName: str, versionType: int) -> bool: ...
    def AssignToColorGroup(self, colorGroup: Any) -> bool: ...
    def RemoveFromColorGroup(self) -> bool: ...
    def SetColorOutputCache(self, enabled: bool) -> bool: ...
    def ResetAllNodeColors(self) -> bool: ...
    def ExportLUT(self, exportType: Any, path: str) -> bool: ...


class AlbumApi(Protocol):
    def GetStills(self) -> list[Any]: ...
    def GetLabel(self, galleryStill: Any) -> str: ...
    def SetLabel(self, galleryStill: Any, label: str) -> bool: ...
    def ImportStills(self, filePaths: list[str]) -> bool: ...
    def ExportStills(
        self, galleryStill: list[Any], folderPath: str, filePrefix: str, format: str
    ) -> bool: ...
    def DeleteStills(self, galleryStill: list[Any]) -> bool: ...


class GalleryApi(Protocol):
    def GetAlbumName(self, galleryStillAlbum: Any) -> str: ...
    def SetAlbumName(self, galleryStillAlbum: Any, albumName: str) -> bool: ...
    def GetCurrentStillAlbum(self) -> AlbumApi | None: ...
    def SetCurrentStillAlbum(self, galleryStillAlbum: Any) -> bool: ...
    def CreateGalleryStillAlbum(self) -> AlbumApi | None: ...
    def CreateGalleryPowerGradeAlbum(self) -> AlbumApi | None: ...
    def GetGalleryStillAlbums(self) -> list[AlbumApi]: ...
    def GetGalleryPowerGradeAlbums(self) -> list[AlbumApi]: ...


class ColorProject(ProjectEdit, Protocol):
    def AddColorGroup(self, groupName: str) -> Any: ...
    def DeleteColorGroup(self, colorGroup: Any) -> bool: ...
    def GetGallery(self) -> Any: ...
    def RefreshLUTList(self) -> bool: ...


def enum(session: Session, name: str) -> object:
    """`resolve.<name>` (KTD24); a Resolve without it is API_FAILED, never a guessed number."""
    value = getattr(session.resolve, name, None)
    if value is None:
        raise AxiError(
            "API_FAILED",
            f"this Resolve has no {name}",
            help=["Update DaVinci Resolve to a version that offers it"],
        )
    return value


def node_number(text: str) -> int:
    try:
        number = int(text)
    except ValueError:
        number = 0
    if number < 1:
        raise AxiError(
            "VALIDATION_ERROR",
            f"node {text} is not a node number",
            help=["Nodes are numbered from 1; `davinci-axi color graph --item V1:1` lists them"],
        )
    return number


def resolve_many(timeline: TimelineApi, text: str) -> list[Located]:
    """A comma-separated list of item references (KTD26), each resolved by `resolve_item`."""
    refs = [part.strip() for part in text.split(",") if part.strip()]
    if not refs:
        raise AxiError(
            "VALIDATION_ERROR",
            "no items given",
            help=["Valid example: V1:2,V1:3 (comma-separated, no spaces)"],
        )
    return [resolve_item(timeline, ref) for ref in refs]


def color_item(target: Target) -> ColorItem:
    return cast(ColorItem, target.located.item)


def item_write(
    inv: Invocation,
    target: Target,
    *,
    command: str,
    words: list[str],
    changes: list[Change],
    call: Callable[[], bool],
    read: Callable[[], dict[str, object]],
    fail: str,
    irreversible: bool = False,
    warning: str = "",
    extra: dict[str, object] | None = None,
) -> Result:
    """Run `call()` for an item verb under `--timeline` handling; `read()` is the read-back."""
    plan = MutationPlan(
        command=command,
        target=target.label,
        changes=changes,
        force_cmd=force_cmd(inv, *words, *tl_words(inv)),
        irreversible=irreversible,
        warning=warning,
        extra=extra if extra is not None else {},
        next=[show_hint(inv, target.ref)],
    )

    def apply() -> None:
        with with_current_timeline(target.session, target.timeline) as switched:
            if switched:
                plan.extra["timeline_restored"] = True
            if not call():
                raise api_failed(fail, "Resolve refused the change")

    def verify() -> dict[str, object]:
        with with_current_timeline(target.session, target.timeline):
            return read()

    return run_mutation(inv, target.session, plan, apply, verify)


def called_write(
    inv: Invocation,
    target: Target,
    *,
    command: str,
    words: list[str],
    call: Callable[[], bool],
    fail: str,
    irreversible: bool = False,
    warning: str = "",
    extra: dict[str, object] | None = None,
) -> Result:
    """KTD23: a write with no getter; `Change("called", False, True)`, checked by the return."""
    shown: dict[str, object] = {"read_back": CALLED_NOTE, **(extra or {})}
    return item_write(
        inv,
        target,
        command=command,
        words=words,
        changes=[Change("called", False, True)],
        call=call,
        read=lambda: {"called": True},
        fail=fail,
        irreversible=irreversible,
        warning=warning,
        extra=shown,
    )


def project_write(
    inv: Invocation,
    session: Session,
    *,
    command: str,
    words: list[str],
    changes: list[Change],
    call: Callable[[], bool],
    read: Callable[[], dict[str, object]],
    fail: str,
    snapshot: SnapshotPolicy = "required",
    irreversible: bool = False,
    warning: str = "",
    extra: dict[str, object] | None = None,
    next_lines: list[str] | None = None,
) -> Result:
    """Like `item_write` for project-level state (groups, gallery, LUT list)."""
    plan = MutationPlan(
        command=command,
        target=f"project {session.require_project().GetName()}",
        changes=changes,
        force_cmd=force_cmd(inv, *words),
        irreversible=irreversible,
        warning=warning,
        snapshot=snapshot,
        extra=extra if extra is not None else {},
        next=list(next_lines or []),
    )

    def apply() -> None:
        if not call():
            raise api_failed(fail, "Resolve refused the change")

    return run_mutation(inv, session, plan, apply, read)


def verb(
    name: str,
    summary: str,
    run: Callable[[Invocation], Result],
    positionals: tuple[Positional, ...] = (),
    flags: tuple[Flag, ...] = (),
    examples: str = "",
    methods: str = "",
    *,
    read: bool = False,
    disk: bool = False,
) -> CommandSpec:
    """One leaf spec. `examples` are `|`-separated; a write verb gets a `--force` example added
    (not a read or a disk-only export). `methods` are space-separated `Class.Method` names."""
    shown = [e for e in examples.split("|") if e]
    if shown and not (read or disk) and not any("--force" in e for e in shown):
        shown.append(shown[0] + " --force")
    return CommandSpec(
        name=name,
        summary=summary,
        run=run,
        positionals=positionals,
        flags=flags,
        examples=tuple(shown),
        reads_only=read,
        methods=tuple(methods.split()),
    )


def parent(
    name: str, summary: str, subcommands: dict[str, CommandSpec], examples: str
) -> CommandSpec:
    """A parent spec whose `methods` are the union of its children's (R36 aggregation)."""
    return CommandSpec(
        name=name,
        summary=summary,
        subcommands=subcommands,
        examples=tuple(examples.split("|")),
        reads_only=False,
        methods=tuple(dict.fromkeys(m for s in subcommands.values() for m in s.methods)),
    )
