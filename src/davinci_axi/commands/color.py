"""`color graph|node|grade` and the `color` parent that assembles every colour verb (U19, R35).

The other verbs live beside this module: versions and groups in `color_items`; stills and albums
in `color_gallery`; CDL, cache, node colours, LUT and the thumbnail in `color_extras`; shared
plumbing (typed boundary, write helpers) in `color_common`. Every change is a dry run until
`--force` (`mutate.run_mutation`). Resolve has no getter for node enabled, grades or DRX, so those
verbs verify with the call's return value only (KTD23) and the output says so. Reads never switch
pages or timelines (R24).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from davinci_axi.commands import color_extras, color_gallery, color_items
from davinci_axi.commands.color_common import (
    ColorGroupApi,
    GraphApi,
    called_write,
    color_item,
    enum,
    item_write,
    node_number,
    parent,
    resolve_many,
    verb,
)
from davinci_axi.commands.item_detail import resolve_item, str_arg
from davinci_axi.commands.item_markers import ITEM_FLAGS, ITEM_ID, Target, open_target
from davinci_axi.commands.items import TIMELINE_FLAG
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import Change
from davinci_axi.output import collapse_home, empty_state
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

LAYER = Flag("layer", "int", None, help="node-stack layer, counted from 1 (default: the first)")
LAYER_FLAGS: tuple[Flag, ...] = (*ITEM_FLAGS, LAYER)
NODE = Positional("node", help="node number, counted from 1 (`color graph` lists them)")
CACHE_MODES = {
    "auto": "CACHE_AUTO_ENABLED",
    "enabled": "CACHE_ENABLED",
    "disabled": "CACHE_DISABLED",
}
GRADE_MODES = {"none": 0, "source-tc": 1, "start-frames": 2}
COLOR_ORDER = (
    "cdl",
    "version",
    "group",
    "cache",
    "node-colors",
    "lut",
    "still",
    "album",
    "thumbnail",
)
GRAPH_READS = (
    "TimelineItem.GetNodeGraph Graph.GetNumNodes Graph.GetNodeLabel Graph.GetToolsInNode "
    "Graph.GetLUT Graph.GetNodeCacheMode Project.GetCurrentTimeline Project.SetCurrentTimeline"
)


class GraphOwner(Protocol):
    def GetNodeGraph(self) -> GraphApi | None: ...


def layer_of(inv: Invocation) -> int | None:
    layer = inv.args.get("layer")
    if layer is not None and (not isinstance(layer, int) or layer < 1):
        raise AxiError(
            "VALIDATION_ERROR",
            "--layer must be 1 or more",
            help=["Layers are numbered from 1; the project setting nodeStackLayers is the maximum"],
        )
    return layer if isinstance(layer, int) else None


def layer_words(layer: int | None) -> list[str]:
    return [] if layer is None else ["--layer", str(layer)]


def graph_of(target: Target, layer: int | None) -> GraphApi:
    item = color_item(target)
    graph = item.GetNodeGraph() if layer is None else item.GetNodeGraph(layer)
    if graph is None:
        where = f"{target.ref} layer {layer}" if layer else target.ref
        raise not_found("node graph", where, "Check --layer against the setting nodeStackLayers")
    return graph


def check_node(graph: GraphApi, number: int) -> None:
    count = graph.GetNumNodes()
    if not 1 <= number <= count:
        raise not_found("node", str(number), f"This graph has {count} nodes (numbered from 1)")


def cache_names(session: Session) -> dict[int, str]:
    """`{int(resolve.CACHE_*): "auto"|"enabled"|"disabled"}` for the constants this Resolve has."""
    found: dict[int, str] = {}
    for word, name in CACHE_MODES.items():
        value = getattr(session.resolve, name, None)
        if isinstance(value, (int, float)):
            found[int(value)] = word
    return found


def node_rows(graph: GraphApi, names: dict[int, str]) -> list[dict[str, object]]:
    return [
        {
            "index": i,
            "label": graph.GetNodeLabel(i),
            "tools": ", ".join(graph.GetToolsInNode(i) or []),
            "lut": graph.GetLUT(i) or None,
            "cache": names.get(graph.GetNodeCacheMode(i), graph.GetNodeCacheMode(i)),
        }
        for i in range(1, graph.GetNumNodes() + 1)
    ]


def _check_graph_flags(inv: Invocation) -> None:
    item, group = str_arg(inv, "item"), str_arg(inv, "group")
    tl_graph = bool(inv.args.get("timeline_graph"))
    pre, post = bool(inv.args.get("pre")), bool(inv.args.get("post"))
    problem = ""
    if sum(map(bool, (item, tl_graph, group))) != 1:
        problem = "pass exactly one of --item, --timeline-graph and --group"
    elif group and pre == post:
        problem = "--group needs exactly one of --pre and --post"
    elif not group and (pre or post):
        problem = "--pre and --post only apply to --group"
    elif inv.args.get("layer") is not None and not item:
        problem = "--layer only applies to --item"
    if problem:
        raise AxiError(
            "VALIDATION_ERROR", problem, help=["Valid example: davinci-axi color graph --item V1:1"]
        )


def run_graph(inv: Invocation) -> Result:
    _check_graph_flags(inv)
    layer = layer_of(inv)
    session = inv.connect()
    group_name, ref = str_arg(inv, "group"), str_arg(inv, "item")
    graph: GraphApi | None
    if ref:
        timeline = session.require_timeline(str_arg(inv, "timeline"))
        target = Target(session, timeline, resolve_item(timeline, ref), ref)
        graph, label = graph_of(target, layer), target.label
    elif group_name:
        groups = cast(list[ColorGroupApi], session.require_project().GetColorGroupsList())
        found = next((g for g in groups if g.GetName() == group_name), None)
        if found is None:
            names = ", ".join(g.GetName() for g in groups) or "(none)"
            raise not_found("color group", group_name, f"Color groups in this project: {names}")
        side = "pre" if inv.args.get("pre") else "post"
        graph = found.GetPreClipNodeGraph() if side == "pre" else found.GetPostClipNodeGraph()
        label = f"color group {group_name} ({side}-clip)"
    else:
        timeline = session.require_timeline(str_arg(inv, "timeline"))
        graph = cast(GraphOwner, timeline).GetNodeGraph()
        label = f"timeline {timeline.GetName()}"
    if graph is None:
        raise api_failed("GetNodeGraph", f"{label} has no node graph")
    rows = node_rows(graph, cache_names(session))
    nodes = rows or empty_state("nodes", "in this graph", {})
    return Result({"graph": label, "count": len(rows), "nodes": nodes})


def _node_target(inv: Invocation) -> tuple[Target, GraphApi, int, int | None]:
    number = node_number(inv.positionals["node"])
    layer = layer_of(inv)
    target = open_target(inv)
    graph = graph_of(target, layer)
    check_node(graph, number)
    return target, graph, number, layer


def _run_enabled(inv: Invocation, enabled: bool) -> Result:
    target, graph, number, layer = _node_target(inv)
    word = "enable" if enabled else "disable"
    return called_write(
        inv,
        target,
        command=f"color node {word}",
        words=["color", "node", word, target.ref, str(number), *layer_words(layer)],
        call=lambda: graph.SetNodeEnabled(number, enabled),
        fail="Graph.SetNodeEnabled",
        extra={"node": number, "label": graph.GetNodeLabel(number)},
    )


def run_node_enable(inv: Invocation) -> Result:
    return _run_enabled(inv, True)


def run_node_disable(inv: Invocation) -> Result:
    return _run_enabled(inv, False)


def run_node_cache(inv: Invocation) -> Result:
    mode = inv.positionals["mode"]
    if mode not in CACHE_MODES:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown cache mode {mode}",
            help=[f"Valid modes: {', '.join(CACHE_MODES)}"],
        )
    target, graph, number, layer = _node_target(inv)
    value = enum(target.session, CACHE_MODES[mode])
    before, after = graph.GetNodeCacheMode(number), int(cast(float, value))
    return item_write(
        inv,
        target,
        command="color node cache",
        words=["color", "node", "cache", target.ref, str(number), mode, *layer_words(layer)],
        changes=[Change("cache", before, after)] if before != after else [],
        call=lambda: graph.SetNodeCacheMode(number, value),
        read=lambda: {"cache": graph.GetNodeCacheMode(number)},
        fail="Graph.SetNodeCacheMode",
        extra={"node": number, "mode": mode},
    )


def run_lut_set(inv: Invocation) -> Result:
    path_text = inv.positionals["path"]
    path = Path(path_text).expanduser().resolve()
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path of the LUT file")
    target, graph, number, layer = _node_target(inv)
    before, after = graph.GetLUT(number) or "", str(path)

    def read() -> dict[str, object]:
        now = graph.GetLUT(number) or ""
        # Resolve reports the LUT relative to its LUT folder, so compare file names.
        return {"lut": after if now and Path(now).name == path.name else now}

    words = ["color", "node", "lut", "set", target.ref, str(number), path_text]
    return item_write(
        inv,
        target,
        command="color node lut set",
        words=[*words, *layer_words(layer)],
        changes=[Change("lut", before, after)] if before != after else [],
        call=lambda: graph.SetLUT(number, after),
        read=read,
        fail="Graph.SetLUT",
        extra={"node": number},
    )


def run_lut_show(inv: Invocation) -> Result:
    target, graph, number, _ = _node_target(inv)
    label, lut = graph.GetNodeLabel(number), graph.GetLUT(number) or None
    return Result({"item": target.label, "node": number, "label": label, "lut": lut})


def run_apply_drx(inv: Invocation) -> Result:
    path = Path(inv.positionals["file"]).expanduser().resolve()
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path of the .drx file")
    mode, layer = str(inv.args.get("mode") or "none"), layer_of(inv)
    every = bool(inv.args.get("all_layers"))
    target = open_target(inv)
    graph = graph_of(target, layer)
    words = ["color", "grade", "apply-drx", target.ref, inv.positionals["file"], "--mode", mode]
    return called_write(
        inv,
        target,
        command="color grade apply-drx",
        words=[*words, *(["--all-layers"] if every else []), *layer_words(layer)],
        call=lambda: graph.ApplyGradeFromDRX(str(path), GRADE_MODES[mode], every),
        fail="Graph.ApplyGradeFromDRX",
        warning="replaces the grade of the item with the one in the DRX file",
        extra={"file": collapse_home(path), "mode": mode, "all_layers": every},
    )


def run_grade_reset(inv: Invocation) -> Result:
    layer = layer_of(inv)
    target = open_target(inv)
    graph = graph_of(target, layer)
    return called_write(
        inv,
        target,
        command="color grade reset",
        words=["color", "grade", "reset", target.ref, *layer_words(layer)],
        call=graph.ResetAllGrades,
        fail="Graph.ResetAllGrades",
        irreversible=True,
        warning="resets every grade of the item's graph; the snapshot is the only way back",
        extra={"nodes": graph.GetNumNodes()},
    )


def run_grade_copy(inv: Invocation) -> Result:
    target = open_target(inv)
    found = resolve_many(target.timeline, inv.positionals["targets"])
    source = target.located.item.GetUniqueId()
    if any(f.item.GetUniqueId() == source for f in found):
        raise AxiError(
            "VALIDATION_ERROR",
            "the source item is also in the target list",
            help=["Pass only the items that should receive the grade"],
        )
    return called_write(
        inv,
        target,
        command="color grade copy",
        words=["color", "grade", "copy", target.ref, inv.positionals["targets"]],
        call=lambda: color_item(target).CopyGrades([f.item for f in found]),
        fail="TimelineItem.CopyGrades",
        extra={"targets": ", ".join(f"{f.track}:{f.position}" for f in found)},
    )


def run_arri_cdl(inv: Invocation) -> Result:
    layer = layer_of(inv)
    target = open_target(inv)
    graph = graph_of(target, layer)
    return called_write(
        inv,
        target,
        command="color grade arri-cdl",
        words=["color", "grade", "arri-cdl", target.ref, *layer_words(layer)],
        call=graph.ApplyArriCdlLut,
        fail="Graph.ApplyArriCdlLut",
        warning="applies the ARRI CDL and LUT found in the clip's metadata",
    )


def _node_verb(
    name: str, summary: str, run: Callable[[Invocation], Result], write: str
) -> CommandSpec:
    example = f"davinci-axi color node {name} V1:1 2"
    methods = f"{GRAPH_READS} Graph.{write}"
    return verb(name, summary, run, (ITEM_ID, NODE), LAYER_FLAGS, example, methods)


NODE_LUT_SPEC = parent(
    "lut",
    "Show or set the LUT of a node",
    {
        "set": verb(
            "set",
            "Apply a LUT file to one node of an item",
            run_lut_set,
            (ITEM_ID, NODE, Positional("path", help="LUT file (.cube, .3dl, ...)")),
            LAYER_FLAGS,
            "davinci-axi color node lut set V1:1 1 ~/luts/film.cube",
            f"{GRAPH_READS} Graph.SetLUT",
        ),
        "show": verb(
            "show",
            "Show the LUT of one node of an item (read-only)",
            run_lut_show,
            (ITEM_ID, NODE),
            (TIMELINE_FLAG, LAYER),
            "davinci-axi color node lut show V1:1 1",
            GRAPH_READS,
            read=True,
        ),
    },
    "davinci-axi color node lut show V1:1 1",
)
NODE_SPEC = parent(
    "node",
    "Enable, disable, cache or set the LUT of a node",
    {
        "enable": _node_verb("enable", "Enable a node", run_node_enable, "SetNodeEnabled"),
        "disable": _node_verb("disable", "Disable a node", run_node_disable, "SetNodeEnabled"),
        "cache": verb(
            "cache",
            "Set the cache mode of a node: auto, enabled or disabled",
            run_node_cache,
            (ITEM_ID, NODE, Positional("mode", help="auto, enabled or disabled")),
            LAYER_FLAGS,
            "davinci-axi color node cache V1:1 2 enabled",
            f"{GRAPH_READS} Graph.SetNodeCacheMode",
        ),
        "lut": NODE_LUT_SPEC,
    },
    "davinci-axi color node cache V1:1 2 enabled|davinci-axi color node lut show V1:1 1",
)
GRADE_SPEC = parent(
    "grade",
    "Apply a DRX grade, reset, copy or apply the ARRI CDL of an item",
    {
        "apply-drx": verb(
            "apply-drx",
            "Apply the grade of a .drx still file to an item",
            run_apply_drx,
            (ITEM_ID, Positional("file", help="the .drx file")),
            (
                *LAYER_FLAGS,
                Flag("mode", "str", "none", choices=tuple(GRADE_MODES), help="keyframe alignment"),
                Flag("all-layers", "bool", False, help="apply to every node-stack layer"),
            ),
            "davinci-axi color grade apply-drx V1:1 ~/grades/look.drx",
            f"{GRAPH_READS} Graph.ApplyGradeFromDRX",
        ),
        "reset": verb(
            "reset",
            "Reset every grade of an item (irreversible except through a snapshot)",
            run_grade_reset,
            (ITEM_ID,),
            LAYER_FLAGS,
            "davinci-axi color grade reset V1:1",
            f"{GRAPH_READS} Graph.ResetAllGrades",
        ),
        "copy": verb(
            "copy",
            "Copy the grade of one item to other items",
            run_grade_copy,
            (ITEM_ID, Positional("targets", help="comma-separated items, e.g. V1:2,V1:3")),
            ITEM_FLAGS,
            "davinci-axi color grade copy V1:1 V1:2,V1:3",
            "TimelineItem.CopyGrades Project.GetCurrentTimeline Project.SetCurrentTimeline",
        ),
        "arri-cdl": verb(
            "arri-cdl",
            "Apply the ARRI CDL and LUT of an item's clip",
            run_arri_cdl,
            (ITEM_ID,),
            LAYER_FLAGS,
            "davinci-axi color grade arri-cdl V1:1",
            f"{GRAPH_READS} Graph.ApplyArriCdlLut",
        ),
    },
    "davinci-axi color grade copy V1:1 V1:2 --force|davinci-axi color grade reset V1:1",
)
GRAPH_SPEC = verb(
    "graph",
    "Show the nodes of an item, the timeline or a colour group graph (read-only)",
    run_graph,
    (),
    (
        TIMELINE_FLAG,
        Flag("item", "str", None, help="item id or <V|A|S><track>:<index> such as V1:1"),
        Flag("timeline-graph", "bool", False, help="the timeline's own node graph"),
        Flag("group", "str", None, help="a colour group name (with --pre or --post)"),
        Flag("pre", "bool", False, help="the group's pre-clip graph"),
        Flag("post", "bool", False, help="the group's post-clip graph"),
        LAYER,
    ),
    "davinci-axi color graph --item V1:1|davinci-axi color graph --item V1:1 --layer 2"
    "|davinci-axi color graph --group Hero --pre|davinci-axi color graph --timeline-graph",
    f"{GRAPH_READS} Timeline.GetNodeGraph Project.GetColorGroupsList "
    "ColorGroup.GetName ColorGroup.GetPreClipNodeGraph ColorGroup.GetPostClipNodeGraph",
    read=True,
)

_PARTS = {**color_items.SUBCOMMANDS, **color_gallery.SUBCOMMANDS, **color_extras.SUBCOMMANDS}
SUBCOMMANDS: dict[str, CommandSpec] = {
    "graph": GRAPH_SPEC,
    "node": NODE_SPEC,
    "grade": GRADE_SPEC,
    **{name: _PARTS[name] for name in COLOR_ORDER},
}
SPEC = parent(
    "color",
    "Inspect and change colour: node graphs, grades, versions, groups, stills, LUTs",
    SUBCOMMANDS,
    "davinci-axi color graph --item V1:1|davinci-axi color version add V1:1 alt --force"
    "|davinci-axi color still grab --force",
)
