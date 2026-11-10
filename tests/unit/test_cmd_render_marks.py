"""`render add` puts the timeline's in/out marks back after `--set MarkIn/MarkOut` (gap 4)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_render
from davinci_axi.fake.core import FakeProject, FakeTimeline

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

START = 86400
SET_MARKS = f"MarkIn={START + 20},MarkOut={START + 30}"
BEFORE = {"video": {"in": 10, "out": 70}}


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_render.extend()
    monkeypatch.setitem(registry.COMMANDS, "render", "davinci_axi.commands.render:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    path = (tmp_path / "out").resolve()
    path.mkdir()
    return path


def _timeline(world: FakeWorld, marks: dict[str, object] | None) -> FakeTimeline:
    project: FakeProject | None = world.resolve.pm.current_project
    assert project is not None and project.current_timeline is not None
    timeline = project.current_timeline
    timeline.start_frame, timeline.end_frame = START, START + 240
    timeline.mark_in_out = dict(marks or {})
    return timeline


def _add(out_dir: Path, *extra: str) -> list[str]:
    return ["render", "add", "--target-dir", str(out_dir), *extra]


def _mark_calls(world: FakeWorld) -> list[str]:
    return [n for n in world.write_calls() if n.endswith(("SetMarkInOut", "ClearMarkInOut"))]


def _jobs(world: FakeWorld) -> int:
    project = world.resolve.pm.current_project
    assert project is not None
    return len(project.render_jobs)


def test_unmarked_timeline_is_cleared_again(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    timeline = _timeline(fake_world, None)
    jobs = _jobs(fake_world)
    code, out, err = run_cli(_add(out_dir, "--set", SET_MARKS, "--force"))
    assert (code, err) == (0, "")
    assert parse_toon_keys(out)["marks_restored"] == "true"
    assert len(fake_world.calls_to("Project.SetRenderSettings")) == 1
    sent = fake_world.calls_to("Project.SetRenderSettings")[0][0]
    assert isinstance(sent, dict) and sent["MarkIn"] == START + 20 and sent["MarkOut"] == START + 30
    assert _jobs(fake_world) == jobs + 1
    assert fake_world.calls_to("Timeline.ClearMarkInOut") == [("video",)]
    assert "video" not in timeline.GetMarkInOut()


def test_previous_marks_are_set_back_last(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    timeline = _timeline(fake_world, BEFORE)
    code, out, _ = run_cli(_add(out_dir, "--set", SET_MARKS, "--force"))
    assert code == 0 and parse_toon_keys(out)["marks_restored"] == "true"
    assert fake_world.calls_to("Timeline.SetMarkInOut")[-1] == (10, 70, "video")
    assert _mark_calls(fake_world)[-1] == "Timeline.SetMarkInOut"
    assert timeline.GetMarkInOut() == BEFORE
    writes = fake_world.write_calls()
    assert writes.index("Project.AddRenderJob") < writes.index("Timeline.SetMarkInOut")


def test_keep_marks_leaves_them_alone(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    _timeline(fake_world, BEFORE)
    code, out, _ = run_cli(_add(out_dir, "--set", SET_MARKS, "--keep-marks", "--force"))
    assert code == 0 and "marks_restored" not in parse_toon_keys(out)
    assert _mark_calls(fake_world) == []


def test_no_mark_setting_means_no_mark_calls(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    _timeline(fake_world, BEFORE)
    code, out, _ = run_cli(_add(out_dir, "--force"))
    assert code == 0 and "marks_restored" not in parse_toon_keys(out)
    assert _mark_calls(fake_world) == []


def test_dry_run_makes_no_mark_calls(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    _timeline(fake_world, BEFORE)
    code, out, _ = run_cli(_add(out_dir, "--set", SET_MARKS))
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == []


def test_marks_restored_is_read_back_when_clear_returns_false(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    timeline = _timeline(fake_world, None)
    fake_world.inject("Timeline.ClearMarkInOut", False)
    jobs = _jobs(fake_world)
    code, out, _ = run_cli(_add(out_dir, "--set", SET_MARKS, "--force"))
    keys = parse_toon_keys(out)
    assert code == 0 and keys["marks_restored"] == "true" and keys["marks_before"] == "none"
    assert timeline.GetMarkInOut() == {}
    assert _jobs(fake_world) == jobs + 1


def test_marks_before_names_the_previous_pair(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    _timeline(fake_world, BEFORE)
    code, out, _ = run_cli(_add(out_dir, "--set", SET_MARKS, "--force"))
    keys = parse_toon_keys(out)
    assert code == 0 and keys["marks_before"] == "10-70" and keys["marks_restored"] == "true"


def test_marks_restored_false_when_the_read_back_differs(
    run_cli: Run,
    parse_toon_keys: Keys,
    fake_world: FakeWorld,
    out_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline = _timeline(fake_world, BEFORE)
    reads = iter([BEFORE, {"video": {"in": 99, "out": 120}}])
    monkeypatch.setattr(timeline, "GetMarkInOut", lambda: next(reads, {}), raising=False)
    jobs = _jobs(fake_world)
    code, out, _ = run_cli(_add(out_dir, "--set", SET_MARKS, "--force"))
    assert code == 0 and parse_toon_keys(out)["marks_restored"] == "false"
    assert _jobs(fake_world) == jobs + 1


def test_marks_come_back_when_the_job_is_not_created(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    timeline = _timeline(fake_world, BEFORE)
    fake_world.inject("Project.AddRenderJob", None)
    code, out, _ = run_cli(_add(out_dir, "--set", SET_MARKS, "--force"))
    assert code == 1 and "API_FAILED" in out
    assert fake_world.calls_to("Timeline.SetMarkInOut")[-1] == (10, 70, "video")
    assert timeline.GetMarkInOut() == BEFORE


def test_help_documents_the_flag_and_drops_the_old_wording(run_cli: Run) -> None:
    code, out, _ = run_cli(["render", "add", "--help"])
    assert code == 0 and "--keep-marks" in out
    assert "leaves them" not in out and "marks persist" not in out
    code, out, _ = run_cli(["render", "deliver", "--help"])
    assert code == 0 and "--keep-marks" in out
