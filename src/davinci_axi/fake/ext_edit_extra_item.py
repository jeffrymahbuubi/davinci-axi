"""Fake timeline-item methods for U25: transitions, takes, sidecar, output blanking and stereo
reads (KTD17). Installed by `ext_edit_extra.extend()`.

Takes live in `FakeTimelineItem.takes` (dicts with `startFrame`, `endFrame`, `mediaPoolItem`;
the first `AddTake` on an item turns the item's own clip into take 1 first, as Resolve does),
transitions are `Transition` items placed on the same track, and the blanking and sidecar state
sits in the object's `__dict__`. Imports only from `davinci_axi.fake.core|media|timeline|
ext_writes_core`, never from the `davinci_axi.fake` package. Each write keeps the `_rec`
injection contract: an injected value is returned (as `bool`, or as the item for
`AddTransition`), otherwise the side effect happens. `extend()` is idempotent.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from davinci_axi.fake.core import FakeProject, FakeProjectFolder
from davinci_axi.fake.media import FakeMediaPoolItem
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem

CATEGORIES = ("simple", "fusion", "ofx", "audio")
POSITIONS = ("start", "end")
ALIGNMENTS = ("left", "center", "right")
FPS = 24
TRANSITION_FRAMES = 12
BLANKING_KEYS = ("Top", "Bottom", "Left", "Right")
CONVERGENCE: dict[int, float] = {0: 0.0, 24: 1.5}
WINDOW: dict[int, dict[str, float]] = {0: {"left": 0.0, "right": 2.0, "top": 0.0, "bottom": 0.0}}


def frames_of(clip: FakeMediaPoolItem) -> int:
    """Whole frames in the clip's `Duration` timecode at 24 fps."""
    parts = str(clip.properties.get("Duration", "00:00:10:00")).replace(";", ":").split(":")
    hours, minutes, seconds, frames = (int(p) for p in parts)
    return ((hours * 60 + minutes) * 60 + seconds) * FPS + frames


def _projects(folder: FakeProjectFolder) -> Iterator[FakeProject]:
    yield from folder.projects.values()
    for child in folder.children.values():
        yield from _projects(child)


def _timeline_of(item: FakeTimelineItem) -> FakeTimeline | None:
    for project in _projects(item._world.resolve.pm.root):
        for timeline in project.timelines:
            track = timeline._track(item.track_type, item.track_index)
            if track is not None and item in track.items:
                return timeline
    return None


