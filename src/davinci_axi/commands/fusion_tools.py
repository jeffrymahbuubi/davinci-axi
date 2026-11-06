"""`fusion tool add|set|connect|disconnect|rename|move|bypass|delete|save-settings|load-settings`.

Each verb edits one comp through `fusion_edit.comp_mutation` (project snapshot, comp snapshot, one
undo event, read-back); `save-settings` only writes a file. `TOOL_SPEC` wraps U17a's read-only
`fusion.TOOL_SPEC` (U17b).
"""

from __future__ import annotations

import shlex
from dataclasses import replace
from pathlib import Path
from typing import Any

from davinci_axi.commands import fusion
from davinci_axi.commands.fusion_edit import (
    ASSIGNMENTS,
    AUTO_POS,
    ITEM_EXAMPLE,
    MUT_FLAGS,
    PORT_TARGET,
    SETTINGS_FILE,
    TOOL_METHODS,
    TOOL_NAME,
    UNSETTABLE,
    Edit,
    comp_mutation,
    find_input,
    find_output,
    head_extra,
    mut_spec,
    open_comp,
    parse_pos,
    safe_lua,
    show_hint,
    source_label,
    split_port,
)
from davinci_axi.commands.item_detail import str_arg
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.fusion_guard import (
    FUSION_FLAGS,
    attrs_of,
    find_tool,
    fusion_call,
    preflight,
    resolve_comp,
    tool_name,
    tool_row,
    tools_of,
)
from davinci_axi.mutate import (
    Change,
    MutationPlan,
    coerce_like,
    parse_assignments,
    refuse_existing,
)
from davinci_axi.output import collapse_home
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result


def _next_name(existing: list[str], reg_id: str) -> str:
    number = 1
    while f"{reg_id}{number}" in existing:
        number += 1
    return f"{reg_id}{number}"


