"""F32/F34(d): the relative-mark helper and `timeline mark show`."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from davinci_axi.commands.timeline_extra_state import MARK_SPEC
from davinci_axi.commands.timeline_marks import (
    MARK_SHOW_SPEC,
    MarkRange,
    mark_range,
    to_relative,
    video_marks,
)
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeProject, FakeTimeline, FakeWorld, ext_edit_extra

Run = Callable[[list[str]], tuple[int, str, str]]


@pytest.fixture(autouse=True)
def _show_registered(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`mark show` is registered by the lead splice; register it here so the CLI path is covered."""
    ext_edit_extra.extend()
    monkeypatch.setitem(MARK_SPEC.subcommands, "show", MARK_SHOW_SPEC)
    yield


def _late(world: FakeWorld, name: str = "late") -> FakeTimeline:
    project = world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    return project.add_timeline(
        name, current=True, start_frame=108000, start_tc="01:15:00:00", frames=240
    )


def _show(run_cli: Run) -> tuple[int, dict[str, Any]]:
    code, out, _ = run_cli(["timeline", "mark", "show", "--json"])
    return code, json.loads(out)


def test_mark_range_converts_between_the_two_frame_systems() -> None:
    marks = MarkRange(100, 200, 108000)
    assert (marks.abs_in, marks.abs_out, marks.frames) == (108100, 108200, 101)
    assert marks.text == "100-200 (absolute 108100-108200)"
    assert MarkRange(60, 120, 108000).frames == 61  # inclusive, as seen live


def test_mark_range_reads_a_pair_and_rejects_the_unset_or_malformed() -> None:
    assert mark_range({"video": {"in": 5, "out": 9}}, "video", 100) == MarkRange(5, 9, 100)
    assert mark_range({"video": {"in": 5, "out": 9}}, "audio", 100) is None
    assert mark_range({}, "video", 100) is None
    assert mark_range({"video": {"in": 5}}, "video", 100) is None
    assert mark_range({"video": {"in": "x", "out": 9}}, "video", 100) is None
    assert mark_range({"video": {"in": 9, "out": 5}}, "video", 100) is None
    assert mark_range({"video": None}, "video", 100) is None


def test_video_marks_uses_the_timeline_start(fake_world: FakeWorld) -> None:
    late = _late(fake_world)
    assert video_marks(late) is None  # type: ignore[arg-type]
    late.mark_in_out = {"video": {"in": 100, "out": 200}}
    assert video_marks(late) == MarkRange(100, 200, 108000)  # type: ignore[arg-type]


def test_to_relative_subtracts_the_start_and_refuses_earlier_frames() -> None:
    assert to_relative(108048, 108000) == 48
    assert to_relative(108000, 108000) == 0
    with pytest.raises(AxiError) as caught:
        to_relative(107999, 108000)
    assert caught.value.code == "VALIDATION_ERROR"
    assert "before the timeline start 108000" in caught.value.message


def test_mark_show_prints_both_frame_systems(run_cli: Run, fake_world: FakeWorld) -> None:
    late = _late(fake_world)
    late.mark_in_out = {"video": {"in": 100, "out": 200}}
    code, data = _show(run_cli)
    assert code == 0
    assert data["timeline"] == "late" and data["start_frame"] == 108000
    assert data["video"] == {
        "in": 100,
        "out": 200,
        "abs_in": 108100,
        "abs_out": 108200,
        "frames": 101,
    }
    assert data["audio"] is None
    assert "relative to the timeline start" in data["note"]
    assert fake_world.write_calls() == []


def test_mark_show_with_audio_marks_too(run_cli: Run, fake_world: FakeWorld) -> None:
    late = _late(fake_world)
    late.mark_in_out = {"audio": {"in": 0, "out": 23}}
    code, data = _show(run_cli)
    assert code == 0 and data["video"] is None
    assert data["audio"]["abs_in"] == 108000 and data["audio"]["frames"] == 24


def test_mark_show_without_marks_exits_0_with_both_null(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _late(fake_world)
    code, data = _show(run_cli)
    assert code == 0 and data["video"] is None and data["audio"] is None
    assert data["start_frame"] == 108000


def test_mark_show_names_the_timeline_flag_in_its_help(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, err = run_cli(["timeline", "mark", "show", "--help"])
    assert (code, err) == (0, "")
    assert "--timeline" in out
