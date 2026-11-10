"""`color graph` read view (R24): the node graph of an item, the timeline or a colour group.

Also home to the graph helpers the colour write verbs share (`layer_of`, `cache_names`).
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import cast

from davinci_axi.commands.color_common import CALLED_NOTE, GraphApi, verb
from davinci_axi.commands.color_targets import (
    TARGET,
    GraphTarget,
    Side,
    Step,
    TargetSpec,
    graph_of,
    open_targets,
    shared_graph,
    targets_write,
)
from davinci_axi.commands.item_detail import resolve_item, str_arg
from davinci_axi.commands.item_markers import ITEM_FLAGS, Target
from davinci_axi.commands.items import TIMELINE_FLAG
from davinci_axi.errors import AxiError
from davinci_axi.mutate import Change
from davinci_axi.output import collapse_home, empty_state
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.session import Session
from davinci_axi.spec import Flag, Invocation, Result

LAYER = Flag("layer", "int", None, help="node-stack layer, counted from 1 (default: the first)")
CACHE_MODES = {
    "auto": "CACHE_AUTO_ENABLED",
    "enabled": "CACHE_ENABLED",
    "disabled": "CACHE_DISABLED",
}
GRAPH_READS = (
    "TimelineItem.GetNodeGraph Graph.GetNumNodes Graph.GetNodeLabel Graph.GetToolsInNode "
    "Graph.GetLUT Graph.GetNodeCacheMode Project.GetCurrentTimeline Project.SetCurrentTimeline"
)
SHARED_READS = (
    "Timeline.GetNodeGraph Project.GetColorGroupsList ColorGroup.GetName "
    "ColorGroup.GetPreClipNodeGraph ColorGroup.GetPostClipNodeGraph"
)
TEMPLATE_NAME = "one-node.drx"
TRY_FLAG = Flag(
    "try", "bool", False, help="call ApplyGradeFromDRX anyway (for Resolve versions after 21.1.1)"
)
NOT_IN_21_1_1 = (
    "Resolve 21.1.1 does not create nodes from a DRX on an empty graph (F29): "
    "add one node in the Color page once (Timeline mode, or the group)"
)


def layer_of(inv: Invocation) -> int | None:
    layer = inv.args.get("layer")
    if layer is not None and (not isinstance(layer, int) or layer < 1):
        raise AxiError(
            "VALIDATION_ERROR",
            "--layer must be 1 or more",
            help=["Layers are numbered from 1; the project setting nodeStackLayers is the maximum"],
        )
    return layer if isinstance(layer, int) else None


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
    graph: GraphApi
    if ref:
        timeline = session.require_timeline(str_arg(inv, "timeline"))
        target = Target(session, timeline, resolve_item(timeline, ref), ref)
        graph, label = graph_of(target, layer), target.label
    elif group_name:
        side: Side = "pre" if inv.args.get("pre") else "post"
        # a group graph never reads the timeline, so no current timeline is needed here
        no_timeline = cast(TimelineApi, None)
        graph, label = shared_graph(
            session, no_timeline, TargetSpec("group", group=group_name, side=side)
        )
    else:
        timeline = session.require_timeline(str_arg(inv, "timeline"))
        graph, label = shared_graph(session, timeline, TargetSpec("timeline"))
    rows = node_rows(graph, cache_names(session))
    nodes = rows or empty_state("nodes", "in this graph", {})
    return Result({"graph": label, "count": len(rows), "nodes": nodes})


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


def template_path() -> Path | None:
    """The one-node DRX shipped in the wheel, or None while no build carries it yet."""
    found = Path(str(resources.files("davinci_axi").joinpath("data").joinpath(TEMPLATE_NAME)))
    return found if found.is_file() else None


def _no_template() -> AxiError:
    return AxiError(
        "NOT_FOUND",
        "no DRX template is shipped in this build",
        help=[
            "The shipped template is only useful with --try on a Resolve newer than 21.1.1; "
            "capture one live: on a clip whose graph has one untouched node run "
            "`color still grab --force`, then `color still export 1 <dir> --prefix one-node "
            "--format drx`, and pass it with --drx",
            "Target the items instead: `color node lut set V1:1,V1:3 1 <lut>`",
        ],
    )


def run_grade_init(inv: Invocation) -> Result:
    if not inv.args.get("try"):
        raise AxiError(
            "NOT_AVAILABLE",
            NOT_IN_21_1_1,
            help=[
                "Then `color node lut set timeline 1 <lut>` and the other node verbs work on "
                "that node",
                "Target the items instead: `color node lut set V1:1,V1:3 1 <lut>` (one snapshot)",
                "Pass --try to call ApplyGradeFromDRX anyway (a later Resolve may create the node)",
            ],
        )
    given = str_arg(inv, "drx")
    if given:
        drx = Path(given).expanduser().resolve()
        if not drx.is_file():
            raise AxiError(
                "NOT_FOUND",
                f"file not found: {collapse_home(drx)}",
                help=["Check the path of the .drx file"],
            )
    else:
        shipped = template_path()
        if shipped is None:
            raise _no_template()
        drx = shipped
    targets = open_targets(inv, graphs=True)
    if targets.spec.kind == "items":
        raise AxiError(
            "VALIDATION_ERROR",
            "init is for the timeline or a group graph; items always have a node",
            help=["Use `color grade apply-drx` to replace the grade of an item"],
        )
    for entry in targets.entries:
        count = entry.graph.GetNumNodes()
        if count > 0:
            raise AxiError(
                "VALIDATION_ERROR",
                f"{entry.label} already has {count} nodes",
                help=["`color grade apply-drx` replaces a grade; init only fills an empty graph"],
            )

    def step(e: GraphTarget) -> Step:
        return (
            [Change("nodes", 0, "1+")],
            lambda: e.graph.ApplyGradeFromDRX(str(drx), 0, False),
            lambda: {"nodes": "1+" if e.graph.GetNumNodes() >= 1 else 0},
        )

    words = ["color", "grade", "init", inv.positionals["target"]]
    return targets_write(
        inv,
        targets,
        command="color grade init",
        words=[*words, *(["--drx", given] if given else []), "--try"],
        step=step,
        fail="Graph.ApplyGradeFromDRX",
        extra={
            "read_back": CALLED_NOTE,
            "drx": collapse_home(drx),
            "template": not given,
            "tried": True,
        },
    )


GRADE_INIT_SPEC = verb(
    "init",
    "Apply a one-node DRX to an empty timeline or group graph "
    "(refused on 21.1.1: the API creates no node)",
    run_grade_init,
    (TARGET,),
    (
        *ITEM_FLAGS,
        Flag(
            "drx",
            "str",
            None,
            help="a .drx still to apply instead of the shipped one-node template",
        ),
        TRY_FLAG,
    ),
    "davinci-axi color grade init timeline --try|davinci-axi color grade init group:Hero:pre "
    "--drx ~/one-node.drx --try",
    f"{GRAPH_READS} {SHARED_READS} Graph.ApplyGradeFromDRX",
    notes=(
        NOT_IN_21_1_1 + "; init exits NOT_AVAILABLE without --try",
        "Observed live on 21.1.1 (F29): ApplyGradeFromDRX reports success on a 0-node timeline "
        "graph and the count stays 0",
        "With --try the result reads the node count back and fails VERIFY_FAILED when it stays 0",
    ),
)
