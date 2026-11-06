"""`item set|rename|enable|disable|delete|color|flag|marker|speed|fades` (U12) on the fake."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import item_edit
from davinci_axi.fake import FakeProject, FakeWorld, ext_writes_core
from davinci_axi.fake import ext_item_edit as ext
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

SAVE, EXPORT = "ProjectManager.SaveProject", "ProjectManager.ExportProject"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Neither the registry line nor the EXTENSIONS entry exists in the worktree."""
    ext_writes_core.extend()
    ext.extend()
    ext.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "item", "davinci_axi.commands.item_edit:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")
    yield


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _edit(world: FakeWorld) -> FakeTimeline:
    timeline = _project(world).timelines[0]
    assert timeline.name == "Edit v1"
    return timeline


def _clip(world: FakeWorld, number: int) -> FakeTimelineItem:
    return _edit(world).tracks["video"][0].items[number - 1]


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _assert_untouched(world: FakeWorld, tmp_path: Path) -> None:
    assert world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


def test_item_detail_view_still_renders_with_the_new_spec(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["item", "V1:2"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["name"] == "clip-2" and keys["track"] == "V1"
    assert "applied" not in keys
    code, out, _ = run_cli(["item", "item-2", "--full"])
    assert code == 0 and "name: clip-2" in out


def test_set_dry_run_shows_the_change_and_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["item", "set", "V1:2", "Opacity=50"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["applied"] == "false" and keys["command"] == "item set"
    assert "item V1:2 (clip-2) in timeline Edit v1" in keys["target"]
    assert "changes[1]{field,before,after}:" in out and "  Opacity,100,50" in out
    assert "`davinci-axi item set V1:2 Opacity=50 --force`" in out
    _assert_untouched(fake_world, tmp_path)
    assert _clip(fake_world, 2).properties["Opacity"] == 100.0


def test_set_force_saves_snapshots_then_makes_one_set_properties_call(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["item", "set", "V1:2", "Opacity=50,ZoomX=1.2", "--force"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert fake_world.write_calls() == [SAVE, EXPORT, "TimelineItem.SetProperties"]
    assert fake_world.calls_to("TimelineItem.SetProperties") == [({"Opacity": 50.0, "ZoomX": 1.2},)]
    assert keys["applied"] == "true"
    assert [d.name for d in _snapshots(tmp_path).iterdir()] == [keys["snapshot"]]
    assert _clip(fake_world, 2).properties["Opacity"] == 50.0
    assert _clip(fake_world, 2).properties["ZoomX"] == 1.2
    assert "timeline_restored" not in keys


def test_set_coerces_to_the_live_type_and_skips_unchanged_values(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["item", "set", "V1:2", "FlipX=true,Opacity=100", "--force"])
    assert code == 0
    assert fake_world.calls_to("TimelineItem.SetProperties") == [({"FlipX": True},)]
    assert _clip(fake_world, 2).properties["FlipX"] is True
    code, out, _ = run_cli(["item", "set", "V1:2", "Opacity=100"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and "changes: []" in out


def test_set_unknown_malformed_or_badly_typed_assignments_exit_2_before_any_write(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["item", "set", "V1:2", "Nope=1"])
    assert code == 2 and parse_toon_keys(out)["code"] == "VALIDATION_ERROR"
    assert "Valid properties for this item: Pan, Tilt, ZoomX" in out
    assert run_cli(["item", "set", "V1:2", "Opacity"])[0] == 2
    assert run_cli(["item", "set", "V1:2", "Opacity=lots", "--force"])[0] == 2
    assert run_cli(["item", "set", "V1:2"])[0] == 2
    _assert_untouched(fake_world, tmp_path)


def test_set_on_another_timeline_switches_and_restores_the_current_one(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    other = _project(fake_world).timelines[1]
    other.add_item("video", 1, "other-clip", other.start_frame, 40)
    code, out, _ = run_cli(
        ["item", "set", "V1:1", "Opacity=25", "--timeline", "Edit v2", "--force"]
    )
    keys = parse_toon_keys(out)
    assert code == 0 and keys["timeline_restored"] == "true"
    assert other.tracks["video"][0].items[0].properties["Opacity"] == 25.0
    assert _project(fake_world).current_timeline is _edit(fake_world)
    assert (
        "--timeline 'Edit v2'"
        in run_cli(["item", "set", "V1:1", "Opacity=10", "--timeline", "Edit v2"])[1]
    )


def test_set_on_the_current_timeline_never_switches(run_cli: Run, fake_world: FakeWorld) -> None:
    run_cli(["item", "set", "V1:1", "Opacity=25", "--force"])
    assert "Project.SetCurrentTimeline" not in fake_world.write_calls()


def test_rename_reads_back_the_name(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["item", "rename", "V1:2", "--to", "Interview"])
    assert code == 0 and "  name,clip-2,Interview" in out
    _assert_untouched(fake_world, tmp_path)
    code, out, _ = run_cli(["item", "rename", "V1:2", "--to", "Interview", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [SAVE, EXPORT, "TimelineItem.SetName"]
    assert _clip(fake_world, 2).name == "Interview"
    assert run_cli(["item", "rename", "V1:2"])[0] == 2
    assert run_cli(["item", "rename", "V1:2", "--to", " "])[0] == 2


def test_enable_on_an_enabled_item_is_a_no_op_without_a_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["item", "enable", "V1:2", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and "changes: []" in out
    assert "snapshot" not in keys
    _assert_untouched(fake_world, tmp_path)


def test_disable_then_items_shows_false_and_enable_restores(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["item", "disable", "V1:2", "--force"])
    assert code == 0 and "  enabled,true,false" in out
    assert fake_world.write_calls() == [SAVE, EXPORT, "TimelineItem.SetClipEnabled"]
    _, out, _ = run_cli(["items", "--fields", "name,enabled", "--track-type", "video"])
    assert "clip-2,false" in out and "clip-1,true" in out
    code, out, _ = run_cli(["item", "enable", "V1:2", "--force"])
    assert code == 0 and _clip(fake_world, 2).enabled is True


def test_color_set_and_clear(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["item", "color", "set", "V1:1", "Teal", "--force"])
    assert code == 0 and "  color,Blue,Teal" in out
    assert _clip(fake_world, 1).color == "Teal"
    code, out, _ = run_cli(["item", "color", "clear", "V1:1", "--force"])
    assert code == 0 and _clip(fake_world, 1).color == ""
    log = fake_world.write_calls()
    assert "TimelineItem.SetClipColor" in log and "TimelineItem.ClearClipColor" in log


def test_color_validation_and_no_op(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["item", "color", "set", "V1:1", "Magenta"])
    assert code == 2 and "Valid colours: Orange, Apricot" in out
    code, out, _ = run_cli(["item", "color", "clear", "V2:1", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    _assert_untouched(fake_world, tmp_path)


def test_flag_add_then_clear(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _, _ = run_cli(["item", "flag", "add", "V1:1", "Blue", "--force"])
    assert code == 0 and sorted(_clip(fake_world, 1).flags) == ["Blue", "Red"]
    assert fake_world.calls_to("TimelineItem.AddFlag") == [("Blue",)]
    code, _, _ = run_cli(["item", "flag", "clear", "V1:1", "--color", "Red", "--force"])
    assert code == 0 and _clip(fake_world, 1).flags == ["Blue"]
    code, _, _ = run_cli(["item", "flag", "clear", "V1:1", "--force"])
    assert code == 0 and _clip(fake_world, 1).flags == []
    assert fake_world.calls_to("TimelineItem.ClearFlags") == [("Red",), ("All",)]


def test_flag_no_ops_and_bad_colour(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    assert run_cli(["item", "flag", "add", "V1:1", "Red", "--force"])[0] == 0
    assert run_cli(["item", "flag", "clear", "V2:1", "--force"])[0] == 0
    assert run_cli(["item", "flag", "add", "V1:1", "Orange"])[0] == 2
    _assert_untouched(fake_world, tmp_path)


def test_marker_add_and_delete(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["item", "marker", "add", "V1:1", "--frame", "5"])
    assert code == 0 and parse_toon_keys(out)["command"] == "item marker add"
    assert "`davinci-axi item marker add V1:1 --frame 5 --color Blue --force`" in out
    _assert_untouched(fake_world, tmp_path)
    code, out, _ = run_cli(
        [
            "item",
            "marker",
            "add",
            "V1:1",
            "--frame",
            "5",
            "--color",
            "Green",
            "--name",
            "Pick",
            "--force",
        ]
    )
    assert code == 0
    assert _clip(fake_world, 1).markers[5]["name"] == "Pick"
    assert fake_world.write_calls() == [SAVE, EXPORT, "TimelineItem.AddMarker"]
    code, out, _ = run_cli(["item", "marker", "add", "V1:1", "--frame", "5", "--force"])
    assert code == 1 and "MARKER_EXISTS" in out
    code, out, _ = run_cli(["item", "marker", "delete", "V1:1", "--frame", "5", "--force"])
    assert code == 0 and 5 not in _clip(fake_world, 1).markers
    code, out, _ = run_cli(["item", "marker", "delete", "V1:1", "--color", "Blue", "--force"])
    assert code == 0 and _clip(fake_world, 1).markers == {}


def test_marker_delete_needs_exactly_one_selector(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    assert run_cli(["item", "marker", "delete", "V1:1"])[0] == 2
    assert run_cli(["item", "marker", "delete", "V1:1", "--frame", "1", "--color", "Red"])[0] == 2
    assert run_cli(["item", "marker", "add", "V1:1"])[0] == 2
    _assert_untouched(fake_world, tmp_path)


def test_speed_sets_the_percentage(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["item", "speed", "V1:1", "--speed", "50"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["item", "speed", "V1:1", "--speed", "50", "--ripple", "--force"])
    assert code == 0 and "options: RippleTimeline" in out
    assert fake_world.calls_to("TimelineItem.SetSpeed") == [
        ({"Percentage": 50.0, "RippleTimeline": True},)
    ]
    assert _clip(fake_world, 1).GetSpeed()["Percentage"] == 50.0
    assert run_cli(["item", "speed", "V1:1", "--speed", "-5"])[0] == 2
    assert run_cli(["item", "speed", "V1:1"])[0] == 2


def test_speed_and_fades_need_resolve_21_1(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.resolve.version = [20, 3, 1, 1, ""]
    for argv in (["speed", "V1:1", "--speed", "50"], ["fades", "A1:1", "--in", "12"]):
        code, out, _ = run_cli(["item", *argv, "--force"])
        assert code == 1 and parse_toon_keys(out)["code"] == "VERSION_TOO_OLD"
        assert "21.1.0" in out
    _assert_untouched(fake_world, tmp_path)


def test_fades_set_both_lengths_and_keep_the_other(run_cli: Run, fake_world: FakeWorld) -> None:
    audio = _edit(fake_world).tracks["audio"][0].items[0]
    code, out, _ = run_cli(["item", "fades", "A1:1", "--in", "12", "--force"])
    assert code == 0 and "  fade_in,0,12" in out
    assert fake_world.calls_to("TimelineItem.SetFades") == [({"FadeIn": 12, "FadeOut": 0},)]
    code, out, _ = run_cli(["item", "fades", "A1:1", "--out", "24", "--force"])
    assert code == 0 and audio.GetFades() == {"FadeIn": 12, "FadeOut": 24}
    assert run_cli(["item", "fades", "A1:1"])[0] == 2
    assert run_cli(["item", "fades", "A1:1", "--in", "-3"])[0] == 2


def test_delete_with_ripple_is_irreversible_and_closes_the_gap(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["item", "delete", "V1:2", "--ripple"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["irreversible"] == "true" and "ripple" in keys["warning"]
    assert "`davinci-axi item delete V1:2 --ripple --force`" in out
    _assert_untouched(fake_world, tmp_path)
    code, out, _ = run_cli(["item", "delete", "V1:2", "--ripple", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["irreversible"] == "true"
    assert fake_world.write_calls() == [SAVE, EXPORT, "Timeline.DeleteClips"]
    items = _edit(fake_world).tracks["video"][0].items
    assert [i.name for i in items] == ["clip-1", "clip-3"]
    assert items[1].start == 86480  # 80 frames earlier than before
    _, out, _ = run_cli(["items", "--track-type", "video", "--track", "1"])
    assert "total: 2" in out


def test_delete_without_ripple_leaves_the_gap_and_accepts_an_item_id(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, _, _ = run_cli(["item", "delete", "item-2", "--force"])
    assert code == 0
    assert [i.start for i in _edit(fake_world).tracks["video"][0].items] == [86400, 86560]


def test_snapshot_failure_aborts_before_any_item_write(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject(EXPORT, False)
    code, out, _ = run_cli(["item", "set", "V1:2", "Opacity=50", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert "TimelineItem.SetProperties" not in fake_world.write_calls()
    assert _clip(fake_world, 2).properties["Opacity"] == 100.0
    fake_world.fail.clear()
    fake_world.inject(SAVE, False)
    code, out, _ = run_cli(["item", "delete", "V1:2", "--force"])
    assert code == 1 and "SNAPSHOT_FAILED" in out
    assert "Timeline.DeleteClips" not in fake_world.write_calls()
    assert len(_edit(fake_world).tracks["video"][0].items) == 3


def test_a_refused_write_is_api_failed_and_names_the_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("TimelineItem.SetName", False)
    code, out, _ = run_cli(["item", "rename", "V1:2", "--to", "X", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert "snapshot" in out


def test_read_back_mismatch_is_verify_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("TimelineItem.GetName", "clip-2")
    code, out, _ = run_cli(["item", "rename", "V1:2", "--to", "X", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "VERIFY_FAILED"
    assert "name is 'clip-2', expected 'X'" in out


def test_no_snapshot_is_refused_before_any_resolve_call(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DAVINCI_AXI_ALLOW_UNSNAPSHOTTED", raising=False)
    code, out, _ = run_cli(["item", "disable", "V1:2", "--no-snapshot", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "WRITE_REFUSED"
    assert fake_world.calls == []
    monkeypatch.setenv("DAVINCI_AXI_ALLOW_UNSNAPSHOTTED", "1")
    code, out, _ = run_cli(["item", "disable", "V1:2", "--no-snapshot", "--force"])
    assert code == 0 and parse_toon_keys(out)["snapshot"] == "skipped"
    assert fake_world.write_calls() == ["TimelineItem.SetClipEnabled"]


def test_unknown_item_and_unknown_timeline_are_not_found(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["item", "disable", "V9:1", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    code, out, _ = run_cli(["item", "rename", "V1:1", "--to", "x", "--timeline", "nope"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    _assert_untouched(fake_world, tmp_path)


def test_bare_parents_exit_2_and_list_their_subcommands(run_cli: Run) -> None:
    for parent, names in (
        ("color", "set, clear"),
        ("flag", "add, clear"),
        ("marker", "add, delete"),
    ):
        code, out, _ = run_cli(["item", parent])
        assert code == 2 and names in out, parent


def _walk(
    path: tuple[str, ...], spec: CommandSpec
) -> Iterator[tuple[tuple[str, ...], CommandSpec]]:
    yield path, spec
    for name, child in spec.subcommands.items():
        yield from _walk((*path, name), child)


def test_every_item_path_has_help_with_examples(run_cli: Run) -> None:
    paths = [path for path, _ in _walk(("item",), item_edit.SPEC)]
    assert ("item", "set") in paths and ("item", "marker", "delete") in paths
    for path in paths:
        code, out, err = run_cli([*path, "--help"])
        assert code == 0 and err == "", path
        assert "examples[" in out, path
    _, out, _ = run_cli(["item", "--help"])
    for name in item_edit.SUBCOMMANDS:
        assert name in out


def test_spec_methods_cover_every_write_the_verbs_make() -> None:
    declared = {m for _, spec in _walk(("item",), item_edit.SPEC) for m in spec.methods}
    writes = {
        f"TimelineItem.{name}"
        for name in [
            "SetProperties",
            "SetName",
            "SetClipEnabled",
            "SetClipColor",
            "ClearClipColor",
            "AddFlag",
            "ClearFlags",
            "AddMarker",
            "DeleteMarkerAtFrame",
            "DeleteMarkersByColor",
            "DeleteMarkerByCustomData",
            "SetSpeed",
            "SetFades",
        ]
    }
    assert writes | {"Timeline.DeleteClips", "Project.SetCurrentTimeline"} <= declared
    for _, spec in _walk(("item",), item_edit.SPEC):
        if spec.run is not None and spec.name not in ("item",):
            assert spec.methods, spec.name
            assert spec.reads_only is False


def test_item_spec_keeps_the_detail_view_and_its_flags() -> None:
    assert item_edit.SPEC.run is not None and item_edit.SPEC.name == "item"
    assert [p.name for p in item_edit.SPEC.positionals] == ["id"]
    assert any("item set" in e for e in item_edit.SPEC.examples)


def test_fake_write_methods_honour_injection_and_validation(fake_world: FakeWorld) -> None:
    clip = _clip(fake_world, 1)
    assert clip.SetProperties({"Opacity": 10.0, "Nope": 1}) is False
    assert clip.properties["Opacity"] == 100.0
    assert clip.SetProperties({"Opacity": 10.0}) is True and clip.properties["Opacity"] == 10.0
    fake_world.inject("TimelineItem.SetName", False)
    assert clip.SetName("x") is False and clip.name == "clip-1"
    fake_world.fail.clear()
    assert clip.SetName("") is False
    assert clip.SetSpeed({"Bogus": 1}) is False
    assert clip.SetFades({"FadeIn": 3}) is True and clip.GetFades() == {"FadeIn": 3, "FadeOut": 0}
    assert clip.ClearFlags("Green") is False and clip.ClearFlags("All") is True
    assert clip.DeleteMarkerAtFrame(99) is False
    assert clip.DeleteMarkerByCustomData("none") is False


def test_fake_delete_clips_honours_ripple_only_when_asked(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    first, second, third = edit.tracks["video"][0].items
    assert edit.DeleteClips([]) is False
    assert edit.DeleteClips([second], False) is True
    assert third.start == 86560
    assert edit.DeleteClips([first], True) is True
    assert third.start == 86480
    assert json.dumps([i.name for i in edit.tracks["video"][0].items]) == '["clip-3"]'
    assert edit.DeleteClips([second]) is False
