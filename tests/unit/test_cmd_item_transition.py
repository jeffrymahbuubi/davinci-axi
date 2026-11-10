"""F37: `item` and `items` on a transition (its source getters return None live)."""

from __future__ import annotations

import json
from collections.abc import Callable

from davinci_axi.fake import FakeProject, FakeTimeline, FakeTimelineItem, FakeWorld

Run = Callable[[list[str]], tuple[int, str, str]]


def _edit(world: FakeWorld) -> FakeTimeline:
    project: FakeProject | None = world.resolve.pm.current_project
    assert project is not None
    return next(t for t in project.timelines if t.name == "Edit v1")


def _with_transition(world: FakeWorld) -> FakeTimelineItem:
    """A 20-frame dissolve across the first cut of V1 (clip-1 | clip-2), sorted into place."""
    edit = _edit(world)
    transition = edit.add_item(
        "video", 1, "Cross Dissolve", edit.start_frame + 70, 20, kind="Transition"
    )
    edit.tracks["video"][0].items.sort(key=lambda item: item.start)
    return transition


def _json(run_cli: Run, *argv: str) -> dict[str, object]:
    code, out, err = run_cli([*argv, "--json"])
    assert (code, err) == (0, "")
    data = json.loads(out)
    assert isinstance(data, dict)
    return data


def test_item_on_a_transition_does_not_crash(run_cli: Run, fake_world: FakeWorld) -> None:
    transition = _with_transition(fake_world)
    data = _json(run_cli, "item", transition.uid)
    assert data["type"] == "Transition"
    assert all(isinstance(data[key], int) for key in ("start", "end", "duration"))
    assert data["duration"] == 20
    for key in ("source_start_time", "source_end_time", "left_offset", "right_offset", "media"):
        assert data[key] is None


def test_item_names_the_clips_on_each_side(run_cli: Run, fake_world: FakeWorld) -> None:
    transition = _with_transition(fake_world)
    sides = _json(run_cli, "item", transition.uid)["sides"]
    assert isinstance(sides, dict)
    assert sides["left"]["name"] == "clip-1" and sides["left"]["track_ref"] == "V1:1"
    assert sides["right"]["name"] == "clip-2" and sides["right"]["track_ref"] == "V1:3"
    assert sides["left"]["end"] == sides["right"]["start"]
    assert set(sides["left"]) == {"id", "name", "track_ref", "start", "end"}


def test_item_by_shorthand_on_the_transition_position(run_cli: Run, fake_world: FakeWorld) -> None:
    transition = _with_transition(fake_world)
    data = _json(run_cli, "item", "V1:2")
    assert data["id"] == transition.uid
    assert data["type"] == "Transition"
    sides = data["sides"]
    assert isinstance(sides, dict) and sides["left"]["name"] == "clip-1"


def test_transition_at_the_edge_has_a_missing_side(run_cli: Run, fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    transition = edit.add_item("video", 1, "Fade", edit.start_frame, 10, kind="Transition")
    edit.tracks["video"][0].items.sort(key=lambda item: item.start)
    sides = _json(run_cli, "item", transition.uid)["sides"]
    assert isinstance(sides, dict)
    assert sides["left"] is None and sides["right"]["name"] == "clip-1"


def test_items_rows_with_type_source_and_media_fields_on_a_transition(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    transition = _with_transition(fake_world)
    data = _json(run_cli, "items", "--fields", "id,type,source_in,media")
    rows = data["items"]
    assert isinstance(rows, list)
    (row,) = [r for r in rows if r["id"] == transition.uid]
    assert row["type"] == "Transition" and row["media"] is None


def test_clip_detail_is_unchanged(run_cli: Run, fake_world: FakeWorld) -> None:
    _with_transition(fake_world)
    data = _json(run_cli, "item", "V1:1")
    assert "sides" not in data
    assert data["type"] == "Video" and data["name"] == "clip-1"
    assert isinstance(data["source_start_time"], float)
    assert isinstance(data["source_end_time"], float)
    assert isinstance(data["left_offset"], int) and isinstance(data["right_offset"], int)
