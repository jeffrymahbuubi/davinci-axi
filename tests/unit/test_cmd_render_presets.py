from __future__ import annotations

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
    monkeypatch.setitem(registry.COMMANDS, "render", "davinci_axi.commands.render:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    path = (tmp_path / "out").resolve()
    path.mkdir()
    return path


# ------------------------------------------------------------ quick export


def _quick(out_dir: Path, *extra: str) -> list[str]:
    return ["render", "quick-export", "H.264 Master", "--target-dir", str(out_dir), *extra]


def test_quick_export_polls_to_render_complete(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, out, err = run_cli(_quick(out_dir, "--name", "clip", "--poll-interval", "0", "--force"))
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert err.splitlines() == ["quick export: Rendering 50%", "quick export: Render Complete 100%"]
    assert "  status,Inactive,Render Complete" in out
    assert fake_world.write_calls() == [*SAVE, "Project.RenderWithQuickExport"]
    assert fake_world.calls_to("Project.RenderWithQuickExport") == [
        ("H.264 Master", {"TargetDir": str(out_dir), "CustomName": "clip"})
    ]


def test_quick_export_dry_run_and_no_wait(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, out, _ = run_cli(_quick(out_dir))
    assert code == 0 and "applied: false" in out and fake_world.write_calls() == []
    code, out, err = run_cli(_quick(out_dir, "--no-wait", "--force"))
    assert (code, err) == (0, "") and "  status,Inactive,started" in out


def test_quick_export_upload_waits_for_the_upload(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, out, err = run_cli(
        _quick(out_dir, "--upload", "--quality", "0", "--poll-interval", "0", "--force")
    )
    assert code == 0 and "Upload Completed" in out
    assert "quick export: Uploading 100%" in err
    sent = fake_world.calls_to("Project.RenderWithQuickExport")[0][1]
    assert sent == {"TargetDir": str(out_dir), "VideoQuality": 0, "EnableUpload": True}


def test_quick_export_unknown_preset_lists_the_quick_presets(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, out, _ = run_cli(
        ["render", "quick-export", "ProRes 422 HQ", "--target-dir", str(out_dir), "--force"]
    )
    assert code == 1 and "NOT_FOUND" in out and "H.264 Master, YouTube" in out
    assert fake_world.write_calls() == []


def test_quick_export_has_the_same_directory_and_offline_checks(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    ext_render.mark_offline(_project(fake_world), "A002_city")
    code, out, _ = run_cli(_quick(out_dir, "--force"))
    assert code == 1 and "OFFLINE_MEDIA" in out
    (out_dir / "x.mov").write_text("x", encoding="utf-8")
    code, out, _ = run_cli(_quick(out_dir, "--allow-offline", "--force"))
    assert code == 1 and "TARGET_DIR_NOT_EMPTY" in out
    assert fake_world.write_calls() == []


def test_quick_export_failure_and_timeout(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    fake_world.inject(
        "Project.GetRenderWithQuickExportStatus", {"JobStatus": "Render Failed", "Error": "codec"}
    )
    code, out, _ = run_cli(_quick(out_dir, "--poll-interval", "0", "--force"))
    assert code == 1 and "JOB_FAILED" in out and "codec" in out
    fake_world.inject("Project.GetRenderWithQuickExportStatus", {"JobStatus": "Rendering"})
    code, out, _ = run_cli(
        _quick(out_dir, "--poll-timeout", "0.05", "--poll-interval", "0", "--force")
    )
    assert code == 1 and "POLL_TIMEOUT" in out


def test_quick_export_refusal_is_api_failed(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    fake_world.inject("Project.RenderWithQuickExport", None)
    code, out, _ = run_cli(_quick(out_dir, "--force"))
    assert code == 1 and "API_FAILED" in out


def test_quick_export_bad_quality_exits_2(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, out, _ = run_cli(_quick(out_dir, "--quality", "-5"))
    assert code == 2 and "--quality" in out and fake_world.calls == []


# ------------------------------------------------------------------ presets


def test_preset_save_then_delete_marks_the_delete_irreversible(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, dry, _ = run_cli(["render", "preset", "save", "mine"])
    assert code == 0 and "applied: false" in dry and fake_world.write_calls() == []
    assert not (tmp_path / "axi-home" / "snapshots").exists()
    code, out, _ = run_cli(["render", "preset", "save", "mine", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [*SAVE, "Project.SaveAsNewRenderPreset"]
    assert "mine" in _project(fake_world).render_presets
    code, out, _ = run_cli(["render", "preset", "delete", "mine", "--force"])
    assert code == 0 and "irreversible: true" in out and "warning:" in out
    assert "mine" not in _project(fake_world).render_presets


def test_preset_delete_dry_run_is_irreversible_and_writes_nothing(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["render", "preset", "delete", "YouTube 1080p"])
    assert code == 0 and "irreversible: true" in out and "applied: false" in out
    assert fake_world.write_calls() == []


def test_preset_save_refuses_an_existing_name_and_delete_a_missing_one(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["render", "preset", "save", "H.264 Master", "--force"])
    assert code == 1 and "ALREADY_EXISTS" in out and fake_world.write_calls() == []
    code, out, _ = run_cli(["render", "preset", "delete", "Nope", "--force"])
    assert code == 1 and "NOT_FOUND" in out and "H.264 Master" in out
    assert fake_world.write_calls() == []


def test_preset_update_is_irreversible_and_not_read_back(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["render", "preset", "update", "H.264 Master", "--force"])
    assert code == 0 and "irreversible: true" in out and "not read back" in out
    assert fake_world.write_calls() == [*SAVE, "Project.UpdateRenderPreset"]


def test_preset_load_takes_no_snapshot_and_is_checked_by_its_return_value(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["render", "preset", "load", "YouTube 1080p", "--force"])
    assert code == 0 and "snapshot: none" in out and "not read back" in out
    assert "  called,false,true" in out
    assert fake_world.write_calls() == ["Project.LoadRenderPreset"]
    assert not (tmp_path / "axi-home" / "snapshots").exists()
    fake_world.inject("Project.LoadRenderPreset", False)
    code, out, _ = run_cli(["render", "preset", "load", "YouTube 1080p", "--force"])
    assert code == 1 and "API_FAILED" in out


def test_preset_export_writes_a_file_and_refuses_to_overwrite(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "presets" / "h264.xml"
    code, out, _ = run_cli(["render", "preset", "export", "H.264 Master", str(target)])
    assert code == 0 and target.read_text(encoding="utf-8").startswith("fake render preset: H.264")
    assert parse_toon_keys(out)["bytes"] == str(target.stat().st_size)
    assert fake_world.write_calls() == ["Resolve.ExportRenderPreset"]
    code, out, _ = run_cli(["render", "preset", "export", "H.264 Master", str(target)])
    assert code == 1 and "FILE_EXISTS" in out
    code, _, _ = run_cli(["render", "preset", "export", "H.264 Master", str(target), "--overwrite"])
    assert code == 0
    code, out, _ = run_cli(["render", "preset", "export", "Nope", str(tmp_path / "n.xml")])
    assert code == 1 and "NOT_FOUND" in out


def test_preset_import_brings_an_exported_preset_back(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "mine.xml"
    run_cli(["render", "preset", "save", "mine", "--force"])
    run_cli(["render", "preset", "export", "mine", str(target)])
    run_cli(["render", "preset", "delete", "mine", "--force"])
    fake_world.calls.clear()
    code, dry, _ = run_cli(["render", "preset", "import", str(target)])
    assert code == 0 and "applied: false" in dry and fake_world.write_calls() == []
    code, out, _ = run_cli(["render", "preset", "import", str(target), "--force"])
    assert code == 0 and "imported[1]: mine" in out
    assert fake_world.write_calls() == [*SAVE, "Resolve.ImportRenderPreset"]
    assert "mine" in _project(fake_world).render_presets


def test_preset_import_missing_file_exits_1_before_any_write(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["render", "preset", "import", str(tmp_path / "no.xml"), "--force"])
    assert code == 1 and "NOT_FOUND" in out and fake_world.calls == []


def test_preset_quick_export_toggles_and_reads_back(run_cli: Run, fake_world: FakeWorld) -> None:
    project = _project(fake_world)
    code, out, _ = run_cli(["render", "preset", "quick-export", "ProRes 422 HQ", "--on", "--force"])
    assert code == 0 and "applied: true" in out
    assert "ProRes 422 HQ" in project.quick_export_presets
    assert fake_world.write_calls() == [*SAVE, "Project.SetQuickExportEnabledForRenderPreset"]
    code, out, _ = run_cli(
        ["render", "preset", "quick-export", "ProRes 422 HQ", "--off", "--force"]
    )
    assert code == 0 and "ProRes 422 HQ" not in project.quick_export_presets


def test_preset_quick_export_already_in_state_is_a_no_op_and_needs_one_flag(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["render", "preset", "quick-export", "YouTube", "--on", "--force"])
    assert code == 1 and "NOT_FOUND" in out  # YouTube is a quick preset, not a render preset
    code, out, _ = run_cli(["render", "preset", "quick-export", "H.264 Master", "--on", "--force"])
    assert code == 0 and "applied: false" in out and fake_world.write_calls() == []
    for flags in ([], ["--on", "--off"]):
        code, out, _ = run_cli(["render", "preset", "quick-export", "H.264 Master", *flags])
        assert code == 2 and "exactly one" in out


def test_preset_snapshot_failure_writes_nothing(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("ProjectManager.ExportProject", False)
    code, out, _ = run_cli(["render", "preset", "save", "mine", "--force"])
    assert code == 1 and "SNAPSHOT_FAILED" in out
    assert "Project.SaveAsNewRenderPreset" not in fake_world.write_calls()


@pytest.mark.parametrize(
    "verb", ["save", "update", "delete", "load", "import", "export", "quick-export"]
)
def test_preset_verbs_have_help_that_never_touches_resolve(
    run_cli: Run, fake_world: FakeWorld, verb: str
) -> None:
    code, out, err = run_cli(["render", "preset", verb, "--help"])
    assert (code, err) == (0, "") and "examples[" in out and fake_world.calls == []


def test_bare_preset_lists_its_subcommands(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "preset"])
    assert code == 2 and "save, update, delete, load, import, export, quick-export" in out
