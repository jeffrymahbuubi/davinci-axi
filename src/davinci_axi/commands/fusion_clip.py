"""`fusion clip create`, `fusion insert composition|generator|title` and `fusion cache set` (U17b).

Resolve-side timeline verbs: a dry run until `--force`, then the project snapshot, the call and a
read-back (R27-R29). `clip create` replaces the chosen items, so it is irreversible; the three
inserts land on the Source Track Selector target and fail when that track is locked (quirk 8).
"""

from __future__ import annotations

import shlex
from contextlib import AbstractContextManager, nullcontext
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
from davinci_axi.commands.insert_place import (
    ABOUT_FRAMES,
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
STALE_MARKS_NOTE = (
    "Timeline marks left by an earlier `render add --set MarkIn=..,MarkOut=..` move the landing "
    "and set the length; the dry run shows them as marks_before; clear them with "
    "`timeline mark clear --force` or pass --at/--frame"
)
INSERT_NOTES = (
    RECIPE,
    "No API picks the track: inserts land on V1; position and length follow the timeline's "
    "in/out marks, which is how --at and --duration work",
    STALE_MARKS_NOTE,
    "Standalone Text+ titles render; see `fusion --help` for the item-comp overlay check",
)
INSERT_METHODS = (
    *TIMELINE_METHODS[2:],
    "Timeline.GetMarkInOut",
    "Timeline.SetMarkInOut",
    "Timeline.ClearMarkInOut",
    "Timeline.GetCurrentTimecode",
    "Timeline.GetSettings",
    "Timeline.GetStartTimecode",
)


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


def _placement_words(inv: Invocation) -> list[str]:
    """The placement flags as given, so the `--force` line repeats them."""
    words: list[str] = []
    for name in ("at", "frame", "duration"):
        value = inv.args.get(name)
        if value is not None:
            words += [f"--{name}", str(value)]
    return words + (["--allow-ripple"] if inv.args.get("allow_ripple") else [])


def _predict(inv: Invocation, timeline: TimelineFusionEdit, recipe_cmd: str) -> Landing | None:
    """Landing and ripple guard; None when a drop-frame playhead hides the landing."""
    try:
        landing = read_landing(inv, timeline)
    except AxiError as err:
        placed = any(inv.args.get(n) is not None for n in ("at", "frame", "duration"))
        if err.code != "VALIDATION_ERROR" or placed:
            raise
        hurt = find_victims(timeline, 0)  # the landing is unknown: any item may be split or moved
        if (hurt.splits or hurt.shifted) and not inv.args.get("allow_ripple"):
            raise AxiError(
                "WRITE_REFUSED",
                f"cannot predict where this lands ({err.message}), so it may split or shift "
                f"{len(hurt.shifted) + len(hurt.splits)} items on every unlocked track",
                help=[
                    "Pass --allow-ripple to do it anyway",
                    RECIPE.replace("<insert command>", recipe_cmd),
                ],
            ) from err
        return None
    refuse_ripple(inv, landing, find_victims(timeline, landing.frame), recipe_cmd)
    return landing


def _insert(inv: Invocation, kind: str, call: str, label: str | None) -> Result:
    check_insert_flags(inv)
    edit = open_item(inv, with_item=False)
    timeline = cast(TimelineFusionEdit, edit.timeline)
    before = _total(edit)
    recipe_cmd = f"fusion insert {kind}" + (f" {shlex.quote(label)}" if label is not None else "")
    landing = _predict(inv, timeline, recipe_cmd)
    victims = find_victims(timeline, landing.frame) if landing else Victims([], [])
    marks = timeline.GetMarkInOut() or {}
    warning = f"{LOCKED_NOTE}; no track can be chosen"
    expected = "V1 (at the playhead)"
    extra: dict[str, object] = {"insert": label or kind}
    if landing is not None:
        warning = f"{landing_warning(landing)}; {warning}"
        extra.update(landing_extra(landing, victims, marks))
        expected = f"V1@{landing.frame}"
    else:
        warning = (
            "the playhead is drop-frame, so the landing cannot be predicted (the call lands on "
            f"V1 at the playhead, {ABOUT_FRAMES} frames, and ripple-inserts); {warning}"
        )
        extra["landing"] = "unknown (drop-frame playhead)"
    created: dict[str, str] = {}

    def apply(plan: MutationPlan) -> None:
        method = getattr(timeline, call)
        args = [label] if label is not None else []
        guard: AbstractContextManager[None] = (
            marks_for(timeline, landing, plan) if landing else nullcontext()
        )
        with guard:
            item = fusion_call(call, method, *args)
            if item is None:
                raise api_failed(call, f"Resolve inserted nothing; {LOCKED_NOTE}")
        # Report where it landed, so the next `fusion tool set --item` can address it
        uid = item.GetUniqueId()
        created["id"] = uid
        located = locate(timeline, uid)
        if located is not None:
            plan.extra["item"] = f"{located.track}:{located.position}"
        plan.extra["item_id"] = uid
        total = _total(edit)
        plan.extra.update(
            start=int(item.GetStart()),
            frames=int(item.GetDuration()),
            items=f"{before} -> {total}",
            ripple=total - before > 1,
        )

    def verify() -> dict[str, object]:
        read = landing_read(timeline, created.get("id"))
        if landing is None and read is not None and read.startswith("V1@"):
            read = expected  # the playhead frame was not predictable; V1 is what is checked
        return {kind: read}

    words = ["fusion", "insert", kind] + ([label] if label is not None else [])
    return item_mutation(
        inv, edit, f"fusion insert {kind}", [*words, *_placement_words(inv)],
        [Change(kind, None, expected)], apply, verify,
        warning=warning, extra=extra,
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
            "Insert an empty Fusion composition at the playhead on V1 (or at --at/--frame)",
            run_insert_composition,
            flags=(*TIMELINE_FLAGS, *INSERT_FLAGS),
            notes=INSERT_NOTES,
            examples=(
                "davinci-axi fusion insert composition",
                "davinci-axi fusion insert composition --allow-ripple --force",
            ),
            methods=INSERT_METHODS,
        ),
        "generator": mut_spec(
            "generator",
            "Insert a Fusion generator by name at the playhead on V1 (or at --at/--frame)",
            run_insert_generator,
            positionals=(_NAME,),
            flags=(*TIMELINE_FLAGS, *INSERT_FLAGS),
            notes=INSERT_NOTES,
            examples=(
                "davinci-axi fusion insert generator 'Solid Color' --allow-ripple --force",
                "davinci-axi fusion insert generator 'Solid Color' --at 01:00:02:00 --duration 60",
            ),
            methods=INSERT_METHODS,
        ),
        "title": mut_spec(
            "title",
            "Insert a Fusion title by name at the playhead on V1 (or at --at/--frame)",
            run_insert_title,
            positionals=(_NAME,),
            flags=(*TIMELINE_FLAGS, *INSERT_FLAGS),
            notes=INSERT_NOTES,
            examples=(
                "davinci-axi fusion insert title 'Text+' --allow-ripple --force",
                "davinci-axi fusion insert title 'Text+' --at 01:00:02:00 --duration 60",
            ),
            methods=INSERT_METHODS,
        ),
    },
    notes=INSERT_NOTES,
    examples=("davinci-axi fusion insert title 'Text+'",),
    methods=INSERT_METHODS,
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