def _add_transition(
    self: FakeTimelineItem, transitionOptions: dict[str, Any]
) -> FakeTimelineItem | None:
    injected, value = self._rec("AddTransition", dict(transitionOptions))
    if injected:
        return value if isinstance(value, FakeTimelineItem) else None
    name = transitionOptions.get("type")
    duration = transitionOptions.get("duration") or TRANSITION_FRAMES
    checks = (
        ("category", CATEGORIES),
        ("position", POSITIONS),
        ("alignment", ALIGNMENTS),
    )
    if not isinstance(name, str) or not name or not isinstance(duration, int) or duration < 1:
        return None
    # Live 21.1.1 refuses the call unless category, position and alignment are all given.
    if any(transitionOptions.get(key) not in options for key, options in checks):
        return None
    timeline = _timeline_of(self)
    if timeline is None:
        return None
    edge = self.start + self.duration if transitionOptions["position"] == "end" else self.start
    # Live centres the transition on the edge (30 frames at a cut on 120 start at 105).
    shift = {"left": duration, "center": duration // 2, "right": 0}
    start = edge - shift[transitionOptions["alignment"]]
    added = timeline.add_item(
        self.track_type, self.track_index, name, start, duration, kind="Transition"
    )
    # Live lists the transition among the clips in timeline order, shifting later indices.
    track = timeline._track(self.track_type, self.track_index)
    if track is not None:
        track.items.sort(key=lambda located: located.start)
    return added


def _update_sidecar(self: FakeTimelineItem) -> bool:
    injected, value = self._rec("UpdateSidecar")
    if injected:
        return bool(value)
    self.__dict__["sidecar_updates"] = int(self.__dict__.get("sidecar_updates", 0)) + 1
    return True


def _add_take(
    self: FakeTimelineItem,
    mediaPoolItem: Any,
    startFrame: int | None = None,
    endFrame: int | None = None,
) -> bool:
    injected, value = self._rec("AddTake", mediaPoolItem, startFrame, endFrame)
    if injected:
        return bool(value)
    if not isinstance(mediaPoolItem, FakeMediaPoolItem):
        return False
    start = startFrame if startFrame is not None else 0
    end = endFrame if endFrame is not None else frames_of(mediaPoolItem)
    if start < 0 or end <= start:
        return False
    if not self.takes:  # Resolve turns the item's own clip into take 1 (live, B2)
        original = {
            "startFrame": self.source_start,
            "endFrame": self.source_start + self.duration,
            "mediaPoolItem": self.media,
        }
        self.takes.append(original)
        self.selected_take = 1
    self.takes.append({"startFrame": start, "endFrame": end, "mediaPoolItem": mediaPoolItem})
    if not self.selected_take:
        self.selected_take = len(self.takes)
    return True


def _delete_take(self: FakeTimelineItem, idx: int) -> bool:
    injected, value = self._rec("DeleteTakeByIndex", idx)
    if injected:
        return bool(value)
    if not 1 <= idx <= len(self.takes):
        return False
    del self.takes[idx - 1]
    if not self.takes:
        self.selected_take = 0
    elif self.selected_take == idx:
        self.selected_take = min(idx, len(self.takes))
    elif self.selected_take > idx:
        self.selected_take -= 1
    return True


def _select_take(self: FakeTimelineItem, idx: int) -> bool:
    injected, value = self._rec("SelectTakeByIndex", idx)
    if injected:
        return bool(value)
    if not 1 <= idx <= len(self.takes):
        return False
    self.selected_take = idx
    return True


def _finalize_take(self: FakeTimelineItem) -> bool:
    injected, value = self._rec("FinalizeTake")
    if injected:
        return bool(value)
    if not self.takes:
        return False
    self.takes = []
    self.selected_take = 0
    return True


def _uses_timeline(self: FakeTimelineItem) -> bool:
    return bool(self.__dict__.setdefault("use_timeline_blanking", True))


def _get_output_blanking(self: FakeTimelineItem) -> dict[str, Any]:
    own = {} if _uses_timeline(self) else dict(self.__dict__.setdefault("blanking", {}))
    return self._ret("GetOutputBlanking", own)


def _set_output_blanking(self: FakeTimelineItem, outputBlanking: dict[str, Any]) -> bool:
    injected, value = self._rec("SetOutputBlanking", dict(outputBlanking))
    if injected:
        return bool(value)
    if not outputBlanking or any(k not in BLANKING_KEYS for k in outputBlanking):
        return False
    if any(not isinstance(v, int) or v < 0 for v in outputBlanking.values()):
        return False
    self.__dict__["blanking"] = {**self.__dict__.get("blanking", {}), **outputBlanking}
    return True


def _get_use_timeline(self: FakeTimelineItem) -> bool:
    return self._ret("GetUseTimelineForOutputBlanking", _uses_timeline(self))


def _set_use_timeline(self: FakeTimelineItem, useTimelineOutputBlanking: bool) -> bool:
    injected, value = self._rec("SetUseTimelineForOutputBlanking", useTimelineOutputBlanking)
    if injected:
        return bool(value)
    self.__dict__["use_timeline_blanking"] = bool(useTimelineOutputBlanking)
    return True


def _convergence(self: FakeTimelineItem) -> dict[int, float]:
    return self._ret("GetStereoConvergenceValues", dict(CONVERGENCE))


def _left_window(self: FakeTimelineItem) -> dict[int, dict[str, float]]:
    return self._ret("GetStereoLeftFloatingWindowParams", {k: dict(v) for k, v in WINDOW.items()})


def _right_window(self: FakeTimelineItem) -> dict[int, dict[str, float]]:
    return self._ret("GetStereoRightFloatingWindowParams", {k: dict(v) for k, v in WINDOW.items()})


_METHODS: dict[str, Callable[..., Any]] = {
    "AddTransition": _add_transition,
    "UpdateSidecar": _update_sidecar,
    "AddTake": _add_take,
    "DeleteTakeByIndex": _delete_take,
    "SelectTakeByIndex": _select_take,
    "FinalizeTake": _finalize_take,
    "GetOutputBlanking": _get_output_blanking,
    "SetOutputBlanking": _set_output_blanking,
    "GetUseTimelineForOutputBlanking": _get_use_timeline,
    "SetUseTimelineForOutputBlanking": _set_use_timeline,
    "GetStereoConvergenceValues": _convergence,
    "GetStereoLeftFloatingWindowParams": _left_window,
    "GetStereoRightFloatingWindowParams": _right_window,
}


def sidecar_updates(item: FakeTimelineItem) -> int:
    """Test helper (not an API call): how many times `UpdateSidecar` succeeded."""
    return int(item.__dict__.get("sidecar_updates", 0))


def extend() -> None:
    """Add the U25 item methods to `FakeTimelineItem`."""
    for name, method in _METHODS.items():
        setattr(FakeTimelineItem, name, method)


__all__ = ["extend", "frames_of", "sidecar_updates"]
