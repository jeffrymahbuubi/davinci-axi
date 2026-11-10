"""`audio gain` (F27): relative clip volume on the fake, read back as AudioVolume."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeProject, FakeWorld, ext_audio
from davinci_axi.fake.timeline import FakeTimeline

Run = Callable[[list[str]], tuple[int, str, str]]

SAVE = ["ProjectManager.SaveProject", "ProjectManager.ExportProject"]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_audio.extend()
    monkeypatch.setitem(registry.COMMANDS, "audio", "davinci_axi.commands.audio:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")
    yield


def _timeline(world: FakeWorld) -> FakeTimeline:
    project = world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    return next(t for t in project.timelines if t.name == "Edit v1")


def _volumes(world: FakeWorld) -> list[object]:
    items = _timeline(world).tracks["audio"][0].items
    return [item.properties.get("AudioVolume") for item in items]


def _json(out: str) -> dict[str, object]:
    data = json.loads(out)
    assert isinstance(data, dict)
    return data


def _rows(data: dict[str, object]) -> list[dict[str, object]]:
    rows = data["changes"]
    assert isinstance(rows, list)
    return rows


def test_gain_dry_run_shows_both_changes_and_writes_nothing(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["audio", "gain", "A1:1,A1:2", "--db", "-3", "--json"])
    data = _json(out)
    assert (code, err) == (0, "") and data["applied"] is False and data["db"] == -3.0
    assert [(r["field"], r["before"], r["after"]) for r in _rows(data)] == [
        ("A1:1 AudioVolume", 0.0, -3.0),
        ("A1:2 AudioVolume", 0.0, -3.0),
    ]
    assert fake_world.write_calls() == [] and not (tmp_path / "axi-home" / "snapshots").exists()
    assert "--db -3" in out and "--force" in out


def test_gain_force_sets_each_item_and_reads_back(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["audio", "gain", "A1:1,A1:2", "--db", "-3", "--force", "--json"])
    assert code == 0 and _json(out)["applied"] is True
    calls = fake_world.calls_to("TimelineItem.SetProperties")
    assert [call[0] for call in calls] == [{"AudioVolume": -3.0}, {"AudioVolume": -3.0}]
    assert fake_world.write_calls()[:2] == SAVE
    assert _volumes(fake_world) == [-3.0, -3.0]


def test_gain_clamps_to_the_range_and_names_the_clamped_items(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["audio", "gain", "A1:1,A1:2", "--db", "40", "--force", "--json"])
    data = _json(out)
    assert code == 0 and data["clamped"] == ["A1:1", "A1:2"]
    assert [r["after"] for r in _rows(data)] == [30.0, 30.0]
    assert _volumes(fake_world) == [30.0, 30.0]


def test_gain_zero_exits_2_before_connecting(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["audio", "gain", "A1:1", "--db", "0"])
    assert code == 2 and "non-zero" in out and fake_world.calls == []


def test_gain_without_db_is_a_usage_error(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["audio", "gain", "A1:1"])
    assert code == 2 and "--db" in out and fake_world.calls == []


def test_gain_refuses_a_rejected_set_properties_naming_the_item(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject("TimelineItem.SetProperties", False)
    code, out, _ = run_cli(["audio", "gain", "A1:1", "--db", "-3", "--force"])
    assert code == 1 and "API_FAILED" in out and "A1:1 was not set" in out


def test_gain_skips_items_whose_volume_is_not_readable(run_cli: Run, fake_world: FakeWorld) -> None:
    _timeline(fake_world).tracks["audio"][0].items[0].properties = {}
    code, out, _ = run_cli(["audio", "gain", "A1:1,A1:2", "--db", "-3", "--json"])
    data = _json(out)
    assert code == 0 and data["not_readable"] == ["A1:1"]
    assert "not readable" in str(data["warning"])
    assert [r["field"] for r in _rows(data)] == ["A1:2 AudioVolume"]
    code, out, _ = run_cli(["audio", "gain", "A1:1,A1:2", "--db", "-3", "--force", "--json"])
    assert code == 0 and _json(out)["applied"] is True
    assert len(fake_world.calls_to("TimelineItem.SetProperties")) == 1
    assert _volumes(fake_world) == [None, -3.0]


def test_gain_on_only_unreadable_items_changes_nothing(run_cli: Run, fake_world: FakeWorld) -> None:
    for item in _timeline(fake_world).tracks["audio"][0].items:
        item.properties = {}
    code, out, _ = run_cli(["audio", "gain", "A1:1,A1:2", "--db", "-3", "--force", "--json"])
    data = _json(out)
    assert code == 0 and data["applied"] is False and data["changes"] == []
    assert data["not_readable"] == ["A1:1", "A1:2"]
    assert fake_world.write_calls() == []


def test_gain_help_lists_the_range_and_the_readback(run_cli: Run) -> None:
    code, out, err = run_cli(["audio", "gain", "--help"])
    assert (code, err) == (0, "") and "examples[" in out and "AudioVolume" in out
