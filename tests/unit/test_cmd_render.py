from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import render, render_common
from davinci_axi.fake import FakeWorld, ext_render
from davinci_axi.fake.core import FakeProject
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register_render(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_render.extend()
    monkeypatch.setitem(registry.COMMANDS, "render", "davinci_axi.commands.render:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def test_presets_lists_presets_and_current_format(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["render", "presets"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["scope"] == "current project"
    assert "presets[3]: H.264 Master,YouTube 1080p,ProRes 422 HQ" in out
    assert "quick_export[2]: H.264 Master,YouTube" in out
    assert keys["current_format"] == "mov"
    assert keys["current_codec"] == "H264"
    assert keys["render_mode"] == "single clip"
    assert "davinci-axi render status" in out


def test_presets_empty_states_the_scope(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    project = _project(fake_world)
    project.render_presets = []
    project.quick_export_presets = []
    code, out, _ = run_cli(["render", "presets"])
    keys = parse_toon_keys(out)
    assert code == 0
    assert keys["presets"] == "0 presets in project davinci-axi-test"
    assert keys["quick_export"] == "0 quick export presets in project davinci-axi-test"


def test_presets_unknown_render_mode_is_labelled(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("Project.GetCurrentRenderMode", 7)
    _, out, _ = run_cli(["render", "presets"])
    assert parse_toon_keys(out)["render_mode"] == "unknown (7)"


def test_status_lists_two_jobs(run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld) -> None:
    code, out, err = run_cli(["render", "status"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["scope"] == "current project"
    assert keys["rendering"] == "false"
    assert keys["total"] == "2"
    assert "by_status[2]{status,count}:" in out
    assert "  Complete,1" in out and "  Ready,1" in out
    assert "jobs[2]{id,name,timeline,status,percent}:" in out
    assert "  job-1,Master export,Edit v1,Complete,100" in out
    assert "  job-2,Review export,Edit v1,Ready,0" in out


def test_status_reports_rendering_in_progress(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _project(fake_world).rendering = True
    assert parse_toon_keys(run_cli(["render", "status"])[1])["rendering"] == "true"


def test_status_fields_add_job_details(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(
        ["render", "status", "--fields", "id,target_dir,format,codec,width,height"]
    )
    assert code == 0
    assert "jobs[2]{id,target_dir,format,codec,width,height}:" in out
    assert "  job-1,/tmp/davinci-axi-renders,mov,H264,1920,1080" in out


def test_status_file_field_reports_the_output_file_name(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["render", "status", "--fields", "id,status,target_dir,file"])
    assert code == 0
    assert "jobs[2]{id,status,target_dir,file}:" in out
    assert "/tmp/davinci-axi-renders," in out
    assert ".mov" in out


def test_status_unknown_field_lists_valid_fields(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "status", "--fields", "nope"])
    assert code == 2
    assert "VALIDATION_ERROR" in out
    assert "Valid fields: id, name, timeline, status, percent" in out


def test_status_limit_shows_a_page_of_the_total(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["render", "status", "--limit", "1"])
    keys = parse_toon_keys(out)
    assert code == 0
    assert keys["total"] == "2"
    assert "jobs[1]{id,name,timeline,status,percent}:" in out
    assert "Showing 1 of 2 jobs" in out


def test_status_empty_queue_states_the_scope(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    project = _project(fake_world)
    project.render_jobs.clear()
    code, out, _ = run_cli(["render", "status"])
    keys = parse_toon_keys(out)
    assert code == 0
    assert keys["jobs"] == "0 jobs in project davinci-axi-test"
    assert keys["total"] == "0"
    assert "by_status" not in keys


def test_status_missing_job_status_becomes_unknown(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("Project.GetRenderJobStatus", None)
    code, out, _ = run_cli(["render", "status"])
    assert code == 0
    assert "  job-1,Master export,Edit v1,unknown,null" in out
    assert "  unknown,2" in out


def test_bare_render_exits_2_listing_subcommands(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render"])
    assert code == 2
    names = "presets, status, formats, add, start, stop, delete, quick-export, frame, format, mode"
    assert f"Valid subcommands for `render`: {names}, preset" in out
    assert fake_world.calls == []


def test_unknown_subcommand_exits_2(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "nope"])
    assert code == 2
    assert "unknown subcommand nope" in out and "presets, status, formats" in out


@pytest.mark.parametrize("argv", [["render"], ["render", "presets"], ["render", "status"]])
def test_help_exits_zero_without_touching_resolve(
    run_cli: Run, fake_world: FakeWorld, argv: list[str]
) -> None:
    code, out, err = run_cli([*argv, "--help"])
    assert (code, err) == (0, "")
    assert "davinci-axi render" in out
    assert fake_world.calls == []


@pytest.mark.parametrize("sub", ["presets", "status"])
def test_read_commands_make_no_write_calls(run_cli: Run, fake_world: FakeWorld, sub: str) -> None:
    fake_world.resolve.page = "color"
    run_cli(["render", sub])
    run_cli(["render", sub, "--json"])
    assert fake_world.write_calls() == []
    assert fake_world.page_changes() == []
    assert fake_world.resolve.GetCurrentPage() == "color"


def test_json_parity_with_toon(run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld) -> None:
    _, toon_out, _ = run_cli(["render", "status"])
    code, json_out, _ = run_cli(["render", "status", "--json"])
    data = json.loads(json_out)
    assert code == 0
    assert set(parse_toon_keys(toon_out)) | {"by_status", "jobs", "help"} == set(data)
    assert data["jobs"][0]["status"] == "Complete"
    assert data["rendering"] is False
    _, presets_out, _ = run_cli(["render", "presets", "--json"])
    assert json.loads(presets_out)["presets"] == ["H.264 Master", "YouTube 1080p", "ProRes 422 HQ"]


@pytest.mark.parametrize("sub", ["presets", "status"])
def test_no_project_exits_1(run_cli: Run, fake_world: FakeWorld, sub: str) -> None:
    fake_world.resolve.pm.current_project = None
    code, out, _ = run_cli(["render", sub])
    assert code == 1
    assert "NO_PROJECT" in out


@pytest.mark.parametrize("sub", ["presets", "status"])
def test_connection_failure_exits_1_with_the_code(
    run_cli: Run, fake_world: FakeWorld, sub: str
) -> None:
    fake_world.connection = "not_running"
    code, out, _ = run_cli(["render", sub])
    assert code == 1
    assert "RESOLVE_NOT_RUNNING" in out


def _paths(spec: CommandSpec, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    found = [prefix] if prefix else []
    for name, child in spec.subcommands.items():
        found.extend(_paths(child, (*prefix, name)))
    return found


def test_every_render_path_has_help_that_never_touches_resolve(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    paths = _paths(render.SPEC)
    assert ("add",) in paths and ("preset", "save") in paths and ("format", "set") in paths
    for path in paths:
        code, out, err = run_cli(["render", *path, "--help"])
        assert (code, err) == (0, ""), path
        assert "examples[" in out, path
    assert fake_world.calls == []


def test_formats_lists_formats_codecs_and_resolutions_without_writing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["render", "formats"])
    assert code == 0 and parse_toon_keys(out)["kind"] == "video"
    assert "formats[2]{name,extension}:" in out and "  QuickTime,mov" in out
    code, out, _ = run_cli(["render", "formats", "--codecs", "mov", "--resolutions"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["codecs_for"] == "mov"
    assert "codecs[2]{name,codec}:" in out and "  ProRes 422 HQ,ProRes422HQ" in out
    assert "resolutions[1]{width,height}:" in out and "  1920,1080" in out
    assert fake_world.calls_to("Project.GetRenderResolutions") == [("mov", None)]
    code, out, _ = run_cli(["render", "formats", "--audio", "--codecs", "wav"])
    assert code == 0 and "  Wave,wav" in out and "  Linear PCM,LinearPCM" in out
    assert fake_world.write_calls() == []


def test_formats_unknown_extension_is_not_found(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "formats", "--codecs", "zzz"])
    assert code == 1 and "NOT_FOUND" in out and "mov, mp4" in out


def test_format_set_changes_the_format_without_a_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, dry, _ = run_cli(["render", "format", "set", "mp4", "H265"])
    assert code == 0 and "applied: false" in dry and fake_world.write_calls() == []
    code, out, _ = run_cli(["render", "format", "set", "mp4", "H265", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true" and "snapshot: none" in out
    assert "  format,mov,mp4" in out and "  codec,H264,H265" in out
    assert fake_world.write_calls() == ["Project.SetCurrentRenderFormatAndCodec"]
    assert _project(fake_world).format_codec == {"format": "mp4", "codec": "H265"}
    assert not (tmp_path / "axi-home" / "snapshots").exists()


def test_format_set_accepts_names_and_rejects_unknown_values(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, _, _ = run_cli(["render", "format", "set", "QuickTime", "ProRes 422 HQ", "--force"])
    assert code == 0
    assert _project(fake_world).format_codec == {"format": "mov", "codec": "ProRes422HQ"}
    code, out, _ = run_cli(["render", "format", "set", "avi", "H264", "--force"])
    assert code == 1 and "NOT_FOUND" in out and "mov, mp4" in out
    code, out, _ = run_cli(["render", "format", "set", "mp4", "Nope", "--force"])
    assert code == 1 and "NOT_FOUND" in out and "H264, H265" in out


def test_format_set_to_the_current_value_is_a_no_op(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "format", "set", "mov", "H264", "--force"])
    assert code == 0 and "applied: false" in out and fake_world.write_calls() == []


def test_format_set_refusal_is_api_failed(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("Project.SetCurrentRenderFormatAndCodec", False)
    code, out, _ = run_cli(["render", "format", "set", "mp4", "H265", "--force"])
    assert code == 1 and "API_FAILED" in out


def test_mode_set_switches_and_reads_back(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "mode", "set", "individual", "--force"])
    assert code == 0 and "  mode,single clip,individual clips" in out
    assert _project(fake_world).render_mode == 0
    assert fake_world.write_calls() == ["Project.SetCurrentRenderMode"]
    code, out, _ = run_cli(["render", "mode", "set", "individual", "--force"])
    assert code == 0 and "applied: false" in out


def test_mode_set_bad_word_exits_2_before_connecting(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "mode", "set", "both"])
    assert code == 2 and "individual, single" in out and fake_world.calls == []


def test_frame_exports_through_the_color_page_and_restores_the_page(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "stills" / "frame.png"
    code, out, _ = run_cli(["render", "frame", str(target)])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["page_restored"] == "true" and keys["timeline"] == "Edit v1"
    assert target.read_text(encoding="utf-8") == "fake still: Edit v1\n"
    assert fake_world.page_changes() == ["color", "edit"]
    assert fake_world.resolve.GetCurrentPage() == "edit"
    assert not (tmp_path / "axi-home" / "snapshots").exists()
    code, out, _ = run_cli(["render", "frame", str(target)])
    assert code == 1 and "FILE_EXISTS" in out
    fake_world.resolve.page = "color"
    code, out, _ = run_cli(["render", "frame", str(target), "--overwrite"])
    assert code == 0 and "page_restored" not in out


def test_frame_failure_still_restores_the_page(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("Project.ExportCurrentFrameAsStill", False)
    code, out, _ = run_cli(["render", "frame", str(tmp_path / "f.png")])
    assert code == 1 and "API_FAILED" in out
    assert fake_world.resolve.GetCurrentPage() == "edit"


def test_spec_methods_are_declared_for_every_verb() -> None:
    methods = set(render.SPEC.methods)
    assert {
        "Project.AddRenderJob",
        "Project.StartRendering",
        "Resolve.ExportRenderPreset",
    } <= methods
    assert {"Project.RenderWithQuickExport", "Project.SetCurrentRenderMode"} <= methods
    assert all(re.fullmatch(r"[A-Z]\w+\.[A-Z]\w+", m) for m in methods)
    for name, spec in render.SPEC.subcommands.items():
        assert spec.methods, name
    assert render.SPEC.reads_only is False


def test_setting_tables_match_the_pyi_and_the_fake() -> None:
    types = render_common.RENDER_SETTING_TYPES
    assert len(types) == 33 and set(types) == set(ext_render.SETTING_KEYS)
    assert types["FrameRate"] is float and types["ExportAlpha"] is bool
    assert set(render_common.JOB_INFO_KEYS) <= set(types)
    assert render_common.JOB_INFO_KEYS == ext_render.INFO_KEYS
