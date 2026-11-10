"""Fake Fusion writes for U17b: tools, wiring, keyframes, settings files and timeline Fusion clips.

Builds on `ext_fusion` (the comps, tools and ports). Every write joins the comp's undo stack through
`FakeComp.push_undo` and is refused (returns None or False) while `edit_state(comp).locked_by_other`
is set. `SetInput` stores keyframes only on an input marked animated by `comp.Execute` with a
`BezierSpline{}` (quirk 3); otherwise the last value wins. `extend()` is idempotent.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

from davinci_axi.fake.ext_fusion import (
    FakeComp,
    FakeFlowView,
    FakeInput,
    FakeOutput,
    FakeTool,
    comp_of,
)
from davinci_axi.fake.insert_place import place_insert
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem

AUTO_POS = -32768.0
SETTINGS_PLACEHOLDER = "fake fusion settings: "
_KEY = "_axi_fusion_edit"
_SPLINE = re.compile(r'FindTool\("(\w+)"\):SetInput\("(\w+)", BezierSpline\{\}\)')
T = TypeVar("T")

# Text+ inputs; the ids are ASSUMED from Text+ .setting files (check: `fusion tool show` live)
_TEXTPLUS_INPUTS: tuple[tuple[str, str, Any], ...] = (
    ("StyledText", "Text", "Text"),
    ("Size", "Number", 0.08),
    ("Font", "Text", "Open Sans"),
    ("Style", "Text", "Regular"),
    ("Red1", "Number", 1.0),
    ("Green1", "Number", 1.0),
    ("Blue1", "Number", 1.0),
    ("Alpha1", "Number", 1.0),
    ("Center", "Point", {1: 0.5, 2: 0.5}),
)

# regId -> inputs (id, type, default) and output ids
_TEMPLATES: dict[str, tuple[tuple[tuple[str, str, Any], ...], tuple[str, ...]]] = {
    "Blur": (
        (
            ("Input", "Image", None),
            ("XBlurSize", "Number", 1.0),
            ("BlurType", "Number", 0.0),
            ("Blend", "Number", 1.0),
        ),
        ("Output",),
    ),
    "Merge": (
        (("Background", "Image", None), ("Foreground", "Image", None), ("Blend", "Number", 1.0)),
        ("Output",),
    ),
    "Transform": (
        (
            ("Input", "Image", None),
            ("Size", "Number", 1.0),
            ("Angle", "Number", 0.0),
            ("Center", "Point", {1: 0.5, 2: 0.5}),
        ),
        ("Output",),
    ),
    "TextPlus": (_TEXTPLUS_INPUTS, ("Output",)),
    "EllipseMask": (
        (
            ("Center", "Point", {1: 0.5, 2: 0.5}),
            ("Width", "Number", 0.5),
            ("Height", "Number", 0.5),
        ),
        ("Mask",),
    ),
    "RectangleMask": (
        (
            ("Center", "Point", {1: 0.5, 2: 0.5}),
            ("Width", "Number", 0.5),
            ("Height", "Number", 0.5),
        ),
        ("Mask",),
    ),
    "ObjectRemoval": (
        (
            ("Input", "Image", None),
            ("Mask", "Mask", None),
            ("SceneAnalysis", "Number", 0.0),
            ("BuildCleanPlate", "Number", 0.0),
            ("Strength", "Number", 1.0),
        ),
        ("Output",),
    ),
}
_OUTPUT_TYPES = {"EllipseMask": "Mask", "RectangleMask": "Mask"}
# Fusion names some tools by a shorter prefix than their registry id (mirrors the command).
_PRINTED_PREFIX = {"RectangleMask": "Rectangle"}


@dataclass
class CompEdit:
    """Edit state on a fake comp: another user holding it, the active tool, `Execute` strings."""

    locked_by_other: bool = False
    active_tool: FakeTool | None = None
    executed: list[str] = field(default_factory=list)


@dataclass
class InputEdit:
    """Keyframe state on a fake input."""

    animated: bool = False
    keys: dict[float, Any] = field(default_factory=dict)


def _stash(owner: Any, factory: Callable[[], T]) -> T:
    if _KEY not in owner.__dict__:
        owner.__dict__[_KEY] = factory()
    return cast(T, owner.__dict__[_KEY])


def edit_state(comp: FakeComp) -> CompEdit:
    """The edit state of `comp` (tests set `locked_by_other`)."""
    return _stash(comp, CompEdit)


def input_state(inp: FakeInput) -> InputEdit:
    """The keyframe state of `inp`."""
    return _stash(inp, InputEdit)


def _refused(comp: FakeComp) -> bool:
    return edit_state(comp).locked_by_other


def _find_input(tool: FakeTool, input_id: str) -> FakeInput | None:
    return next((i for i in tool.inputs if i.input_id == input_id), None)


def _unique_name(comp: FakeComp, reg_id: str) -> str:
    taken = {t.name for t in comp.tools}
    prefix = _PRINTED_PREFIX.get(reg_id, reg_id)
    number = 1
    while f"{prefix}{number}" in taken:
        number += 1
    return f"{prefix}{number}"


def _add_tool(
    self: FakeComp, regId: str, x: float = AUTO_POS, y: float = AUTO_POS
) -> FakeTool | None:
    injected, value = self._rec("AddTool", regId, x, y)
    if injected:
        return value if isinstance(value, FakeTool) else None
    template = _TEMPLATES.get(regId)
    if template is None or _refused(self):
        return None
    auto = x == AUTO_POS and y == AUTO_POS
    pos = (float(len(self.tools)), 1.0) if auto else (float(x), float(y))
    tool = self.add_tool_object(_unique_name(self, regId), regId, pos)
    for input_id, data_type, default in template[0]:
        tool.add_input(input_id, data_type, default)
    for output_id in template[1]:
        tool.add_output(output_id, _OUTPUT_TYPES.get(regId, "Image"))
    self.push_undo(f"Add Tool {tool.name}")
    return tool


def _set_active_tool(self: FakeComp, tool: FakeTool | None) -> None:
    injected, _ = self._rec("SetActiveTool", tool)
    if not injected:
        edit_state(self).active_tool = tool


def _execute(self: FakeComp, code: str) -> None:
    injected, _ = self._rec("Execute", code)
    if injected or _refused(self):
        return
    edit_state(self).executed.append(code)
    match = _SPLINE.search(code)
    if match:
        tool = next((t for t in self.tools if t.name == match.group(1)), None)
        inp = _find_input(tool, match.group(2)) if tool is not None else None
        if inp is not None:
            input_state(inp).animated = True
    self.push_undo("Execute")


def _value_at(inp: FakeInput, time: float | None) -> Any:
    state = input_state(inp)
    if not state.animated or not state.keys:
        return inp.value
    when = inp.tool.comp.CurrentTime if time is None else float(time)
    earlier = [key for key in state.keys if key <= when]
    return state.keys[max(earlier) if earlier else min(state.keys)]


def _get_input(self: FakeTool, inputId: str, time: float | None = None) -> Any:
    found = _find_input(self, inputId)
    return self._ret("GetInput", _value_at(found, time) if found else None, inputId, time)


def _set_input(self: FakeTool, inputId: str, value: Any, time: float | None = None) -> None:
    injected, _ = self._rec("SetInput", inputId, value, time)
    found = _find_input(self, inputId)
    if injected or found is None or _refused(self.comp):
        return
    state = input_state(found)
    if time is not None and state.animated:
        state.keys[float(time)] = value
    else:
        found.value = value
    self.comp.push_undo(f"Set {self.name}.{inputId}")


def _connect_input(self: FakeTool, inputId: str, source: Any) -> bool:
    injected, value = self._rec("ConnectInput", inputId, source)
    if injected:
        return bool(value)
    found = _find_input(self, inputId)
    if found is None or _refused(self.comp):
        return False
    if source is None:
        found.connected = None
    elif isinstance(source, FakeOutput):
        found.connected = source
    elif isinstance(source, FakeTool) and source.outputs:
        found.connected = source.outputs[0]
    else:
        return False
    self.comp.push_undo(f"Connect {self.name}.{inputId}")
    # `connect_returns_false` mimics live Fusion: the link is made but False comes back (F33)
    return not self.comp.__dict__.get("connect_returns_false", False)


def _delete(self: FakeTool) -> None:
    injected, _ = self._rec("Delete")
    if injected or _refused(self.comp) or self not in self.comp.tools:
        return
    mine = set(self.outputs)
    for tool in self.comp.tools:
        for inp in tool.inputs:
            if inp.connected in mine:
                inp.connected = None
    self.comp.tools.remove(self)
    self.comp.flow_view.positions.pop(self.name, None)
    self.comp.push_undo(f"Delete {self.name}")


def _set_attrs(self: FakeTool, attrs: dict[str, Any]) -> bool:
    injected, value = self._rec("SetAttrs", attrs)
    if injected:
        return bool(value)
    if _refused(self.comp) or not attrs:
        return False
    new = attrs.get("TOOLS_Name")
    if new is not None:
        if not new or any(t.name == new for t in self.comp.tools):
            return False
        positions = self.comp.flow_view.positions
        positions[str(new)] = positions.pop(self.name, (0.0, 0.0))
        self.name = str(new)
    if "TOOLB_PassThrough" in attrs:
        self.passthrough = bool(attrs["TOOLB_PassThrough"])
    self.comp.push_undo(f"Set attributes of {self.name}")
    return True


def _save_settings(self: FakeTool, path: str) -> bool:
    injected, value = self._rec("SaveSettings", path)
    if injected:
        return bool(value)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"{SETTINGS_PLACEHOLDER}{self.name}\n")
    except OSError:
        return False
    return True


def _load_settings(self: FakeTool, path: str) -> bool:
    injected, value = self._rec("LoadSettings", path)
    if injected:
        return bool(value)
    if _refused(self.comp):
        return False
    try:
        with open(path, encoding="utf-8") as handle:
            handle.read()
    except OSError:
        return False
    self.comp.push_undo(f"Load settings into {self.name}")
    return True


def _set_pos(self: FakeFlowView, tool: FakeTool, x: float, y: float) -> None:
    injected, _ = self._rec("SetPos", tool, x, y)
    if injected or _refused(tool.comp):
        return
    self.positions[tool.name] = (float(x), float(y))
    tool.comp.push_undo(f"Move {tool.name}")


def _create_fusion_clip(self: FakeTimeline, timelineItems: list[FakeTimelineItem]) -> Any:
    injected, value = self._rec("CreateFusionClip", timelineItems)
    if injected:
        return value if isinstance(value, FakeTimelineItem) else None
    if not timelineItems:
        return None
    first = timelineItems[0]
    start = min(i.start for i in timelineItems)
    end = max(i.start + i.duration for i in timelineItems)
    for item in timelineItems:
        for track in self.tracks.get(item.track_type, []):
            if item in track.items:
                track.items.remove(item)
    clip = self.add_item(first.track_type, first.track_index, "Fusion Clip", start, end - start)
    clip.comps.append("Composition 1")
    return clip


def _insert(self: FakeTimeline, name: str, kind: str) -> FakeTimelineItem | None:
    item = place_insert(self, name, kind)
    if item is not None:
        item.comps.append("Composition 1")
    return item


def _insert_composition(self: FakeTimeline) -> FakeTimelineItem | None:
    injected, value = self._rec("InsertFusionCompositionIntoTimeline")
    if injected:
        return value if isinstance(value, FakeTimelineItem) else None
    return _insert(self, "Fusion Composition", "Fusion Composition")


def _insert_generator(self: FakeTimeline, generatorName: str) -> FakeTimelineItem | None:
    injected, value = self._rec("InsertFusionGeneratorIntoTimeline", generatorName)
    if injected:
        return value if isinstance(value, FakeTimelineItem) else None
    return _insert(self, generatorName, "Generator")


def _insert_title(self: FakeTimeline, titleName: str) -> FakeTimelineItem | None:
    injected, value = self._rec("InsertFusionTitleIntoTimeline", titleName)
    if injected:
        return value if isinstance(value, FakeTimelineItem) else None
    item = _insert(self, titleName, "Title")
    if item is not None:  # live a Text+ title's comp holds a `Template` tool holding the text
        template = comp_of(item).add_tool_object("Template", "TextPlus", (1.0, 0.0))
        for row in _TEXTPLUS_INPUTS:
            template.add_input(*row)
        template.add_output("Output", "Image")
    return item


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeComp, "AddTool"): _add_tool,
    (FakeComp, "SetActiveTool"): _set_active_tool,
    (FakeComp, "Execute"): _execute,
    (FakeTool, "GetInput"): _get_input,
    (FakeTool, "SetInput"): _set_input,
    (FakeTool, "ConnectInput"): _connect_input,
    (FakeTool, "Delete"): _delete,
    (FakeTool, "SetAttrs"): _set_attrs,
    (FakeTool, "SaveSettings"): _save_settings,
    (FakeTool, "LoadSettings"): _load_settings,
    (FakeFlowView, "SetPos"): _set_pos,
    (FakeTimeline, "CreateFusionClip"): _create_fusion_clip,
    (FakeTimeline, "InsertFusionCompositionIntoTimeline"): _insert_composition,
    (FakeTimeline, "InsertFusionGeneratorIntoTimeline"): _insert_generator,
    (FakeTimeline, "InsertFusionTitleIntoTimeline"): _insert_title,
}


def extend() -> None:
    """Add the Fusion write methods to the fake comp, tool, flow view and timeline."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)
