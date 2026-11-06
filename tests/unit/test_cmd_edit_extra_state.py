"""U25 timeline verbs: mark, blanking, stereo, Dolby Vision, selection and custom marker data,
plus the safety checks (dry run, AE6 snapshot failure, --no-snapshot) over every timeline verb.
Clip verbs are in test_cmd_edit_extra.py and item verbs in test_cmd_edit_extra_item.py."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeProject, FakeTimeline, FakeWorld, ext_edit_extra

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

SAVE = "ProjectManager.SaveProject"
EXPORT = "ProjectManager.ExportProject"


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_edit_extra.extend()
    ext_edit_extra.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "timeline", "davinci_axi.commands.timeline_extra:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _edit(world: FakeWorld, name: str = "Edit v1") -> FakeTimeline:
    return next(t for t in _project(world).timelines if t.name == name)


def _count(timeline: FakeTimeline, kind: str, index: int) -> int:
    return len(timeline.tracks[kind][index - 1].items)


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _aaf(tmp_path: Path, name: str = "cut.aaf") -> Path:
    path = tmp_path / name
    path.write_text("fake aaf\n", encoding="utf-8")
    return path


def test_mark_set_reads_back_and_takes_no_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "mark", "set", "--in", "10", "--out", "100", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] == "none"
    assert fake_world.write_calls() == ["Timeline.SetMarkInOut"]
    assert _edit(fake_world).mark_in_out == {
        "video": {"in": 10, "out": 100},
        "audio": {"in": 10, "out": 100},
    }
    assert not _snapshots(tmp_path).exists()


def test_mark_set_one_type_then_clear_it(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    args = ["timeline", "mark", "set", "--in", "5", "--out", "50", "--type", "video", "--force"]
    assert run_cli(args)[0] == 0
    assert fake_world.calls_to("Timeline.SetMarkInOut") == [(5, 50, "video")]
    assert list(_edit(fake_world).mark_in_out) == ["video"]
    code, out, _ = run_cli(["timeline", "mark", "clear", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert _edit(fake_world).mark_in_out == {}
    code, out, _ = run_cli(["timeline", "mark", "clear", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"


def test_mark_dry_run_and_bad_range(run_cli: Run, fake_world: FakeWorld) -> None:
    assert run_cli(["timeline", "mark", "set", "--in", "10", "--out", "100"])[0] == 0
    assert fake_world.write_calls() == [] and _edit(fake_world).mark_in_out == {}
    assert run_cli(["timeline", "mark", "set", "--in", "100", "--out", "10", "--force"])[0] == 2
    assert run_cli(["timeline", "mark", "set", "--in", "1", "--out", "9", "--type", "x"])[0] == 2
    assert run_cli(["timeline", "mark", "set", "--out", "9"])[0] == 2


def test_mark_refused_write_is_api_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("Timeline.SetMarkInOut", False)
    code, out, _ = run_cli(["timeline", "mark", "set", "--in", "1", "--out", "9", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"


def test_blanking_set_sends_all_four_keys_and_show_reads_them(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "blanking", "set", "Top=10"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    code, out, _ = run_cli(["timeline", "blanking", "set", "Top=10", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.calls_to("Timeline.SetOutputBlanking") == [
        ({"Top": 10, "Bottom": 0, "Left": 0, "Right": 0},)
    ]
    assert fake_world.write_calls() == [SAVE, EXPORT, "Timeline.SetOutputBlanking"]
    keys = parse_toon_keys(run_cli(["timeline", "blanking", "show"])[1])
    assert (keys["timeline"], keys["Top"], keys["Left"]) == ("Edit v1", "10", "0")
    code, out, _ = run_cli(["timeline", "blanking", "set", "Top=10", "--force"])
    assert parse_toon_keys(out)["applied"] == "false"


@pytest.mark.parametrize("bad", ["Middle=3", "Top=-1", "Top=x", "Top", "Top=1,Top=2"])
def test_blanking_set_rejects_bad_assignments(
    run_cli: Run, fake_world: FakeWorld, bad: str
) -> None:
    assert run_cli(["timeline", "blanking", "set", bad, "--force"])[0] == 2
    assert fake_world.write_calls() == []


def test_blanking_read_back_mismatch_is_verify_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("Timeline.GetOutputBlanking", {"Top": 0})
    code, out, _ = run_cli(["timeline", "blanking", "set", "Top=10", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "VERIFY_FAILED"


def test_stereo_convert_is_irreversible_and_checked_by_the_return_value(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "stereo", "convert"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and keys["irreversible"] == "true"
    assert "not read back" in keys["warning"] and fake_world.write_calls() == []
    code, out, _ = run_cli(["timeline", "stereo", "convert", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert ext_edit_extra.is_stereo(_edit(fake_world))
    assert fake_world.write_calls() == [SAVE, EXPORT, "Timeline.ConvertTimelineToStereo"]


def test_stereo_convert_refused_is_api_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("Timeline.ConvertTimelineToStereo", False)
    code, out, _ = run_cli(["timeline", "stereo", "convert", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert not ext_edit_extra.is_stereo(_edit(fake_world))


def test_dolby_vision_analyze_passes_the_blend_shots_constant(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "dolby-vision", "analyze", "V1:1,V1:2", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    items, mode = fake_world.calls_to("Timeline.AnalyzeDolbyVision")[0]
    assert len(items) == 2 and mode == fake_world.resolve.DLB_BLEND_SHOTS  # type: ignore[arg-type]
    assert ext_edit_extra.dolby_analyzed(_edit(fake_world)) == {"item-1", "item-2"}


def test_dolby_vision_refused_is_api_failed_and_missing_item_not_found(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("Timeline.AnalyzeDolbyVision", False)
    code, out, _ = run_cli(["timeline", "dolby-vision", "analyze", "V1:1", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert "Studio" in out
    code, out, _ = run_cli(["timeline", "dolby-vision", "analyze", "V9:9", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"


def test_selection_lists_the_selected_items(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "selection"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["selected"] == "0" and "select clips" in out
    edit = _edit(fake_world)
    edit.selected = [edit.tracks["video"][0].items[1], edit.tracks["audio"][0].items[0]]
    code, out, _ = run_cli(["timeline", "selection"])
    assert code == 0 and parse_toon_keys(out)["selected"] == "2"
    assert "clip-2" in out and "A001_beach" in out
    assert "A1" in out and fake_world.write_calls() == []


def test_custom_marker_data_roundtrip(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "marker", "custom", "set", "--frame", "100", "review-1"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    args = ["timeline", "marker", "custom", "set", "--frame", "100", "review-1", "--force"]
    code, out, _ = run_cli(args)
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.calls_to("Timeline.UpdateMarkerCustomData") == [(100, "review-1")]
    assert fake_world.write_calls() == [SAVE, EXPORT, "Timeline.UpdateMarkerCustomData"]
    keys = parse_toon_keys(run_cli(["timeline", "marker", "custom", "get", "--frame", "100"])[1])
    assert keys["custom_data"] == "review-1" and keys["name"] == "Marker 1"
    keys = parse_toon_keys(
        run_cli(["timeline", "marker", "custom", "get", "--find", "review-1"])[1]
    )
    assert keys["frame"] == "100" and keys["color"] == "Red"
    assert run_cli(args)[0] == 0
    assert parse_toon_keys(run_cli(args)[1])["applied"] == "false"


def test_custom_marker_data_errors(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "marker", "custom", "set", "--frame", "7", "x", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND" and "100" in out
    code, out, _ = run_cli(["timeline", "marker", "custom", "get", "--find", "nothing"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert run_cli(["timeline", "marker", "custom", "get"])[0] == 2
    assert run_cli(["timeline", "marker", "custom", "get", "--frame", "1", "--find", "x"])[0] == 2
    assert run_cli(["timeline", "marker", "custom", "set", "--frame", "-4", "x"])[0] == 2
    fake_world.inject("Timeline.UpdateMarkerCustomData", False)
    args = ["timeline", "marker", "custom", "set", "--frame", "100", "x", "--force"]
    code, out, _ = run_cli(args)
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"


MUTATIONS: list[tuple[list[str], str]] = [
    (["timeline", "append", "A003_dusk"], "MediaPool.AppendToTimeline"),
    (["timeline", "build", "scratch", "A001_beach"], "MediaPool.CreateTimelineFromClips"),
    (["timeline", "compound", "V1:1,V1:2"], "Timeline.CreateCompoundClip"),
    (["timeline", "insert", "title", "Text"], "Timeline.InsertTitleIntoTimeline"),
    (["timeline", "insert", "generator", "Solid"], "Timeline.InsertGeneratorIntoTimeline"),
    (["timeline", "insert", "ofx", "Noise"], "Timeline.InsertOFXGeneratorIntoTimeline"),
    (["timeline", "blanking", "set", "Top=10"], "Timeline.SetOutputBlanking"),
    (["timeline", "stereo", "convert"], "Timeline.ConvertTimelineToStereo"),
    (["timeline", "dolby-vision", "analyze", "V1:1"], "Timeline.AnalyzeDolbyVision"),
    (
        ["timeline", "marker", "custom", "set", "--frame", "100", "x"],
        "Timeline.UpdateMarkerCustomData",
    ),
]


@pytest.mark.parametrize(("argv", "method"), MUTATIONS)
def test_every_snapshot_mutation_is_a_dry_run_without_force(
    run_cli: Run,
    parse_toon_keys: Keys,
    fake_world: FakeWorld,
    tmp_path: Path,
    argv: list[str],
    method: str,
) -> None:
    code, out, _ = run_cli(argv)
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert "`davinci-axi timeline" in out and "--force`" in out
    assert fake_world.write_calls() == [] and fake_world.calls_to(method) == []
    assert not _snapshots(tmp_path).exists()


@pytest.mark.parametrize(("argv", "method"), MUTATIONS)
@pytest.mark.parametrize("failing", [SAVE, EXPORT])
def test_snapshot_failure_aborts_before_the_write(
    run_cli: Run,
    parse_toon_keys: Keys,
    fake_world: FakeWorld,
    argv: list[str],
    method: str,
    failing: str,
) -> None:
    fake_world.inject(failing, False)
    code, out, _ = run_cli([*argv, "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert method not in fake_world.write_calls()


def test_import_into_snapshot_failure_aborts_before_the_import(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject(EXPORT, False)
    code, out, _ = run_cli(["timeline", "import-into", str(_aaf(tmp_path)), "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert "Timeline.ImportIntoTimeline" not in fake_world.write_calls()


def test_no_snapshot_is_refused_before_any_resolve_call(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld
) -> None:
    monkeypatch.delenv("DAVINCI_AXI_ALLOW_UNSNAPSHOTTED", raising=False)
    for argv in (
        ["timeline", "append", "A001_beach"],
        ["timeline", "build", "s", "A001_beach"],
        ["timeline", "mark", "set", "--in", "1", "--out", "2"],
        ["timeline", "blanking", "set", "Top=1"],
    ):
        code, out, _ = run_cli([*argv, "--no-snapshot", "--force"])
        assert code == 1 and "WRITE_REFUSED" in out
    assert fake_world.calls == []


def test_the_wrapped_parent_still_serves_the_phase_2_verbs(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    for argv in (
        ["timeline", "rename", "--help"],
        ["timeline", "export", "--help"],
        ["timeline", "marker", "add", "--help"],
        ["timeline", "marker", "custom", "--help"],
        ["timeline", "track", "add", "--help"],
        ["timeline", "insert", "--help"],
    ):
        code, out, err = run_cli(argv)
        assert code == 0 and err == "" and "examples[" in out
    code, out, _ = run_cli(["timeline", "rename", "Edit v2", "--to", "Edit v3", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    code, out, _ = run_cli(["timeline", "marker", "add", "--frame", "5", "--name", "x", "--force"])
    assert code == 0 and 5 in _edit(fake_world).markers


def test_spec_methods_are_declared_on_the_parent_and_exist_in_the_api() -> None:
    from davinci_axi.commands import timeline_extra

    methods = set(timeline_extra.SPEC.methods)
    expected = {
        "MediaPool.AppendToTimeline",
        "MediaPool.CreateTimelineFromClips",
        "Timeline.CreateCompoundClip",
        "Timeline.InsertGeneratorIntoTimeline",
        "Timeline.InsertOFXGeneratorIntoTimeline",
        "Timeline.InsertTitleIntoTimeline",
        "Timeline.ImportIntoTimeline",
        "Timeline.SetMarkInOut",
        "Timeline.ClearMarkInOut",
        "Timeline.GetMarkInOut",
        "Timeline.SetOutputBlanking",
        "Timeline.GetOutputBlanking",
        "Timeline.ConvertTimelineToStereo",
        "Timeline.AnalyzeDolbyVision",
        "Timeline.GetSelectedClips",
        "Timeline.UpdateMarkerCustomData",
        "Timeline.GetMarkerCustomData",
        "Timeline.GetMarkerByCustomData",
        "Timeline.Export",
        "Timeline.AddMarker",
    }
    assert expected <= methods
