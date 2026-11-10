"""`fusion tool add --after` (F16): the new tool is wired between the anchor and its consumers."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_fusion, ext_fusion_edit, ext_writes_core
from davinci_axi.fake.ext_fusion import FakeComp
from davinci_axi.fake.timeline import FakeTimelineItem

Run = Callable[[list[str]], tuple[int, str, str]]

ITEM = ["--item", "V1:2"]
CONNECT = "FusionTool.ConnectInput"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext_fusion.extend()
    ext_fusion_edit.extend()
    monkeypatch.setitem(registry.COMMANDS, "fusion", "davinci_axi.commands.fusion_comp:SPEC")
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    yield


def _comp(world: FakeWorld) -> FakeComp:
    project = world.resolve.pm.current_project
    assert project is not None and project.current_timeline is not None
    clip: FakeTimelineItem = project.current_timeline.tracks["video"][0].items[1]
    return ext_fusion.comp_of(clip)


def _json(run_cli: Run, argv: list[str]) -> tuple[int, dict[str, Any]]:
    code, out, _ = run_cli([*argv, "--json"])
    return code, json.loads(out)


def _rows(data: dict[str, Any]) -> list[tuple[str, object, object]]:
    return [(r["field"], r["before"], r["after"]) for r in data["changes"]]


def _edges(run_cli: Run) -> set[tuple[str, str]]:
    _, out, _ = run_cli(["fusion", "graph", *ITEM, "--json"])
    graph = json.loads(out)
    return {(e["from"], e["to"]) for e in graph["edges"]} if graph["edges"] else set()


def test_plain_add_wires_nothing(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _ = _json(run_cli, ["fusion", "tool", "add", "Blur", *ITEM, "--force"])
    assert code == 0
    assert [t.name for t in _comp(fake_world).tools] == ["MediaIn1", "MediaOut1", "Blur1"]
    assert _edges(run_cli) == {("MediaIn1", "MediaOut1")}
    assert fake_world.calls_to(CONNECT) == []


def test_after_inserts_the_tool_between_the_anchor_and_its_consumer(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    argv = ["fusion", "tool", "add", "Blur", *ITEM, "--after", "MediaIn1", "--force"]
    code, data = _json(run_cli, argv)
    assert code == 0 and data["applied"] is True
    assert _edges(run_cli) == {("MediaIn1", "Blur1"), ("Blur1", "MediaOut1")}
    assert len(fake_world.calls_to(CONNECT)) == 2
    assert _rows(data) == [
        ("tool", None, "Blur1"),
        ("Blur1.Input", None, "MediaIn1.Output"),
        ("MediaOut1.Input", "MediaIn1.Output", "Blur1.Output"),
    ]
    assert data["wired"] == ["Blur1.Input", "MediaOut1.Input"]
    assert fake_world.calls_to("FusionComp.EndUndo") == [(True,)]
    assert fake_world.calls_to("FusionComp.SetActiveTool") == []


def test_after_repoints_every_consumer_of_the_anchor(run_cli: Run, fake_world: FakeWorld) -> None:
    comp = _comp(fake_world)
    merge = comp.AddTool("Merge", 3, 1)
    assert merge is not None
    merge.inputs[0].connected = comp.FindTool("MediaIn1").outputs[0]
    fake_world.calls.clear()
    argv = ["fusion", "tool", "add", "Blur", *ITEM, "--after", "MediaIn1", "--force"]
    code, data = _json(run_cli, argv)
    assert code == 0 and data["wired"] == ["Blur1.Input", "MediaOut1.Input", "Merge1.Background"]
    assert len(fake_world.calls_to(CONNECT)) == 3
    assert _edges(run_cli) == {
        ("MediaIn1", "Blur1"),
        ("Blur1", "MediaOut1"),
        ("Blur1", "Merge1"),
    }


def test_after_with_a_tool_that_has_no_image_input_is_refused_before_any_write(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    argv = ["fusion", "tool", "add", "TextPlus", *ITEM, "--after", "MediaIn1", "--force"]
    code, out, _ = run_cli(argv)
    assert code == 2 and "no image input" in out
    assert ext_fusion.fusion_write_calls(fake_world) == []
    assert fake_world.calls_to("FusionComp.AddTool") == []


def test_a_refused_connection_is_api_failed_and_discards_the_undo_event(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject(CONNECT, False)
    argv = ["fusion", "tool", "add", "Blur", *ITEM, "--after", "MediaIn1", "--force"]
    code, out, _ = run_cli(argv)
    assert code == 1 and "API_FAILED" in out and "Blur1.Input" in out
    assert "reads unconnected" in out
    assert "comp_snapshot" in out
    assert fake_world.calls_to("FusionComp.EndUndo") == [(False,)]


def test_a_false_return_with_the_links_in_place_succeeds_with_a_note(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _comp(fake_world).__dict__["connect_returns_false"] = True
    argv = ["fusion", "tool", "add", "Blur", *ITEM, "--after", "MediaIn1", "--force"]
    code, data = _json(run_cli, argv)
    assert code == 0 and data["applied"] is True
    assert "ConnectInput returned False" in data["note"]
    assert _edges(run_cli) == {("MediaIn1", "Blur1"), ("Blur1", "MediaOut1")}
    assert fake_world.calls_to("FusionComp.EndUndo") == [(True,)]


def test_the_consumer_repoint_is_verified_by_read_back(run_cli: Run, fake_world: FakeWorld) -> None:
    comp = _comp(fake_world)
    original = comp.FindTool("MediaOut1")
    assert original is not None
    # The second ConnectInput (the consumer repoint) returns True but wires nothing.
    calls = {"n": 0}
    real = type(original).ConnectInput

    def flaky(self: Any, input_id: str, source: Any) -> bool:
        calls["n"] += 1
        return True if calls["n"] == 2 else real(self, input_id, source)

    type(original).ConnectInput = flaky  # type: ignore[method-assign]
    try:
        argv = ["fusion", "tool", "add", "Blur", *ITEM, "--after", "MediaIn1", "--force"]
        code, out, _ = run_cli(argv)
    finally:
        type(original).ConnectInput = real  # type: ignore[method-assign]
    assert code == 1 and "API_FAILED" in out and "MediaOut1.Input" in out
    assert fake_world.calls_to("FusionComp.EndUndo") == [(False,)]


def test_rectangle_mask_prints_the_shortened_name(run_cli: Run, fake_world: FakeWorld) -> None:
    code, data = _json(run_cli, ["fusion", "tool", "add", "RectangleMask", *ITEM])
    assert code == 0 and data["applied"] is False
    assert _rows(data) == [("tool", None, "Rectangle1")]
    code, data = _json(run_cli, ["fusion", "tool", "add", "RectangleMask", *ITEM, "--force"])
    assert code == 0 and _rows(data) == [("tool", None, "Rectangle1")]
    assert [t.name for t in _comp(fake_world).tools] == ["MediaIn1", "MediaOut1", "Rectangle1"]


def test_dry_run_with_after_shows_the_planned_links_and_writes_nothing(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, data = _json(run_cli, ["fusion", "tool", "add", "Blur", *ITEM, "--after", "MediaIn1"])
    assert code == 0 and data["applied"] is False
    assert _rows(data) == [
        ("tool", None, "Blur1"),
        ("Blur1.(main input)", None, "MediaIn1.Output"),
        ("MediaOut1.Input", "MediaIn1.Output", "Blur1.Output"),
    ]
    assert ext_fusion.fusion_write_calls(fake_world) == []
    assert fake_world.write_calls() == []
    assert [t.name for t in _comp(fake_world).tools] == ["MediaIn1", "MediaOut1"]


def test_pos_and_after_still_contradict(run_cli: Run, fake_world: FakeWorld) -> None:
    argv = ["fusion", "tool", "add", "Blur", *ITEM, "--pos", "1,1", "--after", "MediaIn1"]
    code, out, _ = run_cli(argv)
    assert code == 2 and "contradict" in out and fake_world.calls == []


def test_mask_recipe_tools_exist_in_the_fake(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _ = _json(
        run_cli, ["fusion", "tool", "add", "EllipseMask", "--name", "Spot", *ITEM, "--force"]
    )
    assert code == 0
    spot = _comp(fake_world).FindTool("Spot")
    assert spot is not None and spot.outputs[0].data_type == "Mask"
    code, _ = _json(run_cli, ["fusion", "tool", "add", "ObjectRemoval", *ITEM, "--force"])
    assert code == 0
    code, _ = _json(
        run_cli, ["fusion", "tool", "connect", "Spot", "ObjectRemoval1.Mask", *ITEM, "--force"]
    )
    assert code == 0
