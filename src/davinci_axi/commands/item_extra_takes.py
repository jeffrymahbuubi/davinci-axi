"""`item take add|delete|select|finalize|list`: take selectors on timeline items (U25).

Every change is a dry run until `--force` and goes through `mutate.run_mutation`. Deleting a take
and finalizing the selection cannot be undone from a script, so both are irreversible (R32); the
snapshot is the way back. `TakeInfo` carries `startFrame`, `endFrame` and `mediaPoolItem`.
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
from davinci_axi.commands.media_folders import Ctx, resolve_clip
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import Change, MutationPlan, force_cmd, run_mutation
from davinci_axi.output import empty_state
from davinci_axi.resolve_types_edit import MediaPoolEdit
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

INDEX = Positional("n", help="take number (1-based; see `item take list`)")
READ_METHODS = (
    "TimelineItem.GetTakesCount",
    "TimelineItem.GetTakeByIndex",
    "TimelineItem.GetSelectedTakeIndex",
)


def _item(target: Target) -> ItemExtra:
    return cast(ItemExtra, target.item)


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


def _index(inv: Invocation, count: int) -> int:
    raw = inv.positionals["n"]
    try:
        number = int(raw)
    except ValueError:
        number = 0
    if not 1 <= number <= count:
        have = f"{count} take{'' if count == 1 else 's'}"
        raise AxiError(
            "VALIDATION_ERROR",
            f"take {raw} does not exist; the item has {have}",
            help=["Takes are numbered from 1; run `davinci-axi item take list <id>` to see them"],
        )
    return number


def _frame_flag(inv: Invocation, key: str) -> int | None:
    value = inv.args.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--{key} must be 0 or more frames",
            help=[f"Valid example: --{key} 24"],
        )
    return value


def _take_text(
    name: str, live_start: object, live_end: object, want_start: object, want_end: object
) -> str:
    """`name 30..120`: only the bounds the caller asked for are part of the text."""
    if want_start is None and want_end is None:
        return name
    lo = "" if want_start is None else str(live_start)
    hi = "" if want_end is None else str(live_end)
    return f"{name} {lo}..{hi}"


def run_take_add(inv: Invocation) -> Result:
    start, end = _frame_flag(inv, "in"), _frame_flag(inv, "out")
    if start is not None and end is not None and end <= start:
        raise AxiError(
            "VALIDATION_ERROR", "--out must be after --in", help=["Valid example: --in 0 --out 48"]
        )
    target = open_target(inv)
    session = target.session
    pool = session.require_project().GetMediaPool()
    ctx = Ctx(session, cast(MediaPoolEdit, pool), pool.GetRootFolder())
    clip = resolve_clip(ctx, inv.positionals["clip"]).clip
    item = _item(target)
    before = int(item.GetTakesCount())
    words = ["item", "take", "add", target.ref, inv.positionals["clip"], *tl_words(inv)]
    for flag, value in (("--in", start), ("--out", end)):
        if value is not None:
            words += [flag, str(value)]
    after = 2 if before == 0 else before + 1  # Resolve turns the item's own clip into take 1 (B2)
    name = clip.GetName()
    changes = [
        Change("takes", before, after),
        Change("new take", None, _take_text(name, start, end, start, end)),
    ]
    plan = _plan(target, "item take add", changes, words, inv)
    plan.extra["clip"] = name
    if before == 0:
        plan.extra["original_became_take_1"] = True
        plan.next.append("The item's own clip became take 1; the new clip is take 2")

    def apply() -> None:
        bounds: list[int] = []
        if start is not None or end is not None:
            bounds.append(start if start is not None else 0)
        if end is not None:
            bounds.append(end)
        if not item.AddTake(clip, *bounds):
            raise api_failed("TimelineItem.AddTake", f"could not add {clip.GetName()} as a take")

    def verify() -> dict[str, object]:
        count = int(item.GetTakesCount())
        take = (item.GetTakeByIndex(count) or {}) if count else {}
        media = take.get("mediaPoolItem")
        text = _take_text(
            media.GetName() if media is not None else "",
            take.get("startFrame"),
            take.get("endFrame"),
            start,
            end,
        )
        return {"takes": count, "new take": text}

    return run_mutation(inv, session, plan, apply, verify)


def run_take_delete(inv: Invocation) -> Result:
    target = open_target(inv)
    item = _item(target)
    before = int(item.GetTakesCount())
    number = _index(inv, before)
    words = ["item", "take", "delete", target.ref, str(number), *tl_words(inv)]
    plan = _plan(target, "item take delete", [Change("takes", before, before - 1)], words, inv)
    plan.irreversible = True
    plan.warning = f"deletes take {number} of {before}; the snapshot is the only way back"

    def apply() -> None:
        if not item.DeleteTakeByIndex(number):
            raise api_failed("TimelineItem.DeleteTakeByIndex", f"take {number} was not deleted")

    return run_mutation(
        inv, target.session, plan, apply, lambda: {"takes": int(item.GetTakesCount())}
    )


def run_take_select(inv: Invocation) -> Result:
    target = open_target(inv)
    item = _item(target)
    number = _index(inv, int(item.GetTakesCount()))
    now = int(item.GetSelectedTakeIndex())
    words = ["item", "take", "select", target.ref, str(number), *tl_words(inv)]
    plan = _plan(
        target,
        "item take select",
        [Change("selected take", now, number)] if now != number else [],
        words,
        inv,
    )

    def apply() -> None:
        if not item.SelectTakeByIndex(number):
            raise api_failed("TimelineItem.SelectTakeByIndex", f"take {number} was not selected")

    return run_mutation(
        inv,
        target.session,
        plan,
        apply,
        lambda: {"selected take": int(item.GetSelectedTakeIndex())},
    )


def run_take_finalize(inv: Invocation) -> Result:
    target = open_target(inv)
    item = _item(target)
    before = int(item.GetTakesCount())
    if before == 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"{target.label} has no takes to finalize",
            help=["Add a take first with `davinci-axi item take add <id> <clip>`"],
        )
    words = ["item", "take", "finalize", target.ref, *tl_words(inv)]
    plan = _plan(target, "item take finalize", [Change("takes", before, 0)], words, inv)
    plan.irreversible = True
    plan.warning = (
        "finalizes the take selection and drops the other takes; restore from the snapshot"
    )

    def apply() -> None:
        if not item.FinalizeTake():
            raise api_failed("TimelineItem.FinalizeTake", "the take selection was not finalized")

    return run_mutation(
        inv, target.session, plan, apply, lambda: {"takes": int(item.GetTakesCount())}
    )


def run_take_list(inv: Invocation) -> Result:
    target = open_target(inv)
    item = target.item
    selected = int(item.GetSelectedTakeIndex())
    rows: list[dict[str, object]] = []
    for index in range(1, int(item.GetTakesCount()) + 1):
        take = item.GetTakeByIndex(index) or {}
        clip = take.get("mediaPoolItem")
        rows.append(
            {
                "index": index,
                "selected": index == selected,
                "clip": clip.GetName() if clip is not None else None,
                "start": take.get("startFrame"),
                "end": take.get("endFrame"),
            }
        )
    data: dict[str, object] = {"item": target.label, "selected": selected}
    data["takes"] = rows or empty_state("takes", f"on item {target.ref}", {})
    return Result(data)


TAKE_SPEC = CommandSpec(
    name="take",
    summary="List, add, select, delete or finalize the takes of an item",
    reads_only=False,
    subcommands={
        "list": CommandSpec(
            name="list",
            summary="List the takes of an item",
            run=run_take_list,
            positionals=(ITEM_ID,),
            flags=(TIMELINE_FLAG,),
            examples=("davinci-axi item take list V1:1",),
            methods=READ_METHODS,
        ),
        "add": CommandSpec(
            name="add",
            summary="Add a media pool clip as a take (starts a take selector if needed)",
            run=run_take_add,
            positionals=(ITEM_ID, Positional("clip", help="clip id or exact name")),
            flags=(
                Flag("in", "int", None, help="source start frame of the take"),
                Flag("out", "int", None, help="source end frame of the take"),
                *ITEM_FLAGS,
            ),
            examples=(
                "davinci-axi item take add V1:1 A002_city",
                "davinci-axi item take add V1:1 A002_city --in 0 --out 48 --force",
            ),
            reads_only=False,
            methods=(
                "TimelineItem.AddTake",
                "TimelineItem.GetTakesCount",
                "TimelineItem.GetTakeByIndex",
            ),
        ),
        "select": CommandSpec(
            name="select",
            summary="Select a take by number",
            run=run_take_select,
            positionals=(ITEM_ID, INDEX),
            flags=ITEM_FLAGS,
            examples=("davinci-axi item take select V1:1 2 --force",),
            reads_only=False,
            methods=(
                "TimelineItem.SelectTakeByIndex",
                "TimelineItem.GetSelectedTakeIndex",
                "TimelineItem.GetTakesCount",
            ),
        ),
        "delete": CommandSpec(
            name="delete",
            summary="Delete a take by number (irreversible)",
            run=run_take_delete,
            positionals=(ITEM_ID, INDEX),
            flags=ITEM_FLAGS,
            examples=("davinci-axi item take delete V1:1 2 --force",),
            reads_only=False,
            methods=("TimelineItem.DeleteTakeByIndex", "TimelineItem.GetTakesCount"),
        ),
        "finalize": CommandSpec(
            name="finalize",
            summary="Finalize the take selection (irreversible)",
            run=run_take_finalize,
            positionals=(ITEM_ID,),
            flags=ITEM_FLAGS,
            examples=("davinci-axi item take finalize V1:1 --force",),
            reads_only=False,
            methods=("TimelineItem.FinalizeTake", "TimelineItem.GetTakesCount"),
        ),
    },
    examples=("davinci-axi item take list V1:1",),
    methods=(
        *READ_METHODS,
        "TimelineItem.AddTake",
        "TimelineItem.SelectTakeByIndex",
        "TimelineItem.DeleteTakeByIndex",
        "TimelineItem.FinalizeTake",
    ),
)
