"""`item blanking`, `item stereo` and `item marker custom` (U25).

Output blanking on an item is either its own four values or the timeline's: `GetOutputBlanking`
is empty while the item follows the timeline. Custom marker data and blanking are project state
and take the usual snapshot; `stereo` only reads.
"""

from __future__ import annotations

from typing import cast

from davinci_axi.commands.item_extra_types import ItemExtra
from davinci_axi.commands.item_markers import (
    ITEM_FLAGS,
    ITEM_ID,
    Target,
    open_target,
    show_hint,
    tl_words,
)
from davinci_axi.commands.items import TIMELINE_FLAG
from davinci_axi.commands.timeline_extra_common import (
    ASSIGN_BLANKING,
    BLANKING_KEYS,
    DATA,
    FIND_FLAG,
    FRAME_FLAG,
    as_host,
    blanking_plan,
    blanking_values,
    check_custom_get_flags,
    frame_arg,
    parse_blanking,
    parse_on_off,
    run_custom_get,
    run_custom_set,
)
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import Change, MutationPlan, force_cmd, run_mutation
from davinci_axi.output import hint
from davinci_axi.spec import CommandSpec, Invocation, Positional, Result

STATE = Positional("state", help="on or off")


def _item(target: Target) -> ItemExtra:
    return cast(ItemExtra, target.item)


def run_blanking_show(inv: Invocation) -> Result:
    target = open_target(inv)
    item = _item(target)
    uses_timeline = bool(item.GetUseTimelineForOutputBlanking())
    data: dict[str, object] = {"item": target.label, "uses_timeline": uses_timeline}
    data.update(blanking_values(item.GetOutputBlanking()))
    help_lines = []
    if uses_timeline:
        help_lines.append("The item follows the timeline blanking, so its own values are empty")
    return Result(data, help_lines)


def run_blanking_set(inv: Invocation) -> Result:
    wanted = parse_blanking(inv.positionals["assignments"])
    target = open_target(inv)
    item = _item(target)
    before = blanking_values(item.GetOutputBlanking())
    uses_timeline = bool(item.GetUseTimelineForOutputBlanking())
    words = ["item", "blanking", "set", target.ref, inv.positionals["assignments"], *tl_words(inv)]
    if uses_timeline:
        raise AxiError(
            "BLANKING_FOLLOWS_TIMELINE",
            f"{target.label} follows the timeline blanking, so its own values would not show",
            help=[
                hint(
                    f"item blanking use-timeline {target.ref} off",
                    "make the item use its own values",
                ),
                "Then repeat this command",
            ],
        )
    next_lines = [show_hint(inv, target.ref)]
    plan = blanking_plan("item blanking set", target.label, before, wanted, words, inv, next_lines)
    full = {**before, **wanted}

    def apply() -> None:
        if not item.SetOutputBlanking(dict(full)):
            raise api_failed("TimelineItem.SetOutputBlanking", "the blanking was not set")

    def verify() -> dict[str, object]:
        return dict(blanking_values(item.GetOutputBlanking()))

    return run_mutation(inv, target.session, plan, apply, verify)


def run_use_timeline(inv: Invocation) -> Result:
    wanted = parse_on_off(inv.positionals["state"])
    target = open_target(inv)
    item = _item(target)
    now = bool(item.GetUseTimelineForOutputBlanking())
    words = ["item", "blanking", "use-timeline", target.ref, "on" if wanted else "off"]
    plan = MutationPlan(
        command="item blanking use-timeline",
        target=target.label,
        changes=[Change("use_timeline", now, wanted)] if now != wanted else [],
        force_cmd=force_cmd(inv, *words, *tl_words(inv)),
        next=[show_hint(inv, target.ref)],
    )

    def apply() -> None:
        if not item.SetUseTimelineForOutputBlanking(wanted):
            raise api_failed(
                "TimelineItem.SetUseTimelineForOutputBlanking", "the flag was not changed"
            )

    return run_mutation(
        inv,
        target.session,
        plan,
        apply,
        lambda: {"use_timeline": bool(item.GetUseTimelineForOutputBlanking())},
    )


def run_stereo(inv: Invocation) -> Result:
    target = open_target(inv)
    item = _item(target)
    convergence = item.GetStereoConvergenceValues() or {}
    data: dict[str, object] = {
        "item": target.label,
        "convergence": [
            {"offset": offset, "value": value} for offset, value in sorted(convergence.items())
        ],
    }
    for side, windows in (
        ("left", item.GetStereoLeftFloatingWindowParams()),
        ("right", item.GetStereoRightFloatingWindowParams()),
    ):
        data[side] = [
            {"offset": offset, **{k: params.get(k) for k in ("left", "right", "top", "bottom")}}
            for offset, params in sorted((windows or {}).items())
        ]
    return Result(data)


def run_custom_get_item(inv: Invocation) -> Result:
    check_custom_get_flags(inv)
    target = open_target(inv)
    return run_custom_get(inv, as_host(target.item), target.label)


