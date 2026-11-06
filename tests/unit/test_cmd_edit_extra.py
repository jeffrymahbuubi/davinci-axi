"""U25 timeline clip verbs: append, build, compound, insert and import-into. State verbs and the
safety checks are in test_cmd_edit_extra_state.py, item verbs in test_cmd_edit_extra_item.py."""

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


def test_append_force_saves_snapshots_then_appends_to_the_track(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    edit = _edit(fake_world)
    clip = _project(fake_world).media_pool.root.subfolders[0].clips[2]
    code, out, err = run_cli(["timeline", "append", "A003_dusk", "--track", "2", "--force"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPool.AppendToTimeline"]
    assert fake_world.calls_to("MediaPool.AppendToTimeline") == [
        ([{"mediaPoolItem": clip, "trackIndex": 2}],)
    ]
    assert _count(edit, "video", 2) == 2
    assert keys["applied"] == "true" and keys["track"] == "V2"
    assert [d.name for d in _snapshots(tmp_path).iterdir()] == [keys["snapshot"]]
    assert "appended,0,1" in out


def test_append_dry_run_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "append", "A003_dusk,A001_beach", "--track", "1"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false"
    assert "appended,0,2" in out
    assert "--track 1 --force" in out
    assert fake_world.write_calls() == []
    assert not _snapshots(tmp_path).exists()
    assert _count(_edit(fake_world), "video", 1) == 3


def test_append_to_another_timeline_switches_and_restores(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "append", "A001_beach", "--timeline", "Edit v2", "--force"])
    assert code == 0 and parse_toon_keys(out)["timeline_restored"] == "true"
    assert _count(_edit(fake_world, "Edit v2"), "video", 1) == 1
    assert _project(fake_world).current_timeline is _edit(fake_world)
    assert fake_world.write_calls().count("Project.SetCurrentTimeline") == 2


def test_append_places_audio_only_clips_on_audio_tracks(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, _, _ = run_cli(["timeline", "append", "music_bed", "--force"])
    assert code == 0
    assert _count(_edit(fake_world), "audio", 1) == 3


def test_append_record_frame_is_passed_and_used(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _, _ = run_cli(["timeline", "append", "A001_beach", "--record-frame", "90000", "--force"])
    assert code == 0
    assert [i.start for i in _edit(fake_world).tracks["video"][0].items][-1] == 90000
    assert fake_world.calls_to("MediaPool.AppendToTimeline")[0][0][0]["recordFrame"] == 90000


def test_append_in_out_trims_the_source_like_resolve(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    # Live 21.1.1: startFrame=30, endFrame=120 gives a 90-frame item with left offset 30.
    code, out, _ = run_cli(["timeline", "append", "A001_beach", "--in", "30", "--out", "120"])
    assert code == 0 and "--in 30 --out 120 --force" in out
    code, _, _ = run_cli(
        ["timeline", "append", "A001_beach", "--in", "30", "--out", "120", "--force"]
    )
    assert code == 0
    info = fake_world.calls_to("MediaPool.AppendToTimeline")[0][0][0]
    assert (info["startFrame"], info["endFrame"]) == (30, 120)
    item = _edit(fake_world).tracks["video"][0].items[-1]
    assert (item.duration, item.GetLeftOffset()) == (90, 30.0)


@pytest.mark.parametrize(
    "flags",
    [
        ["--in", "30"],
        ["--out", "120"],
        ["--in", "120", "--out", "30"],
        ["--in", "-1", "--out", "9"],
    ],
)
def test_append_bad_in_out_is_refused_before_any_write(
    run_cli: Run, fake_world: FakeWorld, flags: list[str]
) -> None:
    assert run_cli(["timeline", "append", "A001_beach", *flags, "--force"])[0] == 2
    assert fake_world.write_calls() == []


def test_append_out_past_the_clip_end_is_refused(run_cli: Run, fake_world: FakeWorld) -> None:
    clip = _project(fake_world).media_pool.root.subfolders[0].clips[0]
    clip.properties["Frames"] = "100"
    code, out, _ = run_cli(
        ["timeline", "append", clip.name, "--in", "0", "--out", "101", "--force"]
    )
    assert code == 2 and "100 frames" in out
    assert fake_world.write_calls() == []


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["timeline", "append", "A001_beach", "--track", "9"], 2),
        (["timeline", "append", "A001_beach", "--track", "0"], 2),
        (["timeline", "append", "A001_beach", "--record-frame", "-1"], 2),
        (["timeline", "append", "nosuchclip"], 1),
        (["timeline", "append", "A001_beach", "--timeline", "nope"], 1),
    ],
)
def test_append_rejects_bad_input_before_any_write(
    run_cli: Run, fake_world: FakeWorld, argv: list[str], code: int
) -> None:
    assert run_cli([*argv, "--force"])[0] == code
    assert fake_world.write_calls() == []


def test_append_that_returns_nothing_is_api_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("MediaPool.AppendToTimeline", [])
    code, out, _ = run_cli(["timeline", "append", "A001_beach", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert "snapshot" in out


def test_build_creates_a_timeline_and_lists_it(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "build", "scratch", "A001_beach,A002_city", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true"
    assert "current timeline" in keys["warning"]
    assert [t.name for t in _project(fake_world).timelines] == ["Edit v1", "Edit v2", "scratch"]
    scratch = _edit(fake_world, "scratch")
    assert [i.name for i in scratch.tracks["video"][0].items] == ["A001_beach", "A002_city"]
    assert "scratch" in run_cli(["timelines"])[1]
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPool.CreateTimelineFromClips"]


def test_build_refuses_an_existing_name_and_a_dry_run_changes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "build", "Edit v2", "A001_beach", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "TIMELINE_EXISTS"
    code, out, _ = run_cli(["timeline", "build", "scratch", "A001_beach"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    assert len(_project(fake_world).timelines) == 2


def test_build_that_resolve_refuses_is_api_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("MediaPool.CreateTimelineFromClips", None)
    code, out, _ = run_cli(["timeline", "build", "scratch", "A001_beach", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"


def test_compound_replaces_the_items_and_is_irreversible(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "compound", "V1:1,V1:2", "--name", "Comp"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["irreversible"] == "true" and keys["applied"] == "false"
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["timeline", "compound", "V1:1,V1:2", "--name", "Comp", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["irreversible"] == "true"
    assert [i.name for i in _edit(fake_world).tracks["video"][0].items] == ["clip-3", "Comp"]
    items = fake_world.calls_to("Timeline.CreateCompoundClip")[0]
    assert items[1] == {"name": "Comp"} and len(items[0]) == 2
    assert fake_world.write_calls() == [SAVE, EXPORT, "Timeline.CreateCompoundClip"]


def test_compound_passes_the_start_timecode_and_checks_it(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, _, _ = run_cli(["timeline", "compound", "V1:1", "--start-tc", "1:00", "--force"])
    assert code == 2 and fake_world.write_calls() == []
    args = ["timeline", "compound", "V1:1", "--start-tc", "00:00:00:00", "--force"]
    assert run_cli(args)[0] == 0
    assert fake_world.calls_to("Timeline.CreateCompoundClip")[0][1] == {
        "startTimecode": "00:00:00:00"
    }


def test_compound_on_a_locked_track_or_missing_item_fails(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "compound", "V1:1,V1:9", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    _edit(fake_world).tracks["video"][0].locked = True
    code, out, _ = run_cli(["timeline", "compound", "V1:1,V1:2", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert len(_edit(fake_world).tracks["video"][0].items) == 3
    assert run_cli(["timeline", "compound", "V1:1,,V1:2"])[0] == 2


@pytest.mark.parametrize(
    ("kind", "method", "item_kind"),
    [
        ("generator", "InsertGeneratorIntoTimeline", "Generator"),
        ("ofx", "InsertOFXGeneratorIntoTimeline", "Generator"),
        ("title", "InsertTitleIntoTimeline", "Title"),
    ],
)
def test_insert_adds_one_item_and_warns_about_the_track(
    run_cli: Run,
    parse_toon_keys: Keys,
    fake_world: FakeWorld,
    kind: str,
    method: str,
    item_kind: str,
) -> None:
    code, out, _ = run_cli(["timeline", "insert", kind, "Solid Color"])
    assert code == 0 and "Source Track Selector" in parse_toon_keys(out)["warning"]
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["timeline", "insert", kind, "Solid Color", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.calls_to(f"Timeline.{method}") == [("Solid Color",)]
    added = _edit(fake_world).tracks["video"][0].items[-1]
    assert (added.name, added.kind) == ("Solid Color", item_kind)


def test_insert_on_a_locked_track_is_api_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _edit(fake_world).tracks["video"][0].locked = True
    code, out, _ = run_cli(["timeline", "insert", "title", "Text", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert "locked" in out
    assert len(_edit(fake_world).tracks["video"][0].items) == 3


def test_import_into_passes_the_options_and_adds_an_item(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _aaf(tmp_path)
    args = ["timeline", "import-into", str(path), "--set", "insertAdditionalTracks=true"]
    code, out, _ = run_cli(args)
    assert code == 0 and parse_toon_keys(out)["irreversible"] == "true"
    assert "edits the current timeline" in out and fake_world.write_calls() == []
    code, out, _ = run_cli([*args, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["items"] == "6 -> 7"
    assert fake_world.calls_to("Timeline.ImportIntoTimeline") == [
        (str(path.resolve()), {"insertAdditionalTracks": True})
    ]
    assert fake_world.write_calls() == [SAVE, EXPORT, "Timeline.ImportIntoTimeline"]


@pytest.mark.parametrize(
    ("name", "extra", "code"),
    [
        ("x.otio", [], 2),
        ("cut.aaf", ["--set", "bogus=1"], 2),
        ("cut.aaf", ["--set", "insertAdditionalTracks=maybe"], 2),
        ("cut.aaf", ["--set", "insertWithOffset=soon"], 2),
        ("missing.aaf", [], 1),
    ],
)
def test_import_into_rejects_bad_input_before_any_write(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, name: str, extra: list[str], code: int
) -> None:
    _aaf(tmp_path)
    (tmp_path / "x.otio").write_text("x", encoding="utf-8")
    assert run_cli(["timeline", "import-into", str(tmp_path / name), *extra, "--force"])[0] == code
    assert fake_world.write_calls() == []


def test_import_into_that_resolve_refuses_is_api_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("Timeline.ImportIntoTimeline", False)
    code, out, _ = run_cli(["timeline", "import-into", str(_aaf(tmp_path)), "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
