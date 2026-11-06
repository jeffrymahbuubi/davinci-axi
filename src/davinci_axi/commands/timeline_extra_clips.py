"""`timeline append|build|compound|insert|import-into`: put clips and files on a timeline (U25).

Every verb is a dry run until `--force` and goes through `mutate.run_mutation`: save, snapshot,
write, read back. `compound` replaces the chosen items, and `import-into` edits a timeline in
place, so both are irreversible except through the snapshot (R32).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, cast

from davinci_axi.commands.item_detail import TRACK_TYPES, resolve_item, walk_items
from davinci_axi.commands.media_folders import Found, clips_target, open_pool, resolve_clips
from davinci_axi.commands.timeline_extra_common import (
    TIMECODE,
    check_range,
    items_hint,
    source_range,
    split_refs,
)
from davinci_axi.commands.timeline_tracks import (
    TIMELINE_FLAG,
    context_words,
    count_items,
    plural,
    target_timeline,
    text_arg,
    timelines_hint,
)
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    coerce_like,
    force_cmd,
    parse_assignments,
    run_mutation,
    with_current_timeline,
)
from davinci_axi.output import collapse_home
from davinci_axi.resolve_types import Info, TimelineApi, TimelineItemApi
from davinci_axi.resolve_types_edit import MediaPoolEdit, TimelineEdit
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

LOCKED_NOTE = "it lands on the Source Track Selector target and fails if that track is locked"
# `AAFImportOptions` keys from the `.pyi`; `sourceClipsFolders` needs Folder objects, so it is
# not offered. Values are bool unless the type says otherwise.
AAF_KEYS: dict[str, type] = {
    "autoImportSourceClipsIntoMediaPool": bool,
    "ignoreFileExtensionsWhenMatching": bool,
    "linkToSourceCameraFiles": bool,
    "useSizingInfo": bool,
    "importMultiChannelAudioTracksAsLinkedGroups": bool,
    "insertAdditionalTracks": bool,
    "insertWithOffset": str,
    "sourceClipsPath": str,
}
INSERTS: dict[str, tuple[str, str]] = {
    "generator": ("InsertGeneratorIntoTimeline", "Insert a generator at the playhead"),
    "ofx": ("InsertOFXGeneratorIntoTimeline", "Insert an OFX generator at the playhead"),
    "title": ("InsertTitleIntoTimeline", "Insert a title at the playhead"),
}


class PoolClips(MediaPoolEdit, Protocol):
    def AppendToTimeline(self, clipInfos: list[Info]) -> list[TimelineItemApi]: ...
    def CreateTimelineFromClips(self, name: str, clipInfos: list[Info]) -> TimelineApi | None: ...


class TimelineClips(TimelineEdit, Protocol):
    def CreateCompoundClip(
        self, timelineItems: list[Any], clipInfo: Info | None = None
    ) -> TimelineItemApi | None: ...
    def ImportIntoTimeline(self, filePath: str, importOptions: Info | None = None) -> bool: ...


def _int_flag(inv: Invocation, key: str, minimum: int) -> int | None:
    value = inv.args.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        flag = key.replace("_", "-")
        raise AxiError(
            "VALIDATION_ERROR",
            f"--{flag} must be {minimum} or more",
            help=[f"Valid example: --{flag} {max(minimum, 1)}"],
        )
    return value


def _track_kind(found: list[Found]) -> str:
    audio_only = all(str(f.clip.GetClipProperty("Type")) == "Audio" for f in found)
    return "audio" if audio_only else "video"


def run_append(inv: Invocation) -> Result:
    track = _int_flag(inv, "track", 1)
    record = _int_flag(inv, "record_frame", 0)
    span = source_range(_int_flag(inv, "in", 0), _int_flag(inv, "out", 1))
    ctx = open_pool(inv)
    session = ctx.session
    timeline = session.require_timeline(text_arg(inv, "timeline"))
    found = resolve_clips(ctx, inv.positionals["clips"])
    if span is not None:
        check_range([one.clip for one in found], span)
    kind = _track_kind(found)
    if track is not None and track > timeline.GetTrackCount(kind):
        have = plural(timeline.GetTrackCount(kind), f"{kind} track")
        raise AxiError(
            "VALIDATION_ERROR",
            f"timeline {timeline.GetName()} has {have}, not {track}",
            help=["Add a track first with `davinci-axi timeline track add`"],
        )
    infos: list[Info] = []
    for one in found:
        info: Info = {"mediaPoolItem": one.clip}
        if track is not None:
            info["trackIndex"] = track
        if record is not None:
            info["recordFrame"] = record
        if span is not None:
            info["startFrame"], info["endFrame"] = span
        infos.append(info)
    wanted = {one.clip.GetUniqueId() for one in found}
    words = ["timeline", "append", inv.positionals["clips"]]
    start, end = span or (None, None)
    for flag, value in (
        ("--track", track),
        ("--record-frame", record),
        ("--in", start),
        ("--out", end),
    ):
        if value is not None:
            words += [flag, str(value)]
    plan = MutationPlan(
        command="timeline append",
        target=f"timeline {timeline.GetName()} ({clips_target(found)})",
        changes=[Change("appended", 0, len(wanted))],
        force_cmd=force_cmd(inv, *words, *context_words(inv)),
        extra={"track": f"{kind[0].upper()}{track}"} if track is not None else {},
        next=items_hint(),
    )
    pool = cast(PoolClips, ctx.pool)
    appended: list[TimelineItemApi] = []

    def apply() -> None:
        with with_current_timeline(session, timeline) as switched:
            if switched:
                plan.extra["timeline_restored"] = True
            result = pool.AppendToTimeline(infos)
        if not result:
            raise api_failed("MediaPool.AppendToTimeline", "Resolve appended nothing")
        appended.extend(result)

    def verify() -> dict[str, object]:
        present = {located.item.GetUniqueId() for located in walk_items(timeline, TRACK_TYPES)}
        got: set[str] = set()
        for item in appended:
            media = item.GetMediaPoolItem()
            if item.GetUniqueId() in present and media is not None:
                got.add(media.GetUniqueId())
        return {"appended": len(got & wanted)}

    return run_mutation(inv, session, plan, apply, verify)


def run_build(inv: Invocation) -> Result:
    name = inv.positionals["name"].strip()
    if not name:
        raise AxiError(
            "VALIDATION_ERROR",
            "the timeline name is empty",
            help=["Valid example: davinci-axi timeline build scratch A001_beach,A002_city"],
        )
    ctx = open_pool(inv)
    session = ctx.session
    if session.find_timeline(name) is not None:
        raise AxiError(
            "TIMELINE_EXISTS",
            f"a timeline named {name} already exists",
            help=["Choose another name; this command never overwrites a timeline"],
        )
    found = resolve_clips(ctx, inv.positionals["clips"])
    project = session.require_project()
    plan = MutationPlan(
        command="timeline build",
        target=f"project {project.GetName()}",
        changes=[Change("timeline", None, name)],
        force_cmd=force_cmd(
            inv, "timeline", "build", name, inv.positionals["clips"], *context_words(inv)
        ),
        warning="the new timeline may become the current timeline",
        extra={"clips": clips_target(found)},
        next=timelines_hint(),
    )
    pool = cast(PoolClips, ctx.pool)

    def apply() -> None:
        made = pool.CreateTimelineFromClips(name, [{"mediaPoolItem": f.clip} for f in found])
        if made is None:
            raise api_failed("MediaPool.CreateTimelineFromClips", f"could not build {name}")

    def verify() -> dict[str, object]:
        return {"timeline": name if session.find_timeline(name) is not None else None}

    return run_mutation(inv, session, plan, apply, verify)


def run_compound(inv: Invocation) -> Result:
    refs = split_refs(inv.positionals["items"], "item", "davinci-axi timeline compound V1:1,V1:2")
    name = text_arg(inv, "name")
    start_tc = text_arg(inv, "start_tc")
    if start_tc is not None and not TIMECODE.match(start_tc):
        raise AxiError(
            "VALIDATION_ERROR",
            f"invalid timecode {start_tc}",
            help=["Valid timecode: HH:MM:SS:FF, e.g. 00:00:00:00"],
        )
    check_snapshot_flags(inv)
    session = inv.connect()
    timeline = cast(TimelineClips, target_timeline(session, inv))
    items = [resolve_item(timeline, ref).item for ref in refs]
    options: Info = {}
    if name is not None:
        options["name"] = name
    if start_tc is not None:
        options["startTimecode"] = start_tc
    words = ["timeline", "compound", ",".join(refs), *context_words(inv)]
    for flag, value in (("--name", name), ("--start-tc", start_tc)):
        if value is not None:
            words += [flag, value]
    shown = name or "created"
    plan = MutationPlan(
        command="timeline compound",
        target=f"timeline {timeline.GetName()} ({', '.join(refs)})",
        changes=[Change("compound clip", None, shown)],
        force_cmd=force_cmd(inv, *words),
        irreversible=True,
        warning="replaces the chosen items with one compound clip; restore from the snapshot",
        next=items_hint(),
    )
    created: dict[str, str] = {}

    def apply() -> None:
        clip = timeline.CreateCompoundClip(items, options or None)
        if clip is None:
            raise api_failed(
                "Timeline.CreateCompoundClip", "Resolve did not create a compound clip"
            )
        created["id"] = clip.GetUniqueId()

    def verify() -> dict[str, object]:
        found = any(
            located.item.GetUniqueId() == created["id"]
            for located in walk_items(timeline, TRACK_TYPES)
        )
        return {"compound clip": shown if found else None}

    return run_mutation(inv, session, plan, apply, verify)


def _run_insert(inv: Invocation, kind: str) -> Result:
    method = INSERTS[kind][0]
    label = inv.positionals["name"].strip()
    if not label:
        raise AxiError(
            "VALIDATION_ERROR",
            f"the {kind} name is empty",
            help=[f"Valid example: davinci-axi timeline insert {kind} 'Text'"],
        )
    check_snapshot_flags(inv)
    session = inv.connect()
    timeline = target_timeline(session, inv)
    before = count_items(timeline)
    plan = MutationPlan(
        command=f"timeline insert {kind}",
        target=f"timeline {timeline.GetName()}",
        changes=[Change("items", before, before + 1)],
        force_cmd=force_cmd(inv, "timeline", "insert", kind, label, *context_words(inv)),
        warning=f"{LOCKED_NOTE}; no track can be chosen",
        extra={"insert": label},
        next=items_hint(),
    )

    def apply() -> None:
        if getattr(timeline, method)(label) is None:
            raise api_failed(f"Timeline.{method}", f"Resolve inserted nothing; {LOCKED_NOTE}")

    return run_mutation(inv, session, plan, apply, lambda: {"items": count_items(timeline)})


def run_insert_generator(inv: Invocation) -> Result:
    return _run_insert(inv, "generator")


def run_insert_ofx(inv: Invocation) -> Result:
    return _run_insert(inv, "ofx")


def run_insert_title(inv: Invocation) -> Result:
    return _run_insert(inv, "title")


def _aaf_options(raw: str | None) -> Info:
    if raw is None:
        return {}
    options: Info = {}
    for key, text in parse_assignments(raw).items():
        if key not in AAF_KEYS:
            raise AxiError(
                "VALIDATION_ERROR",
                f"unknown AAF import option {key}",
                help=[f"Valid options: {', '.join(AAF_KEYS)}"],
            )
        value = coerce_like(text, False) if AAF_KEYS[key] is bool else text
        if key == "insertWithOffset" and not TIMECODE.match(text):
            raise AxiError(
                "VALIDATION_ERROR",
                f"insertWithOffset needs a timecode, got {text}",
                help=["Valid example: insertWithOffset=01:00:00:00"],
            )
        options[key] = value
    return options


def run_import_into(inv: Invocation) -> Result:
    path = Path(inv.positionals["file"]).expanduser().resolve()
    if path.suffix.lower() != ".aaf":
        shown = path.suffix or "(none)"
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot import a {shown} file into a timeline",
            help=["Only .aaf files can be imported into an existing timeline"],
        )
    options = _aaf_options(text_arg(inv, "set"))
    check_snapshot_flags(inv)
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path; nothing was changed")
    session = inv.connect()
    timeline = cast(TimelineClips, target_timeline(session, inv))
    before = count_items(timeline)
    raw_set = text_arg(inv, "set")
    words = ["timeline", "import-into", inv.positionals["file"], *context_words(inv)]
    if raw_set is not None:
        words += ["--set", raw_set]
    plan = MutationPlan(
        command="timeline import-into",
        target=f"timeline {timeline.GetName()}",
        changes=[Change("aaf", "not imported", "imported")],
        force_cmd=force_cmd(inv, *words),
        irreversible=True,
        warning="edits the current timeline in place; restore from the project snapshot",
        extra={"file": collapse_home(path), "items": before},
        next=items_hint(),
    )

    def apply() -> None:
        with with_current_timeline(session, timeline) as switched:
            if switched:
                plan.extra["timeline_restored"] = True
            ok = timeline.ImportIntoTimeline(str(path), options or None)
        if not ok:
            raise api_failed("Timeline.ImportIntoTimeline", f"could not import {path.name}")

    def verify() -> dict[str, object]:
        after = count_items(timeline)
        plan.extra["items"] = f"{before} -> {after}"
        return {"aaf": "imported" if after > before else "no new items"}

    return run_mutation(inv, session, plan, apply, verify)


_CLIPS = Positional("clips", help="clip ids or exact names, comma-separated")
_NAME = Positional("name", help="generator or title name as the Effects Library shows it")

APPEND_SPEC = CommandSpec(
    name="append",
    summary="Append media pool clips to the end of a timeline",
    run=run_append,
    positionals=(_CLIPS,),
    flags=(
        Flag("track", "int", None, help="destination track number (default: Resolve picks)"),
        Flag("record-frame", "int", None, help="record frame to place the clips at"),
        Flag("in", "int", None, help="source frame to start each clip at (needs --out)"),
        Flag("out", "int", None, help="source frame to end each clip at (exclusive; needs --in)"),
        TIMELINE_FLAG,
        *MUTATION_FLAGS,
    ),
    examples=(
        "davinci-axi timeline append A003_dusk --track 2",
        "davinci-axi timeline append A001_beach,A002_city --force",
        "davinci-axi timeline append A001_beach --in 30 --out 120 --force",
    ),
    reads_only=False,
    methods=(
        "MediaPool.AppendToTimeline",
        "Folder.GetClipList",
        "Folder.GetSubFolderList",
        "MediaPoolItem.GetClipProperty",
        "Project.GetCurrentTimeline",
        "Project.SetCurrentTimeline",
        "Timeline.GetItemListInTrack",
    ),
)
BUILD_SPEC = CommandSpec(
    name="build",
    summary="Create a new timeline from media pool clips",
    run=run_build,
    positionals=(Positional("name", help="name of the new timeline"), _CLIPS),
    flags=MUTATION_FLAGS,
    examples=(
        "davinci-axi timeline build scratch A001_beach,A002_city",
        "davinci-axi timeline build scratch A001_beach,A002_city --force",
    ),
    reads_only=False,
    methods=("MediaPool.CreateTimelineFromClips",),
)
COMPOUND_SPEC = CommandSpec(
    name="compound",
    summary="Replace timeline items with one compound clip (irreversible)",
    run=run_compound,
    positionals=(Positional("items", help="comma-separated items such as V1:1,V1:2"),),
    flags=(
        Flag("name", "str", None, help="name of the compound clip"),
        Flag("start-tc", "str", None, help="start timecode of the compound clip"),
        TIMELINE_FLAG,
        *MUTATION_FLAGS,
    ),
    examples=(
        "davinci-axi timeline compound V1:1,V1:2 --name Comp",
        "davinci-axi timeline compound V1:1,V1:2 --name Comp --force",
    ),
    reads_only=False,
    methods=("Timeline.CreateCompoundClip",),
)
IMPORT_INTO_SPEC = CommandSpec(
    name="import-into",
    summary="Import an AAF file into the timeline in place (irreversible)",
    run=run_import_into,
    positionals=(Positional("file", help="an .aaf file"),),
    flags=(
        Flag("set", "str", None, help=f"options as key=value,...: {', '.join(AAF_KEYS)}"),
        TIMELINE_FLAG,
        *MUTATION_FLAGS,
    ),
    examples=(
        "davinci-axi timeline import-into ~/cut.aaf",
        "davinci-axi timeline import-into ~/cut.aaf --set insertAdditionalTracks=true --force",
    ),
    reads_only=False,
    methods=("Timeline.ImportIntoTimeline",),
)
_INSERT_RUNS = {
    "generator": run_insert_generator,
    "ofx": run_insert_ofx,
    "title": run_insert_title,
}
INSERT_SPEC = CommandSpec(
    name="insert",
    summary="Insert a generator, OFX generator or title into the timeline",
    reads_only=False,
    subcommands={
        kind: CommandSpec(
            name=kind,
            summary=summary,
            run=_INSERT_RUNS[kind],
            positionals=(_NAME,),
            flags=(TIMELINE_FLAG, *MUTATION_FLAGS),
            examples=(
                f"davinci-axi timeline insert {kind} 'Text'",
                f"davinci-axi timeline insert {kind} 'Text' --force",
            ),
            reads_only=False,
            methods=(f"Timeline.{method}",),
        )
        for kind, (method, summary) in INSERTS.items()
    },
    examples=("davinci-axi timeline insert title 'Text' --force",),
    methods=tuple(f"Timeline.{method}" for method, _ in INSERTS.values()),
)
