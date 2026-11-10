"""`fusion tool set`: set tool inputs and read them back (split from fusion_tools for F18)."""

from __future__ import annotations

import math

from davinci_axi.commands.fusion_edit import (
    ASSIGNMENTS,
    ITEM_EXAMPLE,
    MUT_FLAGS,
    TOOL_METHODS,
    TOOL_NAME,
    UNSETTABLE,
    comp_mutation,
    find_input,
    mut_spec,
    open_comp,
    show_hint,
)
from davinci_axi.errors import AxiError
from davinci_axi.fusion_guard import (
    attrs_of,
    find_tool,
    fusion_call,
)
from davinci_axi.mutate import (
    Change,
    MutationPlan,
    coerce_like,
    parse_assignments,
)
from davinci_axi.spec import CommandSpec, Invocation, Positional, Result

POINT_HELP = "a Point takes two numbers separated by a space, e.g. Center=0.8 0.1"


def parse_point(text: str) -> dict[int, float]:
    """`"0.8 0.1"` -> `{1: 0.8, 2: 0.1}`. Anything but two finite numbers is VALIDATION_ERROR."""
    parts = text.split()
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        numbers = []
    if len(parts) != 2 or len(numbers) != 2 or not all(math.isfinite(n) for n in numbers):
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot read {text or '(empty)'} as a Point",
            help=[POINT_HELP],
        )
    return {1: numbers[0], 2: numbers[1]}


def is_point(data_type: str, live: object) -> bool:
    """A Point input: declared `Point`, or a live `{1: x, 2: y}` table."""
    if data_type == "Point":
        return True
    if not isinstance(live, dict) or not live:
        return False
    try:
        return {int(k) for k in live} <= {1, 2}
    except (TypeError, ValueError):
        return False


def _point_text(live: object) -> str | None:
    """`"x y"` for a live Point table, None when the tool reports no value."""
    if not isinstance(live, dict):
        return None
    xy = {int(k): float(v) for k, v in live.items()}
    return f"{xy.get(1, 0.0):g} {xy.get(2, 0.0):g}"


def run_set(inv: Invocation) -> Result:
    assignments = parse_assignments(inv.positionals["assignments"])
    edit = open_comp(inv)
    label = inv.positionals["name"]
    tool = find_tool(edit.ref.comp, label)
    changes: list[Change] = []
    wanted: dict[str, object] = {}
    for key, text in assignments.items():
        port = find_input(tool, label, key)
        data_type = str(attrs_of(port).get("INPS_DataType") or "")
        live = fusion_call("GetInput", tool.GetInput, key)
        if data_type not in UNSETTABLE and is_point(data_type, live):
            point = parse_point(text)
            after = f"{point[1]:g} {point[2]:g}"
            before = _point_text(live)
            if before != after:
                wanted[key] = point
                changes.append(Change(key, before, after))
            continue
        if data_type in UNSETTABLE or isinstance(live, (dict, list, tuple)):
            raise AxiError(
                "VALIDATION_ERROR",
                f"input {key} ({data_type or 'table'}) cannot be set from a text value",
                help=["Points take 'Center=x y'; use `fusion tool connect` for images and masks"],
            )
        like = live if live is not None else (0.0 if data_type == "Number" else "")
        value = coerce_like(text, like)
        if value != live:
            wanted[key] = value
            changes.append(Change(key, live, value))
    words = ["fusion", "tool", "set", label, inv.positionals["assignments"]]

    def body(_: MutationPlan) -> None:
        for key, value in wanted.items():
            fusion_call("SetInput", tool.SetInput, key, value)

    def verify() -> dict[str, object]:
        back: dict[str, object] = {}
        for key, value in wanted.items():
            read = fusion_call("GetInput", tool.GetInput, key)
            back[key] = _point_text(read) if isinstance(value, dict) else read
        return back

    nxt = [show_hint(inv, edit, label)]
    return comp_mutation(inv, edit, "fusion tool set", words, changes, body, verify, next_lines=nxt)


def run_press(inv: Invocation) -> Result:
    edit = open_comp(inv)
    label = inv.positionals["name"]
    button = inv.positionals["button"]
    tool = find_tool(edit.ref.comp, label)
    port = find_input(tool, label, button)
    data_type = str(attrs_of(port).get("INPS_DataType") or "")
    if data_type in UNSETTABLE:
        raise AxiError(
            "VALIDATION_ERROR",
            f"input {button} ({data_type}) is not a button",
            help=["Buttons show as Number inputs in `fusion tool show`"],
        )
    row = f"{label}.{button}"
    changes = [Change(row, "idle", "pressed")]
    words = ["fusion", "tool", "press", label, button]

    def body(_: MutationPlan) -> None:
        fusion_call("SetInput", tool.SetInput, button, 1)

    def verify() -> dict[str, object]:
        return {row: "pressed"}

    return comp_mutation(
        inv,
        edit,
        "fusion tool press",
        words,
        changes,
        body,
        verify,
        warning="not read back: Fusion may ignore a scripted button press (unverified on 21.1.1)",
        next_lines=[show_hint(inv, edit, label)],
    )


SET_SPEC = mut_spec(
    "set",
    "Set tool inputs from <input>=<value> pairs and read them back",
    run_set,
    positionals=(TOOL_NAME, ASSIGNMENTS),
    flags=MUT_FLAGS,
    examples=(
        f"davinci-axi fusion tool set Blur1 XBlurSize=2 {ITEM_EXAMPLE} --force",
        f"davinci-axi fusion tool set Transform1 'Center=0.8 0.1' {ITEM_EXAMPLE} --force",
    ),
    methods=TOOL_METHODS,
    notes=(
        "Point inputs take two numbers separated by a space: Center=0.8 0.1 "
        "(comma separates assignments)",
        "Recipe (Object Removal): tool add EllipseMask --name Spot; "
        "tool set Spot 'Center=0.8 0.1,Width=0.2,Height=0.3'; "
        "tool connect Spot ObjectRemoval1.Mask; tool press ObjectRemoval1 SceneAnalysis; "
        "tool press ObjectRemoval1 BuildCleanPlate "
        "(without --name, use the printed name, e.g. Rectangle1 for RectangleMask)",
    ),
)

PRESS_SPEC = mut_spec(
    "press",
    "Press a tool button by setting its input to 1 (unverified: check the result with a render)",
    run_press,
    positionals=(
        TOOL_NAME,
        Positional("button", help="input id of the button, as `fusion tool show` lists it"),
    ),
    flags=MUT_FLAGS,
    examples=(
        f"davinci-axi fusion tool press ObjectRemoval1 SceneAnalysis {ITEM_EXAMPLE} --force",
    ),
    methods=TOOL_METHODS,
    notes=(
        "Buttons such as ObjectRemoval SceneAnalysis / BuildCleanPlate show as Number inputs; "
        "whether Fusion runs them from a script is unknown, so verify with `render frame`",
    ),
)

SET_VERBS: dict[str, CommandSpec] = {"set": SET_SPEC, "press": PRESS_SPEC}
