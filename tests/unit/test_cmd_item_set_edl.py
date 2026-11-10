"""`item set --edl`: per-item properties from a row file, on the fake (R-D2)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import item_edit
from davinci_axi.commands.item_set_edl import SET_EXT_SPEC
from davinci_axi.fake import FakeProject, FakeWorld, ext_writes_core
from davinci_axi.fake import ext_item_edit as ext
from davinci_axi.fake.timeline import FakeTimelineItem
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]
SAVE, EXPORT = "ProjectManager.SaveProject", "ProjectManager.ExportProject"
SET_PROPS = "TimelineItem.SetProperties"
SPEC: CommandSpec = replace(
    item_edit.SPEC, subcommands={**item_edit.SPEC.subcommands, "set": SET_EXT_SPEC}
)
CSV = "item,ZoomX,Tilt\nV1:1,1.25,-200\nV1:3,1.1,\n"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext.extend()
    monkeypatch.setitem(registry.COMMANDS, "item", f"{__name__}:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")
    yield


def _clip(world: FakeWorld, number: int) -> FakeTimelineItem:
    project: FakeProject | None = world.resolve.pm.current_project
    assert project is not None
    return project.timelines[0].tracks["video"][0].items[number - 1]


def _file(tmp_path: Path, text: str = CSV, name: str = "props.csv") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_dry_run_lists_every_real_change_and_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["item", "set", "--edl", _file(tmp_path)])
    assert (code, err) == (0, "")
    keys = parse_toon_keys(out)
    assert keys["applied"] == "false" and keys["rows"] == "2"
    assert '  "V1:1 ZoomX",1,1.25' in out and '  "V1:1 Tilt",0,-200' in out
    assert '  "V1:3 ZoomX",1,1.1' in out
    assert "V1:3 Tilt" not in out
    assert "--edl" in out and "--force" in out
    assert fake_world.write_calls() == []
    assert _clip(fake_world, 1).properties["ZoomX"] == 1.0


def test_force_sets_each_item_once_under_one_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["item", "set", "--edl", _file(tmp_path), "--force"])
    assert (code, err) == (0, "")
    assert parse_toon_keys(out)["applied"] == "true"
    assert len(fake_world.calls_to(SET_PROPS)) == 2
    assert fake_world.write_calls().count(EXPORT) == 1
    assert fake_world.write_calls()[:2] == [SAVE, EXPORT]
    assert _clip(fake_world, 1).properties["ZoomX"] == 1.25
    assert _clip(fake_world, 1).properties["Tilt"] == -200.0
    assert _clip(fake_world, 3).properties["ZoomX"] == 1.1
    assert _clip(fake_world, 3).properties["Tilt"] == 0.0


def test_json_rows_work_the_same(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    uid = _clip(fake_world, 3).GetUniqueId()
    rows = [{"item": "V1:2", "Opacity": 50}, {"item": uid, "Pan": 5}]
    path = _file(tmp_path, json.dumps(rows), "props.json")
    code, out, _ = run_cli(["item", "set", "--edl", path, "--force"])
    assert code == 0, out
    assert _clip(fake_world, 2).properties["Opacity"] == 50.0
    assert _clip(fake_world, 3).properties["Pan"] == 5.0


def test_every_refusal_is_recorded_after_all_rows_were_tried(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject(SET_PROPS, False)
    code, out, _ = run_cli(["item", "set", "--edl", _file(tmp_path), "--force", "--json"])
    assert code == 1
    error = json.loads(out)
    assert error["code"] == "API_FAILED"
    assert error["detail"]["failed"] == ["V1:1", "V1:3"] and error["detail"]["done"] == []
    assert len(fake_world.calls_to(SET_PROPS)) == 2


def test_one_refusal_keeps_the_others(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_clip(fake_world, 3), "SetProperties", lambda _props: False)
    code, out, _ = run_cli(["item", "set", "--edl", _file(tmp_path), "--force", "--json"])
    assert code == 1
    detail = json.loads(out)["detail"]
    assert detail["done"] == ["V1:1"] and detail["failed"] == ["V1:3"]
    assert _clip(fake_world, 1).properties["ZoomX"] == 1.25


def test_unknown_item_is_not_found_before_any_write(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _file(tmp_path, "item,ZoomX\nV1:1,1.5\nV1:9,2\n")
    code, out, _ = run_cli(["item", "set", "--edl", path, "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert fake_world.write_calls() == []


def test_unknown_property_names_the_row_and_valid_keys(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["item", "set", "--edl", _file(tmp_path, "item,Zoom\nV1:1,2\n")])
    assert code == 2 and "row 1" in out and "Zoom" in out and "ZoomX" in out
    assert fake_world.write_calls() == []


def test_bad_value_names_the_row(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(["item", "set", "--edl", _file(tmp_path, "item,ZoomX\nV1:1,wide\n")])
    assert code == 2 and "row 1" in out
    assert fake_world.write_calls() == []


def test_a_row_without_properties_is_refused(run_cli: Run, tmp_path: Path) -> None:
    code, out, _ = run_cli(["item", "set", "--edl", _file(tmp_path, "item,ZoomX\nV1:1,\n")])
    assert code == 2 and "no property" in out


def test_same_item_by_ref_and_id_is_refused(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    uid = _clip(fake_world, 1).GetUniqueId()
    path = _file(tmp_path, f"item,ZoomX\nV1:1,2\n{uid},3\n")
    code, out, _ = run_cli(["item", "set", "--edl", path])
    assert code == 2 and "same item" in out
    assert fake_world.write_calls() == []


def test_edl_together_with_positionals_is_exit_2(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["item", "set", "V1:1", "ZoomX=2", "--edl", _file(tmp_path)])
    assert code == 2, out
    assert "not both" in out + err
    assert fake_world.calls == []


def test_edl_on_an_empty_timeline_is_not_found(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["item", "set", "--edl", _file(tmp_path), "--timeline", "Edit v2"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert fake_world.write_calls() == []


def test_missing_file_is_not_found_before_connecting(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["item", "set", "--edl", str(tmp_path / "gone.csv")])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert fake_world.calls == []


def test_no_op_when_nothing_differs(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    path = _file(tmp_path, "item,ZoomX\nV1:1,1\n")
    code, out, _ = run_cli(["item", "set", "--edl", path, "--force"])
    assert code == 0 and "unchanged" in out
    assert fake_world.write_calls() == []


def test_single_item_set_still_works_and_needs_both_positionals(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["item", "set", "V1:2", "Opacity=50", "--force"])
    assert code == 0, out
    assert _clip(fake_world, 2).properties["Opacity"] == 50.0
    code, out, _ = run_cli(["item", "set", "V1:2"])
    assert code == 2 and "missing <assignments>" in out
    code, out, _ = run_cli(["item", "set"])
    assert code == 2 and "missing <id>" in out


def test_help_shows_the_flag_and_examples(run_cli: Run) -> None:
    code, out, err = run_cli(["item", "set", "--help"])
    assert (code, err) == (0, "")
    assert "--edl" in out and "examples[" in out and "item set --edl props.csv" in out
