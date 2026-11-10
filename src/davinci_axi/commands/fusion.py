"""`fusion`: read Fusion comps, tools, wiring, the tool registry and the undo stack (R34, U17a).

Every verb is read-only: no `--force`, no snapshot, no page switch (R24). A comp is addressed by
`--item` (+ `--comp`) or, for reads only, `--active`. Each verb checks that the target timeline is
current (`preflight`) and runs every Fusion call under a timeout (`fusion_call`, quirk 1); results
carry `resolvepython_processes` and any `warnings`. U17b wraps `SPEC` with the mutating verbs.
"""

from __future__ import annotations

import shlex
from typing import Any, cast

from davinci_axi.commands.fusion_reads_common import (
    FIELDS_EDGES,
    FIELDS_INPUTS,
    FIELDS_REGISTRY,
    FIELDS_TOOLS,
    FIELDS_UNDO,
    ITEM_FLAGS,
    TIMELINE_FLAGS,
    UNREADABLE_TYPES,
)
from davinci_axi.commands.fusion_reads_common import (
    block as _block,
)
from davinci_axi.commands.fusion_reads_common import (
    check_list_flags as _check_list_flags,
)
from davinci_axi.commands.fusion_reads_common import (
    head as _head,
)
from davinci_axi.commands.fusion_reads_common import (
    scalar as _scalar,
)
from davinci_axi.commands.fusion_reads_common import (
    tail as _tail,
)
from davinci_axi.commands.fusion_reads_common import (
    target as _target,
)
from davinci_axi.commands.fusion_reads_common import (
    target_flags as _target_flags,
)
from davinci_axi.commands.item_detail import resolve_item, str_arg
from davinci_axi.errors import AxiError
from davinci_axi.fusion_guard import (
    FUSION_FLAGS,
    UNDO_PREFIX,
    TimelineItemFusion,
    attrs_of,
    find_tool,
    fusion_app,
    fusion_call,
    lua_values,
    preflight,
    source_label,
    tool_row,
    tools_of,
)
from davinci_axi.output import empty_state, hint
from davinci_axi.spec import LIST_FLAGS, CommandSpec, Flag, Invocation, Positional, Result

_REG_TYPES = "CT_Tool CT_Mask CT_SourceTool CT_ParticleTool CT_Modifier CT_ImageFormat"
KNOWN_REG_TYPES = tuple(_REG_TYPES.split())


def run_comps(inv: Invocation) -> Result:
    item_ref = str_arg(inv, "item")
    if item_ref is None:
        raise AxiError(
            "VALIDATION_ERROR",
            "fusion comps needs --item",
            help=["Valid example: davinci-axi fusion comps --item V1:2"],
        )
    session = inv.connect()
    pre = preflight(session, inv)
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    located = resolve_item(timeline, item_ref)
    item = cast(TimelineItemFusion, located.item)
    count = int(fusion_call("GetFusionCompCount", item.GetFusionCompCount) or 0)
    names = [str(n) for n in fusion_call("GetFusionCompNameList", item.GetFusionCompNameList) or []]
    current = fusion_call("GetCurrentComp", fusion_app(session).GetCurrentComp)
    active = attrs_of(current).get("COMPS_Name") if current is not None else None
    data: dict[str, object] = {"timeline": pre.timeline, "item": item_ref, "count": count}
    data["cache"] = fusion_call("GetIsFusionOutputCacheEnabled", item.GetIsFusionOutputCacheEnabled)
    if names:
        data["comps"] = [
            {"index": i, "name": n, "active": n == active} for i, n in enumerate(names, start=1)
        ]
        helps = [
            hint(f"fusion tools --item {shlex.quote(item_ref)}", "list the first comp's tools")
        ]
    else:
        data["comps"] = empty_state("comps", f"on item {item_ref}", {})
        helps = ["Create a comp for the item in Resolve first (Fusion page or Inspector)"]
    return Result(_tail(data, pre), helps)


