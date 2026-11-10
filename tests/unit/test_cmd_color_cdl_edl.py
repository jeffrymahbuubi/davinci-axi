"""`color cdl set --edl`: one CDL per item from a row file, on the fake (R-D3)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.commands import color
from davinci_axi.commands.color_cdl_edl import CDL_EXT_SPEC
from davinci_axi.fake import FakeWorld, ext_color
from davinci_axi.fake.timeline import FakeTimelineItem
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]
SAVE, EXPORT = "ProjectManager.SaveProject", "ProjectManager.ExportProject"
SET_CDL = "TimelineItem.SetCDL"
SPEC: CommandSpec = replace(color.SPEC, subcommands={**color.SPEC.subcommands, "cdl": CDL_EXT_SPEC})
CSV = "item,NodeIndex,Slope,Saturation\nV1:1,1,1.1 1 0.9,\nV1:3,1,,0.5\n"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_color.extend()
    monkeypatch.setitem(registry.COMMANDS, "color", f"{__name__}:SPEC")
    yield


def _item(world: FakeWorld, number: int) -> FakeTimelineItem:
    project = world.resolve.pm.current_project
    assert project is not None
    return project.timelines[0].tracks["video"][0].items[number - 1]


def _file(tmp_path: Path, text: str = CSV, name: str = "grades.csv") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _cdls(world: FakeWorld, number: int) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = _item(world, number).__dict__["cdl"]
    return applied


def test_dry_run_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["color", "cdl", "set", "--edl", _file(tmp_path)])
    assert (code, err) == (0, "")
    keys = parse_toon_keys(out)
    assert keys["applied"] == "false" and keys["rows"] == "2"
    assert "2 CDLs from grades.csv" in out and "--edl" in out
    assert fake_world.write_calls() == []


def test_force_calls_set_cdl_per_row_with_different_dicts_and_one_snapshot(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["color", "cdl", "set", "--edl", _file(tmp_path), "--force"])
    assert (code, err) == (0, ""), out
    assert len(fake_world.calls_to(SET_CDL)) == 2
    assert fake_world.write_calls()[:2] == [SAVE, EXPORT]
    assert fake_world.write_calls().count(EXPORT) == 1
    first, second = _cdls(fake_world, 1)[0], _cdls(fake_world, 3)[0]
    assert first != second
    assert first["Slope"] == "1.1 1 0.9" and first["Saturation"] == 1.0
    assert second["Slope"] == "1 1 1" and second["Saturation"] == 0.5
    assert "cdl" not in _item(fake_world, 2).__dict__


def test_json_rows_and_the_per_ref_cdl_under_json(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    rows = [
        {"item": "V1:1", "NodeIndex": 1, "Slope": "2 2 2"},
        {"item": "V1:2", "NodeIndex": 1, "Slope": "0.5 0.5 0.5"},
    ]
    path = _file(tmp_path, json.dumps(rows), "grades.json")
    code, out, _ = run_cli(["color", "cdl", "set", "--edl", path, "--force", "--json"])
    assert code == 0, out
    data = json.loads(out)
    assert set(data["cdl"]) == {"V1:1", "V1:2"} and "Slope=2 2 2" in data["cdl"]["V1:1"]
    assert _cdls(fake_world, 1)[0]["Slope"] == "2 2 2"
    assert _cdls(fake_world, 2)[0]["Slope"] == "0.5 0.5 0.5"


def test_node_index_beyond_the_graph_is_not_found(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _file(tmp_path, "item,NodeIndex,Saturation\nV1:1,1,0.5\nV1:3,3,0.5\n")
    graphs: Any = _item(fake_world, 3)
    ext_color._graph(graphs, 1, ["Only"])
    graphs.__dict__["graphs"][1].nodes = graphs.__dict__["graphs"][1].nodes[:1]
    code, out, _ = run_cli(["color", "cdl", "set", "--edl", path, "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert "V1:3" in out
    assert fake_world.write_calls() == []


def test_unknown_item_is_not_found_before_any_write(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _file(tmp_path, "item,NodeIndex,Saturation\nV1:1,1,0.5\nV1:9,1,0.5\n")
    code, out, _ = run_cli(["color", "cdl", "set", "--edl", path, "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert fake_world.write_calls() == []


def test_bad_row_names_the_row_and_nothing_connects(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _file(tmp_path, "item,NodeIndex,Slope\nV1:1,1,1 1 1\nV1:3,1,fast\n")
    code, out, _ = run_cli(["color", "cdl", "set", "--edl", path, "--force"])
    assert code == 2 and "row 2" in out and "three numbers" in out
    assert fake_world.calls == []


def test_row_without_a_node_index_or_value_is_refused(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(
        ["color", "cdl", "set", "--edl", _file(tmp_path, "item,Slope\nV1:1,1 1 1\n")]
    )
    assert code == 2 and "row 1" in out and "NodeIndex" in out
    assert fake_world.calls == []


def test_unknown_column_is_refused_listing_the_valid_ones(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(
        ["color", "cdl", "set", "--edl", _file(tmp_path, "item,NodeIndex,Gain\nV1:1,1,2\n")]
    )
    assert code == 2 and "Gain" in out and "Saturation" in out
    assert fake_world.calls == []


def test_edl_together_with_positionals_is_exit_2(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    argv = ["color", "cdl", "set", "V1:1", "NodeIndex=1,Saturation=0.5", "--edl", _file(tmp_path)]
    code, out, err = run_cli(argv)
    assert code == 2 and "not both" in out + err
    assert fake_world.calls == []


def test_edl_on_an_empty_timeline_is_not_found(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    argv = ["color", "cdl", "set", "--edl", _file(tmp_path), "--timeline", "Edit v2"]
    code, out, _ = run_cli(argv)
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert fake_world.write_calls() == []


def test_a_refusal_reports_done_and_failed(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_item(fake_world, 3), "SetCDL", lambda _cdl: False)
    code, out, _ = run_cli(["color", "cdl", "set", "--edl", _file(tmp_path), "--force", "--json"])
    assert code == 1
    error = json.loads(out)
    assert error["code"] == "API_FAILED" and error["detail"]["failed"] == "V1:3"


def test_single_cdl_set_still_works_and_needs_both_positionals(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["color", "cdl", "set", "V1:1", "NodeIndex=1,Saturation=0.8", "--force"])
    assert code == 0, out
    assert _cdls(fake_world, 1)[0]["Saturation"] == 0.8
    code, out, _ = run_cli(["color", "cdl", "set", "V1:1"])
    assert code == 2 and "missing <assignments>" in out
    code, out, _ = run_cli(["color", "cdl", "set"])
    assert code == 2 and "missing <target>" in out


def test_help_shows_the_flag_examples_and_keeps_the_item_only_note(run_cli: Run) -> None:
    code, out, err = run_cli(["color", "cdl", "set", "--help"])
    assert (code, err) == (0, "")
    assert "--edl" in out and "examples[" in out and "color cdl set --edl grades.csv" in out
    assert "item-only" in out
