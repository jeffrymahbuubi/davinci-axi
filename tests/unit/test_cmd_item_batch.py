"""`item delete <ids>`: a list of items deleted under one snapshot, on the fake (U4)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import item_edit
from davinci_axi.commands.item_batch import DELETE_SPEC
from davinci_axi.fake import FakeProject, FakeWorld
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

DELETE = "Timeline.DeleteClips"
SPEC: CommandSpec = replace(
    item_edit.SPEC, subcommands={**item_edit.SPEC.subcommands, "delete": DELETE_SPEC}
)


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "item", f"{__name__}:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")


def _edit(world: FakeWorld) -> FakeTimeline:
    project: FakeProject | None = world.resolve.pm.current_project
    assert project is not None
    return project.timelines[0]


def _v1(world: FakeWorld) -> list[FakeTimelineItem]:
    return _edit(world).tracks["video"][0].items


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def test_dry_run_lists_every_item_and_writes_nothing(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["item", "delete", "V1:1,V1:3", "--json"])
    assert (code, err) == (0, "")
    data = json.loads(out)
    assert data["applied"] is False and data["irreversible"] is True
    assert [row["field"] for row in data["changes"]] == ["V1:1 clip-1", "V1:3 clip-3"]
    assert "deletes 2 items (V1:1, V1:3)" in data["warning"]
    assert "davinci-axi item delete V1:1,V1:3 --force" in "\n".join(data["help"])
    assert fake_world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


def test_force_is_one_delete_call_and_one_snapshot(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    first, _, third = _v1(fake_world)
    code, out, err = run_cli(["item", "delete", "V1:1,V1:3", "--force", "--json"])
    assert (code, err) == (0, "")
    data = json.loads(out)
    assert data["applied"] is True
    assert fake_world.calls_to(DELETE) == [([first, third], False)]
    assert [i.name for i in _v1(fake_world)] == ["clip-2"]
    assert [d.name for d in _snapshots(tmp_path).iterdir()] == [data["snapshot"]]


def test_ripple_closes_the_gaps(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["item", "delete", "V1:1,V1:3", "--ripple", "--force"])
    assert code == 0 and "ripple" in out
    assert [(i.name, i.start) for i in _v1(fake_world)] == [("clip-2", 86400)]
    assert fake_world.calls_to(DELETE)[0][1] is True


def test_unique_ids_and_repeats_are_accepted_once(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _, _ = run_cli(["item", "delete", "item-2,V1:2", "--force"])
    assert code == 0
    assert len(fake_world.calls_to(DELETE)) == 1
    assert [i.name for i in _v1(fake_world)] == ["clip-1", "clip-3"]


def test_a_single_item_keeps_its_old_shape(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["item", "delete", "V1:2", "--json"])
    assert code == 0
    data = json.loads(out)
    assert data["changes"] == [{"field": "item", "before": "V1:2 clip-2", "after": "deleted"}]
    assert data["target"] == "item V1:2 (clip-2) in timeline Edit v1"
    assert "deletes item V1:2 (clip-2) from timeline Edit v1" in data["warning"]
    code, _, _ = run_cli(["item", "delete", "V1:2", "--force"])
    assert code == 0 and [i.name for i in _v1(fake_world)] == ["clip-1", "clip-3"]


def test_an_empty_part_is_refused_before_any_write(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["item", "delete", "V1:1,,V1:3", "--force"])
    assert code == 2 and "VALIDATION_ERROR" in out
    assert fake_world.write_calls() == []


def test_a_missing_item_is_not_found_and_nothing_is_deleted(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["item", "delete", "V1:1,V1:9", "--force"])
    assert code == 1 and "NOT_FOUND" in out
    assert fake_world.write_calls() == []
    assert len(_v1(fake_world)) == 3


def test_a_refused_delete_is_api_failed_and_names_the_snapshot(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject(DELETE, False)
    code, out, _ = run_cli(["item", "delete", "V1:1,V1:3", "--force"])
    assert code == 1 and "API_FAILED" in out and "snapshot" in out


def test_timeline_flag_is_repeated_in_the_force_command(run_cli: Run) -> None:
    code, out, _ = run_cli(["item", "delete", "V1:1,V1:3", "--timeline", "Edit v1"])
    assert code == 0 and "item delete V1:1,V1:3 --timeline 'Edit v1' --force" in out


def test_help_documents_the_list_form(run_cli: Run) -> None:
    code, out, _ = run_cli(["item", "delete", "--help"])
    assert code == 0 and "comma-separated list" in out and "examples[" in out