def run_comp_show(inv: Invocation) -> Result:
    _, pre, ref = _target(inv)
    attrs = attrs_of(ref.comp)
    data = {**_head(pre, ref), "tools": len(tools_of(ref.comp))}
    for key in ("render_start", "render_end", "global_start", "global_end"):
        data[key] = _scalar(attrs.get("COMPN_" + key.title().replace("_", "")))
    data["file"] = _scalar(attrs.get("COMPS_FileName"))
    data["locked"] = bool(fusion_call("IsLocked", ref.comp.IsLocked))
    data["undo_depth"] = len(lua_values(fusion_call("GetUndoStack", ref.comp.GetUndoStack)))
    return Result(_tail(data, pre), [hint(f"fusion tools {_target_flags(ref)}", "list its tools")])


def run_tools(inv: Invocation) -> Result:
    _check_list_flags(inv, FIELDS_TOOLS)
    _, pre, ref = _target(inv)
    selected = bool(inv.args.get("selected"))
    regid = str_arg(inv, "type")
    call_args: list[object] = [selected, *([] if regid is None else [regid])]
    found = lua_values(fusion_call("GetToolList", ref.comp.GetToolList, *call_args))
    rows: list[dict[str, object]] = []
    for tool in found:
        row = tool_row(ref.comp, tool)
        pos = cast("tuple[float, float] | None", row.pop("pos"))
        row["x"], row["y"] = pos if pos is not None else (None, None)
        rows.append(row)
    data = _head(pre, ref)
    filters: dict[str, object] = {"selected": selected or None, "type": regid}
    empty = empty_state("tools", f"in comp {ref.comp_name}", filters)
    shown, helps = _block(inv, data, "tools", rows, FIELDS_TOOLS, empty, "tools")
    if shown:
        first = shlex.quote(str(shown[0]["name"]))
        helps.append(hint(f"fusion tool show {first} {_target_flags(ref)}", "show its ports"))
    return Result(_tail(data, pre), helps)


def _input_rows(tool: Any) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for port in lua_values(fusion_call("GetInputList", tool.GetInputList)):
        attrs = attrs_of(port)
        data_type = str(attrs.get("INPS_DataType") or "")
        input_id = str(attrs.get("INPS_ID") or "")
        value = None
        if data_type not in UNREADABLE_TYPES:
            value = _scalar(fusion_call("GetInput", tool.GetInput, input_id))
        rows.append(
            {
                "id": input_id,
                "name": attrs.get("INPS_Name"),
                "type": data_type,
                "value": value,
                "source": source_label(port),
            }
        )
    return rows


def _output_rows(tool: Any) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for port in lua_values(fusion_call("GetOutputList", tool.GetOutputList)):
        attrs = attrs_of(port)
        linked = lua_values(fusion_call("GetConnectedInputs", port.GetConnectedInputs))
        rows.append(
            {"id": attrs.get("OUTS_ID"), "name": attrs.get("OUTS_Name"), "connected": bool(linked)}
        )
    return rows


def run_tool_show(inv: Invocation) -> Result:
    _check_list_flags(inv, FIELDS_INPUTS)
    _, pre, ref = _target(inv)
    tool = find_tool(ref.comp, inv.positionals["name"])
    row = tool_row(ref.comp, tool)
    inputs = _input_rows(tool)
    data = _head(pre, ref)
    data["tool"], data["id"], data["passthrough"] = row["name"], row["id"], row["passthrough"]
    empty = empty_state("inputs", f"on tool {row['name']}", {})
    shown, helps = _block(
        inv, data, "inputs", inputs, FIELDS_INPUTS, empty, "inputs", "inputs_total"
    )
    outputs = _output_rows(tool)
    data["outputs"] = outputs if outputs else empty_state("outputs", f"on tool {row['name']}", {})
    if shown:
        name = shlex.quote(str(row["name"]))
        example = shlex.quote(str(shown[0]["id"]))
        flags = _target_flags(ref)
        helps.append(hint(f"fusion tool get {name} {example} {flags}", "read one input"))
    return Result(_tail(data, pre), helps)


