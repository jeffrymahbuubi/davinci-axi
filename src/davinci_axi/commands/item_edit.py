"""`item set|rename|enable|disable|delete|color|flag|marker|speed|fades` beside `item <id>` (U12).

`SPEC` wraps the read-only `item` spec with `dataclasses.replace` (KTD15): `item V1:2` stays the
detail view because `cli._descend` falls through to the positionals when the word is not a
subcommand. Every verb is a dry run until `--force` and goes through `mutate.run_mutation`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from davinci_axi.commands.item_detail import TRACK_TYPES, walk_items
from davinci_axi.commands.item_markers import (
    FADES_SPEC,
    ITEM_FLAGS,
    ITEM_ID,
    MARKER_SPEC,
    SPEED_SPEC,
    Target,
    open_target,
    show_hint,
    tl_words,
)
from davinci_axi.commands.items import ITEM_SPEC
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.markers import CLIP_COLORS, MARKER_COLORS
from davinci_axi.mutate import (
    Change,
    MutationPlan,
    coerce_like,
    force_cmd,
    parse_assignments,
    run_mutation,
    with_current_timeline,
)
from davinci_axi.output import hint
from davinci_axi.resolve_types_edit import TimelineEdit
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

ASSIGNMENTS = Positional(
    "assignments", help="comma-separated Property=value pairs, e.g. Opacity=50"
)


def _color_choice(color: str, choices: tuple[str, ...], flag: str = "colour") -> str:
    if color not in choices:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown {flag} {color}",
            help=[f"Valid colours: {', '.join(choices)}"],
        )
    return color


def _plan(
    target: Target, command: str, changes: list[Change], words: list[str], inv: Invocation
) -> MutationPlan:
    return MutationPlan(
        command=command,
        target=target.label,
        changes=changes,
        force_cmd=force_cmd(inv, *words),
        next=[show_hint(inv, target.ref)],
    )


def run_set(inv: Invocation) -> Result:
    assignments = parse_assignments(inv.positionals["assignments"])
    target = open_target(inv)
    item = target.item
    live: dict[str, Any] = item.GetProperties() or {}
    unknown = [key for key in assignments if key not in live]
    if unknown:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown item propert{'y' if len(unknown) == 1 else 'ies'} {', '.join(unknown)}",
            help=[f"Valid properties for this item: {', '.join(live)}"],
        )
    wanted = {key: coerce_like(value, live[key]) for key, value in assignments.items()}
    changed = {key: value for key, value in wanted.items() if value != live[key]}
    words = ["item", "set", target.ref, inv.positionals["assignments"], *tl_words(inv)]
    plan = _plan(
        target, "item set", [Change(k, live[k], v) for k, v in changed.items()], words, inv
    )

    def apply() -> None:
        with with_current_timeline(target.session, target.timeline) as switched:
            if switched:
                plan.extra["timeline_restored"] = True
            if not item.SetProperties(dict(changed)):
                raise api_failed("TimelineItem.SetProperties", "no property was applied")

    def verify() -> dict[str, object]:
        with with_current_timeline(target.session, target.timeline):
            now = item.GetProperties() or {}
        return {key: now.get(key) for key in changed}

    return run_mutation(inv, target.session, plan, apply, verify)


def run_rename(inv: Invocation) -> Result:
    new = str(inv.args.get("to") or "").strip()
    if not new:
        raise AxiError(
            "VALIDATION_ERROR", "--to needs a new name", help=["Valid example: --to 'Interview A'"]
        )
    target = open_target(inv)
    item = target.item
    old = item.GetName()
    words = ["item", "rename", target.ref, *tl_words(inv), "--to", new]
    plan = _plan(
        target, "item rename", [Change("name", old, new)] if old != new else [], words, inv
    )

    def apply() -> None:
        if not item.SetName(new):
            raise api_failed("TimelineItem.SetName", f"could not rename the item to {new}")

    return run_mutation(inv, target.session, plan, apply, lambda: {"name": item.GetName()})


def _run_enabled(inv: Invocation, enabled: bool) -> Result:
    target = open_target(inv)
    item = target.item
    now = bool(item.GetClipEnabled())
    verb = "enable" if enabled else "disable"
    words = ["item", verb, target.ref, *tl_words(inv)]
    plan = _plan(
        target,
        f"item {verb}",
        [Change("enabled", now, enabled)] if now != enabled else [],
        words,
        inv,
    )

    def apply() -> None:
        if not item.SetClipEnabled(enabled):
            raise api_failed("TimelineItem.SetClipEnabled", f"could not {verb} the item")

    return run_mutation(
        inv, target.session, plan, apply, lambda: {"enabled": bool(item.GetClipEnabled())}
    )


def run_enable(inv: Invocation) -> Result:
    return _run_enabled(inv, True)


def run_disable(inv: Invocation) -> Result:
    return _run_enabled(inv, False)


def run_color_set(inv: Invocation) -> Result:
    color = _color_choice(inv.positionals["color"], CLIP_COLORS)
    target = open_target(inv)
    item = target.item
    before = item.GetClipColor() or ""
    words = ["item", "color", "set", target.ref, color, *tl_words(inv)]
    plan = _plan(
        target,
        "item color set",
        [Change("color", before, color)] if before != color else [],
        words,
        inv,
    )

    def apply() -> None:
        if not item.SetClipColor(color):
            raise api_failed("TimelineItem.SetClipColor", f"could not set the colour {color}")

    return run_mutation(
        inv, target.session, plan, apply, lambda: {"color": item.GetClipColor() or ""}
    )


def run_color_clear(inv: Invocation) -> Result:
    target = open_target(inv)
    item = target.item
    before = item.GetClipColor() or ""
    words = ["item", "color", "clear", target.ref, *tl_words(inv)]
    plan = _plan(
        target, "item color clear", [Change("color", before, "")] if before else [], words, inv
    )

    def apply() -> None:
        if not item.ClearClipColor():
            raise api_failed("TimelineItem.ClearClipColor", "could not clear the clip colour")

    return run_mutation(
        inv, target.session, plan, apply, lambda: {"color": item.GetClipColor() or ""}
    )


def run_flag_add(inv: Invocation) -> Result:
    color = _color_choice(inv.positionals["color"], MARKER_COLORS)
    target = open_target(inv)
    item = target.item
    before = sorted(item.GetFlagList() or [])
    after = sorted({*before, color})
    words = ["item", "flag", "add", target.ref, color, *tl_words(inv)]
    plan = _plan(
        target,
        "item flag add",
        [Change("flags", before, after)] if color not in before else [],
        words,
        inv,
    )

    def apply() -> None:
        if not item.AddFlag(color):
            raise api_failed("TimelineItem.AddFlag", f"could not add the {color} flag")

    return run_mutation(
        inv, target.session, plan, apply, lambda: {"flags": sorted(item.GetFlagList() or [])}
    )


def run_flag_clear(inv: Invocation) -> Result:
    raw = inv.args.get("color")
    color = str(raw) if isinstance(raw, str) and raw else "All"
    target = open_target(inv)
    item = target.item
    before = sorted(item.GetFlagList() or [])
    after = [] if color == "All" else [flag for flag in before if flag != color]
    words = ["item", "flag", "clear", target.ref, *tl_words(inv)]
    if color != "All":
        words += ["--color", color]
    plan = _plan(
        target,
        "item flag clear",
        [Change("flags", before, after)] if before != after else [],
        words,
        inv,
    )

    def apply() -> None:
        if not item.ClearFlags(color):
            raise api_failed("TimelineItem.ClearFlags", f"could not clear the {color} flags")

    return run_mutation(
        inv, target.session, plan, apply, lambda: {"flags": sorted(item.GetFlagList() or [])}
    )


def run_delete(inv: Invocation) -> Result:
    ripple = bool(inv.args.get("ripple"))
    target = open_target(inv)
    timeline = target.timeline
    item_id = target.item.GetUniqueId()
    name = target.item.GetName()
    where = f"{target.located.track}:{target.located.position}"
    words = ["item", "delete", target.ref, *tl_words(inv)]
    if ripple:
        words.append("--ripple")
    plan = _plan(target, "item delete", [Change("item", f"{where} {name}", "deleted")], words, inv)
    plan.irreversible = True
    plan.warning = (
        f"deletes item {where} ({name}) from timeline {timeline.GetName()}"
        + (" and closes the gap (ripple)" if ripple else "")
        + "; the snapshot is the only way back"
    )
    plan.next = [hint("items", "list the remaining items")]

    def apply() -> None:
        if not cast(TimelineEdit, timeline).DeleteClips([target.item], ripple):
            raise api_failed("Timeline.DeleteClips", f"item {where} was not deleted")

    def verify() -> dict[str, object]:
        present = any(f.item.GetUniqueId() == item_id for f in walk_items(timeline, TRACK_TYPES))
        return {"item": "still present" if present else "deleted"}

    return run_mutation(inv, target.session, plan, apply, verify)


COLOR_SET_SPEC = CommandSpec(
    name="set",
    summary="Set the clip colour of an item",
    run=run_color_set,
    positionals=(ITEM_ID, Positional("color", help=f"one of {', '.join(CLIP_COLORS)}")),
    flags=ITEM_FLAGS,
    examples=(
        "davinci-axi item color set V1:1 Teal",
        "davinci-axi item color set V1:1 Teal --force",
    ),
    reads_only=False,
    methods=("TimelineItem.GetClipColor", "TimelineItem.SetClipColor"),
)
COLOR_CLEAR_SPEC = CommandSpec(
    name="clear",
    summary="Remove the clip colour of an item",
    run=run_color_clear,
    positionals=(ITEM_ID,),
    flags=ITEM_FLAGS,
    examples=("davinci-axi item color clear V1:1", "davinci-axi item color clear V1:1 --force"),
    reads_only=False,
    methods=("TimelineItem.GetClipColor", "TimelineItem.ClearClipColor"),
)
COLOR_SPEC = CommandSpec(
    name="color",
    summary="Set or clear the clip colour of an item",
    subcommands={"set": COLOR_SET_SPEC, "clear": COLOR_CLEAR_SPEC},
    reads_only=False,
)
FLAG_ADD_SPEC = CommandSpec(
    name="add",
    summary="Add a coloured flag to an item",
    run=run_flag_add,
    positionals=(ITEM_ID, Positional("color", help=f"one of {', '.join(MARKER_COLORS)}")),
    flags=ITEM_FLAGS,
    examples=("davinci-axi item flag add V1:1 Blue", "davinci-axi item flag add V1:1 Blue --force"),
    reads_only=False,
    methods=("TimelineItem.GetFlagList", "TimelineItem.AddFlag"),
)
FLAG_CLEAR_SPEC = CommandSpec(
    name="clear",
    summary="Remove the flags of an item (every flag, or one colour)",
    run=run_flag_clear,
    positionals=(ITEM_ID,),
    flags=(
        *ITEM_FLAGS,
        Flag("color", "str", None, help="only this colour", choices=(*MARKER_COLORS, "All")),
    ),
    examples=(
        "davinci-axi item flag clear V1:1",
        "davinci-axi item flag clear V1:1 --color Red --force",
    ),
    reads_only=False,
    methods=("TimelineItem.GetFlagList", "TimelineItem.ClearFlags"),
)
FLAG_SPEC = CommandSpec(
    name="flag",
    summary="Add or clear flags on an item",
    subcommands={"add": FLAG_ADD_SPEC, "clear": FLAG_CLEAR_SPEC},
    reads_only=False,
)
SET_SPEC = CommandSpec(
    name="set",
    summary="Set item properties such as Opacity, ZoomX or Pan",
    run=run_set,
    positionals=(ITEM_ID, ASSIGNMENTS),
    flags=ITEM_FLAGS,
    examples=(
        "davinci-axi item set V1:2 Opacity=50",
        "davinci-axi item set V1:2 Opacity=50,ZoomX=1.2 --force",
        "davinci-axi item set V1:2 Pan=10 --timeline 'Edit v2' --force",
    ),
    reads_only=False,
    methods=(
        "TimelineItem.GetProperties",
        "TimelineItem.SetProperties",
        "Project.GetCurrentTimeline",
        "Project.SetCurrentTimeline",
    ),
)
RENAME_SPEC = CommandSpec(
    name="rename",
    summary="Rename an item",
    run=run_rename,
    positionals=(ITEM_ID,),
    flags=(*ITEM_FLAGS, Flag("to", "str", None, required=True, help="the new name")),
    examples=(
        "davinci-axi item rename V1:2 --to 'Interview A'",
        "davinci-axi item rename V1:2 --to B --force",
    ),
    reads_only=False,
    methods=("TimelineItem.GetName", "TimelineItem.SetName"),
)
ENABLE_SPEC = CommandSpec(
    name="enable",
    summary="Enable an item",
    run=run_enable,
    positionals=(ITEM_ID,),
    flags=ITEM_FLAGS,
    examples=("davinci-axi item enable V1:2", "davinci-axi item enable V1:2 --force"),
    reads_only=False,
    methods=("TimelineItem.GetClipEnabled", "TimelineItem.SetClipEnabled"),
)
DISABLE_SPEC = CommandSpec(
    name="disable",
    summary="Disable an item",
    run=run_disable,
    positionals=(ITEM_ID,),
    flags=ITEM_FLAGS,
    examples=("davinci-axi item disable V1:2", "davinci-axi item disable V1:2 --force"),
    reads_only=False,
    methods=("TimelineItem.GetClipEnabled", "TimelineItem.SetClipEnabled"),
)
DELETE_SPEC = CommandSpec(
    name="delete",
    summary="Delete an item from its timeline (irreversible except through a snapshot)",
    run=run_delete,
    positionals=(ITEM_ID,),
    flags=(*ITEM_FLAGS, Flag("ripple", "bool", False, help="close the gap left by the item")),
    examples=("davinci-axi item delete V1:2", "davinci-axi item delete V1:2 --ripple --force"),
    reads_only=False,
    methods=("Timeline.DeleteClips", "Timeline.GetItemListInTrack"),
)

SUBCOMMANDS: dict[str, CommandSpec] = {
    "set": SET_SPEC,
    "rename": RENAME_SPEC,
    "enable": ENABLE_SPEC,
    "disable": DISABLE_SPEC,
    "color": COLOR_SPEC,
    "flag": FLAG_SPEC,
    "marker": MARKER_SPEC,
    "speed": SPEED_SPEC,
    "fades": FADES_SPEC,
    "delete": DELETE_SPEC,
}

SPEC = replace(
    ITEM_SPEC,
    summary="Show one timeline item in detail, or change it (dry run until --force)",
    subcommands=SUBCOMMANDS,
    examples=(*ITEM_SPEC.examples, "davinci-axi item set V1:2 Opacity=50 --force"),
    reads_only=False,
)
