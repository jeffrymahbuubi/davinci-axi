"""`timeline clear`: delete every item of a timeline and keep its tracks (F15d).

A dry run until `--force`. The delete is irreversible, so the project is snapshotted first.
A timeline that is already empty is a no-op: `applied: false`, nothing written or snapshotted.
"""

from __future__ import annotations

from typing import cast

from davinci_axi.commands.item_detail import TRACK_TYPES, walk_items
from davinci_axi.commands.timeline_tracks import context_words, count_items, plural
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
    with_current_timeline,
)
from davinci_axi.output import hint
from davinci_axi.resolve_types_edit import TimelineEdit
from davinci_axi.spec import CommandSpec, Invocation, Positional, Result

NAME_ARG = Positional("name", help="timeline name")


def _name(inv: Invocation) -> str:
    name = inv.positionals["name"].strip()
    if not name:
        raise AxiError(
            "VALIDATION_ERROR",
            "the timeline name is empty",
            help=['Valid example: davinci-axi timeline clear "Edit v1"'],
        )
    return name


def run_clear(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    name = _name(inv)
    session = inv.connect()
    timeline = cast(TimelineEdit, session.require_timeline(name))
    located = list(walk_items(timeline, TRACK_TYPES))
    count = len(located)
    kept = {kind: timeline.GetTrackCount(kind) for kind in TRACK_TYPES}
    extra: dict[str, object] = {
        "tracks_kept": f"V{kept['video']} A{kept['audio']} S{kept['subtitle']}"
    }
    if count == 0:
        extra["note"] = "already empty"
    plan = MutationPlan(
        command="timeline clear",
        target=f"timeline {name}",
        changes=[Change("items", count, 0)] if count else [],
        force_cmd=force_cmd(inv, "timeline", "clear", name, *context_words(inv)),
        irreversible=True,
        warning=(
            f"deletes all {plural(count, 'item')} from timeline {name} and keeps its "
            f"{kept['video']} video, {kept['audio']} audio, {kept['subtitle']} subtitle tracks; "
            "the snapshot is the only way back"
        ),
        extra=extra,
        next=[
            hint("items", "list the items left on the timeline"),
            hint("timeline append --edl", "fill it again from an edit list"),
        ],
    )

    def apply() -> None:
        with with_current_timeline(session, timeline) as switched:
            if switched:
                plan.extra["timeline_restored"] = True
            if not timeline.DeleteClips([one.item for one in located], False):
                raise api_failed("Timeline.DeleteClips", f"{count} items were not deleted")

    def verify() -> dict[str, object]:
        return {"items": count_items(timeline)}

    return run_mutation(inv, session, plan, apply, verify)


CLEAR_SPEC = CommandSpec(
    name="clear",
    summary="Delete every item of a timeline and keep its tracks",
    run=run_clear,
    positionals=(NAME_ARG,),
    flags=MUTATION_FLAGS,
    examples=(
        'davinci-axi timeline clear "Edit v1"',
        'davinci-axi timeline clear "Edit v1" --force',
    ),
    reads_only=False,
    methods=(
        "Timeline.DeleteClips",
        "Timeline.GetItemListInTrack",
        "Timeline.GetTrackCount",
        "Project.GetCurrentTimeline",
        "Project.SetCurrentTimeline",
    ),
)
