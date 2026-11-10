"""Shared plumbing for the Fusion mutation verbs (R34, U17b).

Every comp-level mutation goes through `comp_mutation`: it checks the target (`--item` only,
`--active` is read-only), then `run_mutation` with the project snapshot policy `required`; its
`apply()` exports the comp to `snapshot_root()/fusion/` and runs the calls inside one undo event
(`undo_group`: Lock, StartUndo, ..., EndUndo, Unlock). Every call runs under a timeout
(`fusion_call`).
"""

from __future__ import annotations

import functools
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, cast

from davinci_axi.commands.item_detail import Located, resolve_item, str_arg
from davinci_axi.commands.render_frame import frame_to_timecode
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.fusion_guard import (
    FUSION_FLAGS,
    UNDO_PREFIX,
    CompRef,
    Preflight,
    TimelineItemFusion,
    attrs_of,
    comp_name_of,
    comp_snapshot_hint,
    export_comp,
    fusion_app,
    fusion_call,
    lua_values,
    preflight,
    resolve_comp,
    undo_group,
    validate_target,
)
from davinci_axi.fusion_guard import source_label as source_label  # re-export (F16)
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    SnapshotPolicy,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.output import collapse_home, hint
from davinci_axi.resolve_types import TimelineApi, TimelineItemApi
from davinci_axi.resolve_types_edit import ResolveEdit, TimelineEdit
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

VIA_PAGE = Flag(
    "via-page",
    "bool",
    False,
    help=(
        "edit the comp through the Fusion page (playhead to the item, open the page, use the comp "
        "it shows, restore the page), as the UI does; the dry run does not switch pages"
    ),
)
MUT_FLAGS: tuple[Flag, ...] = (*FUSION_FLAGS, VIA_PAGE, *MUTATION_FLAGS)
ITEM_FLAGS: tuple[Flag, ...] = (
    *(f for f in FUSION_FLAGS if f.name in ("item", "timeline", "active")),
    *MUTATION_FLAGS,
)
KEYFRAME_PAUSE = 0.12
AUTO_POS = -32768.0
UNSETTABLE = frozenset({"Image", "Mask"})
_LUA_NAME = re.compile(r"[A-Za-z0-9_]+")
SETTINGS_FILE = Positional("file", help="settings file (.setting)")
TOOL_NAME = Positional("name", help="tool name in the comp, such as Blur1")


@dataclass
class Edit:
    """The comp a mutation works on, with its session and preflight."""

    session: Session
    pre: Preflight
    ref: CompRef
    via_page: bool = False
    previous_page: str | None = None
    switched: bool = False
    page_restored: bool = False
    playhead_moved: bool = False


_PENDING: list[Edit] = []  # edits that left Resolve on the Fusion page; `mut_spec` restores them


def restore_page(edit: Edit) -> None:
    """Return Resolve to the page it was on before `--via-page`; at most once per edit."""
    if not edit.switched or edit.page_restored:
        return
    if edit.previous_page:
        app = cast(ResolveEdit, edit.session.resolve)
        edit.page_restored = bool(app.OpenPage(edit.previous_page))
    if edit in _PENDING:
        _PENDING.remove(edit)


def _restore_pending() -> None:
    for edit in list(_PENDING):
        restore_page(edit)
    _PENDING.clear()


