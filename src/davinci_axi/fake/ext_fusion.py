"""Fake Fusion for U17a: the Fusion app, comps, tools, ports and the TimelineItem comp methods.

A comp exists lazily for every name in `FakeTimelineItem.comps` and holds `MediaIn1 -> MediaOut1`.
Fusion objects are `FakeBase` subclasses with their own `CLASS` (`Fusion`, `FusionComp`,
`FusionTool`, ...), so their calls land in the world's call log; they are not in `WRITE_METHODS`, so
`FUSION_WRITES` and `fusion_write_calls` do that job (KTD28). `FakeComp.hang(method)` makes a method
wait on `world.hang_gate` after logging its call (any method that goes through `_rec`/`_ret`).
Imports only from `davinci_axi.fake.core|timeline|world` (KTD17); `extend()` is idempotent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, TypeVar, cast

from davinci_axi.fake.core import FakeResolve
from davinci_axi.fake.timeline import FakeTimelineItem
from davinci_axi.fake.world import HANG_SECONDS, FakeBase, FakeWorld

PLACEHOLDER = "fake fusion comp: "
CACHE_MODES = ("auto", "enabled", "disabled")
_STATE_KEY = "_axi_fusion"
T = TypeVar("T")

# Fusion-object writes (Class.Method), so a dry run can assert there were none.
_WRITES = {
    "FusionComp": (
        "AddTool AddToolAction Lock Unlock StartUndo EndUndo Undo Redo ClearUndo Execute "
        "ExecuteFile RunScript Paste SetAttrs SetData SetActiveTool Save"
    ),
    "FusionTool": (
        "SetInput ConnectInput Delete SetAttrs SaveSettings LoadSettings AddModifier SetData "
        "SetExpression"
    ),
    "FusionFlowView": "SetPos QueueSetPos FlushSetPosQueue Select",
    "FusionInput": "ConnectTo",
}
FUSION_WRITES: frozenset[str] = frozenset(
    f"{cls}.{name}" for cls, names in _WRITES.items() for name in names.split()
)

_REGS = {"CT_Tool": "MediaIn MediaOut Blur Merge Transform TextPlus", "CT_Modifier": "BezierSpline"}


class FakeFlowView(FakeBase):
    CLASS = "FusionFlowView"

    def __init__(self, world: FakeWorld) -> None:
        super().__init__(world)
        self.positions: dict[str, tuple[float, float]] = {}

    def GetPosTable(self, tool: FakeTool) -> dict[int, float]:
        x, y = self.positions.get(tool.name, (0.0, 0.0))
        return self._ret("GetPosTable", {1: x, 2: y}, tool)


class FakeOutput(FakeBase):
    CLASS = "FusionOutput"

    def __init__(self, world: FakeWorld, tool: FakeTool, output_id: str, data_type: str) -> None:
        super().__init__(world)
        self.tool = tool
        self.output_id = output_id
        self.data_type = data_type

    def GetAttrs(self) -> dict[str, Any]:
        name = self.output_id
        return self._ret(
            "GetAttrs", {"OUTS_ID": name, "OUTS_Name": name, "OUTS_DataType": self.data_type}
        )

    def GetTool(self) -> FakeTool:
        return self._ret("GetTool", self.tool)

    def GetConnectedInputs(self) -> dict[int, FakeInput]:
        found = [
            inp for tool in self.tool.comp.tools for inp in tool.inputs if inp.connected is self
        ]
        return self._ret("GetConnectedInputs", dict(enumerate(found, start=1)))


class FakeInput(FakeBase):
    CLASS = "FusionInput"

    def __init__(
        self, world: FakeWorld, tool: FakeTool, input_id: str, data_type: str, value: Any
    ) -> None:
        super().__init__(world)
        self.tool = tool
        self.input_id = input_id
        self.data_type = data_type
        self.value = value
        self.connected: FakeOutput | None = None

    def GetAttrs(self) -> dict[str, Any]:
        name = self.input_id
        return self._ret(
            "GetAttrs", {"INPS_ID": name, "INPS_Name": name, "INPS_DataType": self.data_type}
        )

    def GetConnectedOutput(self) -> FakeOutput | None:
        return self._ret("GetConnectedOutput", self.connected)

    def GetTool(self) -> FakeTool:
        return self._ret("GetTool", self.tool)


class FakeTool(FakeBase):
    CLASS = "FusionTool"

    def __init__(self, world: FakeWorld, comp: FakeComp, name: str, reg_id: str) -> None:
        super().__init__(world)
        self.comp = comp
        self.name = name
        self.reg_id = reg_id
        self.passthrough = False
        self.selected = False
        self.inputs: list[FakeInput] = []
        self.outputs: list[FakeOutput] = []

    def add_input(self, input_id: str, data_type: str, value: Any = None) -> FakeInput:
        """Build helper (not an API call): add an input."""
        inp = FakeInput(self._world, self, input_id, data_type, value)
        self.inputs.append(inp)
        return inp

    def add_output(self, output_id: str = "Output", data_type: str = "Image") -> FakeOutput:
        """Build helper (not an API call): add an output."""
        out = FakeOutput(self._world, self, output_id, data_type)
        self.outputs.append(out)
        return out

    def GetAttrs(self) -> dict[str, Any]:
        attrs = {"TOOLS_Name": self.name, "TOOLS_RegID": self.reg_id}
        return self._ret("GetAttrs", {**attrs, "TOOLB_PassThrough": self.passthrough})

    def GetInput(self, inputId: str, time: float | None = None) -> Any:
        found = next((i for i in self.inputs if i.input_id == inputId), None)
        return self._ret("GetInput", found.value if found else None, inputId, time)

    def GetInputList(self, dataType: str | None = None) -> dict[int, FakeInput]:
        inputs = [i for i in self.inputs if dataType in (None, "") or i.data_type == dataType]
        return self._ret("GetInputList", dict(enumerate(inputs, start=1)), dataType)

    def GetOutputList(self) -> dict[int, FakeOutput]:
        return self._ret("GetOutputList", dict(enumerate(self.outputs, start=1)))

    def FindMainInput(self, index: int) -> FakeInput | None:
        found = self.inputs[index - 1] if 1 <= index <= len(self.inputs) else None
        return self._ret("FindMainInput", found, index)

    def FindMainOutput(self, index: int) -> FakeOutput | None:
        found = self.outputs[index - 1] if 1 <= index <= len(self.outputs) else None
        return self._ret("FindMainOutput", found, index)


class FakeComp(FakeBase):
    CLASS = "FusionComp"

    def __init__(self, world: FakeWorld, item: FakeTimelineItem, name: str) -> None:
        super().__init__(world)
        self.item = item
        self.name = name
        self.tools: list[FakeTool] = []
        self.undo_stack: list[str] = []
        self.open_undo: str | None = None
        self.pending_undo = False
        self.lock_depth = 0
        self.hung: set[str] = set()
        self.CurrentTime = 0.0
        self.flow_view = FakeFlowView(world)
        self.CurrentFrame: SimpleNamespace | None = SimpleNamespace(FlowView=self.flow_view)
        self._build_default_tools()

    def _build_default_tools(self) -> None:
        media_in = self.add_tool_object("MediaIn1", "MediaIn", (0.0, 0.0))
        media_in.add_input("Layer", "Number", 0.0)
        source = media_in.add_output()
        media_out = self.add_tool_object("MediaOut1", "MediaOut", (3.0, 0.0))
        link = media_out.add_input("Input", "Image")
        media_out.add_input("Index", "Number", 0.0)
        media_out.add_output()
        link.connected = source

    def add_tool_object(self, name: str, reg_id: str, pos: tuple[float, float]) -> FakeTool:
        """Build helper (not an API call): add a tool with no ports."""
        tool = FakeTool(self._world, self, name, reg_id)
        self.tools.append(tool)
        self.flow_view.positions[name] = pos
        return tool

    def hang(self, method: str) -> None:
        """Make `method` wait on `world.hang_gate` when it is called."""
        self.hung.add(method)

    def _rec(self, method: str, *args: object) -> tuple[bool, object]:
        recorded = super()._rec(method, *args)
        if method in self.hung:
            self._world.hang_gate.wait(HANG_SECONDS)
        return recorded

    def push_undo(self, label: str) -> None:
        """Note a write: it joins the open undo event, or becomes its own event."""
        if self.open_undo is None:
            self.undo_stack.append(label)
        else:
            self.pending_undo = True

    def GetAttrs(self) -> dict[str, Any]:
        last = float(max(self.item.duration - 1, 0))
        attrs = {
            "COMPS_Name": self.name,
            "COMPS_FileName": f"Comp:/{self.name}.comp",
            "COMPN_RenderStart": 0.0,
            "COMPN_RenderEnd": last,
            "COMPN_GlobalStart": 0.0,
            "COMPN_GlobalEnd": last,
        }
        return self._ret("GetAttrs", attrs)

    def GetToolList(self, selected: bool = False, regid: str | None = None) -> dict[int, FakeTool]:
        injected, value = self._rec("GetToolList", selected, regid)
        if injected:
            return value if isinstance(value, dict) else {}
        found = [
            t
            for t in self.tools
            if (not selected or t.selected) and (not regid or t.reg_id == regid)
        ]
        return dict(enumerate(found, start=1))

    def FindTool(self, name: str) -> FakeTool | None:
        injected, value = self._rec("FindTool", name)
        if injected:
            return value if isinstance(value, FakeTool) else None
        return next((t for t in self.tools if t.name == name), None)

    def FindToolByID(self, regid: str, prev: FakeTool | None = None) -> FakeTool | None:
        matches = [t for t in self.tools if t.reg_id == regid]
        if prev is not None and prev in matches:
            matches = matches[matches.index(prev) + 1 :]
        return self._ret("FindToolByID", matches[0] if matches else None, regid, prev)

    def StartUndo(self, name: str) -> None:
        injected, _ = self._rec("StartUndo", name)
        if not injected:
            self.open_undo = name
            self.pending_undo = False

    def EndUndo(self, keep: bool) -> None:
        injected, _ = self._rec("EndUndo", keep)
        if injected:
            return
        if keep and self.pending_undo and self.open_undo is not None:
            self.undo_stack.append(self.open_undo)
        self.open_undo = None
        self.pending_undo = False

    def Undo(self, count: int = 1) -> bool:
        injected, value = self._rec("Undo", count)
        if injected:
            return bool(value)
        if count < 1 or count > len(self.undo_stack):
            return False
        del self.undo_stack[-count:]
        return True

    def GetUndoStack(self) -> list[str]:
        return self._ret("GetUndoStack", list(self.undo_stack))

    def Lock(self) -> None:
        injected, _ = self._rec("Lock")
        if not injected:
            self.lock_depth += 1

    def Unlock(self) -> None:
        injected, _ = self._rec("Unlock")
        if not injected:
            self.lock_depth = max(self.lock_depth - 1, 0)

    def IsLocked(self) -> bool:
        return self._ret("IsLocked", self.lock_depth > 0)


class FakeFusion(FakeBase):
    CLASS = "Fusion"

    def __init__(self, world: FakeWorld) -> None:
        super().__init__(world)
        self.current: FakeComp | None = None
        entries = [
            {"REGS_ID": rid, "REGS_Name": rid, "REGS_ClassType": kind}
            for kind, ids in _REGS.items()
            for rid in ids.split()
        ]
        self.regs = [SimpleNamespace(attrs=e, GetAttrs=lambda e=e: dict(e)) for e in entries]

    def GetCurrentComp(self) -> FakeComp | None:
        return self._ret("GetCurrentComp", self.current)

    def GetRegList(self, regType: str | None = None) -> dict[int, SimpleNamespace]:
        found = [r for r in self.regs if not regType or r.attrs["REGS_ClassType"] == regType]
        return self._ret("GetRegList", dict(enumerate(found, start=1)), regType)

    def GetRegAttrs(self, regId: str, regType: str | None = None) -> dict[str, Any] | None:
        match = next((r for r in self.regs if r.attrs["REGS_ID"] == regId), None)
        return self._ret("GetRegAttrs", dict(match.attrs) if match else None, regId, regType)


@dataclass
class ItemFusion:
    """Per-item Fusion state kept on the fake timeline item."""

    comps: dict[str, FakeComp] = field(default_factory=dict)
    cache: str = "auto"
    active: str | None = None


def _stash(owner: Any, factory: Callable[[], T]) -> T:
    """The object kept in `owner.__dict__`, created by `factory` on first use."""
    if _STATE_KEY not in owner.__dict__:
        owner.__dict__[_STATE_KEY] = factory()
    return cast(T, owner.__dict__[_STATE_KEY])


def _state(item: FakeTimelineItem) -> ItemFusion:
    return _stash(item, ItemFusion)


def fusion_of(world: FakeWorld) -> FakeFusion:
    """The world's Fusion app object, created on first use (no call is logged)."""
    return _stash(world.resolve, lambda: FakeFusion(world))


