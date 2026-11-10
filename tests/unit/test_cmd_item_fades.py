"""`item fades` clamps to the item length and warns, on the fake (F26(d))."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeProject, FakeWorld, ext_writes_core
from davinci_axi.fake import ext_item_edit as ext
from davinci_axi.fake.timeline import FakeTimeline

Run = Callable[[list[str]], tuple[int, str, str]]

SET_FADES = "TimelineItem.SetFades"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext.extend()
    monkeypatch.setitem(registry.COMMANDS, "item", "davinci_axi.commands.item_edit:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")
    yield


def _edit(world: FakeWorld) -> FakeTimeline:
    project: FakeProject | None = world.resolve.pm.current_project
    assert project is not None
    return project.timelines[0]


def test_dry_run_clamps_and_warns_without_writing(run_cli: Run, fake_world: FakeWorld) -> None:
    audio = _edit(fake_world).tracks["audio"][0].items[0]
    assert audio.GetDuration() == 120
    code, out, _ = run_cli(["item", "fades", "A1:1", "--in", "120"])
    assert code == 0
    assert "fade_in,0,119" in out
    assert "clamped to 119 (item is 120 frames)" in out
    assert "requested" in out and "120" in out
    assert fake_world.calls_to(SET_FADES) == []


def test_force_sends_the_clamped_value_and_verifies(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["item", "fades", "A1:1", "--in", "120", "--force"])
    assert code == 0
    assert "VERIFY_FAILED" not in out
    assert "applied: true" in out
    assert fake_world.calls_to(SET_FADES) == [({"FadeIn": 119, "FadeOut": 0},)]


def test_no_warning_when_in_range(run_cli: Run) -> None:
    code, out, _ = run_cli(["item", "fades", "A1:1", "--in", "12"])
    assert code == 0
    assert "warning" not in out and "requested" not in out


def test_overlap_warns_without_clamping(run_cli: Run) -> None:
    code, out, _ = run_cli(["item", "fades", "A1:1", "--in", "100", "--out", "100"])
    assert code == 0
    assert "overlap" in out and "clamped" not in out
    assert "fade_in,0,100" in out and "fade_out,0,100" in out


def test_fake_set_fades_clamps_and_rejects_bad_values(fake_world: FakeWorld) -> None:
    audio = _edit(fake_world).tracks["audio"][0].items[0]
    assert audio.SetFades({"FadeIn": 500}) is True
    assert audio.GetFades()["FadeIn"] == 119
    assert audio.SetFades({"FadeOut": -1}) is False
    assert audio.SetFades({"FadeOut": 1.5}) is False
