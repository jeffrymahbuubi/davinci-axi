"""Fake `Fusion.GetCurrentComp` that follows the playhead on the Fusion page (F22 `--via-page`).

`FakeFusion.GetCurrentComp` returns a plain attribute. Here it returns, in order: an injected value
(`world.inject("Fusion.GetCurrentComp", ...)`); `FakeFusion.current` when a test set it; while the
Resolve page is `fusion`, the first comp of the topmost video item with a comp under the current
timeline's playhead (items without comps, such as a title above the clip, are skipped); else
`None`. Call `extend()` after `ext_fusion.extend()`; it is idempotent.
"""

from __future__ import annotations

from davinci_axi.fake.ext_fusion import FakeComp, FakeFusion, comp_of
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem


def _frame_of(timeline: FakeTimeline, code: str) -> int:
    """Absolute frame of `HH:MM:SS:FF` (or `;FF`), counting `round(fps)` frames a second."""
    rate = round(float(timeline.settings.get("timelineFrameRate") or 24))
    hours, minutes, seconds, frames = (int(p) for p in code.replace(";", ":").split(":"))
    return ((hours * 60 + minutes) * 60 + seconds) * rate + frames


def item_at_playhead(timeline: FakeTimeline) -> FakeTimelineItem | None:
    """The topmost video item with a Fusion comp under the playhead, or None."""
    frame = _frame_of(timeline, timeline.current_tc)
    for track in reversed(timeline.tracks["video"]):
        for item in track.items:
            if item.comps and item.start <= frame < item.start + item.duration:
                return item
    return None


def _current_comp(self: FakeFusion) -> FakeComp | None:
    world = self._world
    found = self.current
    project = world.resolve.pm.current_project
    timeline = project.current_timeline if project is not None else None
    if found is None and world.resolve.page == "fusion" and timeline is not None:
        item = item_at_playhead(timeline)
        if item is not None:
            found = comp_of(item)
    return self._ret("GetCurrentComp", found)


def extend() -> None:
    """Replace `FakeFusion.GetCurrentComp` with the playhead-following version."""
    FakeFusion.GetCurrentComp = _current_comp  # type: ignore[method-assign]
