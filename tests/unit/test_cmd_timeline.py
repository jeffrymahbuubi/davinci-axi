from __future__ import annotations

import shlex
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import timeline as timeline_cmd
from davinci_axi.fake import FakeProject, FakeTimeline, FakeWorld, ext_timeline_edit
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_timeline_edit.extend()
    ext_timeline_edit.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "timeline", "davinci_axi.commands.timeline:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _timeline(world: FakeWorld, name: str) -> FakeTimeline:
    return next(t for t in _project(world).timelines if t.name == name)


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _dry_run_is_clean(world: FakeWorld, tmp_path: Path) -> None:
    assert world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


def test_rename_dry_run_matches_ae5(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["timeline", "rename", "Edit v2", "--to", "Edit v3"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["applied"] == "false" and keys["command"] == "timeline rename"
    assert "changes[1]{field,before,after}:" in out
    assert "  name,Edit v2,Edit v3" in out
    assert "davinci-axi timeline rename 'Edit v2' --to 'Edit v3' --force" in out
    assert "snapshot" not in keys
    _dry_run_is_clean(fake_world, tmp_path)
    assert _timeline(fake_world, "Edit v2").name == "Edit v2"


def test_rename_force_saves_snapshots_writes_and_reads_back(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "rename", "Edit v2", "--to", "Edit v3", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true"
    assert fake_world.write_calls()[:3] == [
        "ProjectManager.SaveProject",
        "ProjectManager.ExportProject",
        "Timeline.SetName",
    ]
    assert any(p.is_dir() for p in _snapshots(tmp_path).iterdir())
    assert keys["snapshot"] != "none"
    assert [t.name for t in _project(fake_world).timelines] == ["Edit v1", "Edit v3"]
    assert fake_world.calls_to("Timeline.GetName")


@pytest.mark.parametrize("failing", ["ProjectManager.ExportProject", "ProjectManager.SaveProject"])
def test_snapshot_failure_aborts_before_any_write(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, failing: str
) -> None:
    fake_world.inject(failing, False)
    code, out, _ = run_cli(["timeline", "rename", "Edit v2", "--to", "Edit v3", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert "Timeline.SetName" not in fake_world.write_calls()
    assert _timeline(fake_world, "Edit v2").name == "Edit v2"


def test_a_write_that_did_not_take_is_verify_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("Timeline.SetName", True)
    code, out, _ = run_cli(["timeline", "rename", "Edit v2", "--to", "Edit v3", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "VERIFY_FAILED"
    assert "Edit v2" in out and "Edit v3" in out


def test_no_snapshot_is_refused_before_any_resolve_call(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld
) -> None:
    monkeypatch.delenv("DAVINCI_AXI_ALLOW_UNSNAPSHOTTED", raising=False)
    code, out, _ = run_cli(["timeline", "rename", "Edit v1", "--to", "x", "--no-snapshot"])
    assert code == 1 and "WRITE_REFUSED" in out
    assert fake_world.calls == []


def test_create_adds_an_empty_timeline(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timeline", "create", "scratch", "--force"])
    assert code == 0 and "applied: true" in out
    assert fake_world.write_calls()[-1] == "MediaPool.CreateEmptyTimeline"
    assert [t.name for t in _project(fake_world).timelines] == ["Edit v1", "Edit v2", "scratch"]


def test_duplicate_copies_tracks_and_items_with_new_ids(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "duplicate", "Edit v1", "--to", "Edit v1 copy", "--force"])
    assert code == 0 and "applied: true" in out
    source, copy = _timeline(fake_world, "Edit v1"), _timeline(fake_world, "Edit v1 copy")
    shape = {k: [len(t.items) for t in v] for k, v in source.tracks.items()}
    assert {k: [len(t.items) for t in v] for k, v in copy.tracks.items()} == shape
    ids = {i.uid for tracks in source.tracks.values() for t in tracks for i in t.items}
    copy_ids = {i.uid for tracks in copy.tracks.values() for t in tracks for i in t.items}
    assert ids.isdisjoint(copy_ids) and copy.uid != source.uid
    assert source.name == "Edit v1"
    assert any(i.linked for t in copy.tracks["video"] for i in t.items)


def test_delete_is_irreversible_and_removes_the_timeline(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "delete", "Edit v1"])
    assert code == 0 and parse_toon_keys(out)["irreversible"] == "true"
    assert "6 items" in out
    code, out, _ = run_cli(["timeline", "delete", "Edit v1", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["irreversible"] == "true" and keys["applied"] == "true"
    assert fake_world.write_calls() == [
        "ProjectManager.SaveProject",
        "ProjectManager.ExportProject",
        "MediaPool.DeleteTimelines",
    ]
    assert _project(fake_world).current_timeline is None
    _, listing, _ = run_cli(["timelines"])
    assert "total: 1" in listing


def test_open_switches_the_current_timeline_without_a_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "open", "Edit v2", "--force"])
    assert code == 0 and parse_toon_keys(out)["snapshot"] == "none"
    assert fake_world.write_calls() == ["Project.SetCurrentTimeline"]
    assert not _snapshots(tmp_path).exists()
    assert _project(fake_world).current_timeline is _timeline(fake_world, "Edit v2")


def test_settings_need_custom_settings_first(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timeline", "settings", "set", "timelineFrameRate=25"])
    assert code == 2 and "useCustomSettings=1" in out
    assert fake_world.write_calls() == []


def test_settings_enable_custom_then_change_the_frame_rate(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "settings", "set", "useCustomSettings=1", "--force"])
    assert code == 0 and "applied: true" in out
    assert "applies keys one by one" in out
    code, out, _ = run_cli(["timeline", "settings", "set", "timelineFrameRate=25"])
    assert code == 0 and "  timelineFrameRate,24,25" in out
    code, out, _ = run_cli(["timeline", "settings", "set", "timelineFrameRate=25", "--force"])
    assert code == 0 and "applied: true" in out
    assert _timeline(fake_world, "Edit v1").settings["timelineFrameRate"] == 25.0


def test_settings_both_keys_in_one_call_apply_the_custom_flag_first(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, _, _ = run_cli(
        ["timeline", "settings", "set", "timelineFrameRate=30,useCustomSettings=1", "--force"]
    )
    assert code == 0
    settings = _timeline(fake_world, "Edit v1").settings
    assert settings["useCustomSettings"] == "1" and settings["timelineFrameRate"] == 30.0


def test_timecode_set_moves_the_playhead_without_a_snapshot(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "timecode", "set", "01:00:02:00", "--force"])
    assert code == 0 and "applied: true" in out
    assert fake_world.write_calls() == ["Timeline.SetCurrentTimecode"]
    assert _timeline(fake_world, "Edit v1").GetCurrentTimecode() == "01:00:02:00"


def test_timecode_set_on_another_timeline_restores_the_current_one(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(
        ["timeline", "timecode", "set", "00:59:59;12", "--timeline", "Edit v2", "--force"]
    )
    assert code == 0 and "timeline_restored: true" in out
    assert _timeline(fake_world, "Edit v2").current_tc == "00:59:59;12"
    assert _project(fake_world).current_timeline is _timeline(fake_world, "Edit v1")


def test_track_add_audio(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timeline", "track", "add", "audio"])
    assert code == 0 and "  audio_tracks,1,2" in out
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["timeline", "track", "add", "audio", "--subtype", "mono", "--force"])
    assert code == 0 and "applied: true" in out
    edit = _timeline(fake_world, "Edit v1")
    assert len(edit.tracks["audio"]) == 2 and edit.tracks["audio"][1].subtype == "mono"
    assert fake_world.write_calls()[-1] == "Timeline.AddTrack"


def test_track_delete_names_the_items_and_is_irreversible(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "track", "delete", "video", "2"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["irreversible"] == "true"
    assert "its 1 item;" in keys["warning"]
    code, out, _ = run_cli(["timeline", "track", "delete", "video", "2", "--force"])
    assert code == 0 and "  video_tracks,2,1" in out
    assert len(_timeline(fake_world, "Edit v1").tracks["video"]) == 1
    assert fake_world.write_calls()[-1] == "Timeline.DeleteTrack"


def test_track_delete_renumbers_the_remaining_items(run_cli: Run, fake_world: FakeWorld) -> None:
    run_cli(["timeline", "track", "delete", "video", "1", "--force"])
    (item,) = _timeline(fake_world, "Edit v1").tracks["video"][0].items
    assert (item.name, item.track_index) == ("Title", 1)


def test_marker_add_dry_run_shows_the_absolute_frame(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(
        ["timeline", "marker", "add", "--frame", "10", "--color", "Green", "--name", "Pick"]
    )
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and keys["absolute_frame"] == "86410"
    assert "  marker@10,null,Green Pick" in out
    _dry_run_is_clean(fake_world, tmp_path)


def test_marker_add_on_an_occupied_frame_exits_1(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timeline", "marker", "add", "--frame", "100"])
    assert code == 1 and "MARKER_EXISTS" in out


def test_marker_delete_by_color(run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timeline", "marker", "delete", "--color", "Red"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["irreversible"] == "true" and '  "markers:Red",1,0' in out
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["timeline", "marker", "delete", "--color", "Red", "--force"])
    assert code == 0 and "applied: true" in out
    assert _timeline(fake_world, "Edit v1").GetMarkers() == {}
    assert fake_world.write_calls()[-1] == "Timeline.DeleteMarkersByColor"


def test_marker_delete_by_frame_and_custom_data(run_cli: Run, fake_world: FakeWorld) -> None:
    edit = _timeline(fake_world, "Edit v1")
    run_cli(["timeline", "marker", "add", "--frame", "5", "--custom-data", "take-2", "--force"])
    run_cli(["timeline", "marker", "add", "--frame", "6", "--force"])
    code, out, _ = run_cli(["timeline", "marker", "delete", "--frame", "6"])
    assert code == 0 and "absolute_frame: 86406" in out
    assert run_cli(["timeline", "marker", "delete", "--frame", "6", "--force"])[0] == 0
    assert 6 not in edit.markers
    assert run_cli(["timeline", "marker", "delete", "--custom-data", "take-2", "--force"])[0] == 0
    assert 5 not in edit.markers
    calls = fake_world.write_calls()
    assert "Timeline.DeleteMarkerAtFrame" in calls
    assert "Timeline.DeleteMarkerByCustomData" in calls


@pytest.mark.parametrize("path", [[], ["track"], ["marker"], ["settings"], ["timecode"]])
def test_bare_parents_exit_2_listing_subcommands(
    run_cli: Run, fake_world: FakeWorld, path: list[str]
) -> None:
    code, out, _ = run_cli(["timeline", *path])
    assert code == 2 and "needs a subcommand" in out
    assert fake_world.calls == []


def _paths(spec: CommandSpec, prefix: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
    yield prefix
    for name, child in spec.subcommands.items():
        yield from _paths(child, (*prefix, name))


def test_help_for_every_path_exits_0_with_examples(run_cli: Run, fake_world: FakeWorld) -> None:
    paths = list(_paths(timeline_cmd.SPEC, ("timeline",)))
    assert len(paths) == 22
    for path in paths:
        code, out, err = run_cli([*path, "--help"])
        assert (code, err) == (0, ""), path
        assert "examples[" in out, path
    assert fake_world.calls == []


def test_spec_methods_cover_every_write_call(run_cli: Run, fake_world: FakeWorld) -> None:
    scenario = [
        ["rename", "Edit v2", "--to", "Edit v3"],
        ["create", "scratch"],
        ["duplicate", "scratch", "--to", "scratch 2"],
        ["delete", "scratch 2"],
        ["open", "Edit v3"],
        ["settings", "set", "useCustomSettings=1"],
        ["timecode", "set", "01:00:01:00"],
        ["timecode", "start", "00:59:50:00"],
        ["track", "add", "audio"],
        ["track", "delete", "audio", "2"],
        ["track", "rename", "video", "1", "--to", "Main"],
        ["track", "disable", "video", "1"],
        ["track", "lock", "video", "1"],
        ["marker", "add", "--frame", "3"],
        ["marker", "delete", "--frame", "3"],
    ]
    for words in scenario:
        code, out, _ = run_cli(["timeline", *words, "--force"])
        assert code == 0, (words, out)
    called = {c for c in fake_world.write_calls() if c not in ("ProjectManager.SaveProject",)} - {
        "ProjectManager.ExportProject"
    }
    assert called <= set(timeline_cmd.SPEC.methods)
    expected = set(
        shlex.split("""Timeline.SetName MediaPool.CreateEmptyTimeline Timeline.DuplicateTimeline
        MediaPool.DeleteTimelines Project.SetCurrentTimeline Timeline.SetSettings
        Timeline.SetCurrentTimecode Timeline.SetStartTimecode Timeline.AddTrack
        Timeline.DeleteTrack Timeline.SetTrackName Timeline.SetTrackEnable
        Timeline.SetTrackLock Timeline.AddMarker Timeline.DeleteMarkerAtFrame""")
    )
    assert called == expected, expected ^ called


def test_fake_extension_edge_cases(fake_world: FakeWorld) -> None:
    edit = _timeline(fake_world, "Edit v1")
    assert not (
        edit.DeleteTrack("video", 9) or edit.AddTrack("midi") or edit.SetTrackName("a", 5, "x")
    )
    assert not (edit.SetCurrentTimecode("nope") or edit.AddMarker(100, "Red", "d", "", 1))
    assert edit.DeleteMarkerAtFrame(77) is False
    assert edit.SetSettings({"timelineFrameRate": "30"}) is False  # custom settings are off
    assert edit.SetSettings({"useCustomSettings": "1", "bogus": "1"}) is False
    assert edit.settings["useCustomSettings"] == "1"  # earlier keys stay applied
    assert edit.DuplicateTimeline("Edit v2") is None
    assert edit.SetName("Edit v2") is False
    pool = _project(fake_world).media_pool
    assert pool.CreateEmptyTimeline("Edit v1") is None
    assert pool.DeleteTimelines([]) is False


USAGE_ERRORS = [
    ["create", "  "],
    ["rename", "Edit v2"],
    ["settings", "set", "timelineFrameRate"],
    ["settings", "set", "bogus=1"],
    ["track", "add", "midi"],
    ["track", "add", "video", "--subtype", "mono"],
    ["track", "delete", "video", "0"],
    ["track", "delete", "video", "x"],
    ["marker", "add"],
    ["marker", "add", "--frame", "1", "--color", "Mauve"],
    ["marker", "add", "--frame", "-1"],
    ["marker", "add", "--frame", "999"],
    ["marker", "delete"],
    ["marker", "delete", "--frame", "1", "--color", "Red"],
    ["timecode", "set", "abc"],
    ["timecode", "start", "01:00:00"],
]
NO_OPS = [
    ["rename", "Edit v2", "--to", "Edit v2"],
    ["open", "Edit v1"],
    ["track", "enable", "video", "1"],
    ["track", "unlock", "audio", "1"],
    ["timecode", "set", "01:00:00:00"],
    ["marker", "delete", "--color", "Mint"],
]


@pytest.mark.parametrize("words", USAGE_ERRORS)
def test_usage_errors_exit_2_without_a_write(
    run_cli: Run, fake_world: FakeWorld, words: list[str]
) -> None:
    code, out, _ = run_cli(["timeline", *words, "--force"])
    assert code == 2 and "VALIDATION_ERROR" in out
    assert fake_world.write_calls() == []


@pytest.mark.parametrize("words", NO_OPS)
def test_a_change_that_changes_nothing_is_a_no_op(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, words: list[str]
) -> None:
    code, out, _ = run_cli(["timeline", *words, "--force"])
    assert code == 0 and "applied: false" in out
    _dry_run_is_clean(fake_world, tmp_path)


def test_force_command_keeps_timeline_and_snapshot_out(run_cli: Run, fake_world: FakeWorld) -> None:
    cmd = ["timeline", "track", "add", "video", "--timeline", "Edit v2", "--snapshot-out", "/tmp/x"]
    _, out, _ = run_cli(cmd)
    assert "track add video --timeline 'Edit v2' --snapshot-out /tmp/x --force" in out
    assert run_cli([*cmd[:-2], "--force"])[0] == 0
    assert len(_timeline(fake_world, "Edit v2").tracks["video"]) == 2
    assert len(_timeline(fake_world, "Edit v1").tracks["video"]) == 2


@pytest.mark.parametrize(
    ("words", "code"),
    [
        (["create", "Edit v1"], "TIMELINE_EXISTS"),
        (["duplicate", "Edit v1", "--to", "Edit v2"], "TIMELINE_EXISTS"),
        (["rename", "Edit v2", "--to", "Edit v1"], "TIMELINE_EXISTS"),
        (["rename", "Nope", "--to", "x"], "NOT_FOUND"),
        (["marker", "add", "--frame", "100"], "MARKER_EXISTS"),
        (["track", "delete", "video", "3"], "NOT_FOUND"),
    ],
)
def test_refusals_exit_1_in_the_dry_run(
    run_cli: Run, fake_world: FakeWorld, words: list[str], code: str
) -> None:
    exit_code, out, _ = run_cli(["timeline", *words])
    assert exit_code == 1 and code in out
    assert fake_world.write_calls() == []


@pytest.mark.parametrize("returned", [False, None])
def test_a_failing_write_is_api_failed(
    run_cli: Run, fake_world: FakeWorld, returned: bool | None
) -> None:
    fake_world.inject("Timeline.SetSettings", returned)
    code, out, _ = run_cli(["timeline", "settings", "set", "useCustomSettings=1", "--force"])
    assert code == 1 and "API_FAILED" in out


def test_every_mutating_leaf_takes_the_mutation_flags() -> None:
    def leaves(spec: CommandSpec) -> Iterator[CommandSpec]:
        if spec.run is not None:
            yield spec
        for child in spec.subcommands.values():
            yield from leaves(child)

    found = list(leaves(timeline_cmd.SPEC))
    assert len(found) == 17
    for leaf in found:
        assert leaf.reads_only is False and leaf.methods, leaf.name
        assert {"force", "no-snapshot", "snapshot-out"} <= {f.name for f in leaf.flags}, leaf.name


def test_marker_add_force_reads_back_the_marker(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(
        shlex.split("timeline marker add --frame 10 --color Green --name Pick --force")
    )
    assert code == 0 and "applied: true" in out
    assert fake_world.write_calls()[2] == "Timeline.AddMarker"
    assert _timeline(fake_world, "Edit v1").GetMarkers()[10]["name"] == "Pick"


def test_track_switches_toggle_and_read_back(run_cli: Run, fake_world: FakeWorld) -> None:
    edit = _timeline(fake_world, "Edit v1")
    for verb, kind, attr, expected in (
        ("disable", "video", "enabled", False),
        ("enable", "video", "enabled", True),
        ("lock", "audio", "locked", True),
        ("unlock", "audio", "locked", False),
    ):
        code, out, _ = run_cli(["timeline", "track", verb, kind, "1", "--force"])
        assert code == 0 and "applied: true" in out, verb
        assert getattr(edit.tracks[kind][0], attr) is expected, verb
    calls = fake_world.write_calls()
    assert calls.count("Timeline.SetTrackEnable") == calls.count("Timeline.SetTrackLock") == 2


DRY_RUNS = [
    ["create", "scratch"],
    ["open", "Edit v2"],
    ["delete", "Edit v2"],
    ["settings", "set", "useCustomSettings=1"],
    ["timecode", "set", "01:00:02:00"],
    ["timecode", "start", "00:59:50:00"],
    ["track", "add", "audio"],
    ["track", "lock", "audio", "1"],
    ["marker", "add", "--frame", "3"],
]


@pytest.mark.parametrize("words", DRY_RUNS)
def test_every_verb_is_a_dry_run_without_force(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, words: list[str]
) -> None:
    code, out, _ = run_cli(["timeline", *words])
    assert code == 0 and "applied: false" in out
    assert "--force` to apply this change" in out
    _dry_run_is_clean(fake_world, tmp_path)
