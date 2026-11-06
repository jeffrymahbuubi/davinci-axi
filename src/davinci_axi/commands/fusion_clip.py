"""`fusion clip create`, `fusion insert composition|generator|title` and `fusion cache set` (U17b).

Resolve-side timeline verbs: a dry run until `--force`, then the project snapshot, the call and a
read-back (R27-R29). `clip create` replaces the chosen items, so it is irreversible; the three
inserts land on the Source Track Selector target and fail when that track is locked (quirk 8).
"""

from __future__ import annotations

from typing import cast

from davinci_axi.commands.fusion_edit import (
    ITEM_EXAMPLE,
    ITEM_FLAGS,
    ItemEdit,
    TimelineFusionEdit,
    item_mutation,
    mut_spec,
    open_item,
)
from davinci_axi.commands.item_detail import TRACK_TYPES, resolve_item, walk_items
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.fusion_guard import fusion_call
from davinci_axi.mutate import Change, MutationPlan
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

CACHE_MODES = ("auto", "enabled", "disabled")
LOCKED_NOTE = "it lands on the Source Track Selector target and fails if that track is locked"
TIMELINE_METHODS = (
    "Resolve.Fusion",
    "Timeline.CreateFusionClip",
    "Timeline.InsertFusionCompositionIntoTimeline",
    "Timeline.InsertFusionGeneratorIntoTimeline",
    "Timeline.InsertFusionTitleIntoTimeline",
)
TIMELINE_FLAGS: tuple[Flag, ...] = tuple(f for f in ITEM_FLAGS if f.name not in ("item", "active"))


def _total(edit: ItemEdit) -> int:
    return sum(1 for _ in walk_items(edit.timeline, TRACK_TYPES))


def run_clip_create(inv: Invocation) -> Result:
    refs = list(dict.fromkeys(r.strip() for r in inv.positionals["items"].split(",") if r.strip()))
    if not refs:
        raise AxiError(
            "VALIDATION_ERROR",
            "no items given",
            help=["Valid example: davinci-axi fusion clip create V1:1,V1:2"],
        )
    edit = open_item(inv, with_item=False)
    timeline = cast(TimelineFusionEdit, edit.timeline)
    items = [resolve_item(edit.timeline, ref).item for ref in refs]
    created: dict[str, str] = {}

    def apply(plan: MutationPlan) -> None:
        clip = fusion_call("CreateFusionClip", timeline.CreateFusionClip, items)
        if clip is None:
            raise api_failed("CreateFusionClip", "Resolve did not create a Fusion clip")
        created["id"] = clip.GetUniqueId()

    def verify() -> dict[str, object]:
        found = any(
            loc.item.GetUniqueId() == created["id"] for loc in walk_items(timeline, TRACK_TYPES)
        )
        return {"fusion_clip": "created" if found else None}

    return item_mutation(
        inv, edit, "fusion clip create", ["fusion", "clip", "create", ",".join(refs)],
        [Change("fusion_clip", None, "created")], apply, verify,
        irreversible=True,
        warning="replaces the chosen items with one Fusion clip; restore from the project snapshot",
        extra={"items": ", ".join(refs)},
    )  # fmt: skip


def _insert(inv: Invocation, kind: str, call: str, label: str | None) -> Result:
    edit = open_item(inv, with_item=False)
    timeline = cast(TimelineFusionEdit, edit.timeline)
    before = _total(edit)

    def apply(plan: MutationPlan) -> None:
        method = getattr(timeline, call)
        args = [label] if label is not None else []
        item = fusion_call(call, method, *args)
        if item is None:
            raise api_failed(call, f"Resolve inserted nothing; {LOCKED_NOTE}")
        # Report where it landed, so the next `fusion tool set --item` can address it
        uid = item.GetUniqueId()
        for loc in walk_items(timeline, TRACK_TYPES):
            if loc.item.GetUniqueId() == uid:
                plan.extra["item"] = f"{loc.track}:{loc.position}"
        plan.extra["item_id"] = uid

    words = ["fusion", "insert", kind] + ([label] if label is not None else [])
    return item_mutation(
        inv, edit, f"fusion insert {kind}", words, [Change("items", before, before + 1)],
        apply, lambda: {"items": _total(edit)},
        warning=f"{LOCKED_NOTE}; no track can be chosen",
        extra={"insert": label or kind},
    )  # fmt: skip


def run_insert_composition(inv: Invocation) -> Result:
    return _insert(inv, "composition", "InsertFusionCompositionIntoTimeline", None)