def comp_of(item: FakeTimelineItem, name: str | None = None) -> FakeComp:
    """The comp `name` (default: the first) of `item`, created lazily (no call is logged)."""
    names = item.comps
    wanted = name or (names[0] if names else None)
    if wanted is None or wanted not in names:
        raise LookupError(f"item {item.name} has no comp {wanted}")
    state = _state(item)
    comp = state.comps.get(wanted)
    if comp is None:
        comp = state.comps[wanted] = FakeComp(item._world, item, wanted)
    return comp


def fusion_write_calls(world: FakeWorld) -> list[str]:
    """`Class.Method` for every logged Fusion call that changes a comp or tool."""
    names = (f"{cls}.{method}" for cls, method, _ in world.calls)
    return [name for name in names if name in FUSION_WRITES]


def _resolve_fusion(self: FakeResolve) -> FakeFusion:
    self._rec("Fusion")
    return fusion_of(self._world)


def _as_comp(value: object) -> FakeComp | None:
    return value if isinstance(value, FakeComp) else None


def _item_method(name: str, coerce: Callable[[object], Any]) -> Callable[[Callable[..., Any]], Any]:
    """Wrap a TimelineItem method: log the call and return an injected value (coerced) if set."""

    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        def method(self: FakeTimelineItem, *args: object) -> Any:
            injected, value = self._rec(name, *args)
            return coerce(value) if injected else fn(self, *args)

        return method

    return wrap


