"""`audio gain`: add decibels to each item's clip volume, read back as `AudioVolume` (F27).

Resolve's API has no loudness meter; `AudioVolume` is the only per-item level it reports, and on
some Resolve versions it is empty for audio items. Items whose volume cannot be read are listed
as "not readable" and skipped, never counted as a change.
"""

from __future__ import annotations

from typing import Any

from davinci_axi.commands.audio_common import (
    TIMELINE_READS,
    Picked,
    open_items,
    timeline_step,
    verb,
)
from davinci_axi.commands.item_detail import Located
from davinci_axi.commands.render_common import force_words, methods_of
from davinci_axi.commands.timeline_tracks import TIMELINE_FLAG
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import Change, MutationPlan, run_mutation
from davinci_axi.output import hint
from davinci_axi.spec import Flag, Invocation, Positional, Result

ITEMS = Positional("items", help="timeline items, comma-separated: V1:2, A1:1 or unique ids")
VOLUME_RANGE = (-100.0, 30.0)
NOT_READABLE = "not readable"


def ref_of(one: Located) -> str:
    return f"{one.track}:{one.position}"


def volume(item: Any) -> float | None:
    """The item's AudioVolume in dB, or None when Resolve does not report it (audio items)."""
    value = (item.GetProperties() or {}).get("AudioVolume")
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def volumes(sel: Picked) -> dict[str, float | None]:
    return {ref_of(one): volume(one.item) for one in sel.located}


def volume_text(volumes: dict[str, float | None]) -> str:
    return ", ".join(
        f"{ref}={NOT_READABLE if value is None else format(value, 'g')}"
        for ref, value in volumes.items()
    )


def run_gain(inv: Invocation) -> Result:
    delta = inv.args.get("db")
    if not isinstance(delta, float) or delta == 0.0:
        raise AxiError(
            "VALIDATION_ERROR",
            "--db must be a non-zero number of decibels",
            help=["Valid example: davinci-axi audio gain A1:1,A1:2 --db -3"],
        )
    sel = open_items(inv)
    low, high = VOLUME_RANGE
    rows: list[tuple[str, Any, float, float]] = []
    unreadable: list[str] = []
    clamped: list[str] = []
    for one in sel.located:
        ref, before = ref_of(one), volume(one.item)
        if before is None:
            unreadable.append(ref)
            continue
        wanted = round(before + delta, 3)
        after = max(low, min(high, wanted))
        if after != wanted:
            clamped.append(ref)
        if after != before:
            rows.append((ref, one.item, before, after))
    words = ["audio", "gain", inv.positionals["items"], "--db", f"{delta:g}"]
    plan = MutationPlan(
        command="audio gain",
        target=sel.target,
        changes=[Change(f"{ref} AudioVolume", before, after) for ref, _, before, after in rows],
        force_cmd=force_words(inv, words),
        extra={"db": delta},
        next=[hint(f"item {ref_of(sel.located[0])}", "show the first item")],
    )
    if clamped:
        plan.extra["clamped"] = clamped
    if unreadable:
        plan.extra["not_readable"] = unreadable
        plan.warning = (
            f"AudioVolume is not readable on {', '.join(unreadable)}; "
            "a relative change cannot be computed there, so those items are skipped"
        )

    def apply() -> None:
        with timeline_step(sel.session, sel.timeline, plan):
            for ref, item, _, after in rows:
                if not item.SetProperties({"AudioVolume": after}):
                    raise api_failed("TimelineItem.SetProperties", f"{ref} was not set")

    def verify() -> dict[str, object]:
        with timeline_step(sel.session, sel.timeline, plan):
            return {f"{ref} AudioVolume": volume(item) for ref, item, _, _ in rows}

    return run_mutation(inv, sel.session, plan, apply, verify)


GAIN_SPEC = verb(
    "gain",
    "Add or subtract decibels on each item's clip volume (AudioVolume)",
    run_gain,
    positionals=(ITEMS,),
    flags=(
        TIMELINE_FLAG,
        Flag(
            "db",
            "float",
            None,
            required=True,
            help="change in dB, e.g. -3 or 9.7; the result is clamped to Resolve's -100..30 range",
        ),
    ),
    examples=(
        "davinci-axi audio gain A1:1,A1:2 --db -3",
        "davinci-axi audio gain $(davinci-axi items --track-type audio --fields id --json"
        " | jq -r '[.items[].id]|join(\",\")') --db 9.7 --force",
    ),
    notes=(
        "Relative: each item's AudioVolume moves by --db and is clamped to -100..30; use "
        "`item set <id> AudioVolume=<dB>` for an absolute value",
        "Items whose AudioVolume Resolve does not report are listed as not_readable and skipped",
    ),
    methods=methods_of(TimelineItem="GetProperties SetProperties") + TIMELINE_READS,
)