def run_insert_generator(inv: Invocation) -> Result:
    name = inv.positionals["name"]
    return _insert(inv, "generator", "InsertFusionGeneratorIntoTimeline", name)


def run_insert_title(inv: Invocation) -> Result:
    return _insert(inv, "title", "InsertFusionTitleIntoTimeline", inv.positionals["name"])


def run_cache_set(inv: Invocation) -> Result:
    mode = inv.positionals["mode"]
    if mode not in CACHE_MODES:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown cache mode {mode}",
            help=[f"Valid modes: {', '.join(CACHE_MODES)}"],
        )
    edit = open_item(inv)
    item = edit.item
    before = fusion_call("GetIsFusionOutputCacheEnabled", item.GetIsFusionOutputCacheEnabled)
    changes = [Change("cache", before, mode)] if str(before) != mode else []

    def apply(plan: MutationPlan) -> None:
        if not fusion_call("SetFusionOutputCache", item.SetFusionOutputCache, mode):
            raise api_failed("SetFusionOutputCache", f"could not set the cache to {mode}")

    def verify() -> dict[str, object]:
        return {
            "cache": fusion_call(
                "GetIsFusionOutputCacheEnabled", item.GetIsFusionOutputCacheEnabled
            )
        }

    words = ["fusion", "cache", "set", mode]
    return item_mutation(inv, edit, "fusion cache set", words, changes, apply, verify)


_NAME = Positional("name", help="generator or title name as the Effects Library shows it")

CLIP_SPEC = CommandSpec(
    name="clip",
    summary="Create Fusion clips from timeline items",
    reads_only=False,
    subcommands={
        "create": mut_spec(
            "create",
            "Replace timeline items with one Fusion clip (irreversible)",
            run_clip_create,
            positionals=(Positional("items", help="comma-separated items such as V1:1,V1:2"),),
            flags=TIMELINE_FLAGS,
            examples=(
                "davinci-axi fusion clip create V1:1,V1:2",
                "davinci-axi fusion clip create V1:1 --force",
            ),
            methods=("Timeline.CreateFusionClip",),
        )
    },
    examples=("davinci-axi fusion clip create V1:1,V1:2",),
    methods=("Timeline.CreateFusionClip",),
)

INSERT_SPEC = CommandSpec(
    name="insert",
    summary="Insert a Fusion composition, generator or title into the timeline",
    reads_only=False,
    subcommands={
        "composition": mut_spec(
            "composition",
            "Insert an empty Fusion composition at the playhead",
            run_insert_composition,
            flags=TIMELINE_FLAGS,
            examples=(
                "davinci-axi fusion insert composition",
                "davinci-axi fusion insert composition --force",
            ),
            methods=("Timeline.InsertFusionCompositionIntoTimeline",),
        ),
        "generator": mut_spec(
            "generator",
            "Insert a Fusion generator by name",
            run_insert_generator,
            positionals=(_NAME,),
            flags=TIMELINE_FLAGS,
            examples=("davinci-axi fusion insert generator 'Solid Color' --force",),
            methods=("Timeline.InsertFusionGeneratorIntoTimeline",),
        ),
        "title": mut_spec(
            "title",
            "Insert a Fusion title by name",
            run_insert_title,
            positionals=(_NAME,),
            flags=TIMELINE_FLAGS,
            examples=("davinci-axi fusion insert title 'Text+' --force",),
            methods=("Timeline.InsertFusionTitleIntoTimeline",),
        ),
    },
    examples=("davinci-axi fusion insert title 'Text+'",),
    methods=TIMELINE_METHODS[2:],
)

CACHE_SPEC = CommandSpec(
    name="cache",
    summary="Set a timeline item's Fusion output cache",
    reads_only=False,
    subcommands={
        "set": mut_spec(
            "set",
            "Set the Fusion output cache to auto, enabled or disabled",
            run_cache_set,
            positionals=(Positional("mode", help="auto, enabled or disabled"),),
            flags=ITEM_FLAGS,
            examples=(f"davinci-axi fusion cache set enabled {ITEM_EXAMPLE} --force",),
            methods=(
                "TimelineItem.GetIsFusionOutputCacheEnabled",
                "TimelineItem.SetFusionOutputCache",
            ),
        )
    },
    examples=(f"davinci-axi fusion cache set enabled {ITEM_EXAMPLE}",),
    methods=("TimelineItem.GetIsFusionOutputCacheEnabled", "TimelineItem.SetFusionOutputCache"),
)
