"""`color cdl set --edl`: one CDL per item from a row file, under one snapshot.

Columns are `item` plus the CDL keys (`NodeIndex`, `Slope`, `Offset`, `Power`, `Saturation`);
omitted keys are sent neutral as in `color cdl set`. All rows go through one `targets_write`, so
there is one dry run, one snapshot and one timeline block. `CDL_EXT_SPEC` is `color_extras.CDL_SPEC`
with `target`/`assignments` optional and the `--edl` flag.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

from davinci_axi.commands.color_common import color_item
from davinci_axi.commands.color_extras import (
    ASSIGNMENTS,
    CDL_KEYS,
    CDL_SPEC,
    parse_cdl,
    run_cdl_set,
)
from davinci_axi.commands.color_targets import (
    CALLED_NOTE,
    ITEMS,
    GraphTarget,
    Step,
    called_step,
    check_node_each,
    open_targets,
    targets_write,
)
from davinci_axi.commands.item_detail import str_arg
from davinci_axi.commands.item_markers import ITEM_FLAGS, Target
from davinci_axi.commands.rowfile import read_rows
from davinci_axi.errors import AxiError
from davinci_axi.spec import Flag, Invocation, Positional, Result

CDL_EDL_FLAG = Flag(
    "edl",
    "str",
    None,
    help="JSON or CSV rows: item, NodeIndex, Slope, Offset, Power, Saturation (one CDL per item)",
)


def _parse_row(n: int, row: dict[str, str]) -> dict[str, object]:
    text = ",".join(f"{key}={value}" for key, value in row.items() if key != "item")
    try:
        return parse_cdl(text)
    except AxiError as err:
        raise AxiError(
            err.code, f"row {n}: {err.message}", help=err.help, detail=err.detail
        ) from None


def run_cdl_edl(inv: Invocation) -> Result:
    if inv.positionals.get("target") or inv.positionals.get("assignments"):
        raise AxiError(
            "VALIDATION_ERROR",
            "pass <target> <assignments> or --edl, not both",
            help=["Valid example: davinci-axi color cdl set --edl grades.csv"],
        )
    edl = cast(str, str_arg(inv, "edl"))
    rows = read_rows(Path(edl).expanduser().resolve(), allowed=("item", *CDL_KEYS))
    cdls = {row["item"]: _parse_row(n, row) for n, row in enumerate(rows, 1)}
    refs = list(cdls)
    inner = replace(inv, positionals={**inv.positionals, "target": ",".join(refs)})
    targets = open_targets(inner, graphs=False)
    for entry in targets.entries:
        check_node_each(
            replace(targets, entries=[entry]), int(cast(int, cdls[entry.ref]["NodeIndex"]))
        )

    def step(entry: GraphTarget) -> Step:
        item = color_item(cast(Target, entry.target))
        cdl = cdls[entry.ref]
        return called_step(targets, entry, lambda: item.SetCDL(cdl))

    summary: dict[str, object] | str = (
        {ref: " ".join(f"{k}={v}" for k, v in cdl.items()) for ref, cdl in cdls.items()}
        if inv.opts.json
        else f"{len(cdls)} CDLs from {Path(edl).name}"
    )
    return targets_write(
        inner,
        targets,
        command="color cdl set",
        words=["color", "cdl", "set", "--edl", edl],
        step=step,
        fail="TimelineItem.SetCDL",
        extra={"read_back": CALLED_NOTE, "rows": len(rows), "cdl": summary},
    )


def run_cdl_set_or_edl(inv: Invocation) -> Result:
    if str_arg(inv, "edl") is not None:
        return run_cdl_edl(inv)
    for name in ("target", "assignments"):
        if not inv.positionals.get(name):
            raise AxiError(
                "VALIDATION_ERROR",
                f"missing <{name}>",
                help=[
                    "Valid example: davinci-axi color cdl set V1:1 NodeIndex=1,Saturation=0.8",
                    "Or one CDL per item from a table: davinci-axi color cdl set --edl grades.csv",
                ],
            )
    return run_cdl_set(inv)


_SET = CDL_SPEC.subcommands["set"]
CDL_EXT_SPEC = replace(
    CDL_SPEC,
    subcommands={
        "set": replace(
            _SET,
            positionals=(
                Positional("target", required=False, help=f"{ITEMS.help} (or use --edl)"),
                Positional("assignments", required=False, help=f"{ASSIGNMENTS.help} (or --edl)"),
            ),
            flags=(*ITEM_FLAGS, CDL_EDL_FLAG),
            run=run_cdl_set_or_edl,
            examples=(*_SET.examples, "davinci-axi color cdl set --edl grades.csv --force"),
            notes=(
                *_SET.notes,
                "--edl rows: an `item` column plus NodeIndex and any of Slope, Offset, Power, "
                "Saturation; each item gets its own CDL under one snapshot",
            ),
        )
    },
)
