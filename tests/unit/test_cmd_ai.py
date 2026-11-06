"""`ai` verbs on clips, items and timelines (U20) on the fake: slate, Intellisearch, magic mask,
stabilize, reframe, scene cuts, subtitles and `ai status`. Deblur and multicam are in
`test_cmd_ai_multicam`."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeMediaPoolItem, FakeProject, FakeTimeline, FakeWorld, ext_ai

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

SAVE = ["ProjectManager.SaveProject", "ProjectManager.ExportProject"]
POLL = ["--poll-interval", "0"]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_ai.extend()
    ext_ai.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "ai", "davinci_axi.commands.ai:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")
    yield


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _timeline(world: FakeWorld, name: str = "Edit v1") -> FakeTimeline:
    return next(t for t in _project(world).timelines if t.name == name)


def _clip(world: FakeWorld, name: str) -> FakeMediaPoolItem:
    root = _project(world).media_pool.root
    pool = [*root.clips, *(c for folder in root.subfolders for c in folder.clips)]
    return next(c for c in pool if c.name == name)


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _untouched(world: FakeWorld, tmp_path: Path) -> None:
    assert world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


# ---------------------------------------------------------------- slate


def test_slate_dry_run_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["ai", "slate", "A001_beach", "--marker", "Green", *POLL])
    assert (code, err) == (0, "") and "status,Inactive,Complete" in out
    assert parse_toon_keys(out)["applied"] == "false" and "marker: green" in out
    _untouched(fake_world, tmp_path)


def test_slate_force_polls_with_progress_on_stderr(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["ai", "slate", "A001_beach", "--marker", "Green", *POLL, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and "status,Inactive,Complete" in out
    assert err.splitlines() == ["slate: Analyzing 50%", "slate: Complete 100%"]
    assert fake_world.write_calls() == [*SAVE, "MediaPoolItem.AnalyzeForSlate"]
    green = fake_world.resolve.MARKER_GREEN  # type: ignore[attr-defined]
    assert fake_world.calls_to("MediaPoolItem.AnalyzeForSlate") == [(green,)]
    assert _clip(fake_world, "A001_beach").__dict__["slate_marker"] == green
    assert (_snapshots(tmp_path) / keys["snapshot"]).is_dir()


def test_slate_folder_calls_the_folder_method_and_defaults_to_blue(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["ai", "slate", "--folder", "Master/Footage", *POLL, "--force"])
    assert code == 0 and "media pool folder Master/Footage" in out
    assert fake_world.write_calls() == [*SAVE, "Folder.AnalyzeForSlate"]
    blue = fake_world.resolve.MARKER_BLUE  # type: ignore[attr-defined]
    assert fake_world.calls_to("Folder.AnalyzeForSlate") == [(blue,)]
    for name in ("A001_beach", "A002_city", "A003_dusk"):
        assert _clip(fake_world, name).__dict__["slate_marker"] == blue


def test_slate_validation_exits_two_before_any_call(run_cli: Run, fake_world: FakeWorld) -> None:
    for argv in (
        ["ai", "slate", "A001_beach", "--marker", "plaid"],
        ["ai", "slate"],
        ["ai", "slate", "A001_beach", "--folder", "Master/Footage"],
        ["ai", "slate", "A001_beach", "--poll-timeout", "0"],
    ):
        code, _, _ = run_cli(argv)
        assert code == 2, argv
    assert fake_world.calls == []


def test_slate_without_ai_extras_is_not_available_after_the_snapshot(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    ext_ai.set_ai_extras(fake_world, False)
    code, out, _ = run_cli(["ai", "slate", "A001_beach", *POLL, "--force"])
    assert code == 1 and "NOT_AVAILABLE" in out and "AI Extras" in out
    assert "snapshot" in out  # the snapshot was taken, then the call refused
    assert len(fake_world.calls_to("MediaPoolItem.AnalyzeForSlate")) == 1
    assert fake_world.calls_to("Project.GetAnalyzeForSlateStatus") == [()]  # only the "before" read
    assert _snapshots(tmp_path).is_dir()


def test_slate_no_wait_returns_after_the_start_call(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["ai", "slate", "A001_beach", "--no-wait", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true" and err == ""
    assert "status,Inactive,started" in out
    assert "slate_marker" not in _clip(fake_world, "A001_beach").__dict__


def test_slate_job_failure_timeout_and_busy(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject(
        "Project.GetAnalyzeForSlateStatus", {"JobStatus": "Failed", "Error": "no slate"}
    )
    code, out, err = run_cli(["ai", "slate", "A001_beach", *POLL, "--force"])
    assert code == 1 and "JOB_FAILED" in out and "no slate" in out
    assert "slate: Failed" in err
    fake_world.inject("Project.GetAnalyzeForSlateStatus", {"JobStatus": "Inactive"})
    args = ["ai", "slate", "A001_beach", "--poll-timeout", "0.05", "--poll-interval", "0.01"]
    code, out, _ = run_cli([*args, "--force"])
    assert code == 1 and "POLL_TIMEOUT" in out
    fake_world.inject("Project.GetAnalyzeForSlateStatus", {"JobStatus": "Analyzing"})
    before = len(fake_world.write_calls())
    code, out, _ = run_cli(["ai", "slate", "A001_beach", *POLL, "--force"])
    assert code == 1 and "AI_BUSY" in out
    assert len(fake_world.write_calls()) == before


# ---------------------------------------------------------------- intellisearch


def test_intellisearch_analyze_passes_the_flags(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = ["ai", "intellisearch", "analyze", "A001_beach", "--faces"]
    code, out, _ = run_cli(args)
    assert code == 0 and "not read back" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [*SAVE, "MediaPoolItem.AnalyzeForIntellisearch"]
    assert fake_world.calls_to("MediaPoolItem.AnalyzeForIntellisearch") == [(True, False)]
    code, _, _ = run_cli(["ai", "intellisearch", "analyze", "A002_city", "--better", "--force"])
    assert fake_world.calls_to("MediaPoolItem.AnalyzeForIntellisearch")[-1] == (False, True)
    assert code == 0


def test_intellisearch_analyze_folder_and_unavailable(run_cli: Run, fake_world: FakeWorld) -> None:
    args = ["ai", "intellisearch", "analyze", "--folder", "Master/Footage", "--force"]
    assert run_cli(args)[0] == 0
    assert fake_world.calls_to("Folder.AnalyzeForIntellisearch") == [(False, False)]
    ext_ai.set_ai_extras(fake_world, False)
    code, out, _ = run_cli(args)
    assert code == 1 and "NOT_AVAILABLE" in out


def test_intellisearch_reset_is_irreversible(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    run_cli(["ai", "intellisearch", "analyze", "A001_beach", "--force"])
    code, out, _ = run_cli(["ai", "intellisearch", "reset"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and keys["irreversible"] == "true"
    assert fake_world.calls_to("Project.ResetIntellisearchAnalysis") == []
    code, out, _ = run_cli(["ai", "intellisearch", "reset", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.calls_to("Project.ResetIntellisearchAnalysis") == [()]
    assert "intellisearch" not in _clip(fake_world, "A001_beach").__dict__


# ---------------------------------------------------------------- magic mask, stabilize


def test_magic_mask_create_passes_the_mode_code(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["ai", "magic-mask", "create", "V1:1", "--mode", "both"])
    assert code == 0 and "not read back" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli(["ai", "magic-mask", "create", "V1:1", "--mode", "both", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [*SAVE, "TimelineItem.CreateMagicMask"]
    assert fake_world.calls_to("TimelineItem.CreateMagicMask") == [("BI",)]
    run_cli(["ai", "magic-mask", "create", "V1:2", "--force"])
    assert fake_world.calls_to("TimelineItem.CreateMagicMask")[-1] == ("F",)
    run_cli(["ai", "magic-mask", "create", "V1:2", "--mode", "backward", "--force"])
    assert fake_world.calls_to("TimelineItem.CreateMagicMask")[-1] == ("B",)


def test_magic_mask_bad_mode_and_missing_item(run_cli: Run, fake_world: FakeWorld) -> None:
    assert run_cli(["ai", "magic-mask", "create", "V1:1", "--mode", "sideways"])[0] == 2
    code, out, _ = run_cli(["ai", "magic-mask", "create", "V1:9", "--force"])
    assert code == 1 and "NOT_FOUND" in out
    assert fake_world.write_calls() == []


def test_magic_mask_regenerate_and_stabilize(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    for argv, call in (
        (["ai", "magic-mask", "regenerate", "V1:1"], "TimelineItem.RegenerateMagicMask"),
        (["ai", "stabilize", "V1:1"], "TimelineItem.Stabilize"),
    ):
        code, out, _ = run_cli([*argv, "--force"])
        assert code == 0 and parse_toon_keys(out)["applied"] == "true", argv
        assert fake_world.calls_to(call) == [()]
    ops = ext_ai.ai_ops(_timeline(fake_world).tracks["video"][0].items[0])
    assert [op for op, _ in ops] == ["magic-mask-regenerate", "stabilize"]


def test_stabilize_on_another_timeline_restores_the_current_one(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    project = _project(fake_world)
    project.current_timeline = _timeline(fake_world, "Edit v2")
    code, out, _ = run_cli(["ai", "stabilize", "V1:1", "--timeline", "Edit v1", "--force"])
    assert code == 0 and parse_toon_keys(out)["timeline_restored"] == "true"
    assert project.current_timeline is _timeline(fake_world, "Edit v2")


def test_item_verbs_without_ai_extras_are_not_available(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    ext_ai.set_ai_extras(fake_world, False)
    for argv in (
        ["ai", "stabilize", "V1:1", "--force"],
        ["ai", "magic-mask", "create", "V1:1", "--force"],
    ):
        code, out, _ = run_cli(argv)
        assert code == 1 and "NOT_AVAILABLE" in out and "AI Extras" in out, argv


# ---------------------------------------------------------------- reframe


def test_reframe_polls_and_no_wait_returns(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["ai", "reframe", "V1:1", *POLL, "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert err.splitlines() == ["reframe: Analyzing 50%", "reframe: Complete 100%"]
    assert fake_world.write_calls() == [*SAVE, "TimelineItem.SmartReframe"]
    item = _timeline(fake_world).tracks["video"][0].items[0]
    assert [op for op, _ in ext_ai.ai_ops(item)] == ["reframe"]
    code, out, err = run_cli(["ai", "reframe", "V1:2", "--no-wait", "--force"])
    assert code == 0 and err == "" and "status,Complete,started" in out


def test_reframe_no_wait_on_another_timeline_is_refused(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _project(fake_world).current_timeline = _timeline(fake_world, "Edit v2")
    code, out, _ = run_cli(["ai", "reframe", "V1:1", "--timeline", "Edit v1", "--no-wait"])
    assert code == 2 and "not the current timeline" in out
    assert fake_world.write_calls() == []


# ---------------------------------------------------------------- scene cuts


def test_scene_cuts_adds_markers_and_warns(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    timeline = _timeline(fake_world)
    code, out, _ = run_cli(["ai", "scene-cuts", *POLL])
    assert code == 0 and "adds cuts and markers" in out
    _untouched(fake_world, tmp_path)
    code, out, err = run_cli(["ai", "scene-cuts", *POLL, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["markers_added"] == "2"
    assert err.splitlines() == ["scene cuts: Analyzing 50%", "scene cuts: Complete 100%"]
    assert fake_world.write_calls() == [*SAVE, "Timeline.DetectSceneCuts"]
    assert len(timeline.markers) == 3  # the review marker plus two cuts


def test_scene_cuts_no_wait_and_failure(run_cli: Run, fake_world: FakeWorld) -> None:
    timeline = _timeline(fake_world)
    code, out, err = run_cli(["ai", "scene-cuts", "--no-wait", "--force"])
    assert code == 0 and err == "" and "status,Inactive,started" in out
    assert "markers_added" not in out and len(timeline.markers) == 1
    fake_world.inject("Project.GetDetectSceneCutsStatus", {"JobStatus": "Cancelled"})
    code, out, _ = run_cli(["ai", "scene-cuts", *POLL, "--force"])
    assert code == 1 and "JOB_FAILED" in out


def test_scene_cuts_on_another_timeline_keeps_it_open_then_restores(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    project = _project(fake_world)
    code, out, _ = run_cli(["ai", "scene-cuts", "--timeline", "Edit v2", *POLL, "--force"])
    assert code == 0 and parse_toon_keys(out)["timeline_restored"] == "true"
    assert project.current_timeline is _timeline(fake_world, "Edit v1")
    assert len(_timeline(fake_world, "Edit v2").markers) == 2


def test_scene_cuts_without_ai_extras(run_cli: Run, fake_world: FakeWorld) -> None:
    ext_ai.set_ai_extras(fake_world, False)
    code, out, _ = run_cli(["ai", "scene-cuts", *POLL, "--force"])
    assert code == 1 and "NOT_AVAILABLE" in out
    assert len(fake_world.calls_to("Timeline.DetectSceneCuts")) == 1


# ---------------------------------------------------------------- subtitles


def test_subtitles_pass_the_enum_values(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = ["ai", "subtitles", "--language", "german", "--preset", "netflix"]
    args += ["--chars-per-line", "32", "--line-break", "double", "--gap", "2"]
    code, out, _ = run_cli([*args, *POLL])
    assert code == 0 and "language=german" in out and "charsPerLine=32" in out
    _untouched(fake_world, tmp_path)
    code, out, err = run_cli([*args, *POLL, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["subtitle_tracks_added"] == "1"
    assert err.splitlines() == ["subtitles: Analyzing 50%", "subtitles: Complete 100%"]
    resolve = fake_world.resolve
    assert fake_world.calls_to("Timeline.CreateSubtitlesFromAudio") == [
        (
            {
                "language": resolve.AUTO_CAPTION_GERMAN,  # type: ignore[attr-defined]
                "captionPreset": resolve.AUTO_CAPTION_NETFLIX,  # type: ignore[attr-defined]
                "charsPerLine": 32,
                "lineBreak": resolve.AUTO_CAPTION_LINE_DOUBLE,  # type: ignore[attr-defined]
                "gap": 2,
            },
        )
    ]
    assert _timeline(fake_world).GetTrackCount("subtitle") == 1


def test_subtitles_defaults_pass_no_settings(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["ai", "subtitles", *POLL, "--force"])
    assert code == 0 and "(defaults)" in out
    assert fake_world.calls_to("Timeline.CreateSubtitlesFromAudio") == [(None,)]


def test_subtitles_validation_exits_two_before_any_call(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    for argv in (
        ["ai", "subtitles", "--chars-per-line", "99"],
        ["ai", "subtitles", "--chars-per-line", "0"],
        ["ai", "subtitles", "--gap", "11"],
        ["ai", "subtitles", "--language", "klingon"],
        ["ai", "subtitles", "--preset", "bogus"],
        ["ai", "subtitles", "--line-break", "triple"],
    ):
        assert run_cli(argv)[0] == 2, argv
    assert fake_world.calls == []


def test_subtitles_busy_and_timeout(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("Project.GetCreateSubtitlesFromAudioStatus", {"JobStatus": "Initializing"})
    code, out, _ = run_cli(["ai", "subtitles", *POLL, "--force"])
    assert code == 1 and "AI_BUSY" in out
    fake_world.inject("Project.GetCreateSubtitlesFromAudioStatus", {"JobStatus": "Inactive"})
    args = ["ai", "subtitles", "--poll-timeout", "0.05", "--poll-interval", "0.01", "--force"]
    code, out, _ = run_cli(args)
    assert code == 1 and "POLL_TIMEOUT" in out


def test_subtitles_without_ai_extras(run_cli: Run, fake_world: FakeWorld) -> None:
    ext_ai.set_ai_extras(fake_world, False)
    code, out, _ = run_cli(["ai", "subtitles", *POLL, "--force"])
    assert code == 1 and "NOT_AVAILABLE" in out and "AI Extras" in out
    assert _timeline(fake_world).GetTrackCount("subtitle") == 0


# ---------------------------------------------------------------- status and safety


def test_status_lists_the_five_jobs(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["ai", "status"])
    assert (code, err) == (0, "") and "jobs[5]{job,status,percent,clip,eta_s,error}:" in out
    assert parse_toon_keys(out)["busy"] == "none"
    for job in ("slate", "reframe", "scene-cuts", "subtitles", "transcribe"):
        assert f"{job},Inactive" in out
    fake_world.inject(
        "Project.GetDetectSceneCutsStatus",
        {
            "JobStatus": "Analyzing",
            "CompletionPercentage": 10,
            "CurrentClipIndex": 1,
            "TotalClips": 3,
        },
    )
    code, out, _ = run_cli(["ai", "status"])
    assert parse_toon_keys(out)["busy"] == "scene-cuts"
    assert "scene-cuts,Analyzing,10,1/3" in out
    assert fake_world.write_calls() == []


def test_snapshot_failure_leaves_every_ai_call_unmade(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("ProjectManager.SaveProject", False)
    for argv in (
        ["ai", "slate", "A001_beach", "--force"],
        ["ai", "intellisearch", "analyze", "A001_beach", "--force"],
        ["ai", "intellisearch", "reset", "--force"],
        ["ai", "magic-mask", "create", "V1:1", "--force"],
        ["ai", "stabilize", "V1:1", "--force"],
        ["ai", "reframe", "V1:1", "--force"],
        ["ai", "scene-cuts", "--force"],
        ["ai", "subtitles", "--force"],
    ):
        code, out, _ = run_cli(argv)
        assert code == 1 and "SNAPSHOT_FAILED" in out, argv
    assert fake_world.write_calls() == ["ProjectManager.SaveProject"] * 8
    assert not _snapshots(tmp_path).exists()


def test_no_snapshot_is_refused_by_default(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["ai", "scene-cuts", "--no-snapshot", "--force"])
    assert code == 1 and "WRITE_REFUSED" in out
    assert fake_world.write_calls() == []


def test_help_for_every_ai_path_exits_zero(run_cli: Run) -> None:
    paths = [
        ["ai"],
        ["ai", "intellisearch"],
        ["ai", "intellisearch", "analyze"],
        ["ai", "intellisearch", "reset"],
        ["ai", "slate"],
        ["ai", "magic-mask"],
        ["ai", "magic-mask", "create"],
        ["ai", "magic-mask", "regenerate"],
        ["ai", "stabilize"],
        ["ai", "reframe"],
        ["ai", "scene-cuts"],
        ["ai", "subtitles"],
        ["ai", "deblur"],
        ["ai", "multicam"],
        ["ai", "multicam", "create"],
        ["ai", "multicam", "smart-switch"],
        ["ai", "multicam", "flatten"],
        ["ai", "status"],
    ]
    for path in paths:
        code, out, err = run_cli([*path, "--help"])
        assert code == 0 and err == "" and "examples[" in out, path
