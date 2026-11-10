"""`timeline timecode set|start` refuse frame fields above the timeline rate (F26b)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeTimeline, FakeWorld, ext_timeline_edit

Run = Callable[[list[str]], tuple[int, str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_timeline_edit.extend()
    monkeypatch.setitem(registry.COMMANDS, "timeline", "davinci_axi.commands.timeline:SPEC")


def _timeline(world: FakeWorld, name: str) -> FakeTimeline:
    project = world.resolve.pm.current_project
    assert project is not None
    return next(t for t in project.timelines if t.name == name)


@pytest.mark.parametrize(
    ("verb", "bad", "good"),
    [("set", "01:00:11:30", "01:00:11:29"), ("start", "01:00:00:30", "01:00:00:29")],
)
def test_timecode_frame_field_above_the_rate_is_refused_before_any_write(
    run_cli: Run, fake_world: FakeWorld, verb: str, bad: str, good: str
) -> None:
    _timeline(fake_world, "Edit v1").settings["timelineFrameRate"] = 30.0
    code, out, _ = run_cli(["timeline", "timecode", verb, bad, "--force"])
    assert code == 2 and "00-29" in out
    assert fake_world.write_calls() == []
    code, _, _ = run_cli(["timeline", "timecode", verb, good, "--force"])
    assert code == 0