def _next_name(item: FakeTimelineItem) -> str:
    number = len(item.comps) + 1
    while f"Composition {number}" in item.comps:
        number += 1
    return f"Composition {number}"


def _new_comp(item: FakeTimelineItem) -> FakeComp:
    name = _next_name(item)
    item.comps.append(name)
    _state(item).active = name
    return comp_of(item, name)


@_item_method("GetFusionCompByIndex", _as_comp)
def _by_index(self: FakeTimelineItem, compIndex: int) -> FakeComp | None:
    return comp_of(self, self.comps[compIndex - 1]) if 1 <= compIndex <= len(self.comps) else None


@_item_method("GetFusionCompByName", _as_comp)
def _by_name(self: FakeTimelineItem, compName: str) -> FakeComp | None:
    return comp_of(self, compName) if compName in self.comps else None


@_item_method("AddFusionComp", _as_comp)
def _add(self: FakeTimelineItem) -> FakeComp | None:
    return _new_comp(self)


@_item_method("ImportFusionComp", _as_comp)
def _import(self: FakeTimelineItem, path: str) -> FakeComp | None:
    """Quirk 2: on an item with no comp the import is a silent no-op (returns None)."""
    if not self.comps:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            handle.read()
    except OSError:
        return None
    return _new_comp(self)