def run_add(inv: Invocation) -> Result:
    reg_id = safe_lua("tool id", inv.positionals["id"])
    name, after = str_arg(inv, "name"), str_arg(inv, "after")
    pos_text = str_arg(inv, "pos")
    if pos_text is not None and after is not None:
        raise AxiError(
            "VALIDATION_ERROR",
            "--pos and --after contradict each other",
            help=["--after lets Fusion place the tool next to it; pass one of them"],
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
    predicted = name or _next_name(existing, reg_id)
    words = ["fusion", "tool", "add", reg_id]
    for flag, value in (("--name", name), ("--pos", pos_text), ("--after", after)):
        words += [flag, value] if value is not None else []
    created: dict[str, str] = {}

    def body(plan: MutationPlan) -> None:
        if anchor is not None:
            fusion_call("SetActiveTool", comp.SetActiveTool, anchor)
        tool = fusion_call("AddTool", comp.AddTool, reg_id, x, y)
        if tool is None:
            raise api_failed("AddTool", f"Fusion did not add a {reg_id} tool")
        actual = tool_name(tool)
        if name is not None and actual != name:
            fusion_call("SetAttrs", tool.SetAttrs, {"TOOLS_Name": name})
            actual = name
        created["name"] = actual
        plan.changes[:] = [Change("tool", None, actual)]
        plan.next.insert(0, show_hint(inv, edit, actual))

    def verify() -> dict[str, object]:
        found = fusion_call("FindTool", comp.FindTool, created["name"])
        return {"tool": created["name"] if found is not None else None}

    return comp_mutation(
        inv, edit, "fusion tool add", words, [Change("tool", None, predicted)], body, verify
    )


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
        if data_type in UNSETTABLE or isinstance(live, (dict, list, tuple)):
            raise AxiError(
                "VALIDATION_ERROR",
                f"input {key} ({data_type or 'table'}) cannot be set from a text value",
                help=[
                    "Use `fusion tool connect` for images; point and table inputs are unsupported"
                ],
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
        return {key: fusion_call("GetInput", tool.GetInput, key) for key in wanted}

    nxt = [show_hint(inv, edit, label)]
    return comp_mutation(inv, edit, "fusion tool set", words, changes, body, verify, next_lines=nxt)


def _input_ref(
    inv: Invocation, edit: Edit, key: str, *, required: bool
) -> tuple[Any, str, Any, str]:
    """(tool, tool name, input port, input id) for a `Tool.Input` positional."""
    label, port_id = split_port(inv.positionals[key])
    tool = find_tool(edit.ref.comp, label)
    if port_id is None and required:
        raise AxiError(
            "VALIDATION_ERROR",
            f"{inv.positionals[key]} needs an input: <tool>.<input>",
            help=["Valid example: MediaOut1.Input"],
        )
    if port_id is None:
        main = fusion_call("FindMainInput", tool.FindMainInput, 1)
        if main is None:
            raise not_found("input", f"(main) on tool {label}", "Name one: <tool>.<input>")
        return tool, label, main, str(attrs_of(main).get("INPS_ID"))
    return tool, label, find_input(tool, label, port_id), port_id


def _rewire(
    inv: Invocation, edit: Edit, verb: str, target: str, source: Any, after: str | None
) -> Result:
    tool, label, port, input_id = _input_ref(inv, edit, target, required=verb == "disconnect")
    before = source_label(port)
    field = f"{label}.{input_id}"
    changes = [] if before == after else [Change(field, before, after)]
    words = ["fusion", "tool", verb, *(inv.positionals[k] for k in inv.positionals)]

    def body(_: MutationPlan) -> None:
        if fusion_call("ConnectInput", tool.ConnectInput, input_id, source) is False:
            raise api_failed("ConnectInput", f"Fusion refused to rewire {field}")

    def verify() -> dict[str, object]:
        return {field: source_label(find_input(tool, label, input_id))}

    return comp_mutation(inv, edit, f"fusion tool {verb}", words, changes, body, verify)


def run_connect(inv: Invocation) -> Result:
    edit = open_comp(inv)
    src_label, out_id = split_port(inv.positionals["source"])
    src_tool = find_tool(edit.ref.comp, src_label)
    output = find_output(src_tool, src_label, out_id)
    out_text = f"{src_label}.{attrs_of(output).get('OUTS_ID')}"
    return _rewire(inv, edit, "connect", "target", output, out_text)


def run_disconnect(inv: Invocation) -> Result:
    return _rewire(inv, open_comp(inv), "disconnect", "target", None, None)


def run_rename(inv: Invocation) -> Result:
    new = str(inv.args.get("to") or "").strip()
    if not new:
        raise AxiError(
            "VALIDATION_ERROR", "--to needs a new name", help=["Valid example: --to Soft"]
        )
    safe_lua("tool name", new)
    edit = open_comp(inv)
    comp, old = edit.ref.comp, inv.positionals["name"]
    tool = find_tool(comp, old)
    if new != old and fusion_call("FindTool", comp.FindTool, new) is not None:
        raise AxiError("VALIDATION_ERROR", f"a tool named {new} already exists")
    changes = [Change("name", old, new)] if new != old else []

    def body(_: MutationPlan) -> None:
        fusion_call("SetAttrs", tool.SetAttrs, {"TOOLS_Name": new})

    def verify() -> dict[str, object]:
        return {"name": tool_name(tool)}

    words = ["fusion", "tool", "rename", old, "--to", new]
    return comp_mutation(
        inv,
        edit,
        "fusion tool rename",
        words,
        changes,
        body,
        verify,
        next_lines=[show_hint(inv, edit, new)],
    )


def _flow(comp: Any) -> Any:
    frame = getattr(comp, "CurrentFrame", None)
    flow = getattr(frame, "FlowView", None) if frame is not None else None
    if flow is None:
        raise api_failed("FlowView", "no FlowView: load the comp on the Fusion page")
    return flow


def _pos_text(pos: object) -> str | None:
    if not isinstance(pos, tuple):
        return None
    return f"{pos[0]:g},{pos[1]:g}"


def run_move(inv: Invocation) -> Result:
    x, y = parse_pos(str(inv.args.get("pos") or ""))
    edit = open_comp(inv)
    comp, label = edit.ref.comp, inv.positionals["name"]
    tool = find_tool(comp, label)
    flow = _flow(comp)
    before = _pos_text(tool_row(comp, tool)["pos"])
    after = f"{x:g},{y:g}"

    def body(_: MutationPlan) -> None:
        fusion_call("SetPos", flow.SetPos, tool, x, y)

    def verify() -> dict[str, object]:
        return {"pos": _pos_text(tool_row(comp, tool)["pos"])}

    words = ["fusion", "tool", "move", label, "--pos", f"{x:g},{y:g}"]
    changes = [Change("pos", before, after)] if before != after else []
    return comp_mutation(inv, edit, "fusion tool move", words, changes, body, verify)


def run_bypass(inv: Invocation) -> Result:
    on, off = bool(inv.args.get("on")), bool(inv.args.get("off"))
    if on and off:
        raise AxiError(
            "VALIDATION_ERROR", "--on and --off contradict each other", help=["Pass one or none"]
        )
    edit = open_comp(inv)
    label = inv.positionals["name"]
    tool = find_tool(edit.ref.comp, label)
    current = bool(attrs_of(tool).get("TOOLB_PassThrough"))
    target = True if on else False if off else not current
    changes = [Change("passthrough", current, target)] if target != current else []

    def body(_: MutationPlan) -> None:
        fusion_call("SetAttrs", tool.SetAttrs, {"TOOLB_PassThrough": target})

    def verify() -> dict[str, object]:
        return {"passthrough": bool(attrs_of(tool).get("TOOLB_PassThrough"))}

    words = ["fusion", "tool", "bypass", label, "--on" if target else "--off"]
    return comp_mutation(inv, edit, "fusion tool bypass", words, changes, body, verify)


def run_delete(inv: Invocation) -> Result:
    edit = open_comp(inv)
    comp, label = edit.ref.comp, inv.positionals["name"]
    tool = find_tool(comp, label)

    def body(_: MutationPlan) -> None:
        fusion_call("Delete", tool.Delete)

    def verify() -> dict[str, object]:
        return {"tool": fusion_call("FindTool", comp.FindTool, label)}

    return comp_mutation(
        inv,
        edit,
        "fusion tool delete",
        ["fusion", "tool", "delete", label],
        [Change("tool", label, None)],
        body,
        verify,
        irreversible=True,
        warning="deleting a tool drops its connections; the comp snapshot below restores it",
    )


def run_save_settings(inv: Invocation) -> Result:
    path = Path(inv.positionals["file"]).expanduser().resolve()
    refuse_existing(path, bool(inv.args.get("overwrite")))
    session = inv.connect()
    pre = preflight(session, inv)
    ref = resolve_comp(session, inv, mutating=False)
    tool = find_tool(ref.comp, inv.positionals["name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if not fusion_call("SaveSettings", tool.SaveSettings, str(path)) or not path.is_file():
        raise api_failed(
            "SaveSettings", f"could not save the settings of {inv.positionals['name']}"
        )
    data = head_extra(pre, ref.item_ref, ref.comp_name)
    data.update({"tool": inv.positionals["name"], "path": collapse_home(path)})
    data["bytes"] = path.stat().st_size
    return Result(
        data, [f"Load them back with `fusion tool load-settings {shlex.quote(path.name)}`"]
    )


def run_load_settings(inv: Invocation) -> Result:
    path = Path(inv.positionals["file"]).expanduser().resolve()
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path; nothing was changed")
    edit = open_comp(inv)
    label = inv.positionals["name"]
    tool = find_tool(edit.ref.comp, label)
    loaded: dict[str, object] = {}

    def body(_: MutationPlan) -> None:
        loaded["done"] = bool(fusion_call("LoadSettings", tool.LoadSettings, str(path)))

    def verify() -> dict[str, object]:
        return {"settings_loaded": loaded.get("done", False)}

    words = ["fusion", "tool", "load-settings", label, inv.positionals["file"]]
    return comp_mutation(
        inv,
        edit,
        "fusion tool load-settings",
        words,
        [Change("settings_loaded", False, True)],
        body,
        verify,
        warning="the settings are not read back; check the tool with `fusion tool show`",
        next_lines=[show_hint(inv, edit, label)],
    )


TOOL_VERBS: dict[str, CommandSpec] = {
    "add": mut_spec(
        "add",
        "Add a tool to the comp (Fusion places and wires it unless --pos is given)",
        run_add,
        positionals=(Positional("id", help="registry id, such as Blur or Merge"),),
        flags=(
            Flag("name", "str", None, help="name for the new tool"),
            Flag("pos", "str", None, help="flow position x,y"),
            Flag("after", "str", None, help="connect the new tool after this tool"),
            *MUT_FLAGS,
        ),
        examples=(
            f"davinci-axi fusion tool add Blur {ITEM_EXAMPLE}",
            f"davinci-axi fusion tool add Blur {ITEM_EXAMPLE} --name Soft --force",
        ),
        methods=TOOL_METHODS,
    ),
    "set": mut_spec(
        "set",
        "Set tool inputs from <input>=<value> pairs and read them back",
        run_set,
        positionals=(TOOL_NAME, ASSIGNMENTS),
        flags=MUT_FLAGS,
        examples=(f"davinci-axi fusion tool set Blur1 XBlurSize=2 {ITEM_EXAMPLE} --force",),
        methods=TOOL_METHODS,
    ),
    "connect": mut_spec(
        "connect",
        "Connect a tool output to a tool input",
        run_connect,
        positionals=(Positional("source", help="<tool>[.<output>]"), PORT_TARGET),
        flags=MUT_FLAGS,
        examples=(f"davinci-axi fusion tool connect Blur1 MediaOut1.Input {ITEM_EXAMPLE} --force",),
        methods=TOOL_METHODS,
    ),
    "disconnect": mut_spec(
        "disconnect",
        "Disconnect a tool input",
        run_disconnect,
        positionals=(PORT_TARGET,),
        flags=MUT_FLAGS,
        examples=(f"davinci-axi fusion tool disconnect MediaOut1.Input {ITEM_EXAMPLE} --force",),
        methods=TOOL_METHODS,
    ),
    "rename": mut_spec(
        "rename",
        "Rename a tool",
        run_rename,
        positionals=(TOOL_NAME,),
        flags=(Flag("to", "str", None, required=True, help="new tool name"), *MUT_FLAGS),
        examples=(f"davinci-axi fusion tool rename Blur1 --to Soft {ITEM_EXAMPLE} --force",),
        methods=TOOL_METHODS,
    ),
    "move": mut_spec(
        "move",
        "Move a tool in the flow view (the comp must be loaded on the Fusion page)",
        run_move,
        positionals=(TOOL_NAME,),
        flags=(Flag("pos", "str", None, required=True, help="flow position x,y"), *MUT_FLAGS),
        examples=(f"davinci-axi fusion tool move Blur1 --pos 3,1 {ITEM_EXAMPLE} --force",),
        methods=TOOL_METHODS,
    ),
    "bypass": mut_spec(
        "bypass",
        "Toggle a tool's pass-through (or set it with --on or --off)",
        run_bypass,
        positionals=(TOOL_NAME,),
        flags=(
            Flag("on", "bool", False, help="bypass the tool"),
            Flag("off", "bool", False, help="stop bypassing the tool"),
            *MUT_FLAGS,
        ),
        examples=(f"davinci-axi fusion tool bypass Blur1 {ITEM_EXAMPLE} --force",),
        methods=TOOL_METHODS,
    ),
    "delete": mut_spec(
        "delete",
        "Delete a tool (irreversible; the comp snapshot restores it)",
        run_delete,
        positionals=(TOOL_NAME,),
        flags=MUT_FLAGS,
        examples=(f"davinci-axi fusion tool delete Blur1 {ITEM_EXAMPLE} --force",),
        methods=TOOL_METHODS,
    ),
    "save-settings": mut_spec(
        "save-settings",
        "Save a tool's settings to a file",
        run_save_settings,
        positionals=(TOOL_NAME, SETTINGS_FILE),
        flags=(*FUSION_FLAGS, Flag("overwrite", "bool", False, help="replace the file")),
        examples=(f"davinci-axi fusion tool save-settings Blur1 ~/blur.setting {ITEM_EXAMPLE}",),
        methods=TOOL_METHODS,
    ),
    "load-settings": mut_spec(
        "load-settings",
        "Load a settings file into a tool (not read back)",
        run_load_settings,
        positionals=(TOOL_NAME, SETTINGS_FILE),
        flags=MUT_FLAGS,
        examples=(
            f"davinci-axi fusion tool load-settings Blur1 ~/blur.setting {ITEM_EXAMPLE} --force",
        ),
        methods=TOOL_METHODS,
    ),
}

TOOL_SPEC = replace(
    fusion.TOOL_SPEC,
    summary="Inspect and edit the tools of a Fusion comp",
    subcommands={**fusion.TOOL_SPEC.subcommands, **TOOL_VERBS},
    reads_only=False,
    examples=(*fusion.TOOL_SPEC.examples, f"davinci-axi fusion tool add Blur {ITEM_EXAMPLE}"),
    methods=tuple(dict.fromkeys((*fusion.TOOL_SPEC.methods, *TOOL_METHODS))),
)
