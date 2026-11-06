"""Fake timeline import for U15: `MediaPool.ImportTimelineFromFile`.

`Timeline.Export` and the `EXPORT_*` constants on the fake Resolve object already exist in
`fake/timeline.py` and `fake/core.py`. Imports only from `davinci_axi.fake.core|media|timeline|
ext_writes_core`, never from the `davinci_axi.fake` package (KTD17). The method keeps the `_rec`
injection contract: an injected value is returned as the timeline (or None), otherwise the side
effect happens. `extend()` is idempotent.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from davinci_axi.fake.ext_writes_core import owner_project
from davinci_axi.fake.media import FakeMediaPool
from davinci_axi.fake.timeline import FakeTimeline

SUPPORTED = frozenset({".otio", ".otioz", ".fcpxml", ".xml", ".drt", ".edl", ".aaf"})
IMPORTED_FRAMES = 48


def _import_timeline_from_file(
    self: FakeMediaPool, filePath: str, importOptions: dict[str, Any] | None = None
) -> FakeTimeline | None:
    injected, value = self._rec("ImportTimelineFromFile", filePath, importOptions)
    if injected:
        return value if isinstance(value, FakeTimeline) else None
    path = Path(filePath)
    if path.suffix.lower() not in SUPPORTED or not path.is_file():
        return None
    options = importOptions or {}
    name = str(options.get("timelineName") or path.stem)
    project = owner_project(self)
    if any(t.name == name for t in project.timelines):
        return None
    timeline = project.add_timeline(name, frames=IMPORTED_FRAMES)
    timeline.add_track("video")
    timeline.add_item("video", 1, f"{name} clip", timeline.start_frame, IMPORTED_FRAMES)
    return timeline


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeMediaPool, "ImportTimelineFromFile"): _import_timeline_from_file,
}


def extend() -> None:
    """Add MediaPool.ImportTimelineFromFile."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)


__all__ = ["extend"]