def run_tool_get(inv: Invocation) -> Result:
    _, pre, ref = _target(inv)
    tool = find_tool(ref.comp, inv.positionals["name"])
    input_id = inv.positionals["input"]
    when = inv.args.get("time")
    args: list[object] = [input_id] if when is None else [input_id, float(cast(float, when))]
    value = fusion_call("GetInput", tool.GetInput, *args)
    if value is None:
        ports = lua_values(fusion_call("GetInputList", tool.GetInputList))
        ids = [str(attrs_of(p).get("INPS_ID")) for p in ports]
        if input_id not in ids:
            listing = ", ".join(ids[:30]) + (", ..." if len(ids) > 30 else "")
            name = shlex.quote(inv.positionals["name"])
            raise AxiError(
                "NOT_FOUND",
                f"input {input_id} not found on tool {inv.positionals['name']}",
                help=[
                    f"Inputs on this tool: {listing or '(none)'}",
                    hint(f"fusion tool show {name} {_target_flags(ref)}", "list every input"),
                ],
            )
    data = _head(pre, ref)
    data["tool"], data["input"] = inv.positionals["name"], input_id
    if when is not None:
        data["time"] = when
    data["value"] = _scalar(value)
    return Result(_tail(data, pre))


def _edges(comp: Any) -> list[dict[str, object]]:
    edges: list[dict[str, object]] = []
    for tool in tools_of(comp):
        to_name = attrs_of(tool).get("TOOLS_Name")
        for port in lua_values(fusion_call("GetInputList", tool.GetInputList)):
            source = fusion_call("GetConnectedOutput", port.GetConnectedOutput)
            if source is None:
                continue
            from_tool = fusion_call("GetTool", source.GetTool)
            edges.append(
                {
                    "from": attrs_of(from_tool).get("TOOLS_Name"),
                    "output": attrs_of(source).get("OUTS_ID"),
                    "to": to_name,
                    "input": attrs_of(port).get("INPS_ID"),
                }
            )
    return edges


def run_graph(inv: Invocation) -> Result:
    _check_list_flags(inv, FIELDS_EDGES)
    _, pre, ref = _target(inv)
    data = _head(pre, ref)
    empty = empty_state("edges", f"in comp {ref.comp_name}", {})
    _, helps = _block(inv, data, "edges", _edges(ref.comp), FIELDS_EDGES, empty, "edges")
    return Result(_tail(data, pre), helps)


def _bad_reg_type(name: str, got: object = None) -> AxiError:
    known, find = f"Known types: {', '.join(KNOWN_REG_TYPES)}", "Use --search <text> instead"
    why = f"unknown registry type {name}" if got is None else f"Fusion's {name} is {got!r}"
    return AxiError("VALIDATION_ERROR", why, help=[known, find])


def reg_mask(app: Any, name: str) -> int:
    """The mask Fusion exposes as `fusion.CT_Tool`; the bridge may hand it over as a float."""
    value = getattr(app, name, None)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == int(value):
        return int(value)
    raise _bad_reg_type(name, "missing" if value is None else value)


