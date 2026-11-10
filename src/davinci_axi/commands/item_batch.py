"""`item delete <ids>`: delete one item or a comma-separated list under one snapshot (U4, C).

`DELETE_SPEC` replaces the single-item verb through `item_extra.NEW["delete"]`. The whole list is
one `Timeline.DeleteClips(items, ripple)` call, so one save, one snapshot and one read-back.
"""

from __future__ import annotations

from typing import cast

from davinci_axi.commands.item_detail import TRACK_TYPES, Located, resolve_item, str_arg, walk_items
from davinci_axi.commands.item_edit import (
    TITLE_METHODS,
    TITLE_NOTE,
    title_compound_name,
    title_hint,
)
from davinci_axi.commands.item_markers import ITEM_FLAGS, tl_words
from davinci_axi.commands.timeline_extra_common import split_refs
from davinci_axi.errors import api_failed
from davinci_axi.mutate import (
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.output import hint
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.resolve_types_edit import TimelineEdit
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result


def split_items(timeline: TimelineApi, text: str) -> list[Located]:
    """`V1:2,V1:5` or unique ids -> located items, each once; an empty part is VALIDATION_ERROR."""
    located: list[Located] = []
    seen: set[str] = set()
    for ref in split_refs(text, "item", "V1:2,V1:5"):
        one = resolve_item(timeline, ref)
        uid = one.item.GetUniqueId()
        if uid not in seen:
            seen.add(uid)
            located.append(one)
    return located


def run_delete_many(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    text = inv.positionals["ids"]
    ripple = bool(inv.args.get("ripple"))
    session = inv.connect()
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    located = split_items(timeline, text)
    timeline_name = timeline.GetName()
    refs = [f"{one.track}:{one.position}" for one in located]
    labels = [f"{ref} {one.item.GetName()}" for ref, one in zip(refs, located, strict=True)]
    uids = [one.item.GetUniqueId() for one in located]
    words = ["item", "delete", text, *tl_words(inv)]
    if ripple:
        words.append("--ripple")
    if len(located) == 1:
        changes = [Change("item", labels[0], "deleted")]
        subject = f"item {refs[0]} ({located[0].item.GetName()})"
        target = f"{subject} in timeline {timeline_name}"
    else:
        changes = [Change(label, "present", "deleted") for label in labels]
        subject = f"{len(located)} items ({', '.join(refs)})"
        target = f"{subject} in timeline {timeline_name}"
    compounds = [
        name
        for name in dict.fromkeys(title_compound_name(one.item) for one in located)
        if name is not None
    ]
    plan = MutationPlan(
        command="item delete",
        target=target,
        changes=changes,
        force_cmd=force_cmd(inv, *words),
        irreversible=True,
        warning=(
            f"deletes {subject} from timeline {timeline_name}"
            + (" and closes the gaps (ripple)" if ripple else "")
            + "; the snapshot is the only way back"
        ),
        next=[hint("items", "list the remaining items"), *map(title_hint, compounds)],
    )

    def apply() -> None:
        items = [one.item for one in located]
        if not cast(TimelineEdit, timeline).DeleteClips(items, ripple):
            raise api_failed("Timeline.DeleteClips", f"{len(items)} item(s) were not deleted")

    def verify() -> dict[str, object]:
        present = {f.item.GetUniqueId() for f in walk_items(timeline, TRACK_TYPES)}
        states = ["still present" if uid in present else "deleted" for uid in uids]
        return {change.field: state for change, state in zip(changes, states, strict=True)}

    return run_mutation(inv, session, plan, apply, verify)


DELETE_SPEC = CommandSpec(
    name="delete",
    summary="Delete one item or a list of items from their timeline under one snapshot",
    run=run_delete_many,
    positionals=(
        Positional(
            "ids",
            help="one item or a comma-separated list: V1:2,V1:5 or unique ids",
        ),
    ),
    flags=(*ITEM_FLAGS, Flag("ripple", "bool", False, help="close the gaps left by the items")),
    examples=(
        "davinci-axi item delete V1:2",
        "davinci-axi item delete V1:2,V1:5,V1:9 --ripple --force",
    ),
    reads_only=False,
    methods=("Timeline.DeleteClips", "Timeline.GetItemListInTrack", *TITLE_METHODS),
    notes=(TITLE_NOTE,),
)
