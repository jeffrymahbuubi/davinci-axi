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
from typing import cast

from davinci_axi.commands import color_extras, color_gallery, color_items
from davinci_axi.commands.color_cdl_edl import CDL_EXT_SPEC
from davinci_axi.commands.color_common import (
    called_write,
    color_item,
    enum,
    node_number,
    parent,
    resolve_many,
    verb,
)
from davinci_axi.commands.color_graph import (
    CACHE_MODES,
    GRADE_INIT_SPEC,
    GRAPH_READS,
    GRAPH_SPEC,
    LAYER,
    layer_of,
)
from davinci_axi.commands.color_targets import (
    CALLED_NOTE,
    TARGET,
    GraphTarget,
    Step,
    Targets,
    called_step,
    check_node_each,
    field_for,
    graph_of,
    open_targets,
    targets_write,
)
from davinci_axi.commands.item_markers import ITEM_FLAGS, ITEM_ID, open_target
from davinci_axi.commands.items import TIMELINE_FLAG
from davinci_axi.errors import AxiError, not_found
from davinci_axi.mutate import Change
from davinci_axi.output import collapse_home
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

LAYER_FLAGS: tuple[Flag, ...] = (*ITEM_FLAGS, LAYER)
NODE = Positional("node", help="node number, counted from 1 (`color graph` lists them)")
GRADE_MODES = {"none": 0, "source-tc": 1, "start-frames": 2}
SHARED_READS = (
    "Timeline.GetNodeGraph Project.GetColorGroupsList ColorGroup.GetName "
    "ColorGroup.GetPreClipNodeGraph ColorGroup.GetPostClipNodeGraph"
)
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


def layer_words(layer: int | None) -> list[str]:
    return [] if layer is None else ["--layer", str(layer)]


def _node_target(inv: Invocation) -> tuple[Targets, int, int | None]:
    number = node_number(inv.positionals["node"])
    layer = layer_of(inv)
    targets = open_targets(inv, graphs=True, layer=layer)
    check_node_each(targets, number)
    return targets, number, layer