def run_registry(inv: Invocation) -> Result:
    _check_list_flags(inv, FIELDS_REGISTRY)
    reg_type, search = str_arg(inv, "type"), str_arg(inv, "search") or ""
    if reg_type is not None and reg_type not in KNOWN_REG_TYPES:
        raise _bad_reg_type(reg_type)
    session = inv.connect()
    pre = preflight(session, inv)
    app = fusion_app(session)
    mask = () if reg_type is None else (reg_mask(app, reg_type),)
    entries = lua_values(fusion_call("GetRegList", app.GetRegList, *mask))
    ids = [
        (a.get("REGS_ID"), a.get("REGS_Name") or a.get("REGS_ID")) for a in map(attrs_of, entries)
    ]
    rows: list[dict[str, object]] = [
        {"id": i, "name": n} for i, n in ids if search.lower() in f"{i} {n}".lower()
    ]
    data: dict[str, object] = {"timeline": pre.timeline}
    data |= {k: v for k, v in (("type", reg_type), ("search", search)) if v}
    empty = empty_state("registry entries", "in Fusion", {"type": reg_type, "search": search})
    shown, helps = _block(inv, data, "registry", rows, FIELDS_REGISTRY, empty, "entries")
    data["shown"] = len(shown)
    return Result(_tail(data, pre), helps)


def _undo_name(entry: object) -> str:
    named = entry.get("Name") if isinstance(entry, dict) else None
    return str(named or entry)


def run_undo_stack(inv: Invocation) -> Result:
    _check_list_flags(inv, FIELDS_UNDO)
    _, pre, ref = _target(inv)
    names = [_undo_name(e) for e in lua_values(fusion_call("GetUndoStack", ref.comp.GetUndoStack))]
    rows: list[dict[str, object]] = [
        {"position": i, "name": n, "ours": n.startswith(UNDO_PREFIX)}
        for i, n in enumerate(names, start=1)
    ]
    data = _head(pre, ref)
    empty = empty_state("undo entries", f"in comp {ref.comp_name}", {})
    _, helps = _block(inv, data, "entries", rows, FIELDS_UNDO, empty, "entries")
    return Result(_tail(data, pre), helps)


COMP_METHODS = (
    "Resolve.Fusion",
    "TimelineItem.GetFusionCompNameList",
    "TimelineItem.GetFusionCompByIndex",
    "TimelineItem.GetFusionCompByName",
)

COMPS_SPEC = CommandSpec(
    name="comps",
    summary="List the Fusion comps of a timeline item and which one is active",
    run=run_comps,
    flags=ITEM_FLAGS,
    examples=(
        "davinci-axi fusion comps --item V1:2",
        "davinci-axi fusion comps --item V1:2 --json",
    ),
    methods=(
        "Resolve.Fusion",
        "TimelineItem.GetFusionCompCount",
        "TimelineItem.GetFusionCompNameList",
        "TimelineItem.GetIsFusionOutputCacheEnabled",
    ),
)

COMP_SHOW_SPEC = CommandSpec(
    name="show",
    summary="Show one comp: tool count, render and global range, file, lock and undo depth",
    run=run_comp_show,
    flags=FUSION_FLAGS,
    examples=(
        "davinci-axi fusion comp show --item V1:2",
        "davinci-axi fusion comp show --item V1:2 --comp 'Composition 1'",
    ),
    methods=COMP_METHODS,
)

COMP_SPEC = CommandSpec(
    name="comp",
    summary="Inspect one Fusion comp",
    subcommands={"show": COMP_SHOW_SPEC},
    examples=("davinci-axi fusion comp show --item V1:2",),
    methods=COMP_METHODS,
)

TOOLS_SPEC = CommandSpec(
    name="tools",
    summary="List the tools of a comp with registry id, passthrough and flow position",
    run=run_tools,
    flags=(
        *FUSION_FLAGS,
        Flag("selected", "bool", False, help="only the tools selected on the Fusion page"),
        Flag("type", "str", None, help="only tools of this registry id, such as Blur"),
        *LIST_FLAGS,
    ),
    examples=(
        "davinci-axi fusion tools --item V1:2",
        "davinci-axi fusion tools --active --selected",
    ),
    methods=COMP_METHODS,
)