def _enter_page(edit: Edit, *, force: bool) -> None:
    """Playhead to the item, Fusion page open, `edit.ref.comp` = the comp that page shows (D3).

    The checks run in the dry run too; the playhead and page only move with `--force`.
    """
    ref = edit.ref
    if ref.item is None:
        return
    timeline = cast(TimelineEdit, ref.timeline or edit.session.require_timeline(None))
    if ";" in timeline.GetStartTimecode():
        raise AxiError(
            "VALIDATION_ERROR",
            "--via-page cannot place the playhead on a drop-frame timeline",
            help=[
                "Use `timeline timecode set` to move the playhead, then edit with --active reads"
            ],
        )
    fps = float((timeline.GetSettings() or {}).get("timelineFrameRate") or 0)
    code = frame_to_timecode(int(ref.item.item.GetStart()), fps)
    if not force:
        return
    if timeline.GetCurrentTimecode() != code:
        if not timeline.SetCurrentTimecode(code):
            raise api_failed("SetCurrentTimecode", f"could not move the playhead to {code}")
        edit.playhead_moved = True
    app = cast(ResolveEdit, edit.session.resolve)
    current = app.GetCurrentPage()
    if current != "fusion":
        if not app.OpenPage("fusion"):
            raise api_failed("OpenPage", "could not switch to the fusion page")
        edit.previous_page, edit.switched = current, True
        _PENDING.append(edit)
    try:
        item = cast(ItemFusionEdit, ref.item.item)
        if ref.comp_index > 1:
            fusion_call("LoadFusionCompByName", item.LoadFusionCompByName, ref.comp_name)
        comp = fusion_call("GetCurrentComp", fusion_app(edit.session).GetCurrentComp)
        found = comp_name_of(comp) if comp is not None else "no comp"
        if comp is None or found != ref.comp_name:
            raise api_failed(
                "GetCurrentComp", f"the Fusion page shows {found}, not {ref.comp_name}"
            )
    except AxiError:
        restore_page(edit)
        raise
    edit.ref = replace(ref, comp=comp)


def open_comp(inv: Invocation) -> Edit:
    """Validate the target flags, connect, preflight and load the comp (`--item` only).

    With `--via-page` and `--force` the comp is the one the Fusion page shows (D3).
    """
    check_snapshot_flags(inv)
    validate_target(inv, mutating=True)
    session = inv.connect()
    pre = preflight(session, inv)
    edit = Edit(session, pre, resolve_comp(session, inv, mutating=True))
    edit.via_page = bool(inv.args.get("via_page"))
    if edit.via_page:
        _enter_page(edit, force=bool(inv.args.get("force")))
    return edit


def target_words(inv: Invocation, ref: CompRef) -> list[str]:
    """The flags that address `ref` again, for the `--force` line."""
    words = ["--item", ref.item_ref]
    if inv.args.get("via_page"):
        words.append("--via-page")
    if ref.comp_index > 1:
        words += ["--comp", ref.comp_name]
    timeline = str_arg(inv, "timeline")
    return words + (["--timeline", timeline] if timeline else [])


def head_extra(pre: Preflight, item_ref: str, comp_name: str | None) -> dict[str, object]:
    """The result keys every Fusion mutation carries (timeline, item, comp, process count)."""
    data: dict[str, object] = {"timeline": pre.timeline, "item": item_ref}
    if comp_name is not None:
        data["comp"] = comp_name
    data["resolvepython_processes"] = pre.resolvepython_processes
    if pre.warnings:
        data["warnings"] = list(pre.warnings)
    return data


