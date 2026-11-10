"""One target argument for the colour verbs (F8): an item, a list of items, the timeline graph
or a colour group graph.

`parse_target` reads the text, `open_targets` connects and resolves it to `Targets` (one
`GraphTarget` per graph), and `targets_write` runs one `MutationPlan` over all of them, so a
list of items gets one dry run, one snapshot and one read-back.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast

from davinci_axi.commands.color_common import CALLED_NOTE as CALLED_NOTE
from davinci_axi.commands.color_common import ColorGroupApi, GraphApi, color_item
from davinci_axi.commands.color_items import find_group
from davinci_axi.commands.item_detail import resolve_item, str_arg
from davinci_axi.commands.item_markers import Target, show_hint, tl_words
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    EPSILON,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
    with_current_timeline,
)
from davinci_axi.output import hint
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.session import Session
from davinci_axi.spec import Invocation, Positional, Result

TARGET = Positional(
    "target",
    help=(
        "item id or V1:2; a comma-separated list V1:1,V1:3,<uid>; `timeline` for the timeline "
        "graph; or group:<name>:pre|post for a colour group graph (what `color graph` shows)"
    ),
)
ITEMS = Positional(
    "target",
    help=(
        "item id or V1:2, or a comma-separated list V1:1,V1:3,<uid> "
        "(SetCDL is item-only: no `timeline` or group:<name>:pre|post here)"
    ),
)
TIMELINE_WORD = "timeline"
GROUP_PREFIX = "group:"
Kind = Literal["items", "timeline", "group"]
Side = Literal["pre", "post"]
LIST_SHOW = 4


def summarize_refs(refs: list[str]) -> str:
    """`V1:1 .. V1:45` for a long list; the plain list when it has at most LIST_SHOW refs."""
    if len(refs) <= LIST_SHOW:
        return ", ".join(refs)
    return f"{refs[0]} .. {refs[-1]}"


def empty_graph_help(spec: TargetSpec) -> list[str]:
    """F20: what to do when a timeline or group graph has no nodes in the API."""
    return [
        "A new timeline or group graph has 0 nodes in the API until a node is added once in "
        "the Color page (Timeline mode, or the group)",
        "Resolve 21.1.1 creates no node from a DRX either (F29), so `color grade init` refuses; "
        "add the node in the UI once and every node verb works on it",
        "Or target the items: `davinci-axi color node lut set V1:1,V1:3 1 <lut>` (one snapshot)",
    ]


class GraphOwner(Protocol):
    def GetNodeGraph(self) -> GraphApi | None: ...


@dataclass(frozen=True)
class TargetSpec:
    """The parsed text; no Resolve call has been made yet."""

    kind: Kind
    refs: tuple[str, ...] = ()
    group: str = ""
    side: Side | None = None


def _bad_target(message: str, *help_lines: str) -> AxiError:
    return AxiError("VALIDATION_ERROR", message, help=list(help_lines))


def parse_target(text: str, *, graphs: bool) -> TargetSpec:
    """Read a target. `graphs=False` accepts items only (SetCDL has no shared graph)."""
    text = text.strip()
    if text == TIMELINE_WORD or text.startswith(GROUP_PREFIX):
        if not graphs:
            raise _bad_target(
                f"{text} is not an item",
                "SetCDL is item-only: pass items, e.g. V1:1,V1:3",
                "Shared graphs (timeline, group:<name>:pre|post) work with color node ... "
                "and color grade ...",
            )
        if text == TIMELINE_WORD:
            return TargetSpec("timeline")
        name, sep, side = text[len(GROUP_PREFIX) :].rpartition(":")
        if not (sep and name and side in ("pre", "post")):
            raise _bad_target(
                f"{text} is not a colour group graph",
                "Valid example: group:Hero:pre (or :post)",
            )
        return TargetSpec("group", group=name, side=cast(Side, side))
    refs = tuple(dict.fromkeys(p.strip() for p in text.split(",") if p.strip()))
    if not refs:
        raise _bad_target(
            "no target given",
            "Valid example: V1:2 or V1:2,V1:3 (comma-separated, no spaces)",
        )
    return TargetSpec("items", refs=refs)


@dataclass
class GraphTarget:
    ref: str
    label: str
    graph: GraphApi
    target: Target | None


@dataclass
class Targets:
    session: Session
    timeline: TimelineApi
    spec: TargetSpec
    entries: list[GraphTarget] = field(default_factory=list)
    compact: bool = False

    @property
    def label(self) -> str:
        if len(self.entries) == 1:
            return self.entries[0].label
        count, name = len(self.entries), self.timeline.GetName()
        if self.compact and count > LIST_SHOW:
            return f"{count} items in timeline {name}"
        refs = ", ".join(e.ref for e in self.entries)
        return f"{count} items ({refs}) in timeline {name}"

    def graph_hint(self) -> str:
        spec = self.spec
        if spec.kind == "timeline":
            return hint("color graph --timeline-graph", "list the timeline graph's nodes")
        if spec.kind == "group":
            flags = f"--group {shlex.quote(spec.group)} --{spec.side}"
            return hint(f"color graph {flags}", "list the group graph's nodes")
        first = shlex.quote(self.entries[0].ref)
        return hint(f"color graph --item {first}", "list the item's nodes")


def graph_of(target: Target, layer: int | None) -> GraphApi:
    item = color_item(target)
    graph = item.GetNodeGraph() if layer is None else item.GetNodeGraph(layer)
    if graph is None:
        where = f"{target.ref} layer {layer}" if layer else target.ref
        raise not_found("node graph", where, "Check --layer against the setting nodeStackLayers")
    return graph


def shared_graph(session: Session, timeline: TimelineApi, spec: TargetSpec) -> tuple[GraphApi, str]:
    """The graph and label of a `timeline` or `group` target (a group never reads `timeline`)."""
    graph: GraphApi | None
    if spec.kind == "timeline":
        graph = cast(GraphOwner, timeline).GetNodeGraph()
        label = f"timeline {timeline.GetName()}"
    elif spec.kind == "group":
        groups = cast(list[ColorGroupApi], session.require_project().GetColorGroupsList())
        found = find_group(groups, spec.group)
        graph = found.GetPreClipNodeGraph() if spec.side == "pre" else found.GetPostClipNodeGraph()
        label = f"color group {spec.group} ({spec.side}-clip)"
    else:
        raise ValueError("shared_graph needs a timeline or group target")
    if graph is None:
        raise api_failed("GetNodeGraph", f"{label} has no node graph")
    return graph, label


def open_targets(inv: Invocation, *, graphs: bool, layer: int | None = None) -> Targets:
    """Connect and resolve the `target` positional into one `GraphTarget` per graph."""
    check_snapshot_flags(inv)
    spec = parse_target(inv.positionals["target"], graphs=graphs)
    if layer is not None and spec.kind != "items":
        raise _bad_target(
            "--layer only applies to items",
            "The timeline and colour group graphs have a single layer",
        )
    session = inv.connect()
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    targets = Targets(session, timeline, spec, compact=not inv.opts.json)
    if spec.kind == "items":
        for ref in spec.refs:
            target = Target(session, timeline, resolve_item(timeline, ref), ref)
            targets.entries.append(GraphTarget(ref, target.label, graph_of(target, layer), target))
    else:
        graph, label = shared_graph(session, timeline, spec)
        targets.entries.append(GraphTarget(inv.positionals["target"].strip(), label, graph, None))
    return targets


def field_for(targets: Targets, base: str, entry: GraphTarget) -> str:
    return base if len(targets.entries) == 1 else f"{base}[{entry.ref}]"


def check_node_each(targets: Targets, number: int) -> None:
    items = targets.spec.kind == "items"
    for entry in targets.entries:
        count = entry.graph.GetNumNodes()
        if not 1 <= number <= count:
            help_lines = empty_graph_help(targets.spec) if count == 0 and not items else []
            raise AxiError(
                "NOT_FOUND",
                f"node {number} not found: {entry.label} has {count} nodes (numbered from 1)",
                help=[*help_lines, "`davinci-axi color graph` lists the nodes of a graph"],
            )


def _group_changes(
    steps: list[tuple[GraphTarget, list[Change]]],
) -> tuple[list[Change], list[tuple[Change, list[tuple[str, str]]]]]:
    """Fold per-target rows `base[ref]` into `base[k items]` rows with equal before/after.

    Returns the grouped changes and, per group, its `(ref, original field)` members.
    """
    groups: dict[tuple[str, object, object], list[tuple[str, str]]] = {}
    for entry, changes in steps:
        for c in changes:
            key = (c.field.partition("[")[0], c.before, c.after)
            groups.setdefault(key, []).append((entry.ref, c.field))
    names = [f"{base}[{len(m)} items]" for (base, _, _), m in groups.items()]
    grouped: list[tuple[Change, list[tuple[str, str]]]] = []
    for name, ((base, before, after), members) in zip(names, groups.items(), strict=True):
        if names.count(name) > 1:
            name = f"{base}[{len(members)} items from {members[0][0]}]"
        grouped.append((Change(name, before, after), members))
    return [c for c, _ in grouped], grouped


def _matches(expected: object, actual: object) -> bool:
    if expected == actual:
        return True
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(expected) - float(actual)) <= EPSILON
    return str(expected) == str(actual)


Step = tuple[list[Change], Callable[[], bool], Callable[[], dict[str, object]]]


def called_step(targets: Targets, entry: GraphTarget, call: Callable[[], bool]) -> Step:
    """KTD23 step: a call with no getter; the change is `called: False -> True`."""
    name = field_for(targets, "called", entry)
    return [Change(name, False, True)], call, lambda: {name: True}


def targets_write(
    inv: Invocation,
    targets: Targets,
    *,
    command: str,
    words: list[str],
    step: Callable[[GraphTarget], Step],
    fail: str,
    irreversible: bool = False,
    warning: str = "",
    extra: dict[str, object] | None = None,
) -> Result:
    """One plan for every target: the calls run in order in one timeline block."""
    steps = [(e, *step(e)) for e in targets.entries]
    active = [(e, call, read) for e, changes, call, read in steps if changes]
    shown = extra if extra is not None else {}
    many = len(targets.entries) > 1
    refs = [e.ref for e in targets.entries]
    folded = many and targets.compact and len(refs) > LIST_SHOW
    if many:
        shown["targets"] = summarize_refs(refs) if targets.compact else refs
        shown["count"] = len(refs)
    items = targets.spec.kind == "items"
    rows = [c for _, changes, _, _ in steps for c in changes]
    groups: list[tuple[Change, list[tuple[str, str]]]] = []
    if folded:
        rows, groups = _group_changes([(e, changes) for e, changes, _, _ in steps])
    plan = MutationPlan(
        command=command,
        target=targets.label,
        changes=rows,
        force_cmd=force_cmd(inv, *words, *tl_words(inv)),
        irreversible=irreversible,
        warning=warning,
        extra=shown,
        next=[show_hint(inv, targets.entries[0].ref) if items else targets.graph_hint()],
    )

    def apply() -> None:
        with with_current_timeline(targets.session, targets.timeline) as switched:
            if switched:
                plan.extra["timeline_restored"] = True
            for i, (entry, call, _) in enumerate(active):
                if call():
                    continue
                err = api_failed(fail, f"Resolve refused the change on {entry.ref}")
                if many:
                    err.detail.update(
                        done=[e.ref for e, _, _ in active[:i]],
                        failed=entry.ref,
                        not_done=[e.ref for e, _, _ in active[i + 1 :]],
                    )
                raise err

    def verify() -> dict[str, object]:
        with with_current_timeline(targets.session, targets.timeline):
            merged: dict[str, object] = {}
            for _, _, read in active:
                merged.update(read())
            if not folded:
                return merged
            live: dict[str, object] = {}
            for change, members in groups:
                bad = next(
                    (
                        f"{ref}={merged.get(name, '(not read back)')}"
                        for ref, name in members
                        if name not in merged or not _matches(change.after, merged[name])
                    ),
                    None,
                )
                live[change.field] = change.after if bad is None else bad
            return live

    return run_mutation(inv, targets.session, plan, apply, verify)
