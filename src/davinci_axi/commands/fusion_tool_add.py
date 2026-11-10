"""`fusion tool add`: add a tool; `--after X` wires it between X and X's consumers (F16)."""

from __future__ import annotations

from typing import Any

from davinci_axi.commands.fusion_edit import (
    AUTO_POS,
    ITEM_EXAMPLE,
    MUT_FLAGS,
    TOOL_METHODS,
    comp_mutation,
    find_input,
    find_output,
    input_ports,
    mut_spec,
    open_comp,
    parse_pos,
    safe_lua,
    show_hint,
    source_label,
    split_port,
)
from davinci_axi.commands.item_detail import str_arg
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.fusion_guard import (
    attrs_of,
    find_tool,
    fusion_call,
    lua_values,
    tool_name,
    tools_of,
)
from davinci_axi.mutate import (
    Change,
    MutationPlan,
)
from davinci_axi.spec import Flag, Invocation, Positional, Result

# Registry ids without an image input: `--after` cannot wire them.
NO_IMAGE_INPUT = frozenset({"TextPlus", "Background", "MediaIn", "Loader"})
_DROP_AFTER = "Add it unwired, then connect it with `fusion tool connect`"


def main_image_input(tool: Any, label: str) -> Any:
    """The main input (FindMainInput(1)) of `tool`, else its first Image input."""
    main = fusion_call("FindMainInput", tool.FindMainInput, 1)
    if main is not None:
        return main
    for port in input_ports(tool):
        if attrs_of(port).get("INPS_DataType") == "Image":
            return port
    raise AxiError(
        "VALIDATION_ERROR",
        f"{label} has no image input to wire; drop --after",
        help=[_DROP_AFTER],
    )


def _consumers(output: Any) -> list[Any]:
    """The input ports fed by `output` right now."""
    return lua_values(fusion_call("GetConnectedInputs", output.GetConnectedInputs))


def _port_label(port: Any) -> str:
    owner = fusion_call("GetTool", port.GetTool)
    return f"{tool_name(owner)}.{attrs_of(port).get('INPS_ID')}"


def _connect(
    owner: Any, input_id: str, source: Any, label: str, want: str, notes: list[str]
) -> None:
    """Wire `label` to `source`; the link read back decides; the return value is a hint (F33)."""
    returned = fusion_call("ConnectInput", owner.ConnectInput, input_id, source)
    live = source_label(find_input(owner, tool_name(owner), input_id))
    if live != want:
        raise api_failed(
            "ConnectInput", f"Fusion refused to wire {label} (reads {live or 'unconnected'})"
        )
    if returned is False:
        notes.append(f"ConnectInput returned False for {label} but the link exists")


def insert_after(comp: Any, anchor: Any, tool: Any, notes: list[str]) -> list[tuple[str, str]]:
    """Wire `anchor.Output -> tool` and `tool.Output ->` every former consumer of the anchor.

    Returns `[(port label, expected source label)]`, the new tool's own input first.
    """
    anchor_label, tool_label = tool_name(anchor), tool_name(tool)
    anchor_out = find_output(anchor, anchor_label, None)
    consumers = _consumers(anchor_out)  # before the new tool joins the list
    outputs = lua_values(fusion_call("GetOutputList", tool.GetOutputList))
    if not outputs:
        raise api_failed("GetOutputList", f"{tool_label} has no output to wire")
    tool_out = outputs[0]
    main = main_image_input(tool, tool_label)
    main_id = str(attrs_of(main).get("INPS_ID"))
    source = f"{anchor_label}.{attrs_of(anchor_out).get('OUTS_ID')}"
    own = f"{tool_label}.{main_id}"
    _connect(tool, main_id, anchor_out, own, source, notes)
    wired = [(own, source)]
    expected = f"{tool_label}.{attrs_of(tool_out).get('OUTS_ID')}"
    for port in consumers:
        owner = fusion_call("GetTool", port.GetTool)
        label = _port_label(port)
        _connect(owner, str(attrs_of(port).get("INPS_ID")), tool_out, label, expected, notes)
        wired.append((label, expected))
    return wired


# Fusion names some tools by a shorter prefix than their registry id (RectangleMask -> Rectangle1).
PRINTED_PREFIX = {"RectangleMask": "Rectangle"}


def _next_name(existing: list[str], reg_id: str) -> str:
    number = 1
    while f"{reg_id}{number}" in existing:
        number += 1
    return f"{reg_id}{number}"


def _planned_links(anchor: Any, after: str, predicted: str) -> list[Change]:
    """Dry-run rows for `--after`; the new tool's port ids are guessed until it exists."""
    anchor_out = find_output(anchor, after, None)
    source = f"{after}.{attrs_of(anchor_out).get('OUTS_ID')}"
    rows = [Change(f"{predicted}.(main input)", None, source)]
    rows += [Change(_port_label(p), source, f"{predicted}.Output") for p in _consumers(anchor_out)]
    return rows