def run_custom_set_item(inv: Invocation) -> Result:
    frame = frame_arg(inv)
    target = open_target(inv)
    words = ["item", "marker", "custom", "set", target.ref, inv.positionals["data"]]
    words += ["--frame", str(frame), *tl_words(inv)]
    return run_custom_set(
        inv,
        target.session,
        as_host(target.item),
        target.label,
        "item marker custom set",
        "TimelineItem",
        words,
        [show_hint(inv, target.ref)],
    )


BLANKING_SPEC = CommandSpec(
    name="blanking",
    summary=f"Show or set the output blanking ({', '.join(BLANKING_KEYS)}) of an item",
    reads_only=False,
    subcommands={
        "show": CommandSpec(
            name="show",
            summary="Show the output blanking of an item",
            run=run_blanking_show,
            positionals=(ITEM_ID,),
            flags=(TIMELINE_FLAG,),
            examples=("davinci-axi item blanking show V1:1",),
            methods=(
                "TimelineItem.GetOutputBlanking",
                "TimelineItem.GetUseTimelineForOutputBlanking",
            ),
        ),
        "set": CommandSpec(
            name="set",
            summary="Set output blanking pixels for one item",
            run=run_blanking_set,
            positionals=(ITEM_ID, ASSIGN_BLANKING),
            flags=ITEM_FLAGS,
            examples=(
                "davinci-axi item blanking set V1:1 Top=10,Bottom=10",
                "davinci-axi item blanking set V1:1 Top=10 --force",
            ),
            reads_only=False,
            methods=(
                "TimelineItem.GetOutputBlanking",
                "TimelineItem.GetUseTimelineForOutputBlanking",
                "TimelineItem.SetOutputBlanking",
            ),
        ),
        "use-timeline": CommandSpec(
            name="use-timeline",
            summary="Make an item follow the timeline blanking (on) or use its own (off)",
            run=run_use_timeline,
            positionals=(ITEM_ID, STATE),
            flags=ITEM_FLAGS,
            examples=("davinci-axi item blanking use-timeline V1:1 off --force",),
            reads_only=False,
            methods=(
                "TimelineItem.GetUseTimelineForOutputBlanking",
                "TimelineItem.SetUseTimelineForOutputBlanking",
            ),
        ),
    },
    examples=("davinci-axi item blanking show V1:1",),
    methods=(
        "TimelineItem.GetOutputBlanking",
        "TimelineItem.SetOutputBlanking",
        "TimelineItem.GetUseTimelineForOutputBlanking",
        "TimelineItem.SetUseTimelineForOutputBlanking",
    ),
)
STEREO_SPEC = CommandSpec(
    name="stereo",
    summary="Show the stereo convergence and floating window keyframes of an item",
    run=run_stereo,
    positionals=(ITEM_ID,),
    flags=(TIMELINE_FLAG,),
    examples=("davinci-axi item stereo V1:1",),
    methods=(
        "TimelineItem.GetStereoConvergenceValues",
        "TimelineItem.GetStereoLeftFloatingWindowParams",
        "TimelineItem.GetStereoRightFloatingWindowParams",
    ),
)
CUSTOM_SPEC = CommandSpec(
    name="custom",
    summary="Read or write the custom data of an item marker",
    reads_only=False,
    subcommands={
        "get": CommandSpec(
            name="get",
            summary="Read the custom data of the marker at a frame, or find a marker by its data",
            run=run_custom_get_item,
            positionals=(ITEM_ID,),
            flags=(FRAME_FLAG, FIND_FLAG, TIMELINE_FLAG),
            examples=(
                "davinci-axi item marker custom get V1:1 --frame 10",
                "davinci-axi item marker custom get V1:1 --find review-1",
            ),
            methods=(
                "TimelineItem.GetMarkers",
                "TimelineItem.GetMarkerCustomData",
                "TimelineItem.GetMarkerByCustomData",
            ),
        ),
        "set": CommandSpec(
            name="set",
            summary="Store custom data on the item marker at a frame",
            run=run_custom_set_item,
            positionals=(ITEM_ID, DATA),
            flags=(FRAME_FLAG, *ITEM_FLAGS),
            examples=(
                "davinci-axi item marker custom set V1:1 review-1 --frame 10",
                "davinci-axi item marker custom set V1:1 review-1 --frame 10 --force",
            ),
            reads_only=False,
            methods=(
                "TimelineItem.GetMarkers",
                "TimelineItem.GetMarkerCustomData",
                "TimelineItem.UpdateMarkerCustomData",
            ),
        ),
    },
    examples=("davinci-axi item marker custom get V1:1 --frame 10",),
    methods=(
        "TimelineItem.GetMarkerCustomData",
        "TimelineItem.GetMarkerByCustomData",
        "TimelineItem.UpdateMarkerCustomData",
    ),
)
