"""`render verify` on the fake with ffprobe/ffmpeg stubbed through `ffmpeg_common.run` (U5)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from davinci_axi import registry
from davinci_axi.commands import ffmpeg_common, render
from davinci_axi.commands.render_deliver import encoder_floor_kbps
from davinci_axi.commands.render_verify import VERIFY_SPEC, checks_for, measure_job
from davinci_axi.fake import FakeWorld, ext_render
from davinci_axi.fake.core import FakeProject
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

SPEC: CommandSpec = replace(render.SPEC, subcommands={**render.SUBCOMMANDS, "verify": VERIFY_SPEC})
SIZE = 7_195_566  # 7.2 decimal MB, 6.86 MiB
EBUR = (
    "[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n    I:         -16.3 LUFS\n"
    "\n  True peak:\n    Peak:       -1.3 dBFS\n"
)
VIDEO = {
    "codec_type": "video",
    "codec_name": "h264",
    "width": 1080,
    "height": 1920,
    "r_frame_rate": "24/1",
    "avg_frame_rate": "24/1",
    "bit_rate": "2755000",
}
AUDIO = {"codec_type": "audio", "codec_name": "aac", "channels": 2, "bit_rate": "320000"}


class Tools:
    """A stand-in for `ffmpeg_common.run`: JSON for ffprobe, ebur128 text for ffmpeg."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.size = SIZE
        self.duration = "20.0"
        self.streams: list[dict[str, object]] = [VIDEO, AUDIO]

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if Path(command[0]).name == "ffprobe":
            body = {
                "format": {
                    "size": str(self.size),
                    "duration": self.duration,
                    "bit_rate": "3075000",
                },
                "streams": self.streams,
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


@pytest.fixture
def rendered(tmp_path: Path) -> Path:
    path = tmp_path / "edit.mp4"
    path.write_bytes(b"x")
    return path


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _checks(out: str) -> dict[str, dict[str, object]]:
    return {row["check"]: row for row in json.loads(out)["checks"]}


def _verify(run_cli: Run, file: Path, *extra: str) -> tuple[int, str]:
    code, out, _ = run_cli(["render", "verify", str(file), *extra, "--json"])
    return code, out


def test_file_measured_against_every_cap(run_cli: Run, tools: Tools, rendered: Path) -> None:
    code, out = _verify(run_cli, rendered, "--max-mb", "6", "--lufs", "-14", "--true-peak", "-1")
    assert code == 1
    data = json.loads(out)
    assert (data["bytes"], data["mb"], data["duration_s"]) == (SIZE, 7.2, 20.0)
    assert data["video"]["kbps"] == 2755 and data["audio"]["kbps"] == 320
    assert (data["lufs"], data["true_peak"], data["ok"]) == (-16.3, -1.3, False)
    checks = _checks(out)
    assert checks["size"]["ok"] is False and checks["size"]["measured"] == 7.2
    assert checks["loudness"]["ok"] is False
    assert checks["true_peak"]["ok"] is True
    assert "duration" not in checks
    assert len(tools.calls) == 2


def test_help_names_the_overshoot_floor_and_gain(
    run_cli: Run, tools: Tools, rendered: Path
) -> None:
    assert encoder_floor_kbps(1080, 1920) > 2755
    _, out = _verify(run_cli, rendered, "--max-mb", "6", "--lufs", "-14")
    help_text = "\n".join(json.loads(out)["help"])
    assert "render deliver --max-mb 6" in help_text and "1.20x" in help_text
    assert f"floor of about {encoder_floor_kbps(1080, 1920)} kbps" in help_text
    assert "audio gain <items> --db +2.3" in help_text


def test_size_uses_decimal_megabytes(run_cli: Run, tools: Tools, rendered: Path) -> None:
    tools.size = 6_000_000  # 6.00 MB decimal, but 5.72 MiB
    code, out = _verify(run_cli, rendered, "--max-mb", "6", "--no-loudness")
    assert code == 0 and json.loads(out)["mb"] == 6.0
    assert _checks(out)["size"]["ok"] is True
    tools.size = 6_000_001
    _, out = _verify(run_cli, rendered, "--max-mb", "6", "--no-loudness")
    assert _checks(out)["size"]["ok"] is False


def test_passing_file_has_no_failure_help(run_cli: Run, tools: Tools, rendered: Path) -> None:
    _, out = _verify(run_cli, rendered, "--max-mb", "8", "--lufs", "-16")
    data = json.loads(out)
    assert data["ok"] is True
    assert not any("overshot" in line for line in data.get("help", []))


def test_no_caps_reports_measurements_and_a_hint(
    run_cli: Run, tools: Tools, rendered: Path
) -> None:
    code, out = _verify(run_cli, rendered)
    data = json.loads(out)
    assert code == 0 and data["checks"] == [] and data["ok"] is True
    assert any("--max-mb" in line for line in data["help"])


def test_failed_check_exits_one_and_still_prints_checks(
    run_cli: Run, tools: Tools, rendered: Path
) -> None:
    args = ["render", "verify", str(rendered), "--no-loudness"]
    code, out = _verify(run_cli, rendered, "--no-loudness", "--max-mb", "6")
    assert code == 1 and json.loads(out)["ok"] is False and _checks(out)["size"]["ok"] is False
    assert run_cli([*args, "--max-mb", "8"])[0] == 0
    assert run_cli(args)[0] == 0


def test_strict_flag_is_gone(run_cli: Run, tools: Tools, rendered: Path) -> None:
    assert run_cli(["render", "verify", str(rendered), "--strict"])[0] == 2


def test_no_loudness_makes_one_call_and_drops_lufs(
    run_cli: Run, tools: Tools, rendered: Path
) -> None:
    code, out = _verify(run_cli, rendered, "--no-loudness")
    assert code == 0
    assert len(tools.calls) == 1 and Path(tools.calls[0][0]).name == "ffprobe"
    data = json.loads(out)
    assert "lufs" not in data and "true_peak" not in data


def test_audioless_file_fails_a_loudness_cap(run_cli: Run, tools: Tools, rendered: Path) -> None:
    tools.streams = [VIDEO]
    _, out = _verify(run_cli, rendered, "--lufs", "-14")
    assert _checks(out)["loudness"]["ok"] is False and _checks(out)["loudness"]["measured"] is None
    assert len(tools.calls) == 1


def test_missing_ffmpeg_fails_before_any_resolve_call(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda name: None)
    code, out, _ = run_cli(["render", "verify", "--job", "job-1", "--json"])
    assert code == 1 and json.loads(out)["code"] == "NOT_AVAILABLE"
    assert fake_world.calls == [] and tools.calls == []


def test_ffprobe_alone_is_enough_with_no_loudness(
    run_cli: Run, tools: Tools, rendered: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ffmpeg_common.shutil, "which", lambda name: "/bin/ffprobe" if name == "ffprobe" else None
    )
    assert _verify(run_cli, rendered, "--no-loudness")[0] == 0


def test_job_resolves_its_file_and_reads_the_list_once(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    (tmp_path / "job.mp4").write_bytes(b"x")
    _project(fake_world).render_jobs[0].update(
        {"TargetDir": str(tmp_path), "OutputFilename": "job.mp4", "MarkIn": 0, "MarkOut": 479}
    )
    code, out, err = run_cli(["render", "verify", "--job", "job-1", "--max-mb", "8", "--json"])
    assert (code, err) == (0, "")
    data = json.loads(out)
    assert data["file"].endswith("job.mp4") and "note" not in data
    assert len(fake_world.calls_to("Project.GetRenderJobList")) == 1
    assert fake_world.write_calls() == []
    duration = _checks(out)["duration"]  # 480 frames at 24 fps = 20 s
    assert duration["ok"] is True and duration["limit"].startswith("20 s")
    assert any("render status job-1" in line for line in data["help"])


def test_job_duration_mismatch_fails(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    (tmp_path / "job.mp4").write_bytes(b"x")
    _project(fake_world).render_jobs[0].update(
        {"TargetDir": str(tmp_path), "OutputFilename": "job.mp4", "MarkIn": 100, "MarkOut": 339}
    )
    _, out, _ = run_cli(["render", "verify", "--job", "job-1", "--no-loudness", "--json"])
    assert _checks(out)["duration"]["ok"] is False  # 240 frames = 10 s, file is 20 s


def test_job_name_without_extension_finds_the_one_file(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    (tmp_path / "edit.mov").write_bytes(b"x")
    _project(fake_world).render_jobs[0].update(
        {"TargetDir": str(tmp_path), "OutputFilename": "edit"}
    )
    code, out, _ = run_cli(["render", "verify", "--job", "job-1", "--no-loudness", "--json"])
    assert code == 0 and json.loads(out)["file"].endswith("edit.mov")


def test_job_not_complete_adds_a_note(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    (tmp_path / "job.mp4").write_bytes(b"x")
    project = _project(fake_world)
    project.render_jobs[0].update({"TargetDir": str(tmp_path), "OutputFilename": "job.mp4"})
    project.render_status["job-1"] = {"JobStatus": "Rendering", "CompletionPercentage": 10}
    _, out, _ = run_cli(["render", "verify", "--job", "job-1", "--no-loudness", "--json"])
    assert "Rendering, not Complete" in json.loads(out)["note"]


def test_unknown_job_is_not_found_and_lists_ids(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, out, _ = run_cli(["render", "verify", "--job", "job-9", "--json"])
    data = json.loads(out)
    assert code == 1 and data["code"] == "NOT_FOUND"
    assert "job-1" in data["help"][0] and tools.calls == []


def test_job_whose_file_is_missing_is_not_found(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    job = _project(fake_world).render_jobs[0]
    job.update({"TargetDir": str(tmp_path), "OutputFilename": "gone.mp4"})
    code, out, _ = run_cli(["render", "verify", "--job", "job-1", "--json"])
    assert code == 1 and json.loads(out)["code"] == "NOT_FOUND"


def test_missing_file_is_not_found(run_cli: Run, tools: Tools, tmp_path: Path) -> None:
    code, out = _verify(run_cli, tmp_path / "nope.mp4")
    assert code == 1 and json.loads(out)["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "extra",
    [
        ["--max-mb", "0"],
        ["--max-mb", "-3"],
        ["--lufs-tolerance", "-1"],
        ["--true-peak", "3"],
        ["--no-loudness", "--lufs", "-14"],
        ["--no-loudness", "--true-peak", "-1"],
        ["--job", "job-1"],
    ],
)
def test_bad_arguments_exit_two_without_a_resolve_call(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, rendered: Path, extra: list[str]
) -> None:
    code, out = _verify(run_cli, rendered, *extra)
    assert code == 2 and json.loads(out)["code"] == "VALIDATION_ERROR"
    assert fake_world.calls == [] and tools.calls == []


def test_no_file_and_no_job_exits_two(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, _ = run_cli(["render", "verify", "--json"])
    assert code == 2 and json.loads(out)["code"] == "VALIDATION_ERROR"
    assert fake_world.calls == []


def test_checks_for_adds_only_the_given_caps() -> None:
    measured: dict[str, object] = {"bytes": 5_000_000, "mb": 5.0, "lufs": -14.4, "true_peak": -0.5}
    assert checks_for(measured, {}) == []
    caps = {"max_mb": 5.0, "lufs": -14.0, "lufs_tolerance": 0.5, "true_peak": -1.0}
    rows = {r["check"]: r["ok"] for r in checks_for(measured, caps)}
    assert rows == {"size": True, "loudness": True, "true_peak": False}
    tight = checks_for(measured, {"lufs": -14.0, "lufs_tolerance": 0.3})
    assert tight[0]["ok"] is False


def test_help_lists_examples_and_decimal_mb(run_cli: Run) -> None:
    code, out, _ = run_cli(["render", "verify", "--help"])
    assert code == 0 and "examples[" in out and "1,000,000 bytes" in out


def test_measure_job_returns_data_with_a_duration_check_and_help(
    fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    (tmp_path / "job.mp4").write_bytes(b"x")
    _project(fake_world).render_jobs[0].update(
        {"TargetDir": str(tmp_path), "OutputFilename": "job.mp4", "MarkIn": 0, "MarkOut": 479}
    )
    resolve = fake_world.resolve
    session = Session(cast(Any, resolve), (21, 1, 1), "DaVinci Resolve Studio", True)
    data, help_lines = measure_job(session, "job-1", {"max_mb": 6.0}, loud=False)
    rows = {str(c["check"]): c for c in cast(list[dict[str, object]], data["checks"])}
    assert set(rows) == {"size", "duration"} and rows["duration"]["ok"] is True
    assert data["ok"] is False and str(data["file"]).endswith("job.mp4") and "lufs" not in data
    assert any("render deliver --max-mb 6" in line for line in help_lines)
    assert len(tools.calls) == 1


def test_failing_loudness_help_names_render_master_before_the_gain_line(
    run_cli: Run, tools: Tools, rendered: Path
) -> None:
    _, out = _verify(run_cli, rendered, "--lufs", "-14")
    lines = json.loads(out)["help"]
    master = next(i for i, line in enumerate(lines) if "render master" in line)
    gain = next(i for i, line in enumerate(lines) if "audio gain" in line)
    assert master < gain and "--lufs -14" in lines[master] and "video copied" in lines[master]


def test_help_names_render_sheet_before_the_job_line(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    (tmp_path / "job.mp4").write_bytes(b"x")
    _project(fake_world).render_jobs[0].update(
        {"TargetDir": str(tmp_path), "OutputFilename": "job.mp4"}
    )
    code, out, _ = run_cli(["render", "verify", "--job", "job-1", "--no-loudness", "--json"])
    lines = json.loads(out)["help"]
    sheet = next(i for i, line in enumerate(lines) if "render sheet" in line)
    status = next(i for i, line in enumerate(lines) if "render status" in line)
    assert code == 0 and sheet < status
    assert "--every 10" in lines[sheet] and "frame every 10 s" in lines[sheet]