@_item_method("ExportFusionComp", bool)
def _export(self: FakeTimelineItem, path: str, compIndex: int) -> bool:
    if not 1 <= compIndex <= len(self.comps):
        return False
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"{PLACEHOLDER}{self.comps[compIndex - 1]}\n")
    except OSError:
        return False
    return True


@_item_method("DeleteFusionCompByName", bool)
def _delete(self: FakeTimelineItem, compName: str) -> bool:
    if compName not in self.comps:
        return False
    self.comps.remove(compName)
    state = _state(self)
    state.comps.pop(compName, None)
    state.active = None if state.active == compName else state.active
    return True


@_item_method("LoadFusionCompByName", _as_comp)
def _load(self: FakeTimelineItem, compName: str) -> FakeComp | None:
    if compName not in self.comps:
        return None
    _state(self).active = compName
    return comp_of(self, compName)


@_item_method("RenameFusionCompByName", bool)
def _rename(self: FakeTimelineItem, oldName: str, newName: str) -> bool:
    if oldName not in self.comps or not newName or newName in self.comps:
        return False
    state = _state(self)
    self.comps[self.comps.index(oldName)] = newName
    comp = state.comps.pop(oldName, None)
    if comp is not None:
        comp.name = newName
        state.comps[newName] = comp
    state.active = newName if state.active == oldName else state.active
    return True


def _get_cache(self: FakeTimelineItem) -> str:
    return self._ret("GetIsFusionOutputCacheEnabled", _state(self).cache)


@_item_method("SetFusionOutputCache", bool)
def _set_cache(self: FakeTimelineItem, cacheValue: str) -> bool:
    if cacheValue not in CACHE_MODES:
        return False
    _state(self).cache = cacheValue
    return True


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeResolve, "Fusion"): _resolve_fusion,
    (FakeTimelineItem, "GetFusionCompByIndex"): _by_index,
    (FakeTimelineItem, "GetFusionCompByName"): _by_name,
    (FakeTimelineItem, "AddFusionComp"): _add,
    (FakeTimelineItem, "ImportFusionComp"): _import,
    (FakeTimelineItem, "ExportFusionComp"): _export,
    (FakeTimelineItem, "DeleteFusionCompByName"): _delete,
    (FakeTimelineItem, "LoadFusionCompByName"): _load,
    (FakeTimelineItem, "RenameFusionCompByName"): _rename,
    (FakeTimelineItem, "GetIsFusionOutputCacheEnabled"): _get_cache,
    (FakeTimelineItem, "SetFusionOutputCache"): _set_cache,
}


def extend() -> None:
    """Add Resolve.Fusion and the TimelineItem Fusion comp methods."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)
