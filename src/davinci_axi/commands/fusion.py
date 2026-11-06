"""`fusion`: read Fusion comps, tools, wiring, the tool registry and the undo stack (R34, U17a).

Every verb is read-only: no `--force`, no snapshot, no page switch (R24). A comp is addressed by
`--item` (+ `--comp`) or, for reads only, `--active`. Each verb first checks that the target
timeline is current (`preflight`) and runs every Fusion call under a timeout (`fusion_call`, quirk
1); results carry `resolvepython_processes` and any `warnings`. U17b wraps `SPEC` with the
mutating verbs.
"""

from __future__ import annotations

import shlex
from typing import Any, cast

from davinci_axi.commands.item_detail import resolve_item, str_arg
from davinci_axi.errors import AxiError
from davinci_axi.fusion_guard import (
    FUSION_FLAGS,
    UNDO_PREFIX,
    CompRef,
    Preflight,
    TimelineItemFusion,
    attrs_of,
    find_tool,
    fusion_app,
    fusion_call,
    lua_values,
    preflight,
    resolve_comp,
    tool_row,
    tools_of,
    validate_target,
)
from davinci_axi.output import apply_limit, empty_state, hint, select_fields
from davinci_axi.session import Session
from davinci_axi.spec import LIST_FLAGS, CommandSpec, Flag, Invocation, Positional, Result

ITEM_FLAGS = tuple(f for f in FUSION_FLAGS if f.name in ("item", "timeline"))
TIMELINE_FLAGS = tuple(f for f in FUSION_FLAGS if f.name == "timeline")
FIELDS_TOOLS = ("name", "id", "passthrough", "x", "y")
FIELDS_INPUTS = ("id", "name", "type", "value")
FIELDS_EDGES = ("from", "output", "to", "input")
FIELDS_REGISTRY = ("id", "name")
FIELDS_UNDO = ("position", "name", "ours")
UNREADABLE_TYPES = frozenset({"Image", "Mask"})


def _scalar(value: object) -> object:
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def _requested(inv: Invocation) -> list[str] | None:
    raw = inv.args.get("fields")
    return [str(f) for f in raw] if isinstance(raw, list) else None


def _limit(inv: Invocation) -> int:
    raw = inv.args.get("limit")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else 100


def _check_list_flags(inv: Invocation, default: tuple[str, ...]) -> None:
    """Validate `--fields` and `--limit` before the first Resolve call (R11)."""
    select_fields([dict.fromkeys(default)], default, _requested(inv))
    apply_limit([], _limit(inv))


def _target(inv: Invocation) -> tuple[Session, Preflight, CompRef]:
    validate_target(inv, mutating=False)
    session = inv.connect()
    pre = preflight(session, inv)
    return session, pre, resolve_comp(session, inv, mutating=False)


def _head(pre: Preflight, ref: CompRef) -> dict[str, object]:
    data: dict[str, object] = {"timeline": pre.timeline}
    if ref.item_ref:
        data["item"] = ref.item_ref
    data["comp"] = ref.comp_name
    return data


def _tail(data: dict[str, object], pre: Preflight) -> dict[str, object]:
    data["resolvepython_processes"] = pre.resolvepython_processes
    if pre.warnings:
        data["warnings"] = list(pre.warnings)
    return data


def _target_flags(ref: CompRef) -> str:
    """The flags that address `ref` again, for next-step hints."""
    if not ref.item_ref:
        return "--active"
    flags = f"--item {shlex.quote(ref.item_ref)}"
    return flags if ref.comp_index <= 1 else f"{flags} --comp {shlex.quote(ref.comp_name)}"


