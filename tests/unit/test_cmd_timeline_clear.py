from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import timeline_extra
from davinci_axi.commands.timeline_clear import CLEAR_SPEC
from davinci_axi.fake import FakeProject, FakeTimeline, FakeWorld, ext_timeline_edit
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

SPEC: CommandSpec = replace(
    timeline_extra.SPEC,
    subcommands={**timeline_extra.SPEC.subcommands, "clear": CLEAR_SPEC},
)


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_timeline_edit.extend()
    monkeypatch.setitem(registry.COMMANDS, "timeline", f"{__name__}:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _timeline(world: FakeWorld, name: str) -> FakeTimeline:
    return next(t for t in _project(world).timelines if t.name == name)


def _items(timeline: FakeTimeline) -> int:
    return sum(len(track.items) for tracks in timeline.tracks.values() for track in tracks)


def _tracks(timeline: FakeTimeline) -> dict[str, int]:
    return {kind: len(tracks) for kind, tracks in timeline.tracks.items()}


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def test_dry_run_shows_the_items_and_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["timeline", "clear", "Edit v1"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["applied"] == "false" and keys["command"] == "timeline clear"
    assert keys["irreversible"] == "true" and keys["tracks_kept"] == "V2 A1 S0"
    assert "  items,6,0" in out
    assert "deletes all 6 items from timeline Edit v1" in keys["warning"]
    assert "timeline clear 'Edit v1' --force" in out
    assert fake_world.write_calls() == []
    assert not _snapshots(tmp_path).exists()
    assert _items(_timeline(fake_world, "Edit v1")) == 6


def test_force_deletes_every_item_and_keeps_the_tracks(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    edit = _timeline(fake_world, "Edit v1")
    before = _tracks(edit)
    code, out, _ = run_cli(["timeline", "clear", "Edit v1", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and "timeline_restored" not in keys
    calls = fake_world.calls_to("Timeline.DeleteClips")
    assert len(calls) == 1 and len(calls[0][0]) == 6
    assert _items(edit) == 0 and _tracks(edit) == before
    assert len(list(_snapshots(tmp_path).iterdir())) == 1
    assert "snapshot" in keys


def test_a_non_current_timeline_is_switched_to_and_restored(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    second = _timeline(fake_world, "Edit v2")
    second.add_item("video", 1, "extra", second.start_frame, 40)
    current = _project(fake_world).current_timeline
    code, out, _ = run_cli(["timeline", "clear", "Edit v2", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["timeline_restored"] == "true"
    assert _items(second) == 0
    assert _project(fake_world).current_timeline is current


def test_an_empty_timeline_is_a_no_op(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "clear", "Edit v2", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and keys["note"] == "already empty"
    assert fake_world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


def test_a_failed_delete_is_api_failed_with_the_snapshot(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject("Timeline.DeleteClips", False)
    code, out, _ = run_cli(["timeline", "clear", "Edit v1", "--force"])
    assert code == 1 and "API_FAILED" in out and "snapshot" in out
    assert _items(_timeline(fake_world, "Edit v1")) == 6


def test_an_unknown_timeline_exits_1(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timeline", "clear", "Nope", "--force"])
    assert code == 1 and "Nope" in out
    assert fake_world.write_calls() == []


def test_open_notes_when_the_timeline_is_already_current(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "open", "Edit v1", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["note"] == "already the current timeline"
    assert keys["applied"] == "false"
    code, out, _ = run_cli(["timeline", "open", "Edit v2", "--force"])
    assert code == 0 and "note" not in parse_toon_keys(out)
