"""`fusion keyframe set` and `fusion undo` (R34, U17b, quirk 3).

`keyframe set` makes the input animated with `comp.Execute` and a `BezierSpline{}`, then sets each
key with a short pause; it does not Lock the comp (Fusion drops keyframes on a locked comp). `undo`
undoes the comp's own undo stack and refuses entries davinci-axi did not make unless `--any`.
"""

from __future__ import annotations

import time
from typing import Any

from davinci_axi.commands.fusion_edit import (
    ITEM_EXAMPLE,
    MUT_FLAGS,
    TOOL_METHODS,
    TOOL_NAME,
    UNSETTABLE,
    comp_mutation,
    find_input,
    mut_spec,
    open_comp,
    safe_lua,
    show_hint,
)
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.fusion_guard import UNDO_PREFIX, attrs_of, find_tool, fusion_call, lua_values
from davinci_axi.mutate import Change, MutationPlan, parse_assignments
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

KEYFRAME_PAUSE = 0.12


def run_keyframe(inv: Invocation) -> Result:
    keys = {float(k): v for k, v in _key_pairs(inv.positionals["keys"]).items()}
    tool_label = safe_lua("tool name", inv.positionals["name"])
    input_id = safe_lua("input id", inv.positionals["input"])
    edit = open_comp(inv)
    comp = edit.ref.comp
    tool = find_tool(comp, tool_label)
    port = find_input(tool, tool_label, input_id)
    if str(attrs_of(port).get("INPS_DataType")) in UNSETTABLE:
        raise AxiError(
            "VALIDATION_ERROR",
            f"input {input_id} cannot be animated",
            help=[
                "Animate a number, point or color input",
                "Check the input id with `davinci-axi fusion tool show <name>`",
            ],
        )
    ordered = sorted(keys.items())
    changes = [
        Change(f"{input_id}@{t:g}", fusion_call("GetInput", tool.GetInput, input_id, t), v)
        for t, v in ordered
    ]
    spline = f'comp:FindTool("{tool_label}"):SetInput("{input_id}", BezierSpline{{}})'

    def body(_: MutationPlan) -> None:
        fusion_call("Execute", comp.Execute, spline)
        for when, value in ordered:
            time.sleep(KEYFRAME_PAUSE)
            fusion_call("SetInput", tool.SetInput, input_id, value, when)

    def verify() -> dict[str, object]:
        return {
            f"{input_id}@{t:g}": fusion_call("GetInput", tool.GetInput, input_id, t)
            for t, _ in ordered
        }

    words = ["fusion", "keyframe", "set", tool_label, input_id, inv.positionals["keys"]]
    return comp_mutation(
        inv,
        edit,
        "fusion keyframe set",
        words,
        changes,
        body,
        verify,
        lock=False,
        warning="not locked while keyframing (Fusion drops keyframes on a locked comp)",
        next_lines=[show_hint(inv, edit, tool_label)],
    )


def _key_pairs(text: str) -> dict[str, float]:
    pairs = parse_assignments(text)
    try:
        return {k: float(v) for k, v in pairs.items()}
    except ValueError:
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot read {text} as time=value pairs",
            help=["Valid example: 0=0,24=5 (frame=value, comma-separated)"],
        ) from None


def undo_names(comp: Any) -> list[str]:
    """The undo entry names, newest last (ASSUMED shape of `GetUndoStack`, OQ12)."""
    names = []
    for entry in lua_values(fusion_call("GetUndoStack", comp.GetUndoStack)):
        named = entry.get("Name") if isinstance(entry, dict) else None
        names.append(str(named or entry))
    return names


def run_undo(inv: Invocation) -> Result:
    count = inv.args.get("count")
    count = count if isinstance(count, int) and not isinstance(count, bool) else 1
    if count < 1:
        raise AxiError(
            "VALIDATION_ERROR",
            "--count needs a number of 1 or more",
            help=["Valid example: --count 2"],
        )
    edit = open_comp(inv)
    comp = edit.ref.comp
    names = undo_names(comp)
    top = names[-count:] if count <= len(names) else []
    if not top:
        raise AxiError(
            "UNDO_REFUSED",
            f"the undo stack has {len(names)} entries, fewer than --count {count}",
            help=["Run `fusion undo-stack` to see the entries"],
        )
    foreign = [n for n in top if not n.startswith(UNDO_PREFIX)]
    if foreign and not inv.args.get("any"):
        raise AxiError(
            "UNDO_REFUSED",
            f"the top {count} undo entries are not all davinci-axi's: {', '.join(foreign)}",
            help=["Pass --any to undo them anyway (they may be the user's own edits)"],
            detail={"entries": ", ".join(top)},
        )
    changes = [Change("undo_depth", len(names), len(names) - count)]

    def body(plan: MutationPlan) -> None:
        plan.extra["undone"] = ", ".join(top)
        if fusion_call("Undo", comp.Undo, count) is False:
            raise api_failed("Undo", "Fusion could not undo")

    def verify() -> dict[str, object]:
        return {"undo_depth": len(undo_names(comp))}

    words = ["fusion", "undo", "--count", str(count)] + (["--any"] if foreign else [])
    return comp_mutation(
        inv,
        edit,
        "fusion undo",
        words,
        changes,
        body,
        verify,
        group=False,
        warning="undoes only the comp's own undo stack; Resolve-side changes stay",
    )


KEYFRAME_SPEC = CommandSpec(
    name="keyframe",
    summary="Animate a tool input with keyframes",
    reads_only=False,
    subcommands={
        "set": mut_spec(
            "set",
            "Set keyframes on an input: <time>=<value> pairs, not locked while setting",
            run_keyframe,
            positionals=(
                TOOL_NAME,
                Positional("input", help="input id, such as XBlurSize"),
                Positional("keys", help="comma-separated <frame>=<value> pairs, such as 0=0,24=5"),
            ),
            flags=MUT_FLAGS,
            examples=(
                f"davinci-axi fusion keyframe set Blur1 XBlurSize 0=0,24=5 {ITEM_EXAMPLE} --force",
            ),
            methods=TOOL_METHODS,
        )
    },
    examples=(f"davinci-axi fusion keyframe set Blur1 XBlurSize 0=0,24=5 {ITEM_EXAMPLE}",),
    methods=TOOL_METHODS,
)

UNDO_SPEC = mut_spec(
    "undo",
    "Undo the comp's last davinci-axi edits (--any also undoes other entries)",
    run_undo,
    flags=(
        Flag("count", "int", 1, help="how many undo entries to undo"),
        Flag("any", "bool", False, help="also undo entries davinci-axi did not make"),
        *MUT_FLAGS,
    ),
    examples=(
        f"davinci-axi fusion undo {ITEM_EXAMPLE} --force",
        f"davinci-axi fusion undo --count 2 {ITEM_EXAMPLE}",
    ),
    methods=TOOL_METHODS,
)