def comp_mutation(
    inv: Invocation,
    edit: Edit,
    command: str,
    words: list[str],
    changes: list[Change],
    body: Callable[[MutationPlan], None],
    verify: Callable[[], dict[str, object]],
    *,
    lock: bool = True,
    group: bool = True,
    irreversible: bool = False,
    warning: str = "",
    next_lines: list[str] | None = None,
    extra: dict[str, object] | None = None,
) -> Result:
    """`run_mutation` for a comp edit: export the comp, then `body(plan)` in one undo event.

    With `--via-page` the page is restored when the edit ends, whichever way it ends.
    """
    ref = edit.ref
    plan = MutationPlan(
        command=command,
        target=f"comp {ref.comp_name} of item {ref.item_ref}",
        changes=changes,
        force_cmd=force_cmd(inv, *words, *target_words(inv, ref)),
        irreversible=irreversible,
        warning=warning,
        extra={**head_extra(edit.pre, ref.item_ref, ref.comp_name), **(extra or {})},
        next=list(next_lines or []),
    )
    if edit.via_page:
        plan.extra["via_page"] = True
    exported: list[Path] = []

    def apply() -> None:
        path = export_comp(ref, command)
        exported.append(path)
        plan.extra["comp_snapshot"] = collapse_home(path)
        plan.next.append(restore_hint(path, ref.item_ref))
        try:
            if group:
                with undo_group(ref.comp, UNDO_PREFIX + command, lock=lock):
                    body(plan)
            else:
                body(plan)
        except AxiError as err:
            err.detail.setdefault("comp_snapshot", collapse_home(path))
            err.help.append(restore_hint(path, ref.item_ref))
            raise

    try:
        result = run_mutation(inv, edit.session, plan, apply, verify)
    except AxiError as err:
        if exported and "comp_snapshot" not in err.detail:  # a VERIFY_FAILED after the apply
            err.detail["comp_snapshot"] = collapse_home(exported[0])
            err.help.append(restore_hint(exported[0], ref.item_ref))
        raise
    finally:
        restore_page(edit)
    if edit.switched:
        result.data["page_restored"] = edit.page_restored
        if not edit.page_restored:
            result.help.append(f"Open the {edit.previous_page} page again: Resolve did not switch")
    if edit.playhead_moved:
        result.data["playhead_moved"] = True
    return result


def restore_hint(path: Path, item_ref: str) -> str:
    """`comp_snapshot_hint` with the real item instead of the `<id>` placeholder."""
    return comp_snapshot_hint(path).replace("<id>", shlex.quote(item_ref))


def show_hint(inv: Invocation, edit: Edit, name: str) -> str:
    flags = " ".join(shlex.quote(w) for w in target_words(inv, edit.ref))
    return hint(f"fusion tool show {shlex.quote(name)} {flags}", "inspect the tool")


def safe_lua(kind: str, text: str) -> str:
    """Names that go into a Lua string must be plain identifiers."""
    if not _LUA_NAME.fullmatch(text):
        raise AxiError(
            "VALIDATION_ERROR",
            f"{kind} {text} has characters other than letters, digits and underscore",
            help=["Fusion tool and input names use only letters, digits and underscores"],
        )
    return text


def parse_pos(text: str) -> tuple[float, float]:
    parts = text.split(",")
    try:
        if len(parts) != 2:
            raise ValueError(text)
        return float(parts[0]), float(parts[1])
    except ValueError:
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot read {text} as a flow position",
            help=["Valid example: --pos 3,1 (x,y in the flow view)"],
        ) from None


def input_ports(tool: Any) -> list[Any]:
    return lua_values(fusion_call("GetInputList", tool.GetInputList))


def find_input(tool: Any, tool_label: str, input_id: str) -> Any:
    """The input port `input_id` of `tool`; NOT_FOUND lists the input ids."""
    ports = input_ports(tool)
    for port in ports:
        if attrs_of(port).get("INPS_ID") == input_id:
            return port
    ids = [str(attrs_of(p).get("INPS_ID")) for p in ports]
    shown = ", ".join(ids[:30]) + (", ..." if len(ids) > 30 else "")
    raise not_found("input", f"{input_id} on tool {tool_label}", f"Inputs on this tool: {shown}")


def find_output(tool: Any, tool_label: str, output_id: str | None) -> Any:
    outputs = lua_values(fusion_call("GetOutputList", tool.GetOutputList))
    ids = [str(attrs_of(o).get("OUTS_ID")) for o in outputs]
    if output_id is None and outputs:
        return outputs[0]
    for out, found in zip(outputs, ids, strict=True):
        if found == output_id:
            return out
    raise not_found(
        "output", f"{output_id or '(first)'} on tool {tool_label}", f"Outputs: {', '.join(ids)}"
    )


def split_port(text: str) -> tuple[str, str | None]:
    name, _, port = text.partition(".")
    return name, port or None


