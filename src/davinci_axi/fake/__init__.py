"""The built-in fake Resolve, selected with `DAVINCI_AXI_FAKE=1` (KTD4).

Tests inject a world with `set_world`. Later phases extend the core classes from
`fake/ext_<area>.py` modules whose `extend()` the lead registers in `EXTENSIONS`.
"""

from __future__ import annotations

from collections.abc import Callable

from davinci_axi.fake import (
    ext_ai,
    ext_app,
    ext_audio,
    ext_color,
    ext_edit_extra,
    ext_fusion,
    ext_fusion_edit,
    ext_item_edit,
    ext_media_edit,
    ext_presets,
    ext_project,
    ext_render,
    ext_storage,
    ext_timeline_edit,
    ext_timeline_io,
    ext_writes_core,
)
from davinci_axi.fake.core import FakeProject, FakeResolve
from davinci_axi.fake.media import FakeMediaPoolItem
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem
from davinci_axi.fake.world import HANG_SECONDS, WRITE_METHODS, FakeWorld

__all__ = [
    "EXTENSIONS",
    "WRITE_METHODS",
    "FakeMediaPoolItem",
    "FakeProject",
    "FakeResolve",
    "FakeTimeline",
    "FakeTimelineItem",
    "FakeWorld",
    "current_world",
    "default_world",
    "scriptapp",
    "set_world",
]

_world: FakeWorld | None = None

# Lead: add `from davinci_axi.fake import ext_<area>` and `EXTENSIONS.append(ext_<area>.extend)`
# on the lines directly below, before the loop that applies them.
EXTENSIONS: list[Callable[[], None]] = [
    ext_writes_core.extend,
    ext_project.extend,
    ext_timeline_edit.extend,
    ext_item_edit.extend,
    ext_media_edit.extend,
    ext_timeline_io.extend,
    ext_render.extend,
    ext_fusion.extend,
    ext_presets.extend,
    ext_fusion_edit.extend,
    ext_color.extend,
    ext_audio.extend,
    ext_ai.extend,
    ext_storage.extend,
    ext_edit_extra.extend,
    ext_app.extend,
]
for _extend in EXTENSIONS:
    _extend()


def set_world(world: FakeWorld | None) -> None:
    """Install `world` as the one `scriptapp` serves; None resets to a fresh default world."""
    global _world
    _world = world


def current_world() -> FakeWorld:
    """The installed world, created on first use as `default_world()`."""
    global _world
    if _world is None:
        _world = default_world()
    return _world


def scriptapp(name: str) -> object | None:
    """Stand-in for `DaVinciResolveScript.scriptapp`; honours `world.connection`."""
    world = current_world()
    if world.connection == "hang":
        world.hang_gate.wait(HANG_SECONDS)
        return None
    if world.connection == "ok" and name == "Resolve":
        return world.resolve
    return None


def default_world() -> FakeWorld:
    """Project `davinci-axi-test` (2 timelines, V1 V2 A1, 6 items) plus a second project."""
    world = FakeWorld()
    manager = world.resolve.pm
    manager.add_project("sample-project", modified="2026-09-15T12:00:00+00:00")
    project = manager.add_project("davinci-axi-test", current=True)
    project.render_presets = ["H.264 Master", "YouTube 1080p", "ProRes 422 HQ"]
    project.quick_export_presets = ["H.264 Master", "YouTube"]
    _fill_media(project)
    _fill_timelines(project)
    project.add_render_job("Master export", "Edit v1", "Complete", 100)
    project.add_render_job("Review export", "Edit v1", "Ready", 0)
    return world


def _fill_media(project: FakeProject) -> None:
    root = project.media_pool.root
    footage = root.add_folder("Footage")
    footage.add_clip("A001_beach", "/media/davinci-axi-test/A001_beach.mov", duration="00:00:05:00")
    footage.add_clip("A002_city", "/media/davinci-axi-test/A002_city.mov", duration="00:00:08:00")
    footage.add_clip("A003_dusk", "/media/davinci-axi-test/A003_dusk.mov", duration="00:00:06:00")
    root.add_clip(
        "music_bed",
        "/media/davinci-axi-test/music_bed.wav",
        clip_type="Audio",
        duration="00:01:00:00",
    )
    root.clips[0].markers[24] = {
        "color": "Green",
        "duration": 1,
        "note": "pick",
        "name": "Take",
        "customData": "",
    }


def _fill_timelines(project: FakeProject) -> None:
    footage = project.media_pool.root.subfolders[0].clips
    music = project.media_pool.root.clips[0]
    edit = project.add_timeline("Edit v1", current=True, frames=240)
    for kind in ("video", "video", "audio"):
        edit.add_track(kind)
    start = edit.start_frame
    clips = [
        edit.add_item(
            "video", 1, f"clip-{n + 1}", start + 80 * n, 80, media=footage[n], color="Blue"
        )
        for n in range(3)
    ]
    title = edit.add_item("video", 2, "Title", start + 40, 60, kind="Generator")
    audio = [
        edit.add_item("audio", 1, "A001_beach", start, 120, media=footage[0]),
        edit.add_item("audio", 1, "music_bed", start + 120, 120, media=music),
    ]
    clips[0].linked.append(audio[0])
    audio[0].linked.append(clips[0])
    clips[0].flags.append("Red")
    clips[0].markers[10] = {
        "color": "Blue",
        "duration": 1,
        "note": "check cut",
        "name": "Cut",
        "customData": "",
    }
    clips[1].comps.append("Composition 1")
    clips[1].versions[0].append("Version 1")
    title.properties["Opacity"] = 80.0
    edit.markers[100] = {
        "color": "Red",
        "duration": 1,
        "note": "review",
        "name": "Marker 1",
        "customData": "",
    }
    edit.current_video_item = clips[0]
    second = project.add_timeline("Edit v2", frames=120)
    second.add_track("video")
    second.add_track("audio")
