from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_render
from davinci_axi.fake.core import FakeProject

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

SAVE = ["ProjectManager.SaveProject", "ProjectManager.ExportProject"]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_render.extend()
    ext_render.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "render", "davinci_axi.commands.render:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    path = (tmp_path / "out").resolve()
    path.mkdir()
    return path


def _add(out_dir: Path, *extra: str) -> list[str]:
    return ["render", "add", "--target-dir", str(out_dir), *extra]


def _status(world: FakeWorld, job_id: str) -> str:
    return str(_project(world).render_status[job_id]["JobStatus"])


def _job(world: FakeWorld, job_id: str) -> dict[str, object]:
    return next(j for j in _project(world).render_jobs if j["JobId"] == job_id)


# ---------------------------------------------------------------- render add


def test_add_dry_run_shows_the_job_and_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path, tmp_path: Path
) -> None:
    code, out, err = run_cli(_add(out_dir))
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["applied"] == "false"
    assert "changes[3]{field,before,after}:" in out
    assert "  job,null,(new job)" in out
    assert f"  TargetDir,null,{out_dir}" in out
    assert "--force` to apply" in out
    assert "stay as the project's render settings" in keys["settings_note"]
    assert "--preset" in keys["settings_note"]
    assert fake_world.write_calls() == []
    assert not _snapshots(tmp_path).exists()
    assert len(_project(fake_world).render_jobs) == 2


def test_add_force_saves_snapshots_sets_settings_then_adds_the_job(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path, tmp_path: Path
) -> None:
    code, out, _ = run_cli(_add(out_dir, "--force"))
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["job"] == "job-3"
    assert "stay as the project's render settings" in keys["settings_note"]
    assert fake_world.write_calls() == [*SAVE, "Project.SetRenderSettings", "Project.AddRenderJob"]
    names = [f"{c}.{m}" for c, m, _ in fake_world.calls]
    assert names.index("Project.GetRenderJobList", names.index("Project.AddRenderJob")) > 0
    assert fake_world.calls_to("Project.SetRenderSettings") == [({"TargetDir": str(out_dir)},)]
    assert (_snapshots(tmp_path) / keys["snapshot"]).is_dir()
    _, status_out, _ = run_cli(["render", "status"])
    assert "total: 3" in status_out and "  job-3,Job 3,Edit v1,Ready,0" in status_out
    assert _job(fake_world, "job-3")["TargetDir"] == str(out_dir)


