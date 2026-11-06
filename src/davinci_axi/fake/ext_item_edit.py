"""Fake write methods for timeline items and `Timeline.DeleteClips` (U12, KTD17).

Imports only from `davinci_axi.fake.timeline`, never from the `davinci_axi.fake` package, because
the package is still mid-import when `EXTENSIONS` run. Each method keeps the `_rec` injection
contract: an injected value is returned as `bool`, otherwise the side effect happens.
`extend()` is idempotent.

`SetSpeed` and `SetFades` use the key names of the real `.pyi` (`Percentage`, `PitchCorrection`,
`StretchKeyframesToFit`, `RippleTimeline`; `FadeIn`, `FadeOut`), replacing the placeholder keys
the core fake starts with.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem

SPEED_KEYS = ("Percentage", "PitchCorrection", "StretchKeyframesToFit", "RippleTimeline")
FADE_KEYS = ("FadeIn", "FadeOut")


def _set_name(self: FakeTimelineItem, name: str) -> bool:
    injected, value = self._rec("SetName", name)
    if injected:
        return bool(value)
    if not name:
        return False
    self.name = name
    return True


def _set_properties(self: FakeTimelineItem, properties: dict[str, Any]) -> bool:
    injected, value = self._rec("SetProperties", properties)
    if injected:
        return bool(value)
    if not properties or any(key not in self.properties for key in properties):
        return False
    self.properties.update(properties)
    return True


def _set_speed(self: FakeTimelineItem, speedOptions: dict[str, Any]) -> bool:
    injected, value = self._rec("SetSpeed", speedOptions)
    if injected:
        return bool(value)
    if not speedOptions or any(key not in SPEED_KEYS for key in speedOptions):
        return False
    if "Percentage" not in self.speed:
        self.speed = {"Percentage": float(self.speed.get("Speed", 100.0))}
    self.speed.update(speedOptions)
    return True


def _set_fades(self: FakeTimelineItem, fades: dict[str, Any]) -> bool:
    injected, value = self._rec("SetFades", fades)
    if injected:
        return bool(value)
    if not fades or any(key not in FADE_KEYS for key in fades):
        return False
    if "FadeIn" not in self.fades:
        self.fades = {"FadeIn": 0, "FadeOut": 0}
    self.fades.update(fades)
    return True


def _add_flag(self: FakeTimelineItem, color: str) -> bool:
    injected, value = self._rec("AddFlag", color)
    if injected:
        return bool(value)
    if not color:
        return False
    if color not in self.flags:
        self.flags.append(color)
    return True


def _clear_flags(self: FakeTimelineItem, color: str) -> bool:
    injected, value = self._rec("ClearFlags", color)
    if injected:
        return bool(value)
    kept = [] if color == "All" else [flag for flag in self.flags if flag != color]
    changed = len(kept) != len(self.flags)
    self.flags = kept
    return changed


def _set_clip_color(self: FakeTimelineItem, colorName: str) -> bool:
    injected, value = self._rec("SetClipColor", colorName)
    if injected:
        return bool(value)
    if not colorName:
        return False
    self.color = colorName
    return True


def _clear_clip_color(self: FakeTimelineItem) -> bool:
    injected, value = self._rec("ClearClipColor")
    if injected:
        return bool(value)
    self.color = ""
    return True


def _set_clip_enabled(self: FakeTimelineItem, enabled: bool) -> bool:
    injected, value = self._rec("SetClipEnabled", enabled)
    if injected:
        return bool(value)
    self.enabled = bool(enabled)
    return True


def _add_marker(
    self: FakeTimelineItem,
    frameId: int,
    color: str,
    name: str,
    note: str,
    duration: int,
    customData: str | None = None,
) -> bool:
    injected, value = self._rec("AddMarker", frameId, color, name, note, duration, customData)
    if injected:
        return bool(value)
    if frameId in self.markers or not color:
        return False
    self.markers[frameId] = {
        "color": color,
        "duration": duration,
        "note": note,
        "name": name,
        "customData": customData or "",
    }
    return True


def _delete_marker_at(self: FakeTimelineItem, frameNum: int) -> bool:
    injected, value = self._rec("DeleteMarkerAtFrame", frameNum)
    if injected:
        return bool(value)
    return self.markers.pop(frameNum, None) is not None


def _delete_markers_by_color(self: FakeTimelineItem, color: str) -> bool:
    injected, value = self._rec("DeleteMarkersByColor", color)
    if injected:
        return bool(value)
    doomed = [f for f, m in self.markers.items() if color == "All" or m["color"] == color]
    for frame in doomed:
        del self.markers[frame]
    return bool(doomed)


def _delete_marker_by_custom(self: FakeTimelineItem, customData: str) -> bool:
    injected, value = self._rec("DeleteMarkerByCustomData", customData)
    if injected:
        return bool(value)
    doomed = [f for f, m in self.markers.items() if m["customData"] == customData]
    for frame in doomed:
        del self.markers[frame]
    return bool(doomed)


def _delete_clips(
    self: FakeTimeline, timelineItems: list[FakeTimelineItem], rippleDelete: bool = False
) -> bool:
    """Remove the items from their tracks; with ripple, later items on the track move left."""
    injected, value = self._rec("DeleteClips", timelineItems, rippleDelete)
    if injected:
        return bool(value)
    if not timelineItems:
        return False
    removed = False
    for item in timelineItems:
        for tracks in self.tracks.values():
            for track in tracks:
                if item not in track.items:
                    continue
                track.items.remove(item)
                removed = True
                if rippleDelete:
                    for later in track.items:
                        if later.start >= item.start + item.duration:
                            later.start -= item.duration
    for item in timelineItems:
        if item in self.selected:
            self.selected.remove(item)
        if self.current_video_item is item:
            self.current_video_item = None
    return removed


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeTimelineItem, "SetName"): _set_name,
    (FakeTimelineItem, "SetProperties"): _set_properties,
    (FakeTimelineItem, "SetSpeed"): _set_speed,
    (FakeTimelineItem, "SetFades"): _set_fades,
    (FakeTimelineItem, "AddFlag"): _add_flag,
    (FakeTimelineItem, "ClearFlags"): _clear_flags,
    (FakeTimelineItem, "SetClipColor"): _set_clip_color,
    (FakeTimelineItem, "ClearClipColor"): _clear_clip_color,
    (FakeTimelineItem, "SetClipEnabled"): _set_clip_enabled,
    (FakeTimelineItem, "AddMarker"): _add_marker,
    (FakeTimelineItem, "DeleteMarkerAtFrame"): _delete_marker_at,
    (FakeTimelineItem, "DeleteMarkersByColor"): _delete_markers_by_color,
    (FakeTimelineItem, "DeleteMarkerByCustomData"): _delete_marker_by_custom,
    (FakeTimeline, "DeleteClips"): _delete_clips,
}


def extend() -> None:
    """Add the TimelineItem write methods and `Timeline.DeleteClips`."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)


__all__ = ["extend"]
