"""`fusion tool set` Point inputs, `fusion tool press` and the recipe notes (U9) on the fake."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.commands.fusion_tool_set import SET_VERBS, is_point, parse_point
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeWorld, ext_fusion, ext_fusion_edit, ext_writes_core
from davinci_axi.fake.ext_fusion import FakeComp
from davinci_axi.fake.timeline import FakeTimelineItem

Run = Callable[[list[str]], tuple[int, str, str]]

ITEM = ["--item", "V1:2"]


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext_fusion.extend()
    ext_fusion_edit.extend()
    monkeypatch.setitem(registry.COMMANDS, "fusion", "davinci_axi.commands.fusion_comp:SPEC")
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    yield


def _clip(world: FakeWorld, number: int) -> FakeTimelineItem:
    project = world.resolve.pm.current_project
    assert project is not None and project.current_timeline is not None
    return project.current_timeline.tracks["video"][0].items[number - 1]


def _comp(world: FakeWorld) -> FakeComp:
    return ext_fusion.comp_of(_clip(world, 2))


def _json(run_cli: Run, argv: list[str]) -> tuple[int, dict[str, Any]]:
    code, out, _ = run_cli([*argv, "--json"])
    return code, json.loads(out)


def _add_transform(world: FakeWorld) -> None:
    """Put a `Transform1` in the comp without the CLI, then forget the calls and undo entries."""
    comp = _comp(world)
    assert comp.AddTool("Transform", 1.0, 1.0) is not None
    world.calls.clear()
    comp.undo_stack.clear()


def test_parse_point_and_is_point() -> None:
    assert parse_point("0.8  0.1") == {1: 0.8, 2: 0.1}
    for bad in ("0.8", "a b", "1 2 3", "", "nan 1"):
        with pytest.raises(AxiError):
            parse_point(bad)
    assert is_point("Point", None)
    assert is_point("Number", {1: 0.5, 2: 0.5})
    assert not is_point("Number", 1.0)
    assert not is_point("Number", {})
    assert not is_point("Number", {1: 0.5, 3: 0.5})


def test_set_verbs_export_press() -> None:
    assert set(SET_VERBS) == {"set", "press"}


def test_set_point_reads_back(run_cli: Run, fake_world: FakeWorld) -> None:
    _add_transform(fake_world)
    argv = ["fusion", "tool", "set", "Transform1", "Center=0.8 0.1", *ITEM]
    code, data = _json(run_cli, argv)
    assert code == 0 and data["applied"] is False
    assert data["changes"] == [{"field": "Center", "before": "0.5 0.5", "after": "0.8 0.1"}]
    assert ext_fusion.fusion_write_calls(fake_world) == []
    code, data = _json(run_cli, [*argv, "--force"])
    assert code == 0 and data["applied"] is True
    assert fake_world.calls_to("FusionTool.SetInput")[0] == ("Center", {1: 0.8, 2: 0.1}, None)
    assert _comp(fake_world).FindTool("Transform1").GetInput("Center") == {1: 0.8, 2: 0.1}
    assert data["changes"] == [{"field": "Center", "before": "0.5 0.5", "after": "0.8 0.1"}]


def test_set_point_unchanged_is_a_noop(run_cli: Run, fake_world: FakeWorld) -> None:
    _add_transform(fake_world)
    argv = ["fusion", "tool", "set", "Transform1", "Center=0.5 0.5", *ITEM, "--force"]
    code, data = _json(run_cli, argv)
    assert code == 0 and data["applied"] is False and data["changes"] == []
    assert ext_fusion.fusion_write_calls(fake_world) == []


@pytest.mark.parametrize("assignment", ["Center=0.8", "Center=a b", "Center=1,2"])
def test_set_point_rejects_malformed_before_any_write(
    run_cli: Run, fake_world: FakeWorld, assignment: str
) -> None:
    _add_transform(fake_world)
    code, _, _ = run_cli(["fusion", "tool", "set", "Transform1", assignment, *ITEM, "--force"])
    assert code == 2
    assert ext_fusion.fusion_write_calls(fake_world) == []


def test_set_still_refuses_images(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["fusion", "tool", "set", "MediaOut1", "Input=1", *ITEM, "--force"])
    assert code == 2 and "VALIDATION_ERROR" in out and "fusion tool connect" in out
    assert ext_fusion.fusion_write_calls(fake_world) == []


def test_press_sets_the_input_to_one(run_cli: Run, fake_world: FakeWorld) -> None:
    _add_transform(fake_world)
    argv = ["fusion", "tool", "press", "Transform1", "Angle", *ITEM]
    code, data = _json(run_cli, argv)
    assert code == 0 and data["applied"] is False
    assert ext_fusion.fusion_write_calls(fake_world) == []
    code, data = _json(run_cli, [*argv, "--force"])
    assert code == 0 and data["applied"] is True and data["warning"]
    assert fake_world.calls_to("FusionTool.SetInput") == [("Angle", 1, None)]
    assert "pressed" in json.dumps(data)


def test_press_validates_before_any_write(run_cli: Run, fake_world: FakeWorld) -> None:
    _add_transform(fake_world)
    code, out, _ = run_cli(["fusion", "tool", "press", "Transform1", "Nope", *ITEM, "--force"])
    assert code == 1 and "NOT_FOUND" in out
    code, out, _ = run_cli(["fusion", "tool", "press", "MediaOut1", "Input", *ITEM, "--force"])
    assert code == 2 and "not a button" in out
    code, out, _ = run_cli(["fusion", "tool", "press", "Nope1", "Angle", *ITEM, "--force"])
    assert code == 1 and "NOT_FOUND" in out
    assert ext_fusion.fusion_write_calls(fake_world) == []


def test_help_carries_the_point_syntax_and_recipe(run_cli: Run) -> None:
    code, out, _ = run_cli(["fusion", "tool", "set", "--help"])
    assert code == 0 and "Center=0.8 0.1" in out and "ObjectRemoval1.Mask" in out
    code, out, _ = run_cli(["fusion", "tool", "press", "--help"])
    assert code == 0 and "SceneAnalysis" in out