def test_add_applies_typed_settings_and_reports_the_unmapped_ones(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    settings = "FormatWidth=1280,FormatHeight=720,FrameRate=23.976,MultiPassEncode=true"
    code, out, _ = run_cli(_add(out_dir, "--name", "Final", "--set", settings, "--force"))
    assert code == 0
    sent = fake_world.calls_to("Project.SetRenderSettings")[0][0]
    assert sent == {
        "TargetDir": str(out_dir),
        "CustomName": "Final",
        "FormatWidth": 1280,
        "FormatHeight": 720,
        "FrameRate": 23.976,
        "MultiPassEncode": True,
    }
    job = _job(fake_world, "job-3")
    assert (job["FormatWidth"], job["MultiPassEncode"]) == (1280, True)
    assert job["OutputFilename"] == "Final.mov"  # the echo carries an extension; still verified
    assert "applied_settings[1]: FrameRate=23.976" in out


def test_add_passes_the_preset_before_the_settings(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, out, _ = run_cli(_add(out_dir, "--preset", "H.264 Master", "--force"))
    assert code == 0 and "preset: H.264 Master" in out
    assert fake_world.write_calls() == [
        *SAVE,
        "Project.LoadRenderPreset",
        "Project.SetRenderSettings",
        "Project.AddRenderJob",
    ]
    assert _job(fake_world, "job-3")["PresetName"] == "H.264 Master"


def test_add_unknown_preset_is_not_found_before_any_write(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path, tmp_path: Path
) -> None:
    code, out, _ = run_cli(_add(out_dir, "--preset", "Nope", "--force"))
    assert code == 1 and "NOT_FOUND" in out
    assert "H.264 Master, YouTube 1080p, ProRes 422 HQ" in out
    assert fake_world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


def test_add_refuses_a_directory_that_already_holds_files(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    (out_dir / "old.mov").write_text("x", encoding="utf-8")
    code, out, _ = run_cli(_add(out_dir, "--force"))
    assert code == 1 and "TARGET_DIR_NOT_EMPTY" in out
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(_add(out_dir, "--allow-existing", "--force"))
    assert code == 0 and "applied: true" in out


def test_add_needs_an_existing_directory_or_mkdir(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "new" / "deeper"
    code, out, _ = run_cli(["render", "add", "--target-dir", str(target), "--force"])
    assert code == 1 and "NOT_FOUND" in out and "--mkdir" in out
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["render", "add", "--target-dir", str(target), "--mkdir"])
    assert code == 0 and "--mkdir --force" in out and not target.exists()
    code, _, _ = run_cli(["render", "add", "--target-dir", str(target), "--mkdir", "--force"])
    assert code == 0 and target.is_dir()


def test_add_a_file_as_target_is_a_usage_error(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    file = tmp_path / "f.txt"
    file.write_text("x", encoding="utf-8")
    code, out, _ = run_cli(["render", "add", "--target-dir", str(file)])
    assert code == 2 and "is a file" in out and fake_world.calls == []


def test_add_refuses_offline_media_unless_allowed(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    ext_render.mark_offline(_project(fake_world), "A001_beach")
    code, out, _ = run_cli(_add(out_dir, "--force"))
    assert code == 1 and "OFFLINE_MEDIA" in out and "A001_beach" in out
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(_add(out_dir, "--allow-offline", "--force"))
    assert code == 0 and "applied: true" in out


def test_add_video_quality_warns_when_it_looks_like_bits_per_second(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, out, _ = run_cli(_add(out_dir, "--set", "VideoQuality=20000000"))
    assert code == 0 and "VideoQuality 20000000 looks like bits per second" in out
    assert "kbps" in out
    code, out, _ = run_cli(_add(out_dir, "--set", "VideoQuality=20000"))
    assert code == 0 and "bits per second" not in out
    assert fake_world.write_calls() == []


def test_add_help_lists_the_set_keys_and_the_kbps_unit(run_cli: Run) -> None:
    code, out, _ = run_cli(["render", "add", "--help"])
    assert code == 0 and "VideoQuality" in out and "SelectAllFrames" in out
    assert "kbps" in out and "MarkIn/MarkOut" in out


@pytest.mark.parametrize(
    ("settings", "needle"),
    [
        ("FormatWidth=abc", "whole number"),
        ("Nope=1", "unknown render setting"),
        ("TargetDir=/tmp", "has its own flag"),
        ("VideoQuality=Awful", "VideoQuality"),
        ("MultiPassEncode=maybe", "true or false"),
    ],
)
def test_add_bad_settings_exit_2_before_connecting(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path, settings: str, needle: str
) -> None:
    code, out, _ = run_cli(_add(out_dir, "--set", settings))
    assert code == 2 and needle in out
    assert fake_world.calls == []


def test_add_video_quality_accepts_a_bit_rate_or_a_word(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    run_cli(_add(out_dir, "--set", "VideoQuality=Best", "--force"))
    run_cli(_add(out_dir, "--set", "VideoQuality=8000", "--force"))
    sent = [args[0]["VideoQuality"] for args in fake_world.calls_to("Project.SetRenderSettings")]
    assert sent == ["Best", 8000]


def test_add_missing_target_dir_flag_exits_2(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "add"])
    assert code == 2 and "--target-dir" in out and fake_world.calls == []


def test_add_on_another_timeline_restores_the_current_one(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, out, _ = run_cli(_add(out_dir, "--timeline", "Edit v2", "--force"))
    assert code == 0 and parse_toon_keys(out)["timeline_restored"] == "true"
    assert _job(fake_world, "job-3")["TimelineName"] == "Edit v2"
    project = _project(fake_world)
    assert project.current_timeline is not None and project.current_timeline.name == "Edit v1"
    assert fake_world.write_calls().count("Project.SetCurrentTimeline") == 2


def test_add_snapshot_failure_leaves_no_job(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    fake_world.inject("ProjectManager.ExportProject", False)
    code, out, _ = run_cli(_add(out_dir, "--force"))
    assert code == 1 and "SNAPSHOT_FAILED" in out
    assert "Project.AddRenderJob" not in fake_world.write_calls()
    assert len(_project(fake_world).render_jobs) == 2


def test_add_without_a_job_id_is_api_failed(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    fake_world.inject("Project.AddRenderJob", "")
    code, out, _ = run_cli(_add(out_dir, "--force"))
    assert code == 1 and "API_FAILED" in out and "AddRenderJob" in out


def test_add_rejected_settings_is_api_failed_and_adds_no_job(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    fake_world.inject("Project.SetRenderSettings", False)
    code, out, _ = run_cli(_add(out_dir, "--force"))
    assert code == 1 and "SetRenderSettings" in out
    assert "Project.AddRenderJob" not in fake_world.write_calls()


def test_add_verify_mismatch_names_the_field_and_the_snapshot(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    other = {"JobId": "job-3", "TimelineName": "Edit v1", "TargetDir": "/elsewhere"}
    fake_world.inject("Project.GetRenderJobList", [other])
    code, out, _ = run_cli(_add(out_dir, "--force"))
    assert code == 1 and "VERIFY_FAILED" in out and "TargetDir" in out


# -------------------------------------------------------------- render start


def test_start_polls_to_complete_with_progress_on_stderr(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["render", "start", "job-2", "--poll-interval", "0", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true"
    assert err.splitlines() == ["render: Rendering 50%", "render: Complete 100%"]
    assert "render:" not in out
    assert "  status,Ready,Complete" in out and "  job-2,Complete,100" in out
    assert fake_world.write_calls() == [*SAVE, "Project.StartRendering"]
    assert fake_world.calls_to("Project.StartRendering") == [(["job-2"], False)]
    assert _status(fake_world, "job-2") == "Complete" and not _project(fake_world).rendering


def test_start_rerender_dry_run_warns_that_the_file_is_overwritten(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["render", "start", "job-1", "--rerender"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert "overwrites in place: Master export.mov" in out
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["render", "start", "job-2"])
    assert code == 0 and "overwrites in place" not in out


def test_start_help_says_rerender_overwrites_in_place(run_cli: Run) -> None:
    code, out, _ = run_cli(["render", "start", "--help"])
    assert code == 0 and "overwrites the existing output file in" in out


def test_start_dry_run_changes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["render", "start", "job-2"])
    assert (code, err) == (0, "") and parse_toon_keys(out)["applied"] == "false"
    assert "  status,Ready,Complete" in out
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    assert _status(fake_world, "job-2") == "Ready"


def test_start_without_ids_or_with_both_exits_2(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "start", "--force"])
    assert code == 2 and "--all" in out
    code, out, _ = run_cli(["render", "start", "job-2", "--all", "--force"])
    assert code == 2 and "not both" in out
    assert fake_world.calls == []


def test_start_refuses_a_complete_job_unless_rerender(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "start", "job-1", "--force"])
    assert code == 1 and "JOB_COMPLETE" in out and "--rerender" in out
    assert fake_world.write_calls() == []
    code, out, err = run_cli(
        ["render", "start", "job-1", "--rerender", "--poll-interval", "0", "--force"]
    )
    assert code == 0 and "applied: true" in out and "render: Complete 100%" in err


def test_start_all_skips_complete_jobs_and_says_so(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "start", "--all", "--poll-interval", "0", "--force"])
    assert code == 0 and "job-1 (already Complete)" in out and "  job-2,Complete,100" in out
    code, out, _ = run_cli(["render", "start", "--all", "--force"])
    assert code == 0 and "applied: false" in out and "every selected job is already" in out
    assert fake_world.calls_to("Project.StartRendering") == [(["job-2"], False)]


def test_start_several_jobs_label_each_progress_line(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _, err = run_cli(
        ["render", "start", "job-1,job-2", "--rerender", "--poll-interval", "0", "--force"]
    )
    assert code == 0
    assert "render job-1: Complete 100%" in err and "render job-2: Complete 100%" in err


def test_start_no_wait_returns_after_the_start_call(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, err = run_cli(["render", "start", "job-2", "--no-wait", "--force"])
    assert (code, err) == (0, "")
    assert "  status,Ready,started" in out and "  job-2,Rendering,50" in out
    assert _status(fake_world, "job-2") == "Rendering" and _project(fake_world).rendering


def test_start_failed_job_is_job_failed_and_names_the_snapshot(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject("Project.GetRenderJobStatus", {"JobStatus": "Failed", "Error": "disk full"})
    code, out, err = run_cli(["render", "start", "job-2", "--poll-interval", "0", "--force"])
    assert code == 1 and "JOB_FAILED" in out and "disk full" in out and "snapshot" in out
    assert "render: Failed" in err


def test_start_poll_timeout_is_reported(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject(
        "Project.GetRenderJobStatus", {"JobStatus": "Rendering", "CompletionPercentage": 10}
    )
    argv = ["render", "start", "job-2", "--poll-timeout", "0.05", "--poll-interval", "0", "--force"]
    code, out, err = run_cli(argv)
    assert code == 1 and "POLL_TIMEOUT" in out
    assert err.splitlines() == ["render: Rendering 10%"]


def test_start_unknown_job_lists_the_queue(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "start", "job-9", "--force"])
    assert code == 1 and "NOT_FOUND" in out and "job-1, job-2" in out
    assert fake_world.write_calls() == []


def test_start_while_rendering_is_busy(run_cli: Run, fake_world: FakeWorld) -> None:
    _project(fake_world).rendering = True
    code, out, _ = run_cli(["render", "start", "job-2", "--force"])
    assert code == 1 and "RENDER_BUSY" in out and "render stop" in out
    assert fake_world.write_calls() == []


def test_start_api_refusal_is_api_failed(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("Project.StartRendering", False)
    code, out, _ = run_cli(["render", "start", "job-2", "--force"])
    assert code == 1 and "API_FAILED" in out and "StartRendering" in out


def test_start_bad_poll_flags_exit_2_before_connecting(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _, _ = run_cli(["render", "start", "job-2", "--poll-timeout", "0"])
    assert code == 2 and fake_world.calls == []


def test_start_snapshot_failure_starts_nothing(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("ProjectManager.SaveProject", False)
    code, out, _ = run_cli(["render", "start", "job-2", "--force"])
    assert code == 1 and "SNAPSHOT_FAILED" in out
    assert "Project.StartRendering" not in fake_world.write_calls()


# --------------------------------------------------- render stop and delete


def test_stop_when_idle_is_a_no_op(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["render", "stop", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and "no render is running" in out
    assert fake_world.write_calls() == []


def test_stop_cancels_the_running_render_without_a_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    run_cli(["render", "start", "job-2", "--no-wait", "--force"])
    fake_world.calls.clear()
    code, dry, _ = run_cli(["render", "stop"])
    assert code == 0 and "applied: false" in dry and fake_world.write_calls() == []
    code, out, _ = run_cli(["render", "stop", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true" and "snapshot: none" in out
    assert fake_world.write_calls() == ["Project.StopRendering"]
    assert _status(fake_world, "job-2") == "Cancelled" and not _project(fake_world).rendering


def test_delete_all_is_irreversible_and_empties_the_queue(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, dry, _ = run_cli(["render", "delete", "--all"])
    assert code == 0 and "irreversible: true" in dry and "applied: false" in dry
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["render", "delete", "--all", "--force"])
    assert code == 0 and "irreversible: true" in out and "applied: true" in out
    assert fake_world.write_calls() == [*SAVE, "Project.DeleteAllRenderJobs"]
    _, status, _ = run_cli(["render", "status"])
    assert parse_toon_keys(status)["total"] == "0"


def test_delete_named_jobs_leaves_the_others(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "delete", "job-1", "--force"])
    assert code == 0 and "irreversible: true" in out
    assert fake_world.calls_to("Project.DeleteRenderJob") == [("job-1",)]
    assert [j["JobId"] for j in _project(fake_world).render_jobs] == ["job-2"]


def test_delete_needs_ids_and_known_ids(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "delete", "--force"])
    assert code == 2 and "--all" in out
    code, out, _ = run_cli(["render", "delete", "job-9", "--force"])
    assert code == 1 and "NOT_FOUND" in out and fake_world.write_calls() == []


def test_delete_an_empty_queue_is_a_no_op(run_cli: Run, fake_world: FakeWorld) -> None:
    _project(fake_world).render_jobs.clear()
    code, out, _ = run_cli(["render", "delete", "--all", "--force"])
    assert code == 0 and "applied: false" in out and fake_world.write_calls() == []


# --------------------------------------------------------------- json parity


def test_json_output_for_a_forced_add(run_cli: Run, fake_world: FakeWorld, out_dir: Path) -> None:
    code, out, _ = run_cli([*_add(out_dir, "--force"), "--json"])
    data = json.loads(out)
    assert code == 0 and data["applied"] is True and data["job"] == "job-3"
    assert data["changes"][0] == {"field": "job", "before": None, "after": "job-3"}
