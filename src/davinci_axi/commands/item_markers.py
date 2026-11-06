"""Shared item-mutation plumbing plus `item marker add|delete`, `item speed` and `item fades`.

Every item verb takes `<id>` first, then `--timeline` and the mutation flags, resolves the item
with `item_detail.resolve_item` and casts it to `TimelineItemEdit`. Marker frames are offsets
from the start of the item (the Resolve API quirk), which `markers.py` documents.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import cast

from davinci_axi.commands.item_detail import Located, resolve_item, str_arg
from davinci_axi.commands.items import TIMELINE_FLAG
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.markers import (
    MARKER_ADD_FLAGS,
    MARKER_DELETE_FLAGS,
    add_plan,
    check_add_flags,
    check_delete_flags,
    delete_plan,
)
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.output import hint
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.resolve_types_edit import TimelineItemEdit
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

ITEM_ID = Positional("id", help="item unique id, or <V|A|S><track>:<index> (1-based) such as V1:2")
ITEM_FLAGS: tuple[Flag, ...] = (TIMELINE_FLAG, *MUTATION_FLAGS)
SPEED_MINIMUM = (21, 1, 0)
SPEED_OPTIONS = (
    ("pitch-correction", "PitchCorrection"),
    ("stretch-keyframes", "StretchKeyframesToFit"),
    ("ripple", "RippleTimeline"),
)


@dataclass
class Target:
    """The item a verb works on, with the session and timeline it was found in."""

    session: Session
    timeline: TimelineApi
    located: Located
    ref: str

    @property
    def item(self) -> TimelineItemEdit:
        return cast(TimelineItemEdit, self.located.item)

    @property
    def label(self) -> str:
        """`item V1:2 (clip-2) in timeline Edit v1`."""
        where = f"{self.located.track}:{self.located.position}"
        return f"item {where} ({self.item.GetName()}) in timeline {self.timeline.GetName()}"


def tl_words(inv: Invocation) -> list[str]:
    """`--timeline <name>` when the caller named one, so the printed command repeats it."""
    name = str_arg(inv, "timeline")
    return ["--timeline", name] if name else []


def open_target(inv: Invocation) -> Target:
    """Refuse a bad `--no-snapshot`, connect, then resolve `<id>` in the chosen timeline."""
    check_snapshot_flags(inv)
    session = inv.connect()
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    ref = inv.positionals["id"]
    return Target(session, timeline, resolve_item(timeline, ref), ref)


def show_hint(inv: Invocation, ref: str) -> str:
    suffix = " ".join(shlex.quote(w) for w in tl_words(inv))
    return hint(
        f"item {shlex.quote(ref)}{' ' + suffix if suffix else ''}", "show the item in detail"
    )


def run_marker_add(inv: Invocation) -> Result:
    check_add_flags(inv)
    target = open_target(inv)
    words = ["item", "marker", "add", target.ref, *tl_words(inv)]
    plan, apply, verify = add_plan(inv, target.item, target.label, words)
    plan.command = "item marker add"
    plan.next = [show_hint(inv, target.ref)]
    return run_mutation(inv, target.session, plan, apply, verify)


def run_marker_delete(inv: Invocation) -> Result:
    check_delete_flags(inv)
    target = open_target(inv)
    words = ["item", "marker", "delete", target.ref, *tl_words(inv)]
    plan, apply, verify = delete_plan(inv, target.item, target.label, words)
    plan.command = "item marker delete"
    plan.next = [show_hint(inv, target.ref)]
    return run_mutation(inv, target.session, plan, apply, verify)


def _number_error(flag: str, text: str) -> AxiError:
    return AxiError("VALIDATION_ERROR", f"--{flag} {text}", help=[f"Valid example: --{flag} 50"])


def run_speed(inv: Invocation) -> Result:
    percent = inv.args.get("speed")
    if not isinstance(percent, float) or percent < 0:
        raise _number_error("speed", "must be 0 or more (a percentage; 0 is a freeze frame)")
    options: dict[str, object] = {"Percentage": percent}
    extra_flags: list[str] = []
    for flag, key in SPEED_OPTIONS:
        if inv.args.get(flag.replace("-", "_")):
            options[key] = True
            extra_flags.append(f"--{flag}")
    target = open_target(inv)
    target.session.require_version("item speed", SPEED_MINIMUM)
    item = target.item
    before = (item.GetSpeed() or {}).get("Percentage")
    words = ["item", "speed", target.ref, *tl_words(inv), "--speed", f"{percent:g}", *extra_flags]
    changed = before is None or abs(float(before) - percent) > 1e-6 or len(options) > 1
    plan = MutationPlan(
        command="item speed",
        target=target.label,
        changes=[Change("speed", before, percent)] if changed else [],
        force_cmd=force_cmd(inv, *words),
        extra={"options": ", ".join(k for k in options if k != "Percentage")}
        if len(options) > 1
        else {},
        next=[show_hint(inv, target.ref)],
    )

    def apply() -> None:
        if not item.SetSpeed(options):
            raise api_failed("TimelineItem.SetSpeed", f"speed {percent:g} was not set")

    def verify() -> dict[str, object]:
        return {"speed": (item.GetSpeed() or {}).get("Percentage")}

    return run_mutation(inv, target.session, plan, apply, verify)


def run_fades(inv: Invocation) -> Result:
    wanted: dict[str, int] = {}
    for flag, key in (("in", "FadeIn"), ("out", "FadeOut")):
        value = inv.args.get(flag)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise _number_error(flag, "must be 0 or more frames")
        wanted[key] = value
    if not wanted:
        raise AxiError(
            "VALIDATION_ERROR",
            "pass at least one of --in and --out",
            help=["Valid example: --in 12 --out 24 (fade lengths in frames)"],
        )
    target = open_target(inv)
    target.session.require_version("item fades", SPEED_MINIMUM)
    item = target.item
    current = item.GetFades() or {}
    full = {key: int(wanted.get(key, current.get(key) or 0)) for key in ("FadeIn", "FadeOut")}
    names = {"FadeIn": "fade_in", "FadeOut": "fade_out"}
    changes = [
        Change(names[key], int(current.get(key) or 0), full[key])
        for key in wanted
        if int(current.get(key) or 0) != full[key]
    ]
    words = ["item", "fades", target.ref, *tl_words(inv)]
    for flag, key in (("--in", "FadeIn"), ("--out", "FadeOut")):
        if key in wanted:
            words += [flag, str(wanted[key])]
    plan = MutationPlan(
        command="item fades",
        target=target.label,
        changes=changes,
        force_cmd=force_cmd(inv, *words),
        next=[show_hint(inv, target.ref)],
    )

    def apply() -> None:
        if not item.SetFades(dict(full)):
            raise api_failed("TimelineItem.SetFades", "the fade lengths were not set")

    def verify() -> dict[str, object]:
        live = item.GetFades() or {}
        return {names[key]: int(live.get(key) or 0) for key in full}

    return run_mutation(inv, target.session, plan, apply, verify)


MARKER_ADD_SPEC = CommandSpec(
    name="add",
    summary="Add a marker to an item (frame is an offset from the item start)",
    run=run_marker_add,
    positionals=(ITEM_ID,),
    flags=(*ITEM_FLAGS, *MARKER_ADD_FLAGS),
    examples=(
        "davinci-axi item marker add V1:2 --frame 5 --color Green --name Pick",
        "davinci-axi item marker add V1:2 --frame 5 --name Pick --force",
    ),
    reads_only=False,
    methods=("TimelineItem.GetMarkers", "TimelineItem.AddMarker"),
)
MARKER_DELETE_SPEC = CommandSpec(
    name="delete",
    summary="Delete item markers by frame, colour or custom data (one selector)",
    run=run_marker_delete,
    positionals=(ITEM_ID,),
    flags=(*ITEM_FLAGS, *MARKER_DELETE_FLAGS),
    examples=(
        "davinci-axi item marker delete V1:2 --frame 5",
        "davinci-axi item marker delete V1:2 --color Green --force",
    ),
    reads_only=False,
    methods=(
        "TimelineItem.GetMarkers",
        "TimelineItem.DeleteMarkerAtFrame",
        "TimelineItem.DeleteMarkersByColor",
        "TimelineItem.DeleteMarkerByCustomData",
    ),
)
MARKER_SPEC = CommandSpec(
    name="marker",
    summary="Add or delete markers on one item",
    subcommands={"add": MARKER_ADD_SPEC, "delete": MARKER_DELETE_SPEC},
    reads_only=False,
)
SPEED_SPEC = CommandSpec(
    name="speed",
    summary="Set the speed of one item (Resolve 21.1 or newer)",
    run=run_speed,
    positionals=(ITEM_ID,),
    flags=(
        *ITEM_FLAGS,
        Flag("speed", "float", None, required=True, help="speed in percent (0 is a freeze frame)"),
        Flag("pitch-correction", "bool", False, help="apply pitch correction to linked audio"),
        Flag("stretch-keyframes", "bool", False, help="stretch keyframes to fit"),
        Flag("ripple", "bool", False, help="ripple the timeline"),
    ),
    examples=(
        "davinci-axi item speed V1:2 --speed 50",
        "davinci-axi item speed V1:2 --speed 200 --ripple --force",
    ),
    reads_only=False,
    methods=("TimelineItem.GetSpeed", "TimelineItem.SetSpeed"),
)
FADES_SPEC = CommandSpec(
    name="fades",
    summary="Set the fade-in and fade-out lengths of one item (Resolve 21.1 or newer)",
    run=run_fades,
    positionals=(ITEM_ID,),
    flags=(
        *ITEM_FLAGS,
        Flag("in", "int", None, help="fade-in length in frames"),
        Flag("out", "int", None, help="fade-out length in frames"),
    ),
    examples=(
        "davinci-axi item fades A1:1 --in 12",
        "davinci-axi item fades A1:1 --in 12 --out 24 --force",
    ),
    reads_only=False,
    methods=("TimelineItem.GetFades", "TimelineItem.SetFades"),
)