TOOL_SHOW_SPEC = CommandSpec(
    name="show",
    summary="Show a tool's inputs (id, name, type, value, source) and outputs",
    run=run_tool_show,
    positionals=(Positional("name", help="tool name in the comp, such as Blur1"),),
    flags=(*FUSION_FLAGS, *LIST_FLAGS),
    examples=(
        "davinci-axi fusion tool show MediaOut1 --item V1:2",
        "davinci-axi fusion tool show Blur1 --item V1:2 --fields id,value --limit 20",
    ),
    notes=(
        "Image and Mask inputs have no value; `source` names the Tool.Output feeding them "
        "(null = unconnected), as `fusion graph` lists",
    ),
    methods=COMP_METHODS,
)

TOOL_GET_SPEC = CommandSpec(
    name="get",
    summary="Read one input of a tool, at the current time or at --time",
    run=run_tool_get,
    positionals=(
        Positional("name", help="tool name in the comp, such as Blur1"),
        Positional("input", help="input id, such as XBlurSize"),
    ),
    flags=(*FUSION_FLAGS, Flag("time", "float", None, help="frame to read the input at")),
    examples=(
        "davinci-axi fusion tool get Blur1 XBlurSize --item V1:2",
        "davinci-axi fusion tool get Blur1 XBlurSize --item V1:2 --time 24",
    ),
    methods=COMP_METHODS,
)

TOOL_SPEC = CommandSpec(
    name="tool",
    summary="Inspect one tool of a Fusion comp",
    subcommands={"show": TOOL_SHOW_SPEC, "get": TOOL_GET_SPEC},
    examples=("davinci-axi fusion tool show MediaOut1 --item V1:2",),
    methods=COMP_METHODS,
)

GRAPH_SPEC = CommandSpec(
    name="graph",
    summary="List the connections between tools as from, output, to, input edges",
    run=run_graph,
    flags=(*FUSION_FLAGS, *LIST_FLAGS),
    examples=("davinci-axi fusion graph --item V1:2", "davinci-axi fusion graph --active --json"),
    notes=("Edges are read from GetConnectedOutput: they are the wiring Fusion holds, not a plan",),
    methods=COMP_METHODS,
)

REGISTRY_SPEC = CommandSpec(
    name="registry",
    summary="List the installed Fusion registry entries (tool ids)",
    run=run_registry,
    flags=(
        *TIMELINE_FLAGS,
        Flag("type", "str", None, help=f"registry type: {', '.join(KNOWN_REG_TYPES)}"),
        Flag("search", "str", None, help="only ids or names containing this text, any case"),
        *LIST_FLAGS,
    ),
    examples=(
        "davinci-axi fusion registry",
        "davinci-axi fusion registry --type CT_Tool --limit 20",
        "davinci-axi fusion registry --search grain",
    ),
    notes=("Without --type Fusion lists all ~1400 entries; the data shows shown and total",),
    methods=("Resolve.Fusion",),
)

UNDO_STACK_SPEC = CommandSpec(
    name="undo-stack",
    summary="List the comp's undo entries, oldest first, and which are davinci-axi's",
    run=run_undo_stack,
    flags=(*FUSION_FLAGS, *LIST_FLAGS),
    examples=(
        "davinci-axi fusion undo-stack --item V1:2",
        "davinci-axi fusion undo-stack --active",
    ),
    methods=COMP_METHODS,
)

SPEC = CommandSpec(
    name="fusion",
    summary="Read Fusion comps, tools, wiring and the undo stack of a timeline item",
    subcommands={
        "comps": COMPS_SPEC,
        "comp": COMP_SPEC,
        "tools": TOOLS_SPEC,
        "tool": TOOL_SPEC,
        "graph": GRAPH_SPEC,
        "registry": REGISTRY_SPEC,
        "undo-stack": UNDO_STACK_SPEC,
    },
    examples=("davinci-axi fusion comps --item V1:2", "davinci-axi fusion graph --item V1:2"),
    methods=tuple(dict.fromkeys((*COMPS_SPEC.methods, *COMP_METHODS))),
)
