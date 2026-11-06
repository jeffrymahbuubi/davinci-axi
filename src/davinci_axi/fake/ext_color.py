"""Fake colour for U19: node graphs, colour groups, colour versions, CDL, grade copy, caches.

The gallery, stills, thumbnails and LUT export live in `ext_color_gallery`; `extend()` here calls
its `extend()` too, so the lead registers only `ext_color.extend`. Imports only from
`davinci_axi.fake.core|timeline|world`, never from the `davinci_axi.fake` package (KTD17). Each
write method keeps the `_rec` injection contract: an injected value is returned (as `bool` unless
the real call returns an object), otherwise the side effect happens. `extend()` is idempotent.

State that the core fake does not model lives in each object's `__dict__` (`graphs`, `cdl`,
`color_group`, `current_version`, `color_cache`, `color_groups`), created lazily.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from davinci_axi.fake.core import FakeProject
from davinci_axi.fake.ext_color_gallery import extend as extend_gallery
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem
from davinci_axi.fake.world import FakeBase, FakeWorld

NODE_STACK_LAYERS = 3

DEFAULT_VERSION: dict[str, Any] = {"versionName": "Version 1", "versionType": 0}

CDL_KEYS = ("NodeIndex", "Slope", "Offset", "Power", "Saturation")


class FakeGraph(FakeBase):
    CLASS = "Graph"

    def __init__(self, world: FakeWorld, labels: list[str]) -> None:
        super().__init__(world)
        cache = int(world.resolve.CACHE_AUTO_ENABLED)  # type: ignore[attr-defined]
        self.nodes: list[dict[str, Any]] = [
            {"label": label, "tools": ["Color Wheels"], "lut": "", "cache": cache, "enabled": True}
            for label in labels
        ]
        self.drx: list[tuple[str, int, bool]] = []
        self.arri_applied = False
        self.resets = 0

    def _node(self, nodeIndex: int) -> dict[str, Any] | None:
        return self.nodes[nodeIndex - 1] if 1 <= nodeIndex <= len(self.nodes) else None

    def GetNumNodes(self) -> int:
        return self._ret("GetNumNodes", len(self.nodes))

    def GetLUT(self, nodeIndex: int) -> str:
        node = self._node(nodeIndex)
        return self._ret("GetLUT", str(node["lut"]) if node else "", nodeIndex)

    def GetNodeCacheMode(self, nodeIndex: int) -> int:
        node = self._node(nodeIndex)
        return self._ret("GetNodeCacheMode", int(node["cache"]) if node else -1, nodeIndex)

    def GetNodeLabel(self, nodeIndex: int) -> str:
        node = self._node(nodeIndex)
        return self._ret("GetNodeLabel", str(node["label"]) if node else "", nodeIndex)

    def GetToolsInNode(self, nodeIndex: int) -> list[str]:
        node = self._node(nodeIndex)
        return self._ret("GetToolsInNode", list(node["tools"]) if node else [], nodeIndex)

    def SetLUT(self, nodeIndex: int, lutPath: str) -> bool:
        injected, value = self._rec("SetLUT", nodeIndex, lutPath)
        node = self._node(nodeIndex)
        if injected:
            return bool(value)
        if node is None or not lutPath:
            return False
        node["lut"] = lutPath
        return True

    def SetNodeCacheMode(self, nodeIndex: int, cacheValue: Any) -> bool:
        injected, value = self._rec("SetNodeCacheMode", nodeIndex, cacheValue)
        node = self._node(nodeIndex)
        if injected:
            return bool(value)
        if node is None or not isinstance(cacheValue, (int, float)):
            return False
        node["cache"] = int(cacheValue)
        return True

    def SetNodeEnabled(self, nodeIndex: int, isEnabled: bool) -> bool:
        injected, value = self._rec("SetNodeEnabled", nodeIndex, isEnabled)
        node = self._node(nodeIndex)
        if injected:
            return bool(value)
        if node is None:
            return False
        node["enabled"] = bool(isEnabled)
        return True

    def ApplyArriCdlLut(self) -> bool:
        injected, value = self._rec("ApplyArriCdlLut")
        if injected:
            return bool(value)
        self.arri_applied = True
        return True

    def ApplyGradeFromDRX(self, path: str, gradeMode: int, applyToAllLayers: bool = False) -> bool:
        injected, value = self._rec("ApplyGradeFromDRX", path, gradeMode, applyToAllLayers)
        if injected:
            return bool(value)
        if gradeMode not in (0, 1, 2) or not Path(path).is_file():
            return False
        self.drx.append((path, gradeMode, bool(applyToAllLayers)))
        return True

    def ResetAllGrades(self) -> bool:
        injected, value = self._rec("ResetAllGrades")
        if injected:
            return bool(value)
        for node in self.nodes:
            node["lut"] = ""
        self.resets += 1
        return True


def _graph(owner: FakeBase, key: object, labels: list[str]) -> FakeGraph:
    graphs: dict[object, FakeGraph] = owner.__dict__.setdefault("graphs", {})
    if key not in graphs:
        graphs[key] = FakeGraph(owner._world, labels)
    return graphs[key]


class FakeColorGroup(FakeBase):
    CLASS = "ColorGroup"

    def __init__(self, world: FakeWorld, name: str) -> None:
        super().__init__(world)
        self.name = name
        self.pre = FakeGraph(world, ["Pre-clip"])
        self.post = FakeGraph(world, ["Post-clip"])

    def GetName(self) -> str:
        return self._ret("GetName", self.name)

    def SetName(self, groupName: str) -> bool:
        injected, value = self._rec("SetName", groupName)
        if injected:
            return bool(value)
        project = self._world.resolve.pm.current_project
        taken = [g.name for g in _groups(project) if g is not self] if project else []
        if not groupName or groupName in taken:
            return False
        self.name = groupName
        return True

    def GetClipsInTimeline(self, timeline: FakeTimeline | None = None) -> list[FakeTimelineItem]:
        project = self._world.resolve.pm.current_project
        scope = timeline or (project.current_timeline if project else None)
        items = (
            [i for tracks in scope.tracks.values() for t in tracks for i in t.items]
            if scope
            else []
        )
        found = [i for i in items if i.__dict__.get("color_group") is self]
        return self._ret("GetClipsInTimeline", found, timeline)

    def GetPreClipNodeGraph(self) -> FakeGraph:
        return self._ret("GetPreClipNodeGraph", self.pre)

    def GetPostClipNodeGraph(self) -> FakeGraph:
        return self._ret("GetPostClipNodeGraph", self.post)


def _groups(project: FakeProject | None) -> list[FakeColorGroup]:
    if project is None:
        return []
    groups: list[FakeColorGroup] = project.__dict__.setdefault("color_groups", [])
    return groups


def _get_color_groups(self: FakeProject) -> list[FakeColorGroup]:
    return self._ret("GetColorGroupsList", list(_groups(self)))


def _add_color_group(self: FakeProject, groupName: str) -> FakeColorGroup | None:
    injected, value = self._rec("AddColorGroup", groupName)
    if injected:
        return value if isinstance(value, FakeColorGroup) else None
    if not groupName or any(g.name == groupName for g in _groups(self)):
        return None
    group = FakeColorGroup(self._world, groupName)
    _groups(self).append(group)
    return group


def _delete_color_group(self: FakeProject, colorGroup: FakeColorGroup) -> bool:
    injected, value = self._rec("DeleteColorGroup", colorGroup)
    if injected:
        return bool(value)
    if colorGroup not in _groups(self):
        return False
    _groups(self).remove(colorGroup)
    for timeline in self.timelines:
        for tracks in timeline.tracks.values():
            for track in tracks:
                for item in track.items:
                    if item.__dict__.get("color_group") is colorGroup:
                        item.__dict__["color_group"] = None
    return True


def _timeline_graph(self: FakeTimeline) -> FakeGraph:
    injected, value = self._rec("GetNodeGraph")
    if injected and isinstance(value, FakeGraph):
        return value
    return _graph(self, "timeline", ["Timeline"])


def _item_node_graph(self: FakeTimelineItem, layerIdx: int | None = None) -> FakeGraph | None:
    injected, value = self._rec("GetNodeGraph", layerIdx)
    if injected:
        return value if isinstance(value, FakeGraph) else None
    layer = 1 if layerIdx is None else layerIdx
    if not 1 <= layer <= NODE_STACK_LAYERS:
        return None
    return _graph(self, layer, ["Primary", "Secondary"])


def _get_color_group(self: FakeTimelineItem) -> FakeColorGroup | None:
    return self._ret("GetColorGroup", self.__dict__.get("color_group"))


def _assign_to_group(self: FakeTimelineItem, colorGroup: FakeColorGroup) -> bool:
    injected, value = self._rec("AssignToColorGroup", colorGroup)
    if injected:
        return bool(value)
    project = self._world.resolve.pm.current_project
    if colorGroup not in _groups(project):
        return False
    self.__dict__["color_group"] = colorGroup
    return True


def _remove_from_group(self: FakeTimelineItem) -> bool:
    injected, value = self._rec("RemoveFromColorGroup")
    if injected:
        return bool(value)
    if self.__dict__.get("color_group") is None:
        return False
    self.__dict__["color_group"] = None
    return True


def _current_version(self: FakeTimelineItem) -> dict[str, Any]:
    return self._ret(
        "GetCurrentVersion", dict(self.__dict__.get("current_version", DEFAULT_VERSION))
    )


def _add_version(self: FakeTimelineItem, versionName: str, versionType: int) -> bool:
    injected, value = self._rec("AddVersion", versionName, versionType)
    if injected:
        return bool(value)
    names = self.versions.get(versionType)
    if names is None or not versionName or versionName in names:
        return False
    names.append(versionName)
    return True


def _load_version(self: FakeTimelineItem, versionName: str, versionType: int) -> bool:
    injected, value = self._rec("LoadVersionByName", versionName, versionType)
    if injected:
        return bool(value)
    if versionName not in self.versions.get(versionType, []):
        return False
    self.__dict__["current_version"] = {"versionName": versionName, "versionType": versionType}
    return True


def _rename_version(self: FakeTimelineItem, oldName: str, newName: str, versionType: int) -> bool:
    injected, value = self._rec("RenameVersionByName", oldName, newName, versionType)
    if injected:
        return bool(value)
    names = self.versions.get(versionType, [])
    if oldName not in names or not newName or newName in names:
        return False
    names[names.index(oldName)] = newName
    current = self.__dict__.get("current_version", DEFAULT_VERSION)
    if current["versionName"] == oldName and current["versionType"] == versionType:
        self.__dict__["current_version"] = {**current, "versionName": newName}
    return True


def _delete_version(self: FakeTimelineItem, versionName: str, versionType: int) -> bool:
    injected, value = self._rec("DeleteVersionByName", versionName, versionType)
    if injected:
        return bool(value)
    names = self.versions.get(versionType, [])
    if versionName not in names:
        return False
    names.remove(versionName)
    current = self.__dict__.get("current_version", DEFAULT_VERSION)
    if current["versionName"] == versionName and current["versionType"] == versionType:
        self.__dict__["current_version"] = dict(DEFAULT_VERSION)
    return True


def _set_cdl(self: FakeTimelineItem, CDL: dict[str, Any]) -> bool:
    injected, value = self._rec("SetCDL", CDL)
    if injected:
        return bool(value)
    graph = _item_graph(self)
    index = CDL.get("NodeIndex")
    if any(key not in CDL_KEYS for key in CDL) or not isinstance(index, int) or graph is None:
        return False
    # Resolve 21.1.1 refuses a CDL unless all of Slope, Offset, Power and Saturation are present
    if not 1 <= index <= len(graph.nodes) or len(CDL) < len(CDL_KEYS):
        return False
    self.__dict__.setdefault("cdl", []).append(dict(CDL))
    return True


def _item_graph(item: FakeTimelineItem) -> FakeGraph | None:
    return _graph(item, 1, ["Primary", "Secondary"])


def _copy_grades(self: FakeTimelineItem, tgtTimelineItems: list[FakeTimelineItem]) -> bool:
    injected, value = self._rec("CopyGrades", tgtTimelineItems)
    if injected:
        return bool(value)
    if not tgtTimelineItems or any(t is self for t in tgtTimelineItems):
        return False
    source = _item_graph(self)
    assert source is not None
    for target in tgtTimelineItems:
        copy = _item_graph(target)
        assert copy is not None
        for mine, theirs in zip(source.nodes, copy.nodes, strict=False):
            theirs["lut"] = mine["lut"]
        target.__dict__["grade_copied_from"] = self.uid
    return True


def _is_color_cache(self: FakeTimelineItem) -> bool:
    return self._ret("GetIsColorOutputCacheEnabled", bool(self.__dict__.get("color_cache", False)))


def _set_color_cache(self: FakeTimelineItem, enabled: bool) -> bool:
    injected, value = self._rec("SetColorOutputCache", enabled)
    if injected:
        return bool(value)
    self.__dict__["color_cache"] = bool(enabled)
    return True


def _reset_node_colors(self: FakeTimelineItem) -> bool:
    injected, value = self._rec("ResetAllNodeColors")
    if injected:
        return bool(value)
    self.__dict__["node_colors_reset"] = self.__dict__.get("node_colors_reset", 0) + 1
    return True


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeProject, "GetColorGroupsList"): _get_color_groups,
    (FakeProject, "AddColorGroup"): _add_color_group,
    (FakeProject, "DeleteColorGroup"): _delete_color_group,
    (FakeTimeline, "GetNodeGraph"): _timeline_graph,
    (FakeTimelineItem, "GetNodeGraph"): _item_node_graph,
    (FakeTimelineItem, "GetColorGroup"): _get_color_group,
    (FakeTimelineItem, "AssignToColorGroup"): _assign_to_group,
    (FakeTimelineItem, "RemoveFromColorGroup"): _remove_from_group,
    (FakeTimelineItem, "GetCurrentVersion"): _current_version,
    (FakeTimelineItem, "AddVersion"): _add_version,
    (FakeTimelineItem, "LoadVersionByName"): _load_version,
    (FakeTimelineItem, "RenameVersionByName"): _rename_version,
    (FakeTimelineItem, "DeleteVersionByName"): _delete_version,
    (FakeTimelineItem, "SetCDL"): _set_cdl,
    (FakeTimelineItem, "CopyGrades"): _copy_grades,
    (FakeTimelineItem, "GetIsColorOutputCacheEnabled"): _is_color_cache,
    (FakeTimelineItem, "SetColorOutputCache"): _set_color_cache,
    (FakeTimelineItem, "ResetAllNodeColors"): _reset_node_colors,
}


def extend() -> None:
    """Add the colour methods, then the gallery ones from `ext_color_gallery`."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)
    extend_gallery()


__all__ = ["FakeColorGroup", "FakeGraph", "extend"]
