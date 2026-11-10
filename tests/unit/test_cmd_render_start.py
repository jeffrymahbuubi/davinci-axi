"""`render start --verify` on the fake with ffprobe/ffmpeg stubbed through `ffmpeg_common.run`."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.commands import ffmpeg_common, render
from davinci_axi.commands.render_start import START_SPEC
from davinci_axi.fake import FakeWorld, ext_render
from davinci_axi.fake.core import FakeProject
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

SPEC: CommandSpec = replace(render.SPEC, subcommands={**render.SUBCOMMANDS, "start": START_SPEC})
SIZE = 7_195_566  # 7.2 decimal MB
EBUR = (
    "[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n    I:         -16.3 LUFS\n"
    "\n  True peak:\n    Peak:       -1.3 dBFS\n"
)
VIDEO = {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920}
AUDIO = {"codec_type": "audio", "codec_name": "aac", "channels": 2, "bit_rate": "320000"}
POLL = ["--poll-interval", "0", "--force"]


class Tools:
    """A stand-in for `ffmpeg_common.run`: JSON for ffprobe, ebur128 text for ffmpeg."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if Path(command[0]).name == "ffprobe":
            body = {
                "format": {"size": str(SIZE), "duration": "20.0", "bit_rate": "3075000"},
                "streams": [VIDEO, AUDIO],
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(body), "")
        return subprocess.CompletedProcess(command, 0, "", EBUR)


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_render.extend()
    monkeypatch.setitem(registry.COMMANDS, "render", f"{__name__}:SPEC")
    monkeypatch.delenv("FFPROBE", raising=False)
    monkeypatch.delenv("FFMPEG", raising=False)


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch) -> Tools:
    fake = Tools()
    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda name: f"/opt/homebrew/bin/{name}")
    monkeypatch.setattr(ffmpeg_common, "run", fake)
    return fake


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _job(world: FakeWorld, job_id: str, directory: Path, *, file: bool = True) -> None:
    """Point job `job_id` at `directory/<job_id>.mp4` (a 20 s mark range) and create the file."""
    job = next(j for j in _project(world).render_jobs if j["JobId"] == job_id)
    job.update(
        {
            "TargetDir": str(directory),
            "OutputFilename": f"{job_id}.mp4",
            "MarkIn": 0,
            "MarkOut": 479,
        }
    )
    if file:
        (directory / f"{job_id}.mp4").write_bytes(b"x")


def _start(run_cli: Run, *extra: str) -> tuple[int, dict[str, Any], str]:
    code, out, err = run_cli(["render", "start", "job-2", *extra, "--json"])
    return code, json.loads(out), err


def _checks(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["check"]: c for c in data["verify"][0]["checks"]}


def test_verify_reports_checks_and_exits_zero_when_they_pass(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    _job(fake_world, "job-2", tmp_path)
    code, data, err = _start(run_cli, "--verify", "--max-mb", "8", "--lufs", "-16", *POLL)
    assert code == 0 and data["applied"] is True
    row = data["verify"][0]
    assert row["job"] == "job-2" and row["file"].endswith("job-2.mp4") and row["ok"] is True
    assert (row["mb"], row["duration_s"], row["lufs"], row["true_peak"]) == (7.2, 20.0, -16.3, -1.3)
    assert set(_checks(data)) == {"size", "loudness", "duration"}
    assert "render: Complete 100%" in err
    assert len(tools.calls) == 2


def test_failed_cap_exits_one_but_the_render_stays_done(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    _job(fake_world, "job-2", tmp_path)
    code, data, _ = _start(run_cli, "--verify", "--max-mb", "6", *POLL)
    assert code == 1 and data["applied"] is True
    assert _checks(data)["size"]["ok"] is False and data["verify"][0]["ok"] is False
    assert (data["changes"][0]["before"], data["changes"][0]["after"]) == ("Ready", "Complete")
    assert _project(fake_world).render_status["job-2"]["JobStatus"] == "Complete"
    help_text = "\n".join(data["help"])
    assert "the render completed; the verify caps failed" in help_text
    assert "render deliver --max-mb 6" in help_text and "render status job-2" in help_text


def test_any_cap_flag_implies_verify(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    _job(fake_world, "job-2", tmp_path)
    code, data, _ = _start(run_cli, "--true-peak", "-1", *POLL)
    assert code == 0 and set(_checks(data)) == {"true_peak", "duration"}


def test_without_verify_no_tool_runs(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, data, _ = _start(run_cli, *POLL)
    assert code == 0 and "verify" not in data and tools.calls == []


def test_verify_with_no_wait_exits_two_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    for extra in (["--verify"], ["--max-mb", "6"]):
        code, data, _ = _start(run_cli, *extra, "--no-wait", "--force")
        assert code == 2 and data["code"] == "VALIDATION_ERROR"
        assert "needs --wait" in str(data["error"])
    assert fake_world.calls == [] and tools.calls == []


def test_missing_tools_fail_before_any_resolve_call(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda name: None)
    code, data, _ = _start(run_cli, "--verify", "--force")
    assert code == 1 and data["code"] == "NOT_AVAILABLE"
    assert fake_world.calls == [] and tools.calls == []


@pytest.mark.parametrize(
    "extra", [["--max-mb", "0"], ["--lufs-tolerance", "-1"], ["--true-peak", "3"]]
)
def test_bad_caps_exit_two_without_a_resolve_call(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, extra: list[str]
) -> None:
    code, data, _ = _start(run_cli, "--verify", *extra, "--force")
    assert code == 2 and data["code"] == "VALIDATION_ERROR"
    assert fake_world.calls == [] and tools.calls == []


def test_missing_file_becomes_a_failed_row_not_a_crash(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    _job(fake_world, "job-2", tmp_path, file=False)
    code, data, _ = _start(run_cli, "--verify", "--max-mb", "8", *POLL)
    assert code == 1 and data["applied"] is True
    row = data["verify"][0]
    assert row["ok"] is False and "job-2.mp4" in row["error"] and "checks" not in row
    assert _project(fake_world).render_status["job-2"]["JobStatus"] == "Complete"
    assert tools.calls == []


def test_several_jobs_each_get_a_row_and_help_is_deduplicated(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    _job(fake_world, "job-1", tmp_path)
    _job(fake_world, "job-2", tmp_path)
    argv = ["render", "start", "job-1,job-2", "--rerender", "--verify", "--max-mb", "6"]
    code, out, _ = run_cli([*argv, *POLL, "--json"])
    data = json.loads(out)
    assert code == 1
    assert [row["job"] for row in data["verify"]] == ["job-1", "job-2"]
    overshoot = [line for line in data["help"] if "render deliver --max-mb 6" in line]
    assert len(overshoot) == 1 and len(data["help"]) == len(set(data["help"]))


def test_dry_run_measures_nothing_and_repeats_the_verify_flags(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, data, _ = _start(run_cli, "--verify", "--max-mb", "6", "--lufs", "-14")
    assert code == 0 and data["applied"] is False and "verify" not in data
    assert "--verify --max-mb 6 --lufs -14" in str(data["help"])
    assert fake_world.write_calls() == [] and tools.calls == []


def test_strict_flag_is_gone(run_cli: Run, fake_world: FakeWorld) -> None:
    assert run_cli(["render", "start", "job-2", "--strict"])[0] == 2


def test_help_lists_the_verify_flags_and_examples(run_cli: Run) -> None:
    code, out, _ = run_cli(["render", "start", "--help"])
    assert code == 0 and "examples[" in out
    for flag in ("--verify", "--max-mb", "--lufs-tolerance", "--true-peak"):
        assert flag in out
