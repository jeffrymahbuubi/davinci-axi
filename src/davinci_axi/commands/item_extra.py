"""`item` edit extras: link, unlink, transition, sidecar, takes, blanking, stereo and custom
marker data (U25, R30, R36).

`SPEC` wraps `item_edit.SPEC` with `replace()` (KTD15); the registry points `item` here, and
`item V1:2` stays the detail view. The take, blanking and stereo verbs live in
`item_extra_takes.py` and `item_extra_blanking.py`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from davinci_axi.commands import item_edit
from davinci_axi.commands.item_detail import TRACK_TYPES, resolve_item, walk_items
from davinci_axi.commands.item_extra_blanking import (
    BLANKING_SPEC,
    CUSTOM_SPEC,
    STEREO_SPEC,
)
from davinci_axi.commands.item_extra_takes import TAKE_SPEC
from davinci_axi.commands.item_extra_types import ItemExtra, TimelineLink
from davinci_axi.commands.item_markers import (
    ITEM_FLAGS,
    ITEM_ID,
    MARKER_SPEC,
    open_target,
    show_hint,
    tl_words,
)
from davinci_axi.commands.timeline_extra_common import run_called, split_refs
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.resolve_types import Info, TimelineItemApi
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

CATEGORIES = ("simple", "fusion", "ofx", "audio")
POSITIONS = ("start", "end")
ALIGNMENTS = ("left", "center", "right")
LINK_METHODS = ("Timeline.SetClipsLinked", "TimelineItem.GetLinkedItems")
ITEMS = Positional("items", help="comma-separated items such as V1:1,A1:1")


def _state(items: list[TimelineItemApi]) -> str:
    """`linked` when every item is linked to all the others, `unlinked` when to none of them."""
    ids = {item.GetUniqueId() for item in items}
    others = len(ids) - 1
    counts = []
    for item in items:
        mine = {o.GetUniqueId() for o in item.GetLinkedItems() or []} - {item.GetUniqueId()}
        counts.append(len(mine) if others == 0 else len(mine & ids))
    if others == 0:
        return "linked" if any(counts) else "unlinked"
    if all(c == others for c in counts):
        return "linked"
    return "unlinked" if not any(counts) else "partly linked"


def _run_link(inv: Invocation, linked: bool) -> Result:
    verb = "link" if linked else "unlink"
    refs = split_refs(inv.positionals["items"], "item", f"davinci-axi item {verb} V1:1,A1:1")
    if linked and len(refs) < 2:
        raise AxiError(
            "VALIDATION_ERROR",
            "name at least two items to link",
            help=["Valid example: davinci-axi item link V1:1,A1:1"],
        )
    check_snapshot_flags(inv)
    session = inv.connect()
    name = inv.args.get("timeline")
    timeline = cast(TimelineLink, session.require_timeline(name if isinstance(name, str) else None))
    items = [resolve_item(timeline, ref).item for ref in refs]
    want = "linked" if linked else "unlinked"
    before = _state(items)
    plan = MutationPlan(
        command=f"item {verb}",
        target=f"timeline {timeline.GetName()} ({', '.join(refs)})",
        changes=[Change("linked", before, want)] if before != want else [],
        force_cmd=force_cmd(inv, "item", verb, ",".join(refs), *tl_words(inv)),
        next=[show_hint(inv, refs[0])],
    )

    def apply() -> None:
        if not timeline.SetClipsLinked(items, linked):
            raise api_failed("Timeline.SetClipsLinked", f"the items were not {want}")

    return run_mutation(inv, session, plan, apply, lambda: {"linked": _state(items)})


def run_link(inv: Invocation) -> Result:
    return _run_link(inv, True)


def run_unlink(inv: Invocation) -> Result:
    return _run_link(inv, False)


def run_transition(inv: Invocation) -> Result:
    kind = inv.args.get("type")
    duration = inv.args.get("duration")
    if not isinstance(kind, str) or not kind.strip():
        raise AxiError(
            "VALIDATION_ERROR",
            "--type needs a transition name",
            help=["Valid example: --type 'Cross Dissolve'"],
        )
    if duration is not None and (not isinstance(duration, int) or duration < 1):
        raise AxiError(
            "VALIDATION_ERROR",
            "--duration must be 1 or more frames",
            help=["Valid example: --duration 12"],
        )
    options: Info = {"type": kind}
    words = ["item", "transition", inv.positionals["id"], "--type", kind]
    for key in ("category", "position", "alignment", "duration"):
        value = inv.args.get(key)
        if value is not None:
            options[key] = value
            words += [f"--{key}", str(value)]
    target = open_target(inv)
    item = cast(ItemExtra, target.item)
    plan = MutationPlan(
        command="item transition",
        target=target.label,
        changes=[Change("transition", None, kind)],
        force_cmd=force_cmd(inv, *words, *tl_words(inv)),
        warning="the transition lands on the item edge and may be refused when there is no handle;"
        " it is listed among the clips, so later item indices on the track shift by one",
        next=[show_hint(inv, target.ref)],
    )
    created: dict[str, str] = {}

    def apply() -> None:
        made = item.AddTransition(options)
        if made is None:
            raise api_failed("TimelineItem.AddTransition", f"could not add {kind}")
        created["id"] = made.GetUniqueId()

    def verify() -> dict[str, object]:
        found = any(
            located.item.GetUniqueId() == created["id"]
            for located in walk_items(target.timeline, TRACK_TYPES)
        )
        return {"transition": kind if found else None}

    return run_mutation(inv, target.session, plan, apply, verify)


def run_sidecar_update(inv: Invocation) -> Result:
    target = open_target(inv)
    item = cast(ItemExtra, target.item)
    plan = MutationPlan(
        command="item sidecar update",
        target=target.label,
        changes=[],
        force_cmd=force_cmd(inv, "item", "sidecar", "update", target.ref, *tl_words(inv)),
        warning="writes the BRAW .sidecar or R3D .RMD file next to the media; not read back",
        next=[show_hint(inv, target.ref)],
    )
    return run_called(
        inv,
        target.session,
        plan,
        "TimelineItem.UpdateSidecar",
        item.UpdateSidecar,
        "only BRAW and R3D clips have a sidecar file",
    )


LINK_SPEC = CommandSpec(
    name="link",
    summary="Link timeline items so they move and trim together",
    run=run_link,
    positionals=(ITEMS,),
    flags=ITEM_FLAGS,
    examples=("davinci-axi item link V1:1,A1:1", "davinci-axi item link V1:1,A1:1 --force"),
    reads_only=False,
    methods=LINK_METHODS,
)
UNLINK_SPEC = CommandSpec(
    name="unlink",
    summary="Unlink timeline items",
    run=run_unlink,
    positionals=(ITEMS,),
    flags=ITEM_FLAGS,
    examples=("davinci-axi item unlink V1:1,A1:1", "davinci-axi item unlink V1:1,A1:1 --force"),
    reads_only=False,
    methods=LINK_METHODS,
)
TRANSITION_SPEC = CommandSpec(
    name="transition",
    summary="Add a transition to the start or end of an item",
    run=run_transition,
    positionals=(ITEM_ID,),
    flags=(
        Flag("type", "str", None, required=True, help="transition name, e.g. 'Cross Dissolve'"),
        Flag("category", "str", "simple", choices=CATEGORIES, help="transition category"),
        Flag("position", "str", "start", choices=POSITIONS, help="item edge: start or end"),
        Flag("alignment", "str", "center", choices=ALIGNMENTS, help="placement against the edge"),
        Flag("duration", "int", None, help="length in frames (default: Resolve calculates it)"),
        *ITEM_FLAGS,
    ),
    examples=(
        "davinci-axi item transition V1:2 --type 'Cross Dissolve' --duration 12",
        "davinci-axi item transition V1:2 --type 'Cross Dissolve' --position end --force",
    ),
    reads_only=False,
    methods=("TimelineItem.AddTransition", "Timeline.GetItemListInTrack"),
)
SIDECAR_SPEC = CommandSpec(
    name="sidecar",
    summary="Update the sidecar file of a BRAW or R3D item",
    reads_only=False,
    subcommands={
        "update": CommandSpec(
            name="update",
            summary="Write the item's changes to its .sidecar or .RMD file (not read back)",
            run=run_sidecar_update,
            positionals=(ITEM_ID,),
            flags=ITEM_FLAGS,
            examples=(
                "davinci-axi item sidecar update V1:1",
                "davinci-axi item sidecar update V1:1 --force",
            ),
            reads_only=False,
            methods=("TimelineItem.UpdateSidecar",),
        )
    },
    examples=("davinci-axi item sidecar update V1:1 --force",),
    methods=("TimelineItem.UpdateSidecar",),
)

WRAPPED_MARKER = replace(
    MARKER_SPEC,
    summary="Add or delete item markers, or read and write their custom data",
    subcommands={**MARKER_SPEC.subcommands, "custom": CUSTOM_SPEC},
    methods=tuple(dict.fromkeys((*MARKER_SPEC.methods, *CUSTOM_SPEC.methods))),
)

NEW: dict[str, CommandSpec] = {
    "link": LINK_SPEC,
    "unlink": UNLINK_SPEC,
    "transition": TRANSITION_SPEC,
    "sidecar": SIDECAR_SPEC,
    "take": TAKE_SPEC,
    "blanking": BLANKING_SPEC,
    "stereo": STEREO_SPEC,
    "marker": WRAPPED_MARKER,
}


def _all_methods() -> tuple[str, ...]:
    found: dict[str, None] = dict.fromkeys(item_edit.SPEC.methods)
    stack = [*item_edit.SPEC.subcommands.values(), *NEW.values()]
    while stack:
        spec = stack.pop(0)
        found.update(dict.fromkeys(spec.methods))
        stack.extend(spec.subcommands.values())
    return tuple(found)


SPEC = replace(
    item_edit.SPEC,
    subcommands={**item_edit.SPEC.subcommands, **NEW},
    examples=(
        *item_edit.SPEC.examples,
        "davinci-axi item link V1:1,A1:1 --force",
        "davinci-axi item take list V1:1",
    ),
    methods=_all_methods(),
)
