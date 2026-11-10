"""`timeline append|build|compound|insert|import-into`: put clips and files on a timeline (U25).

Every verb is a dry run until `--force` and goes through `mutate.run_mutation`: save, snapshot,
write, read back. `compound` replaces the chosen items, and `import-into` edits a timeline in
place, so both are irreversible except through the snapshot (R32).
"""

from __future__ import annotations

import shlex
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Protocol, cast

from davinci_axi.commands.fusion_clip import STALE_MARKS_NOTE
from davinci_axi.commands.insert_place import (
    INSERT_FLAGS,
    RECIPE,
    Landing,
    Victims,
    check_insert_flags,
    find_victims,
    landing_extra,
    landing_read,
    landing_warning,
    locate,
    marks_for,
    read_landing,
    refuse_ripple,
)
from davinci_axi.commands.item_detail import TRACK_TYPES, resolve_item, walk_items
from davinci_axi.commands.media_folders import Found, clips_target, open_pool, resolve_clips
from davinci_axi.commands.timeline_extra_common import (
    TIMECODE,
    items_hint,
    split_refs,
)
from davinci_axi.commands.timeline_tracks import (
    TIMELINE_FLAG,
    context_words,
    count_items,
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
    "generator": ("InsertGeneratorIntoTimeline", "a generator"),
    "ofx": ("InsertOFXGeneratorIntoTimeline", "an OFX generator"),
    "title": ("InsertTitleIntoTimeline", "a title"),
}
INSERT_METHODS = (
    "Timeline.GetMarkInOut",
    "Timeline.SetMarkInOut",
    "Timeline.ClearMarkInOut",
    "Timeline.GetCurrentTimecode",
    "Timeline.GetSettings",
    "Timeline.GetStartTimecode",
)


class PoolClips(MediaPoolEdit, Protocol):
    def AppendToTimeline(self, clipInfos: list[Info]) -> list[TimelineItemApi]: ...
    def CreateTimelineFromClips(self, name: str, clipInfos: list[Info]) -> TimelineApi | None: ...


class TimelineClips(TimelineEdit, Protocol):
    def CreateCompoundClip(
        self, timelineItems: list[Any], clipInfo: Info | None = None
    ) -> TimelineItemApi | None: ...
    def ImportIntoTimeline(self, filePath: str, importOptions: Info | None = None) -> bool: ...


def int_flag(inv: Invocation, key: str, minimum: int) -> int | None:
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


def track_kind(found: list[Found]) -> str:
    audio_only = all(str(f.clip.GetClipProperty("Type")) == "Audio" for f in found)
    return "audio" if audio_only else "video"


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


def _insert_words(inv: Invocation, kind: str, label: str) -> list[str]:
    words = ["timeline", "insert", kind, label, *context_words(inv)]
    for flag in ("at", "frame", "duration"):
        value = inv.args.get(flag)
        if value is not None:
            words += [f"--{flag}", str(value)]
    if inv.args.get("allow_ripple"):
        words.append("--allow-ripple")
    return words


def _predict(
    inv: Invocation, timeline: TimelineApi, recipe_cmd: str
) -> tuple[Landing | None, Victims]:
    """Where the insert lands and who it hurts; `None` when a drop-frame playhead hides it."""
    try:
        landing = read_landing(inv, timeline)
    except AxiError as err:
        placed = any(inv.args.get(flag) is not None for flag in ("at", "frame", "duration"))
        if err.code != "VALIDATION_ERROR" or placed:
            raise
        everything = find_victims(timeline, 0)
        if everything.shifted and not inv.args.get("allow_ripple"):
            raise AxiError(
                "WRITE_REFUSED",
                "the landing cannot be predicted on a drop-frame timeline, and the insert may "
                f"split or shift {len(everything.shifted)} items on every unlocked track",
                help=[
                    "Pass --allow-ripple to do it anyway",
                    RECIPE.replace("<insert command>", recipe_cmd),
                ],
            ) from err
        return None, Victims([], [])
    victims = find_victims(timeline, landing.frame)
    refuse_ripple(inv, landing, victims, recipe_cmd)
    return landing, victims


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
    check_insert_flags(inv)
    session = inv.connect()
    timeline = target_timeline(session, inv)
    landing, victims = _predict(inv, timeline, f"timeline insert {kind} {shlex.quote(label)}")
    if landing is None:
        warning = (
            "landing not predicted on a drop-frame timeline; refusing ripple anyway "
            "unless --allow-ripple"
        )
        extra: dict[str, object] = {"insert": label, "landing": "not predicted"}
        planned = "V1"
    else:
        warning = landing_warning(landing)
        extra = {"insert": label, **landing_extra(landing, victims, timeline.GetMarkInOut() or {})}
        planned = f"V1@{landing.frame}"
    plan = MutationPlan(
        command=f"timeline insert {kind}",
        target=f"timeline {timeline.GetName()}",
        changes=[Change(kind, None, planned)],
        force_cmd=force_cmd(inv, *_insert_words(inv, kind, label)),
        warning=warning,
        extra=extra,
        next=items_hint(),
    )
    placed: list[str] = []

    def apply() -> None:
        before = count_items(timeline)
        with marks_for(timeline, landing, plan) if landing else nullcontext():
            item = getattr(timeline, method)(label)
        if item is None:
            raise api_failed(f"Timeline.{method}", f"Resolve inserted nothing; {LOCKED_NOTE}")
        uid = item.GetUniqueId()
        placed.append(uid)
        after = count_items(timeline)
        plan.extra.update(
            item_id=uid,
            start=int(item.GetStart()),
            frames=int(item.GetDuration()),
            items=f"{before} -> {after}",
            ripple=after - before > 1,
        )
        found = locate(timeline, uid)
        if found is not None:
            plan.extra["item"] = f"{found.track}:{found.position}"

    def verify() -> dict[str, object]:
        read = landing_read(timeline, placed[0] if placed else None)
        if landing is None and read is not None:
            read = "V1"  # nothing was predicted, so V1 is the only claim to check
        return {kind: read}

    return run_mutation(inv, session, plan, apply, verify)


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


CLIPS_ARG = Positional("clips", help="clip ids or exact names, comma-separated")
_NAME = Positional("name", help="generator or title name as the Effects Library shows it")

BUILD_SPEC = CommandSpec(
    name="build",
    summary="Create a new timeline from media pool clips",
    run=run_build,
    positionals=(Positional("name", help="name of the new timeline"), CLIPS_ARG),
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
            summary=(
                f"Insert {noun} at the playhead on V1 (or at --at/--frame; "
                "ripples unless the timeline is empty there)"
            ),
            run=_INSERT_RUNS[kind],
            positionals=(_NAME,),
            flags=(TIMELINE_FLAG, *INSERT_FLAGS, *MUTATION_FLAGS),
            notes=(STALE_MARKS_NOTE,),
            examples=(
                f"davinci-axi timeline insert {kind} 'Text'",
                f"davinci-axi timeline insert {kind} 'Text' --force",
            ),
            reads_only=False,
            methods=(f"Timeline.{method}", *INSERT_METHODS),
        )
        for kind, (method, noun) in INSERTS.items()
    },
    examples=("davinci-axi timeline insert title 'Text' --force",),
    notes=(
        RECIPE,
        "No API picks the track: inserts land on V1; position and length follow the timeline's "
        "in/out marks, which is how --at and --duration work",
        STALE_MARKS_NOTE,
    ),
    methods=(*(f"Timeline.{method}" for method, _ in INSERTS.values()), *INSERT_METHODS),
)
