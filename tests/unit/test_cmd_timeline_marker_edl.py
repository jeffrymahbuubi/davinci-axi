"""`timeline marker add --edl`: many markers from a row file on the fake (round 3, D)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.commands import timeline_extra
from davinci_axi.commands.timeline_marker_edl import MARKER_ADD_EDL_SPEC
from davinci_axi.fake import FakeProject, FakeTimeline, FakeWorld, ext_timeline_edit
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

ADD = "Timeline.AddMarker"
EXPORT = "ProjectManager.ExportProject"
MARKER = replace(
    timeline_extra.WRAPPED_MARKER,
    subcommands={**timeline_extra.WRAPPED_MARKER.subcommands, "add": MARKER_ADD_EDL_SPEC},
)
SPEC: CommandSpec = replace(
    timeline_extra.SPEC, subcommands={**timeline_extra.SPEC.subcommands, "marker": MARKER}
)
CSV = "frame,color,name\n10,Red,a\n50,Green,b\n70,Blue,c\n"
ADD_CMD = ["timeline", "marker", "add"]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_timeline_edit.extend()
    monkeypatch.setitem(registry.COMMANDS, "timeline", f"{__name__}:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")


def _timeline(world: FakeWorld, name: str = "Edit v1") -> FakeTimeline:
    project: FakeProject | None = world.resolve.pm.current_project
    assert project is not None
    return next(t for t in project.timelines if t.name == name)


def _file(tmp_path: Path, text: str = CSV, name: str = "rows.csv") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _json(out: str) -> dict[str, Any]:
    data = json.loads(out)
    assert isinstance(data, dict)
    return data


def test_dry_run_lists_a_change_per_row_and_records_no_add(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli([*ADD_CMD, "--edl", _file(tmp_path), "--json"])
    data = _json(out)
    assert (code, err) == (0, "")
    assert data["applied"] is False and data["rows"] == 3 and data["written"] == 0
    assert [c["field"] for c in data["changes"]] == ["marker@10", "marker@50", "marker@70"]
    assert [c["after"] for c in data["changes"]] == ["Red a", "Green b", "Blue c"]
    assert data["skipped"] == [] and "--edl" in out and "--force" in out
    assert fake_world.calls_to(ADD) == [] and fake_world.write_calls() == []


def test_force_adds_every_row_under_one_snapshot_and_reads_back_once(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli([*ADD_CMD, "--edl", _file(tmp_path), "--force", "--json"])
    data = _json(out)
    assert (code, err) == (0, "")
    assert data["applied"] is True and data["written"] == 3 and data["skipped"] == []
    assert len(fake_world.calls_to(ADD)) == 3
    assert fake_world.write_calls().count(EXPORT) == 1
    markers = _timeline(fake_world).markers
    assert {10, 50, 70} <= set(markers) and markers[50]["color"] == "Green"
    names = [f"{owner}.{method}" for owner, method, _ in fake_world.calls]
    assert names[names.index(ADD) :].count("Timeline.GetMarkers") == 1  # the verify read
    assert any("timeline marker delete --color Red --force" in h for h in data["help"])


def test_json_rows_work_like_csv_and_defaults_apply(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    rows = [{"frame": 5, "name": "x"}, {"frame": 9, "color": "Cyan", "duration": 4, "note": "n"}]
    path = _file(tmp_path, json.dumps(rows), "rows.json")
    code, _, _ = run_cli([*ADD_CMD, "--edl", path, "--force"])
    markers = _timeline(fake_world).markers
    assert code == 0
    assert (markers[5]["color"], markers[5]["duration"]) == ("Blue", 1)
    assert (markers[9]["color"], markers[9]["duration"], markers[9]["note"]) == ("Cyan", 4, "n")


def test_a_frame_with_a_marker_is_skipped_not_written(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    taken = {"color": "Red", "duration": 1, "note": "", "name": "old", "customData": ""}
    _timeline(fake_world).markers[50] = taken
    code, out, _ = run_cli([*ADD_CMD, "--edl", _file(tmp_path), "--force", "--json"])
    data = _json(out)
    assert code == 0 and data["written"] == 2 and data["rows"] == 3
    assert data["skipped"] == [{"frame": 50, "existing": "Red old"}]
    assert len(fake_world.calls_to(ADD)) == 2
    assert _timeline(fake_world).markers[50]["name"] == "old"


def test_every_frame_taken_is_a_no_op(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    path = _file(tmp_path, "frame\n100\n")
    code, out, _ = run_cli([*ADD_CMD, "--edl", path, "--force", "--json"])
    data = _json(out)
    assert code == 0 and data["applied"] is False and data["changes"] == []
    assert data["note"] == "every frame already has a marker" and len(data["skipped"]) == 1
    assert fake_world.write_calls() == []


def test_some_rows_refused_warns_and_drops_them(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = FakeTimeline.AddMarker

    def flaky(self: FakeTimeline, frame: int, *rest: Any) -> bool:
        return False if frame == 50 else bool(original(self, frame, *rest))

    monkeypatch.setattr(FakeTimeline, "AddMarker", flaky)
    code, out, _ = run_cli([*ADD_CMD, "--edl", _file(tmp_path), "--force", "--json"])
    data = _json(out)
    assert code == 0 and data["written"] == 2
    assert "1 row(s) refused by AddMarker: 50" in data["warning"]
    assert [c["field"] for c in data["changes"]] == ["marker@10", "marker@70"]


def test_every_row_refused_is_api_failed(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject(ADD, False)
    code, out, _ = run_cli([*ADD_CMD, "--edl", _file(tmp_path), "--force", "--json"])
    assert code == 1 and "API_FAILED" in out


def test_duplicate_frame_exits_two(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    path = _file(tmp_path, "frame,name\n10,a\n50,b\n50,c\n")
    code, out, _ = run_cli([*ADD_CMD, "--edl", path, "--json"])
    assert code == 2 and "row 3: 50 already appears in row 2" in out
    assert fake_world.calls == []


def test_frame_written_twice_in_different_spellings_exits_two(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _file(tmp_path, "frame\n5\n05\n")
    code, out, _ = run_cli([*ADD_CMD, "--edl", path, "--json"])
    assert code == 2 and "row 2: 5 already appears in row 1" in out


def test_frame_past_the_end_names_the_row(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _file(tmp_path, "frame\n10\n9999\n")
    code, out, _ = run_cli([*ADD_CMD, "--edl", path, "--force", "--json"])
    assert code == 2 and "row 2: frame 9999 is past the end (240 frames)" in out
    assert fake_world.write_calls() == []


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("frame,color\n10,Mauve\n", "row 1: Mauve is not a marker colour"),
        ("frame\n-3\n", "row 1: frame must be a whole number of 0 or more"),
        ("frame\nabc\n", "row 1: frame must be a whole number"),
        ("frame,duration\n3,0\n", "row 1: duration must be a whole number of 1 or more"),
        ("frame,mood\n3,x\n", "row 1: unknown column mood"),
        ("name\nx\n", "row 1: frame is required"),
    ],
)
def test_bad_rows_exit_two_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, text: str, needle: str
) -> None:
    code, out, _ = run_cli([*ADD_CMD, "--edl", _file(tmp_path, text), "--json"])
    data = _json(out)
    assert code == 2 and needle in data["error"] and data["help"]
    assert fake_world.calls == []


def test_a_bad_colour_names_the_valid_ones(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _file(tmp_path, "frame,color\n10,Mauve\n")
    _, out, _ = run_cli([*ADD_CMD, "--edl", path, "--json"])
    assert "Valid colours: Blue, Cyan, Green" in " ".join(_json(out)["help"])


@pytest.mark.parametrize(
    "extra",
    [
        ["--frame", "1"],
        ["--frame", "0"],
        ["--color", "Red"],
        ["--name", "x"],
        ["--note", "x"],
        ["--duration", "4"],
        ["--custom-data", "x"],
    ],
)
def test_edl_with_a_single_marker_flag_exits_two(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, extra: list[str]
) -> None:
    code, out, _ = run_cli([*ADD_CMD, "--edl", _file(tmp_path), *extra, "--json"])
    data = _json(out)
    assert code == 2 and "use --edl or the marker flags, not both" in data["error"]
    assert data["help"] and fake_world.calls == []


def test_missing_file_is_not_found(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli([*ADD_CMD, "--edl", str(tmp_path / "none.csv"), "--json"])
    assert code == 1 and "NOT_FOUND" in out and fake_world.calls == []


def test_no_edl_and_no_frame_explains_both_forms(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli([*ADD_CMD, "--json"])
    assert code == 2 and any("--edl" in h for h in _json(out)["help"])


def test_single_add_is_unchanged(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli([*ADD_CMD, "--frame", "10", "--name", "Pick"])
    assert code == 0 and "  marker@10,null,Blue Pick" in out
    code, _, _ = run_cli([*ADD_CMD, "--frame", "10", "--name", "Pick", "--force"])
    assert code == 0 and 10 in _timeline(fake_world).markers


def test_timeline_flag_targets_another_timeline(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _file(tmp_path, "frame,name\n20,x\n")
    code, out, _ = run_cli([*ADD_CMD, "--edl", path, "--timeline", "Edit v2", "--force", "--json"])
    data = _json(out)
    assert code == 0 and data["written"] == 1 and "Edit v2" in data["target"]
    assert 20 in _timeline(fake_world, "Edit v2").markers
    assert 20 not in _timeline(fake_world).markers


def test_dry_run_force_command_keeps_edl_and_timeline(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _file(tmp_path, "frame\n20\n")
    _, out, _ = run_cli([*ADD_CMD, "--edl", path, "--timeline", "Edit v2", "--json"])
    line = next(h for h in _json(out)["help"] if "--force" in h)
    assert f"--edl {path}" in line and "--timeline 'Edit v2'" in line


def test_help_shows_edl_and_frame_is_optional(run_cli: Run) -> None:
    code, out, err = run_cli([*ADD_CMD, "--help"])
    assert code == 0 and err == "" and "--edl" in out and "marker add --edl gaps.csv" in out


def test_every_documented_column_reaches_add_marker(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _file(tmp_path, "frame,color,name,note,duration,custom\n10,Red,a,n,4,tag-1\n")
    code, _, _ = run_cli([*ADD_CMD, "--edl", path, "--force", "--json"])
    assert code == 0
    assert fake_world.calls_to(ADD) == [(10, "Red", "a", "n", 4, "tag-1")]
    assert _timeline(fake_world).markers[10]["customData"] == "tag-1"


def test_custom_data_column_still_works(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    rows = [{"frame": 7, "name": "x", "custom_data": "old-name"}]
    path = _file(tmp_path, json.dumps(rows), "rows.json")
    code, _, _ = run_cli([*ADD_CMD, "--edl", path, "--force", "--json"])
    assert code == 0 and fake_world.calls_to(ADD) == [(7, "Blue", "x", "", 1, "old-name")]


def test_custom_and_custom_data_in_one_row_exit_two(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _file(tmp_path, "frame,custom,custom_data\n10,a,b\n")
    code, out, _ = run_cli([*ADD_CMD, "--edl", path, "--json"])
    data = _json(out)
    assert code == 2 and "row 1: give custom or custom_data, not both" in data["error"]
    assert data["help"] and fake_world.calls == []
