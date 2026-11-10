"""F24 U3: `fusion insert composition|generator|title` predicts the landing, refuses a ripple,
places through the marks and reads the landing back."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.fake import (
    FakeProject,
    FakeTimeline,
    FakeWorld,
    ext_fusion,
    ext_fusion_edit,
    ext_writes_core,
)

Run = Callable[[list[str]], tuple[int, str, str]]

INSERT = "Timeline.InsertFusionTitleIntoTimeline"
TITLE = ["fusion", "insert", "title", "Text+"]


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext_fusion.extend()
    ext_fusion_edit.extend()
    monkeypatch.setitem(registry.COMMANDS, "fusion", "davinci_axi.commands.fusion_comp:SPEC")
    yield


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    return project


def _edit(world: FakeWorld) -> FakeTimeline:
    return next(t for t in _project(world).timelines if t.name == "Edit v1")


def _scratch(world: FakeWorld) -> FakeTimeline:
    scratch = _project(world).add_timeline("scratch", current=True)
    scratch.add_track("video")
    scratch.add_track("audio")
    return scratch


def _json(run_cli: Run, argv: list[str]) -> tuple[int, dict[str, Any]]:
    code, out, _ = run_cli([*argv, "--json"])
    return code, json.loads(out)


def _names(world: FakeWorld) -> list[str]:
    return [f"{cls}.{method}" for cls, method, _ in world.calls]


def _count(timeline: FakeTimeline, kind: str, index: int) -> int:
    return len(timeline.tracks[kind][index - 1].items)


def test_insert_on_a_cut_timeline_is_refused_before_any_write(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _edit(fake_world).current_tc = "01:00:00:15"
    code, data = _json(run_cli, TITLE)
    assert code == 1 and data["code"] == "WRITE_REFUSED"
    assert "V1:1" in str(data["error"]) and "86415" in str(data["error"])
    help_text = " ".join(str(h) for h in data["help"])
    assert "--allow-ripple" in help_text and "fusion insert title Text+ --force" in help_text
    assert "timeline compound" in help_text and "--record-frame" in help_text
    assert fake_world.write_calls() == [] and fake_world.calls_to(INSERT) == []
    code, _ = _json(run_cli, [*TITLE, "--force"])
    assert code == 1 and fake_world.calls_to(INSERT) == []


def test_allow_ripple_dry_run_predicts_the_landing(run_cli: Run, fake_world: FakeWorld) -> None:
    _edit(fake_world).current_tc = "01:00:00:15"
    code, data = _json(run_cli, [*TITLE, "--allow-ripple"])
    assert code == 0 and data["applied"] is False
    assert data["changes"] == [{"field": "title", "before": None, "after": "V1@86415"}]
    assert "ripple" in str(data["warning"]) and "V1 at 86415" in str(data["warning"])
    assert data["splits"] == "V1:1, A1:1" and data["landing"] == "V1@86415 (01:00:00:15)"
    assert data["frames"] == "about 150 (Standard Generator Duration)"
    assert "--allow-ripple --force" in data["help"][0]
    assert fake_world.write_calls() == [] and fake_world.calls_to(INSERT) == []


def test_allow_ripple_force_lands_and_reports_the_ripple(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    edit = _edit(fake_world)
    edit.current_tc = "01:00:00:15"
    code, data = _json(run_cli, [*TITLE, "--allow-ripple", "--force"])
    assert code == 0 and data["applied"] is True
    assert data["changes"][0]["after"] == "V1@86415"
    assert (data["start"], data["frames"]) == (86415, 150)
    assert data["items"] == "6 -> 9" and data["ripple"] is True
    inserted = next(i for i in edit.tracks["video"][0].items if i.name == "Text+")
    assert data["item_id"] == inserted.GetUniqueId() and data["item"] == "V1:5"
    assert "Timeline.SetMarkInOut" not in _names(fake_world)
    assert _count(edit, "video", 1) == 5 and _count(edit, "audio", 1) == 3


@pytest.mark.parametrize(
    ("argv", "call", "name"),
    [
        (["composition"], "InsertFusionCompositionIntoTimeline", "Fusion Composition"),
        (["generator", "Solid Color"], "InsertFusionGeneratorIntoTimeline", "Solid Color"),
    ],
)
def test_every_verb_lands_on_an_empty_scratch_timeline_without_refusal(
    run_cli: Run, fake_world: FakeWorld, argv: list[str], call: str, name: str
) -> None:
    scratch = _scratch(fake_world)
    code, data = _json(run_cli, ["fusion", "insert", *argv, "--force"])
    assert code == 0 and data["applied"] is True
    assert data["changes"][0]["after"] == "V1@86400"
    assert (data["start"], data["frames"], data["items"], data["ripple"]) == (
        86400,
        150,
        "0 -> 1",
        False,
    )
    assert scratch.tracks["video"][0].items[0].name == name
    assert fake_world.calls_to(f"Timeline.{call}") == [tuple(argv[1:])]


def test_at_and_duration_go_through_the_marks_and_restore_them(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    scratch = _scratch(fake_world)
    placed = [*TITLE, "--at", "01:00:02:00", "--duration", "60"]
    code, data = _json(run_cli, placed)
    assert code == 0 and data["applied"] is False
    assert "--at 01:00:02:00 --duration 60 --force" in data["help"][0]
    assert fake_world.calls_to("Timeline.SetMarkInOut") == []
    code, data = _json(run_cli, [*placed, "--force"])
    assert code == 0 and data["applied"] is True
    assert (data["start"], data["frames"], data["marks_restored"]) == (86448, 60, True)
    assert data["changes"][0]["after"] == "V1@86448"
    mark_calls = [
        (m, a)
        for cls, m, a in fake_world.calls
        if m in ("SetMarkInOut", "ClearMarkInOut", "InsertFusionTitleIntoTimeline")
    ]
    assert mark_calls == [
        ("SetMarkInOut", (48, 107, "video")),
        ("InsertFusionTitleIntoTimeline", ("Text+",)),
        ("ClearMarkInOut", ("video",)),
    ]
    assert scratch.mark_in_out.get("video") is None


def test_preset_marks_are_put_back_after_a_placed_insert(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    scratch = _scratch(fake_world)
    scratch.mark_in_out = {"video": {"in": 100, "out": 200}}
    code, data = _json(run_cli, [*TITLE, "--frame", "86448", "--duration", "60", "--force"])
    assert code == 0 and data["marks_restored"] is True
    assert data["marks_before"] == "100-200 (timeline-relative)"
    assert fake_world.calls_to("Timeline.SetMarkInOut")[-1] == (100, 200, "video")
    assert scratch.mark_in_out["video"] == {"in": 100, "out": 200}


def test_marks_that_cannot_be_set_stop_before_the_insert(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _scratch(fake_world)
    fake_world.inject("Timeline.SetMarkInOut", False)
    code, data = _json(run_cli, [*TITLE, "--at", "01:00:02:00", "--duration", "60", "--force"])
    assert code == 1 and data["code"] == "API_FAILED"
    assert fake_world.calls_to(INSERT) == []


def test_an_item_that_lands_on_v2_is_verify_failed(run_cli: Run, fake_world: FakeWorld) -> None:
    scratch = _scratch(fake_world)
    scratch.add_track("video")
    stray = scratch.add_item("video", 2, "Text+", 86400, 150, kind="Title")
    fake_world.inject(INSERT, stray)
    code, data = _json(run_cli, [*TITLE, "--allow-ripple", "--force"])
    assert code == 1 and data["code"] == "VERIFY_FAILED"
    assert "V2@86400" in json.dumps(data) and "V1@86400" in json.dumps(data)


@pytest.mark.parametrize(
    "flags",
    [
        ["--at", "01:00:02:00", "--frame", "86448"],
        ["--duration", "0"],
        ["--at", "1:2"],
        ["--frame", "-1"],
        ["--at", "01:00:02;00"],
    ],
)
def test_bad_placement_flags_exit_2_without_connecting(
    run_cli: Run, fake_world: FakeWorld, flags: list[str]
) -> None:
    code, data = _json(run_cli, [*TITLE, *flags])
    assert code == 2 and data["code"] == "VALIDATION_ERROR"
    assert fake_world.calls == []


def test_a_drop_frame_playhead_skips_the_prediction_but_still_refuses(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _edit(fake_world).current_tc = "01:00:00;15"
    code, data = _json(run_cli, TITLE)
    assert code == 1 and data["code"] == "WRITE_REFUSED"
    assert "cannot predict" in str(data["error"]) and "drop-frame" in str(data["error"])
    assert "timeline compound" in " ".join(str(h) for h in data["help"])
    assert fake_world.calls_to(INSERT) == []


def test_a_drop_frame_playhead_with_allow_ripple_warns_and_applies(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    edit = _edit(fake_world)
    edit.current_tc = "01:00:00;15"
    code, data = _json(run_cli, [*TITLE, "--allow-ripple"])
    assert code == 0 and data["applied"] is False
    assert "drop-frame" in str(data["warning"]) and "cannot be predicted" in str(data["warning"])
    assert data["landing"] == "unknown (drop-frame playhead)"
    code, data = _json(run_cli, [*TITLE, "--allow-ripple", "--force"])
    assert code == 0 and data["applied"] is True and data["ripple"] is True
    assert any(i.name == "Text+" for i in edit.tracks["video"][0].items)


def test_a_drop_frame_playhead_on_an_empty_timeline_is_not_refused(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    scratch = _scratch(fake_world)
    scratch.current_tc = "01:00:00;00"
    code, data = _json(run_cli, [*TITLE, "--force"])
    assert code == 0 and data["applied"] is True and data["ripple"] is False


def test_a_drop_frame_playhead_with_a_placement_flag_still_fails(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _edit(fake_world).current_tc = "01:00:00;15"
    code, data = _json(run_cli, [*TITLE, "--duration", "60"])
    assert code == 2 and data["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("argv", [["fusion", "insert", "--help"], [*TITLE, "--help"]])
def test_help_carries_the_scratch_route_and_the_notes(run_cli: Run, argv: list[str]) -> None:
    code, out, _ = run_cli(argv)
    assert code == 0
    for word in ("timeline compound", "--record-frame", "in/out marks", "Text+ titles render"):
        assert word in out


def test_title_help_lists_the_placement_flags(run_cli: Run) -> None:
    code, out, _ = run_cli([*TITLE, "--help"])
    assert code == 0
    for flag in ("--at", "--frame", "--duration", "--allow-ripple"):
        assert flag in out


def _late(world: FakeWorld) -> FakeTimeline:
    """An empty timeline starting at 108000 (01:15:00:00 at 24 fps): relative != absolute (F32)."""
    late = _project(world).add_timeline(
        "late", current=True, start_frame=108000, start_tc="01:15:00:00", frames=240
    )
    late.add_track("video")
    late.add_track("audio")
    return late


def test_at_and_duration_write_relative_marks_on_a_late_start_timeline(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    late = _late(fake_world)
    code, data = _json(run_cli, [*TITLE, "--at", "01:15:02:00", "--duration", "60", "--force"])
    assert code == 0 and data["applied"] is True, data
    assert (data["start"], data["frames"], data["marks_restored"]) == (108048, 60, True)
    assert data["changes"][0]["after"] == "V1@108048"
    assert data["marks_set"] == "48-107 (timeline-relative)"
    assert fake_world.calls_to("Timeline.SetMarkInOut") == [(48, 107, "video")]
    assert late.mark_in_out.get("video") is None


def test_hand_set_marks_on_a_late_start_timeline_land_at_the_mark_in(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    late = _late(fake_world)
    late.mark_in_out = {"video": {"in": 60, "out": 120}}
    code, data = _json(run_cli, [*TITLE, "--force"])
    assert code == 0 and data["applied"] is True, data
    assert (data["start"], data["frames"]) == (108060, 61)
    assert data["marks_before"] == "60-120 (timeline-relative)"
    assert fake_world.calls_to("Timeline.SetMarkInOut") == []


def test_a_frame_before_the_timeline_start_writes_nothing(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _late(fake_world)
    code, data = _json(run_cli, [*TITLE, "--frame", "107000", "--force"])
    assert code != 0 and data["code"] == "VALIDATION_ERROR"
    assert fake_world.write_calls() == []