def _block(
    inv: Invocation,
    data: dict[str, object],
    key: str,
    rows: list[dict[str, object]],
    default: tuple[str, ...],
    empty: str,
    noun: str,
    total_key: str = "total",
) -> tuple[list[dict[str, object]], list[str]]:
    """Put the limited rows (or the empty-state text) under `key`; also return the --limit hint."""
    shown, total = apply_limit(rows, _limit(inv))
    data[total_key] = total
    data[key] = select_fields(shown, default, _requested(inv)) if shown else empty
    more = total > len(shown)
    return shown, [f"Showing {len(shown)} of {total} {noun}; raise --limit to see more"] * more


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
        first = f"fusion tools --item {shlex.quote(item_ref)}"
        helps = [hint(first, "list the tools in the first comp")]
    else:
        data["comps"] = empty_state("comps", f"on item {item_ref}", {})
        helps = ["Create a comp for the item in Resolve first (Fusion page or Inspector)"]
    return Result(_tail(data, pre), helps)


def run_comp_show(inv: Invocation) -> Result:
    _, pre, ref = _target(inv)
    attrs = attrs_of(ref.comp)
    data = _head(pre, ref)
    data["tools"] = len(tools_of(ref.comp))
    for key in ("render_start", "render_end", "global_start", "global_end"):
        data[key] = _scalar(attrs.get("COMPN_" + key.title().replace("_", "")))
    data["file"] = _scalar(attrs.get("COMPS_FileName"))
    data["locked"] = bool(fusion_call("IsLocked", ref.comp.IsLocked))
    data["undo_depth"] = len(lua_values(fusion_call("GetUndoStack", ref.comp.GetUndoStack)))
    flags = _target_flags(ref)
    return Result(
        _tail(data, pre),
        [hint(f"fusion tools {flags}", "list the tools of this comp")],
    )


def run_tools(inv: Invocation) -> Result:
    _check_list_flags(inv, FIELDS_TOOLS)
    _, pre, ref = _target(inv)
    selected = bool(inv.args.get("selected"))
    regid = str_arg(inv, "type")
    call_args: list[object] = [selected]
    if regid is not None:
        call_args.append(regid)
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
            }
        )
    return rows


def _output_rows(tool: Any) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for port in lua_values(fusion_call("GetOutputList", tool.GetOutputList)):
        attrs = attrs_of(port)
        linked = lua_values(fusion_call("GetConnectedInputs", port.GetConnectedInputs))
        rows.append(
            {
                "id": attrs.get("OUTS_ID"),
                "name": attrs.get("OUTS_Name"),
                "connected": len(linked) > 0,
            }
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
    data["tool"] = inv.positionals["name"]
    data["input"] = input_id
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


def _registry_row(entry: Any) -> dict[str, object]:
    attrs = attrs_of(entry)
    reg_id = attrs.get("REGS_ID")
    return {"id": reg_id, "name": attrs.get("REGS_Name") or reg_id}


def run_registry(inv: Invocation) -> Result:
    _check_list_flags(inv, FIELDS_REGISTRY)
    session = inv.connect()
    pre = preflight(session, inv)
    app = fusion_app(session)
    reg_type = str_arg(inv, "type") or ""
    rows = [
        _registry_row(e) for e in lua_values(fusion_call("GetRegList", app.GetRegList, reg_type))
    ]
    data: dict[str, object] = {"timeline": pre.timeline}
    empty = empty_state("registry entries", "in Fusion", {"type": reg_type})
    _, helps = _block(inv, data, "registry", rows, FIELDS_REGISTRY, empty, "entries")
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
    summary="Show a tool's inputs (id, name, type, value) and outputs",
    run=run_tool_show,
    positionals=(Positional("name", help="tool name in the comp, such as Blur1"),),
    flags=(*FUSION_FLAGS, *LIST_FLAGS),
    examples=(
        "davinci-axi fusion tool show MediaOut1 --item V1:2",
        "davinci-axi fusion tool show Blur1 --item V1:2 --fields id,value --limit 20",
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
    methods=COMP_METHODS,
)

REGISTRY_SPEC = CommandSpec(
    name="registry",
    summary="List the installed Fusion registry entries (tool ids)",
    run=run_registry,
    flags=(
        *TIMELINE_FLAGS,
        Flag("type", "str", None, help="registry type, such as CT_Tool"),
        *LIST_FLAGS,
    ),
    examples=(
        "davinci-axi fusion registry",
        "davinci-axi fusion registry --type CT_Tool --limit 20",
    ),
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
