"""`item set --edl`: set properties on many items from a row file, under one snapshot.

Each row is an `item` ref (`V1:3` or a unique id) plus one column per property. The plan lists
`<ref> <Property>: before -> after` for every real change; the calls run in one timeline block,
a refusal on one item is recorded and the rest continue, then `API_FAILED` names `done`/`failed`.
`SET_EXT_SPEC` is `item_edit.SET_SPEC` with `id`/`assignments` optional and the `--edl` flag; the
single-item path stays `item_edit.run_set`.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from davinci_axi.commands import item_edit
from davinci_axi.commands.item_detail import resolve_item, str_arg
from davinci_axi.commands.item_edit import ASSIGNMENTS, SET_SPEC
from davinci_axi.commands.item_markers import ITEM_FLAGS, ITEM_ID, show_hint, tl_words
from davinci_axi.commands.rowfile import read_rows
from davinci_axi.errors import AxiError
from davinci_axi.mutate import (
    Change,
    MutationPlan,
    check_snapshot_flags,
    coerce_like,
    force_cmd,
    run_mutation,
    with_current_timeline,
)
from davinci_axi.resolve_types_edit import TimelineItemEdit
from davinci_axi.spec import Flag, Invocation, Positional, Result

EDL_FLAG = Flag(
    "edl", "str", None, help="JSON or CSV rows: item plus property columns (ZoomX, Tilt, ...)"
)


def _coerce(n: int, value: str, live: object) -> object:
    try:
        return coerce_like(value, live)
    except AxiError as err:
        raise AxiError(
            err.code, f"row {n}: {err.message}", help=err.help, detail=err.detail
        ) from None


def run_set_edl(inv: Invocation) -> Result:
    if inv.positionals.get("id") or inv.positionals.get("assignments"):
        raise AxiError(
            "VALIDATION_ERROR",
            "pass <id> <assignments> or --edl, not both",
            help=["Valid example: davinci-axi item set --edl props.csv"],
        )
    edl = cast(str, str_arg(inv, "edl"))
    rows = read_rows(Path(edl).expanduser().resolve())
    for n, row in enumerate(rows, 1):
        if len(row) < 2:
            raise AxiError(
                "VALIDATION_ERROR",
                f"row {n}: {row['item']} has no property to set",
                help=["Add columns such as ZoomX or Tilt; empty cells are skipped"],
            )
    check_snapshot_flags(inv)
    session = inv.connect()
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    refs = [row["item"] for row in rows]
    items = [cast(TimelineItemEdit, resolve_item(timeline, ref).item) for ref in refs]
    ids = [item.GetUniqueId() for item in items]
    for n, uid in enumerate(ids):
        if ids.index(uid) != n:
            raise AxiError(
                "VALIDATION_ERROR",
                f"row {n + 1}: {refs[n]} is the same item as {refs[ids.index(uid)]}",
                help=["One row per item"],
            )
    changed: dict[str, dict[str, object]] = {}
    changes: list[Change] = []
    for n, (row, item, ref) in enumerate(zip(rows, items, refs, strict=True), 1):
        live: dict[str, Any] = item.GetProperties() or {}
        unknown = [key for key in row if key != "item" and key not in live]
        if unknown:
            raise AxiError(
                "VALIDATION_ERROR",
                f"row {n}: unknown item property {', '.join(unknown)} for {ref}",
                help=[f"Valid properties for this item: {', '.join(live)}"],
            )
        wanted = {k: _coerce(n, v, live[k]) for k, v in row.items() if k != "item"}
        diff = {k: v for k, v in wanted.items() if v != live[k]}
        if diff:
            changed[ref] = diff
            changes += [Change(f"{ref} {k}", live[k], v) for k, v in diff.items()]
    by_ref = dict(zip(refs, items, strict=True))
    plan = MutationPlan(
        command="item set",
        target=f"{len(rows)} items in timeline {timeline.GetName()}",
        changes=changes,
        force_cmd=force_cmd(inv, "item", "set", "--edl", edl, *tl_words(inv)),
        extra={
            "rows": len(rows),
            "items": refs,
            "unchanged": [r for r in refs if r not in changed],
        },
        next=[show_hint(inv, refs[0])],
    )

    def apply() -> None:
        done: list[str] = []
        failed: list[str] = []
        with with_current_timeline(session, timeline) as switched:
            if switched:
                plan.extra["timeline_restored"] = True
            for ref, props in changed.items():
                (done if by_ref[ref].SetProperties(dict(props)) else failed).append(ref)
        if failed:
            raise AxiError(
                "API_FAILED",
                f"set {len(done)} of {len(changed)} items; "
                f"Resolve refused the change on {', '.join(failed)}",
                help=["The items that were set stay; the snapshot restores the state before all"],
                detail={"done": done, "failed": failed},
            )

    def verify() -> dict[str, object]:
        live_now: dict[str, object] = {}
        with with_current_timeline(session, timeline):
            for ref, props in changed.items():
                now = by_ref[ref].GetProperties() or {}
                live_now.update({f"{ref} {k}": now.get(k) for k in props})
        return live_now

    return run_mutation(inv, session, plan, apply, verify)


def run_set_or_edl(inv: Invocation) -> Result:
    if str_arg(inv, "edl") is not None:
        return run_set_edl(inv)
    for name in ("id", "assignments"):
        if not inv.positionals.get(name):
            raise AxiError(
                "VALIDATION_ERROR",
                f"missing <{name}>",
                help=[
                    "Valid example: davinci-axi item set V1:2 Opacity=50",
                    "Or set many items from a table: davinci-axi item set --edl props.csv",
                ],
            )
    return item_edit.run_set(inv)


SET_EXT_SPEC = replace(
    SET_SPEC,
    positionals=(
        Positional("id", required=False, help=f"{ITEM_ID.help} (or use --edl)"),
        Positional("assignments", required=False, help=f"{ASSIGNMENTS.help} (or use --edl)"),
    ),
    flags=(*ITEM_FLAGS, EDL_FLAG),
    run=run_set_or_edl,
    examples=(*SET_SPEC.examples, "davinci-axi item set --edl props.csv --force"),
    notes=(
        *SET_SPEC.notes,
        "--edl rows: an `item` column (V1:3 or a unique id) plus one column per property; "
        "empty CSV cells are skipped; one snapshot for the whole file",
    ),
)