def run_add(inv: Invocation) -> Result:
    reg_id = safe_lua("tool id", inv.positionals["id"])
    name, after = str_arg(inv, "name"), str_arg(inv, "after")
    pos_text = str_arg(inv, "pos")
    if pos_text is not None and after is not None:
        raise AxiError(
            "VALIDATION_ERROR",
            "--pos and --after contradict each other",
            help=["--after wires the tool in; --pos only places it. Pass one of them"],
        )
    if after is not None and reg_id in NO_IMAGE_INPUT:
        raise AxiError(
            "VALIDATION_ERROR",
            f"{reg_id} has no image input/output to wire; drop --after",
            help=[_DROP_AFTER],
        )
    x, y = parse_pos(pos_text) if pos_text is not None else (AUTO_POS, AUTO_POS)
    edit = open_comp(inv)
    comp = edit.ref.comp
    existing = [tool_name(t) for t in tools_of(comp)]
    if name is not None and name in existing:
        raise AxiError(
            "VALIDATION_ERROR",
            f"a tool named {name} already exists",
            help=[f"Tools in this comp: {', '.join(existing)}"],
        )
    anchor = find_tool(comp, after) if after is not None else None
    predicted = name or _next_name(existing, PRINTED_PREFIX.get(reg_id, reg_id))
    words = ["fusion", "tool", "add", reg_id]
    for flag, value in (("--name", name), ("--pos", pos_text), ("--after", after)):
        words += [flag, value] if value is not None else []
    planned = [Change("tool", None, predicted)]
    if anchor is not None and after is not None:
        planned += _planned_links(anchor, after, predicted)
    created: dict[str, str] = {}
    notes: list[str] = []
    expect: list[tuple[str, str]] = []

    def body(plan: MutationPlan) -> None:
        tool = fusion_call("AddTool", comp.AddTool, reg_id, x, y)
        if tool is None:
            raise api_failed("AddTool", f"Fusion did not add a {reg_id} tool")
        actual = tool_name(tool)
        if name is not None and actual != name:
            fusion_call("SetAttrs", tool.SetAttrs, {"TOOLS_Name": name})
            actual = name
        created["name"] = actual
        rows = [Change("tool", None, actual)]
        if anchor is not None:
            expect.extend(insert_after(comp, anchor, tool, notes))
            source = expect[0][1]
            rows += [
                Change(label, None if i == 0 else source, want)
                for i, (label, want) in enumerate(expect)
            ]
            plan.extra["wired"] = [label for label, _ in expect]
            if notes:
                plan.extra["note"] = "; ".join(notes)
        plan.changes[:] = rows
        plan.next.insert(0, show_hint(inv, edit, actual))

    def verify() -> dict[str, object]:
        found = fusion_call("FindTool", comp.FindTool, created["name"])
        live: dict[str, object] = {"tool": created["name"] if found is not None else None}
        for label, _ in expect:
            owner, port_id = split_port(label)
            live[label] = source_label(find_input(find_tool(comp, owner), owner, str(port_id)))
        return live

    return comp_mutation(inv, edit, "fusion tool add", words, planned, body, verify)


ADD_SPEC = mut_spec(
    "add",
    "Add a tool to the comp; --after X wires it between X and X's consumers",
    run_add,
    positionals=(Positional("id", help="registry id, such as Blur or Merge"),),
    flags=(
        Flag("name", "str", None, help="name for the new tool"),
        Flag("pos", "str", None, help="flow position x,y (placement only, nothing is wired)"),
        Flag(
            "after",
            "str",
            None,
            help="insert the new tool after this tool: X.Output -> new tool, "
            "new tool.Output -> X's former consumers",
        ),
        *MUT_FLAGS,
    ),
    examples=(
        f"davinci-axi fusion tool add Blur {ITEM_EXAMPLE}",
        f"davinci-axi fusion tool add Blur {ITEM_EXAMPLE} --name Soft --after MediaIn1 --force",
    ),
    notes=(
        "Without --after the tool is added unwired; connect it with `fusion tool connect`",
        "Recipe (mask): tool add EllipseMask --name Spot; "
        "tool set Spot 'Center=0.8 0.1,Width=0.2,Height=0.3'; "
        "tool connect Spot ObjectRemoval1.Mask "
        "(without --name, use the printed name, e.g. Rectangle1 for RectangleMask)",
        "The dry-run name is a prediction: Fusion shortens some ids (RectangleMask -> Rectangle1); "
        "pass --name to pin it, and address the tool by the name `tool add` prints",
    ),
    methods=TOOL_METHODS,
)
