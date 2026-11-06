"""Shared plumbing for the `audio` commands (U18): write Protocols, picking and checking.

`Picked` resolves a comma-separated `items` positional against a timeline; `Scope` resolves
`<clips>` or `--folder <path>` in the media pool. `NOT_AVAILABLE` (KTD29) is what a False from an
AI call becomes: Studio or AI Extras is missing. Enum arguments are words mapped to
`resolve.<CONST>` at run time (KTD24).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast

from davinci_axi.commands.item_detail import Located, resolve_item, str_arg
from davinci_axi.commands.media_folders import (
    Ctx,
    Found,
    clips_target,
    open_pool,
    resolve_clips,
    resolve_folder,
    walk_folders,
)
from davinci_axi.commands.render_common import methods_of
from davinci_axi.errors import AxiError
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    MutationPlan,
    check_snapshot_flags,
    with_current_timeline,
)
from davinci_axi.resolve_types import (
    FolderApi,
    Info,
    MediaPoolItemApi,
    TimelineApi,
    TimelineItemApi,
)
from davinci_axi.resolve_types_edit import MediaPoolEdit, ProjectEdit
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

STUDIO_AI = "DaVinci Resolve Studio with AI Extras"


class AudioTimeline(TimelineApi, Protocol):
    def GetVoiceIsolationState(self, trackIndex: int) -> Info: ...
    def SetVoiceIsolationState(self, trackIndex: int, voiceIsolationState: Info) -> bool: ...
    def NormalizeAudioLevel(
        self, timelineItems: list[Any], normalizeAudioOptions: Info | None = None
    ) -> bool: ...
    def AutoAlignClips(
        self, timelineItems: list[Any], autoAlignOptions: Info | None = None
    ) -> bool: ...


class AudioItem(TimelineItemApi, Protocol):
    def SetVoiceIsolationState(self, state: Info) -> bool: ...
    def SetSourceAudioChannelMapping(self, audioMapping: str) -> bool: ...


class AudioClip(MediaPoolItemApi, Protocol):
    def SetAudioMapping(self, audioMapping: str) -> bool: ...
    def GetTranscription(self, useNestedClipTranscription: bool = False) -> Info: ...


class AudioPool(MediaPoolEdit, Protocol):
    def AutoSyncAudio(self, mediaPoolItems: list[Any], audioSyncSettings: Info) -> bool: ...


class AudioProject(ProjectEdit, Protocol):
    def GetTranscribeAudioStatus(self) -> Info: ...
    def InsertAudioToCurrentTrackAtPlayhead(
        self, mediaPath: str, startOffsetInSamples: int, durationInSamples: int
    ) -> bool: ...
    def ApplyFairlightPresetToCurrentTimeline(self, presetName: str) -> bool: ...
    def GenerateSpeech(self, speechSettings: Info) -> MediaPoolItemApi | None: ...


def not_available(method: str, needs: str = STUDIO_AI) -> AxiError:
    return AxiError(
        "NOT_AVAILABLE",
        f"Resolve returned False for {method}; this needs {needs}",
        help=[
            "Check the licence (Studio) and that AI Extras is installed",
            "Check that Resolve shows no dialog and the media is online",
        ],
        detail={"method": method},
    )


def enum(session: Session, name: str) -> Any:
    """`resolve.<NAME>` (KTD24); a missing constant is API_FAILED."""
    value = getattr(session.resolve, name, None)
    if value is None:
        raise AxiError(
            "API_FAILED",
            f"this Resolve has no {name}",
            help=["Update Resolve to a version that offers it"],
        )
    return value


@dataclass
class Picked:
    """Timeline items chosen by a comma-separated positional, with their session and timeline."""

    session: Session
    timeline: AudioTimeline
    located: list[Located]

    @property
    def items(self) -> list[Any]:
        return [one.item for one in self.located]

    @property
    def refs(self) -> str:
        return ", ".join(f"{one.track}:{one.position}" for one in self.located)

    @property
    def target(self) -> str:
        where = f"in timeline {self.timeline.GetName()}"
        if len(self.located) == 1:
            one = self.located[0]
            return f"item {one.track}:{one.position} ({one.item.GetName()}) {where}"
        return f"{len(self.located)} items ({self.refs}) {where}"


def open_items(inv: Invocation) -> Picked:
    """Refuse a bad `--no-snapshot`, connect, and resolve the `items` positional."""
    check_snapshot_flags(inv)
    session = inv.connect()
    timeline = cast(AudioTimeline, session.require_timeline(str_arg(inv, "timeline")))
    located: list[Located] = []
    seen: set[str] = set()
    for part in (p.strip() for p in inv.positionals["items"].split(",")):
        if not part:
            raise AxiError(
                "VALIDATION_ERROR",
                "an empty item in the comma-separated list",
                help=["Valid example: A1:1,A1:2"],
            )
        one = resolve_item(timeline, part)
        uid = one.item.GetUniqueId()
        if uid not in seen:
            seen.add(uid)
            located.append(one)
    return Picked(session, timeline, located)


def segment_count(clip: MediaPoolItemApi, nested: bool = False) -> int:
    transcription = cast(AudioClip, clip).GetTranscription(nested) or {}
    return len(transcription.get("segments") or [])


# ---- clips or a folder ------------------------------------------------------------------------


@dataclass
class Scope:
    """Either some clips or one folder (and everything under it)."""

    ctx: Ctx
    found: list[Found]
    folder: tuple[str, FolderApi] | None
    words: list[str]

    @property
    def target(self) -> str:
        if self.folder is not None:
            return f"media pool folder {self.folder[0]} (and its sub folders)"
        return clips_target(self.found)

    @property
    def objects(self) -> list[Any]:
        return [self.folder[1]] if self.folder is not None else [f.clip for f in self.found]

    def clips(self) -> list[Found]:
        if self.folder is None:
            return self.found
        path, root = self.folder
        return [
            Found(where, clip)
            for where, folder in walk_folders(root, path)
            for clip in folder.GetClipList() or []
        ]


def pick_scope(inv: Invocation, base: list[str]) -> Scope:
    """`<clips>` or `--folder <path>`, exactly one of them."""
    clips, folder = inv.positionals.get("clips"), str_arg(inv, "folder")
    if (clips is None) == (folder is None):
        raise AxiError(
            "VALIDATION_ERROR",
            "pass clips or --folder, exactly one of them",
            help=[f"Valid example: davinci-axi {' '.join(base)} A001_beach"],
        )
    ctx = open_pool(inv)
    if folder is not None:
        return Scope(ctx, [], resolve_folder(ctx, folder), [*base, "--folder", folder])
    return Scope(ctx, resolve_clips(ctx, str(clips)), None, [*base, str(clips)])


SCOPE_ARGS = (Positional("clips", required=False, help="clip ids or exact names, comma-separated"),)
FOLDER_FLAG = Flag("folder", "str", None, help="a media pool folder path instead of clips")
UNTESTED = "the call has no getter, so the result is not read back"

MEDIA_READS = (
    "MediaPool.GetRootFolder",
    "MediaPool.GetCurrentFolder",
    "Folder.GetClipList",
    "Folder.GetSubFolderList",
    "Folder.GetName",
    "Folder.GetUniqueId",
    "MediaPoolItem.GetName",
    "MediaPoolItem.GetUniqueId",
)


def verb(name: str, summary: str, run: Callable[[Invocation], Result], **kw: Any) -> CommandSpec:
    """A mutation verb: the given flags plus the mutation flags, `reads_only=False`."""
    flags = (*kw.pop("flags", ()), *MUTATION_FLAGS)
    return CommandSpec(name=name, summary=summary, run=run, flags=flags, reads_only=False, **kw)


def flag_words(inv: Invocation, names: tuple[str, ...]) -> list[str]:
    """`--name value` for every named value flag the caller gave, for the force command."""
    words: list[str] = []
    for name in names:
        value = inv.args.get(name.replace("-", "_"))
        if value not in (None, "", False):
            words += [f"--{name}", str(value)]
    return words


TIMELINE_READS = methods_of(
    Timeline="GetItemListInTrack GetTrackCount GetName GetUniqueId",
    TimelineItem="GetName GetUniqueId GetTrackTypeAndIndex",
    Project="GetCurrentTimeline SetCurrentTimeline GetName",
)


@contextmanager
def timeline_step(session: Session, timeline: AudioTimeline, plan: MutationPlan) -> Iterator[None]:
    """`with_current_timeline` for a write, noting a restore in the output."""
    with with_current_timeline(session, timeline) as switched:
        if switched:
            plan.extra["timeline_restored"] = True
        yield