def _run_enabled(inv: Invocation, enabled: bool) -> Result:
    targets, number, layer = _node_target(inv)
    word = "enable" if enabled else "disable"

    def step(e: GraphTarget) -> Step:
        return called_step(targets, e, lambda: e.graph.SetNodeEnabled(number, enabled))

    return targets_write(
        inv,
        targets,
        command=f"color node {word}",
        words=["color", "node", word, inv.positionals["target"], str(number), *layer_words(layer)],
        step=step,
        fail="Graph.SetNodeEnabled",
        extra={
            "read_back": CALLED_NOTE,
            "node": number,
            "label": targets.entries[0].graph.GetNodeLabel(number),
        },
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
    targets, number, layer = _node_target(inv)
    value = enum(targets.session, CACHE_MODES[mode])
    after = int(cast(float, value))

    def step(e: GraphTarget) -> Step:
        name, before = field_for(targets, "cache", e), e.graph.GetNodeCacheMode(number)
        return (
            [Change(name, before, after)] if before != after else [],
            lambda: e.graph.SetNodeCacheMode(number, value),
            lambda: {name: e.graph.GetNodeCacheMode(number)},
        )

    return targets_write(
        inv,
        targets,
        command="color node cache",
        words=[
            "color",
            "node",
            "cache",
            inv.positionals["target"],
            str(number),
            mode,
            *layer_words(layer),
        ],
        step=step,
        fail="Graph.SetNodeCacheMode",
        extra={"node": number, "mode": mode},
    )


def run_lut_set(inv: Invocation) -> Result:
    path_text = inv.positionals["path"]
    path = Path(path_text).expanduser().resolve()
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path of the LUT file")
    targets, number, layer = _node_target(inv)
    after = str(path)
    warning = color_extras.lut_path_warning(path)

    def step(e: GraphTarget) -> Step:
        name, before = field_for(targets, "lut", e), e.graph.GetLUT(number) or ""

        def read() -> dict[str, object]:
            now = e.graph.GetLUT(number) or ""
            # Resolve reports the LUT relative to its LUT folder, so compare file names.
            return {name: after if now and Path(now).name == path.name else now}

        return (
            [Change(name, before, after)] if before != after else [],
            lambda: e.graph.SetLUT(number, after),
            read,
        )

    words = ["color", "node", "lut", "set", inv.positionals["target"], str(number), path_text]
    try:
        return targets_write(
            inv,
            targets,
            command="color node lut set",
            words=[*words, *layer_words(layer)],
            step=step,
            fail="Graph.SetLUT",
            warning=warning,
            extra={"node": number},
        )
    except AxiError as err:
        if err.code == "API_FAILED":
            err.help.insert(0, color_extras.INSTALL_HELP)
        raise


def run_lut_show(inv: Invocation) -> Result:
    targets, number, _ = _node_target(inv)
    if len(targets.entries) == 1:
        e = targets.entries[0]
        key = "item" if targets.spec.kind == "items" else "target"
        return Result(
            {
                key: e.label,
                "node": number,
                "label": e.graph.GetNodeLabel(number),
                "lut": _lut(e, number),
            }
        )
    rows = [
        {"target": e.label, "label": e.graph.GetNodeLabel(number), "lut": _lut(e, number)}
        for e in targets.entries
    ]
    return Result({"node": number, "luts": rows})


def _lut(entry: GraphTarget, number: int) -> str | None:
    return entry.graph.GetLUT(number) or None


def run_apply_drx(inv: Invocation) -> Result:
    path = Path(inv.positionals["file"]).expanduser().resolve()
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path of the .drx file")
    mode, layer = str(inv.args.get("mode") or "none"), layer_of(inv)
    every = bool(inv.args.get("all_layers"))
    targets = open_targets(inv, graphs=True, layer=layer)

    def step(e: GraphTarget) -> Step:
        return called_step(
            targets, e, lambda: e.graph.ApplyGradeFromDRX(str(path), GRADE_MODES[mode], every)
        )

    words = ["color", "grade", "apply-drx", inv.positionals["target"], inv.positionals["file"]]
    return targets_write(
        inv,
        targets,
        command="color grade apply-drx",
        words=[*words, "--mode", mode, *(["--all-layers"] if every else []), *layer_words(layer)],
        step=step,
        fail="Graph.ApplyGradeFromDRX",
        warning="replaces the grade of each target with the one in the DRX file",
        extra={
            "read_back": CALLED_NOTE,
            "file": collapse_home(path),
            "mode": mode,
            "all_layers": every,
        },
    )


def run_grade_reset(inv: Invocation) -> Result:
    layer = layer_of(inv)
    targets = open_targets(inv, graphs=True, layer=layer)
    counts = [(e.ref, e.graph.GetNumNodes()) for e in targets.entries]
    nodes: object = counts[0][1] if len(counts) == 1 else ", ".join(f"{r}: {n}" for r, n in counts)

    def step(e: GraphTarget) -> Step:
        return called_step(targets, e, e.graph.ResetAllGrades)

    return targets_write(
        inv,
        targets,
        command="color grade reset",
        words=["color", "grade", "reset", inv.positionals["target"], *layer_words(layer)],
        step=step,
        fail="Graph.ResetAllGrades",
        irreversible=True,
        warning="resets every grade of each target's graph; the snapshot is the only way back",
        extra={"read_back": CALLED_NOTE, "nodes": nodes},
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
    example = f"davinci-axi color node {name} V1:1 2|davinci-axi color node {name} timeline 1"
    methods = f"{GRAPH_READS} {SHARED_READS} Graph.{write}"
    return verb(name, summary, run, (TARGET, NODE), LAYER_FLAGS, example, methods)


NODE_LUT_SPEC = parent(
    "lut",
    "Show or set the LUT of a node",
    {
        "set": verb(
            "set",
            "Apply a LUT file to one node of an item, item list or shared graph",
            run_lut_set,
            (TARGET, NODE, Positional("path", help="LUT file (.cube, .3dl, ...)")),
            LAYER_FLAGS,
            "davinci-axi color node lut set V1:1 1 ~/luts/film.cube"
            "|davinci-axi color node lut set timeline 1 ~/luts/film.cube"
            "|davinci-axi color node lut set V1:1,V1:3 1 ~/luts/film.cube",
            f"{GRAPH_READS} {SHARED_READS} Graph.SetLUT",
            notes=(
                "Resolve only accepts LUTs it has discovered in its LUT folders (master folder or "
                "Preferences > System > General custom paths); the dry run warns about other paths",
                "A new timeline or group graph has 0 nodes until one is added in the Color page "
                "once (no API adds it on 21.1.1, F29); the item-list form `V1:1,V1:3` works now",
            ),
        ),
        "show": verb(
            "show",
            "Show the LUT of one node of an item, item list or shared graph (read-only)",
            run_lut_show,
            (TARGET, NODE),
            (TIMELINE_FLAG, LAYER),
            "davinci-axi color node lut show V1:1 1|davinci-axi color node lut show timeline 1",
            f"{GRAPH_READS} {SHARED_READS}",
            read=True,
        ),
    },
    "davinci-axi color node lut show V1:1 1",
)
NODE_SPEC = parent(
    "node",
    "Enable, disable, cache or set the LUT of a node",
    {
        "enable": _node_verb(
            "enable",
            "Enable a node of an item, item list or shared graph",
            run_node_enable,
            "SetNodeEnabled",
        ),
        "disable": _node_verb(
            "disable",
            "Disable a node of an item, item list or shared graph",
            run_node_disable,
            "SetNodeEnabled",
        ),
        "cache": verb(
            "cache",
            "Set the cache mode of a node of an item, item list or shared graph: "
            "auto, enabled or disabled",
            run_node_cache,
            (TARGET, NODE, Positional("mode", help="auto, enabled or disabled")),
            LAYER_FLAGS,
            "davinci-axi color node cache V1:1 2 enabled"
            "|davinci-axi color node cache group:Hero:pre 1 enabled",
            f"{GRAPH_READS} {SHARED_READS} Graph.SetNodeCacheMode",
        ),
        "lut": NODE_LUT_SPEC,
    },
    "davinci-axi color node cache V1:1 2 enabled|davinci-axi color node lut show V1:1 1",
    (
        "No API to add a node (AddNode does not exist) or to attach OFX/ResolveFX to one; "
        "build the node tree in the Color page, then address nodes by number here.",
        "Read a node tree back with `color graph --item V1:1`.",
        "A new timeline or group graph has 0 nodes until one is added in the Color page once "
        "(no API adds it on 21.1.1, F29); the item-list form `V1:1,V1:3` works right away.",
    ),
)
GRADE_SPEC = parent(
    "grade",
    "Apply a DRX grade, reset, copy or apply the ARRI CDL of an item",
    {
        "init": GRADE_INIT_SPEC,
        "apply-drx": verb(
            "apply-drx",
            "Apply the grade of a .drx still file to an item, item list or shared graph",
            run_apply_drx,
            (TARGET, Positional("file", help="the .drx file")),
            (
                *LAYER_FLAGS,
                Flag("mode", "str", "none", choices=tuple(GRADE_MODES), help="keyframe alignment"),
                Flag("all-layers", "bool", False, help="apply to every node-stack layer"),
            ),
            "davinci-axi color grade apply-drx V1:1 ~/grades/look.drx"
            "|davinci-axi color grade apply-drx group:Hero:post ~/grades/look.drx",
            f"{GRAPH_READS} {SHARED_READS} Graph.ApplyGradeFromDRX",
        ),
        "reset": verb(
            "reset",
            "Reset every grade of an item, item list or shared graph "
            "(irreversible except through a snapshot)",
            run_grade_reset,
            (TARGET,),
            LAYER_FLAGS,
            "davinci-axi color grade reset V1:1|davinci-axi color grade reset timeline",
            f"{GRAPH_READS} {SHARED_READS} Graph.ResetAllGrades",
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
    (
        "A .drx made from a hand-built grade (including OFX) is the scripted route to "
        "effects the API cannot add.",
    ),
)

_PARTS = {**color_items.SUBCOMMANDS, **color_gallery.SUBCOMMANDS, **color_extras.SUBCOMMANDS}
_PARTS["cdl"] = CDL_EXT_SPEC
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
    (
        "Resolve's scripting API cannot add OFX/ResolveFX to a node or add nodes: put a look "
        "on an existing node with `color node lut set` (.cube or .dctl) or apply a saved grade "
        "with `color grade apply-drx`.",
        "Shared looks: target `timeline` or group:<name>:pre|post, or a comma-separated item "
        "list, with one snapshot.",
    ),
)
