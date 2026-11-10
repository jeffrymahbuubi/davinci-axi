"""`color cdl set` on an item list (F8) and its help notes (F7), on the fake."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_color
from davinci_axi.fake.timeline import FakeTimelineItem

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]
SAVE, EXPORT = "ProjectManager.SaveProject", "ProjectManager.ExportProject"
SET_CDL = "TimelineItem.SetCDL"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_color.extend()
    monkeypatch.setitem(registry.COMMANDS, "color", "davinci_axi.commands.color:SPEC")
    yield


def _item(world: FakeWorld, number: int, track: int = 1) -> FakeTimelineItem:
    project = world.resolve.pm.current_project
    assert project is not None
    return project.timelines[0].tracks["video"][track - 1].items[number - 1]


def _apply(
    run_cli: Run, keys: Keys, world: FakeWorld, argv: list[str]
) -> tuple[dict[str, str], str]:
    code, out, err = run_cli([*argv, "--force"])
    assert (code, err) == (0, ""), out
    assert keys(out)["applied"] == "true", out
    assert world.write_calls()[:2] == [SAVE, EXPORT]
    return keys(out), out


def test_cdl_set_on_a_list_calls_each_item_once_with_one_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    keys, _ = _apply(
        run_cli,
        parse_toon_keys,
        fake_world,
        ["color", "cdl", "set", "V1:1,V1:3", "NodeIndex=1,Saturation=0.5"],
    )
    assert len(fake_world.calls_to(SET_CDL)) == 2
    assert fake_world.write_calls().count(EXPORT) == 1
    for number in (1, 3):
        applied = _item(fake_world, number).__dict__["cdl"]
        assert len(applied) == 1 and applied[0]["Saturation"] == 0.5
    assert keys["targets"].strip('"') == "V1:1, V1:3"
    assert keys["count"] == "2"
    assert "NodeIndex=1" in keys["cdl"] and "Slope, Offset, Power" in keys["neutral"]
    assert "read_back" in keys


@pytest.mark.parametrize("target", ["timeline", "group:Hero:pre"])
def test_cdl_set_refuses_shared_graphs_before_connecting(
    run_cli: Run, fake_world: FakeWorld, target: str
) -> None:
    code, out, err = run_cli(["color", "cdl", "set", target, "NodeIndex=1,Saturation=0.5"])
    assert code == 2, out
    assert "SetCDL" in out + err
    assert fake_world.calls == []


def test_cdl_set_list_partial_failure(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    second = _item(fake_world, 3)
    monkeypatch.setattr(second, "SetCDL", lambda _cdl: False)
    code, out, _ = run_cli(
        ["color", "cdl", "set", "V1:1,V1:3", "NodeIndex=1,Saturation=0.5", "--force"]
    )
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert 'failed: "V1:3"' in out and "not_done: []" in out
    assert '"V1:1"' in out  # done
    assert len(_item(fake_world, 1).__dict__["cdl"]) == 1


def test_cdl_node_range_is_checked_for_every_item(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    graphs: Any = _item(fake_world, 3)
    ext_color._graph(graphs, 1, ["Only"])
    graphs.__dict__["graphs"][1].nodes = graphs.__dict__["graphs"][1].nodes[:1]
    code, out, _ = run_cli(
        ["color", "cdl", "set", "V1:1,V1:3", "NodeIndex=2,Saturation=0.5", "--force"]
    )
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert "V1:3" in out
    assert fake_world.write_calls() == []


def test_cdl_help_has_the_item_only_and_no_getcdl_notes(run_cli: Run) -> None:
    code, out, err = run_cli(["color", "cdl", "--help"])
    assert (code, err) == (0, "")
    assert "GetCDL" in out and "item-only" in out
    code, out, _ = run_cli(["color", "cdl", "set", "--help"])
    assert code == 0 and "item-only" in out  # the positional's help
