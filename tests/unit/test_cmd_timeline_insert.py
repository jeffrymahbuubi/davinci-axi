"""F24 U2: `timeline insert generator|ofx|title` predicts the landing, refuses a ripple and places
the insert through the video marks."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeProject, FakeTimeline, FakeWorld, ext_edit_extra

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

INSERT = "Timeline.InsertTitleIntoTimeline"
SET_MARKS = "Timeline.SetMarkInOut"
CLEAR_MARKS = "Timeline.ClearMarkInOut"
BASE = ["timeline", "insert", "title", "Text"]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_edit_extra.extend()
    monkeypatch.setitem(registry.COMMANDS, "timeline", "davinci_axi.commands.timeline_extra:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")


def _edit(world: FakeWorld) -> FakeTimeline:
    project = world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    return next(t for t in project.timelines if t.name == "Edit v1")


def _scratch(world: FakeWorld) -> FakeTimeline:
    project = world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    scratch = project.add_timeline("scratch", current=True)
    scratch.add_track("video")
    return scratch


def _calls(world: FakeWorld, *names: str) -> list[str]:
    return [f"{cls}.{method}" for cls, method, _ in world.calls if f"{cls}.{method}" in names]


def test_a_dry_run_on_a_busy_timeline_is_refused_and_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _edit(fake_world).current_tc = "01:00:00:15"
    code, out, _ = run_cli(BASE)
    assert code == 1 and parse_toon_keys(out)["code"] == "WRITE_REFUSED"
    assert "V1:1" in out and "--allow-ripple" in out
    assert fake_world.write_calls() == []


def test_allow_ripple_dry_run_names_the_landing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _edit(fake_world).current_tc = "01:00:00:15"
    code, out, _ = run_cli([*BASE, "--allow-ripple"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false"
    assert "title,null,V1@86415" in out
    assert "ripple" in keys["warning"] and "V1:1" in keys["splits"]
    assert fake_world.write_calls() == []
    assert "--allow-ripple --force" in out  # the force command repeats the flag


def test_allow_ripple_force_inserts_at_the_playhead_and_reads_the_landing_back(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _edit(fake_world).current_tc = "01:00:00:15"
    code, out, _ = run_cli([*BASE, "--allow-ripple", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and "title,null,V1@86415" in out
    assert keys["items"] == "6 -> 9" and keys["ripple"] == "true"
    assert keys["start"] == "86415" and keys["frames"] == "150"
    assert _calls(fake_world, SET_MARKS, CLEAR_MARKS) == []
    assert fake_world.calls_to(INSERT) == [("Text",)]


def test_an_empty_scratch_timeline_needs_no_allow_ripple(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _scratch(fake_world)
    code, out, _ = run_cli([*BASE, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and "title,null,V1@86400" in out
    assert keys["frames"] == "150" and keys["item"] == '"V1:1"'


def test_at_and_duration_set_the_marks_around_the_insert_and_clear_them(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    scratch = _scratch(fake_world)
    code, out, _ = run_cli([*BASE, "--at", "01:00:02:00", "--duration", "60", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and "title,null,V1@86448" in out, out
    assert keys["start"] == "86448" and keys["frames"] == "60"
    assert keys["marks_restored"] == "true"
    assert _calls(fake_world, SET_MARKS, INSERT, CLEAR_MARKS) == [SET_MARKS, INSERT, CLEAR_MARKS]
    assert fake_world.calls_to(SET_MARKS) == [(48, 107, "video")]
    assert fake_world.calls_to(CLEAR_MARKS) == [("video",)]
    assert scratch.mark_in_out.get("video") is None


def test_preset_marks_are_put_back_after_the_insert(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    edit = _edit(fake_world)
    edit.mark_in_out = {"video": {"in": 0, "out": 100}}
    args = [*BASE, "--at", "01:00:02:00", "--duration", "60", "--allow-ripple", "--force"]
    code, out, _ = run_cli(args)
    assert code == 0 and "title,null,V1@86448" in out, out
    assert fake_world.calls_to(SET_MARKS) == [(48, 107, "video"), (0, 100, "video")]
    assert fake_world.calls_to(CLEAR_MARKS) == []
    assert edit.mark_in_out["video"] == {"in": 0, "out": 100}


def test_a_failed_mark_write_is_api_failed_and_inserts_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _scratch(fake_world)
    fake_world.inject(SET_MARKS, False)
    code, out, _ = run_cli([*BASE, "--at", "01:00:02:00", "--duration", "60", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert fake_world.calls_to(INSERT) == []


@pytest.mark.parametrize(
    "extra",
    [
        ["--at", "01:00:02:00", "--frame", "86448"],
        ["--duration", "0"],
        ["--at", "1:2"],
        ["--frame", "-1"],
    ],
)
def test_bad_placement_flags_exit_2_before_connecting(
    run_cli: Run, fake_world: FakeWorld, extra: list[str]
) -> None:
    code, _, _ = run_cli([*BASE, *extra, "--force"])
    assert code == 2
    assert fake_world.calls == []


def test_an_item_that_lands_on_another_track_is_verify_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    edit = _edit(fake_world)
    edit.current_tc = "01:00:00:15"
    stray = edit.add_item("video", 2, "Text", 86415, 150, kind="Title")
    fake_world.inject(INSERT, stray)
    code, out, _ = run_cli([*BASE, "--allow-ripple", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "VERIFY_FAILED"
    assert "V2@86415" in out


def test_a_drop_frame_playhead_is_not_predicted_and_ripple_is_refused(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    edit = _edit(fake_world)
    edit.start_tc = edit.current_tc = "01:00:00;00"
    code, out, _ = run_cli(BASE)
    assert code == 1 and parse_toon_keys(out)["code"] == "WRITE_REFUSED"
    assert "drop-frame" in out and "--allow-ripple" in out
    code, out, _ = run_cli([*BASE, "--allow-ripple"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false"
    assert "landing not predicted on a drop-frame timeline" in keys["warning"]
    code, out, _ = run_cli([*BASE, "--allow-ripple", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true", out


def test_a_drop_frame_scratch_timeline_inserts_without_a_prediction(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    scratch = _scratch(fake_world)
    scratch.start_tc = scratch.current_tc = "01:00:00;00"
    code, out, _ = run_cli([*BASE, "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true", out


def test_the_help_carries_the_scratch_timeline_recipe(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, err = run_cli(["timeline", "insert", "--help"])
    assert (code, err) == (0, "")
    assert "timeline compound" in out and "--record-frame" in out
    code, out, _ = run_cli(["timeline", "insert", "title", "--help"])
    assert code == 0 and "--allow-ripple" in out and "--duration" in out


def _late(world: FakeWorld) -> FakeTimeline:
    """An empty timeline starting at 108000 (01:15:00:00 at 24 fps): relative != absolute (F32)."""
    project = world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    late = project.add_timeline(
        "late", current=True, start_frame=108000, start_tc="01:15:00:00", frames=240
    )
    late.add_track("video")
    return late


def test_at_and_duration_write_relative_marks_on_a_late_start_timeline(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    late = _late(fake_world)
    code, out, _ = run_cli([*BASE, "--at", "01:15:02:00", "--duration", "60", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and "title,null,V1@108048" in out, out
    assert keys["start"] == "108048" and keys["frames"] == "60"
    assert keys["marks_restored"] == "true" and keys["applied"] == "true"
    assert keys["marks_set"] == "48-107 (timeline-relative)"
    assert fake_world.calls_to(SET_MARKS) == [(48, 107, "video")]
    assert fake_world.calls_to(CLEAR_MARKS) == [("video",)]
    assert late.mark_in_out.get("video") is None
    assert "VERIFY_FAILED" not in out


def test_marks_set_by_hand_place_the_insert_without_a_mark_write(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    late = _late(fake_world)
    late.mark_in_out = {"video": {"in": 60, "out": 120}}
    code, out, _ = run_cli([*BASE, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and "title,null,V1@108060" in out, out
    assert keys["start"] == "108060" and keys["frames"] == "61"
    assert keys["marks_before"] == "60-120 (timeline-relative)"
    assert fake_world.calls_to(SET_MARKS) == [] and fake_world.calls_to(CLEAR_MARKS) == []
    assert late.mark_in_out["video"] == {"in": 60, "out": 120}


def test_previous_marks_are_restored_as_read_on_a_late_start_timeline(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    late = _late(fake_world)
    late.mark_in_out = {"video": {"in": 10, "out": 20}}
    code, out, _ = run_cli([*BASE, "--at", "01:15:02:00", "--duration", "60", "--force"])
    assert code == 0 and "title,null,V1@108048" in out, out
    assert fake_world.calls_to(SET_MARKS) == [(48, 107, "video"), (10, 20, "video")]
    assert late.mark_in_out["video"] == {"in": 10, "out": 20}


def test_a_frame_before_the_timeline_start_is_refused_before_any_write(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _late(fake_world)
    code, out, _ = run_cli([*BASE, "--frame", "107000", "--duration", "60", "--force"])
    assert code != 0 and parse_toon_keys(out)["code"] == "VALIDATION_ERROR"
    assert "before the timeline start 108000" in out
    assert fake_world.write_calls() == []