def mut_spec(
    name: str, summary: str, run: Callable[[Invocation], Result], **kw: Any
) -> CommandSpec:
    @functools.wraps(run)
    def guarded(inv: Invocation) -> Result:
        try:
            return run(inv)
        finally:  # an error between `open_comp` and `comp_mutation` must not strand the page
            _restore_pending()

    return CommandSpec(name=name, summary=summary, run=guarded, reads_only=False, **kw)


ITEM_EXAMPLE = "--item V1:2"
TOOL_METHODS = ("Resolve.Fusion", "TimelineItem.GetFusionCompByIndex")
ASSIGNMENTS = Positional(
    "assignments", help="comma-separated <input>=<value> pairs, such as XBlurSize=2"
)
PORT_TARGET = Positional("target", help="<tool>.<input>, such as MediaOut1.Input")


class ItemFusionEdit(TimelineItemFusion, Protocol):
    def AddFusionComp(self) -> Any | None: ...
    def ImportFusionComp(self, path: str) -> Any | None: ...
    def DeleteFusionCompByName(self, compName: str) -> bool: ...
    def LoadFusionCompByName(self, compName: str) -> Any | None: ...
    def RenameFusionCompByName(self, oldName: str, newName: str) -> bool: ...
    def SetFusionOutputCache(self, cacheValue: str) -> bool: ...


class TimelineFusionEdit(TimelineApi, Protocol):
    def CreateFusionClip(self, timelineItems: list[TimelineItemApi]) -> Any | None: ...
    def InsertFusionCompositionIntoTimeline(self) -> Any | None: ...
    def InsertFusionGeneratorIntoTimeline(self, generatorName: str) -> Any | None: ...
    def InsertFusionTitleIntoTimeline(self, titleName: str) -> Any | None: ...


@dataclass
class ItemEdit:
    """A timeline item (or the timeline alone) a Resolve-side verb works on."""

    session: Session
    timeline: TimelineApi
    pre: Any
    item_ref: str
    located: Located | None

    @property
    def item(self) -> ItemFusionEdit:
        if self.located is None:
            raise ValueError("this verb has no item")
        return cast(ItemFusionEdit, self.located.item)


def open_item(inv: Invocation, *, with_item: bool = True) -> ItemEdit:
    """Validate the flags, connect, preflight and resolve `--item` (or only the timeline)."""
    check_snapshot_flags(inv)
    if with_item:
        validate_target(inv, mutating=True)
    session = inv.connect()
    pre = preflight(session, inv)
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    ref = str_arg(inv, "item") or ""
    located = resolve_item(timeline, ref) if with_item else None
    return ItemEdit(session, timeline, pre, ref, located)


def item_flags(inv: Invocation, edit: ItemEdit) -> list[str]:
    words = ["--item", edit.item_ref] if edit.item_ref else []
    timeline = str_arg(inv, "timeline")
    return words + (["--timeline", timeline] if timeline else [])


def item_mutation(
    inv: Invocation,
    edit: ItemEdit,
    command: str,
    words: list[str],
    changes: list[Change],
    apply: Callable[[MutationPlan], None],
    verify: Callable[[], dict[str, object]],
    *,
    policy: SnapshotPolicy = "required",
    irreversible: bool = False,
    warning: str = "",
    extra: dict[str, object] | None = None,
) -> Result:
    """`run_mutation` for a Resolve-side Fusion verb (no undo group: Fusion cannot undo it)."""
    label = f"item {edit.item_ref}" if edit.item_ref else f"timeline {edit.pre.timeline}"
    plan = MutationPlan(
        command=command,
        target=label,
        changes=changes,
        force_cmd=force_cmd(inv, *words, *item_flags(inv, edit)),
        irreversible=irreversible,
        warning=warning,
        snapshot=policy,
        extra={**head_extra(edit.pre, edit.item_ref, None), **(extra or {})},
    )
    return run_mutation(inv, edit.session, plan, lambda: apply(plan), verify)


def comp_names(item: ItemFusionEdit) -> list[str]:
    return [str(n) for n in fusion_call("GetFusionCompNameList", item.GetFusionCompNameList) or []]
