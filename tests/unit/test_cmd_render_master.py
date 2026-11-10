"""`render master` with ffprobe/ffmpeg stubbed through `ffmpeg_common.run` (round 3, U1)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import ffmpeg_common, render
from davinci_axi.commands.render_master import (
    MASTER_SPEC,
    Caps,
    Pass1,
    default_out,
    parse_loudnorm,
    pass1_command,
    pass2_command,
)
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeWorld, ext_render
from davinci_axi.fake.core import FakeProject
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

SPEC: CommandSpec = replace(render.SPEC, subcommands={**render.SUBCOMMANDS, "master": MASTER_SPEC})
PASS1 = """[Parsed_loudnorm_0 @ 0x7f]
{
\t"input_i" : "-16.30",
\t"input_tp" : "-1.30",
\t"input_lra" : "6.00",
\t"input_thresh" : "-26.80",
\t"output_i" : "-14.60",
\t"output_tp" : "-1.50",
\t"output_lra" : "5.20",
\t"output_thresh" : "-24.90",
\t"normalization_type" : "linear",
\t"target_offset" : "0.30"
}
"""
EBUR = (
    "Summary:\n\n  Integrated loudness:\n    I:         {i} LUFS\n"
    "\n  True peak:\n    Peak:  {p} dBFS\n"
)
VIDEO = {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920}
AUDIO = {"codec_type": "audio", "codec_name": "aac", "channels": 2, "bit_rate": "256000"}


class Tools:
    """A stand-in for `ffmpeg_common.run` keyed on the tool name and the filter chain."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.pass1 = PASS1
        self.pass1_code = 0
        self.pass2_code = 0
        self.measured = ("-14.1", "-1.6")
        self.streams: list[dict[str, object]] = [VIDEO, AUDIO]

    def names(self) -> list[str]:
        return [Path(c[0]).name for c in self.calls]

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        text = " ".join(command)
        if Path(command[0]).name == "ffprobe":
            body = {
                "format": {"size": "7195566", "duration": "20.0", "bit_rate": "3075000"},
                "streams": self.streams,
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(body), "")
        if "print_format=json" in text:
            return subprocess.CompletedProcess(command, self.pass1_code, "", self.pass1)
        if "measured_I" in text:
            Path(command[-1]).write_bytes(b"half")  # written even when pass 2 then fails
            return subprocess.CompletedProcess(command, self.pass2_code, "", "Error: disk")
        lufs, peak = self.measured
        return subprocess.CompletedProcess(command, 0, "", EBUR.format(i=lufs, p=peak))


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
def source(tmp_path: Path) -> Path:
    path = tmp_path / "in.mp4"
    path.write_bytes(b"x")
    return path


def _master(run_cli: Run, *args: str) -> tuple[int, dict[str, object]]:
    code, out, _ = run_cli(["render", "master", *args, "--json"])
    return code, json.loads(out)


def _help(data: dict[str, object]) -> str:
    lines = data.get("help") or []
    assert isinstance(lines, list)
    return "\n".join(str(line) for line in lines)


def test_three_passes_in_order_and_ok(run_cli: Run, tools: Tools, source: Path) -> None:
    code, data = _master(run_cli, str(source), "--lufs", "-14")
    assert code == 0 and data["ok"] is True
    out = source.with_name("in-master.mp4")
    first, second = tools.calls[1], tools.calls[2]
    assert "loudnorm=I=-14:TP=-1.8:LRA=11:print_format=json" in first
    assert "-vn" in first and first[-3:] == ["-f", "null", "-"]
    assert second[second.index("-c:v") + 1] == "copy"
    chain = second[second.index("-af") + 1]
    assert chain.startswith("loudnorm=I=-14:TP=-1.8:LRA=11:measured_I=")
    assert "measured_I=-16.3" in chain and "offset=0.3" in chain and "linear=true" in chain
    assert "measured_TP=-1.3" in chain and "measured_thresh=-26.8" in chain
    assert second[second.index("-ar") + 1] == "48000"
    assert second[second.index("-b:a") + 1] == "256k" and "-movflags" in second
    assert second[-1] == str(out) and out.exists()
    # probe of the input, pass 1, pass 2, then ffprobe and ebur128 on the output
    assert tools.names() == ["ffprobe", "ffmpeg", "ffmpeg", "ffprobe", "ffmpeg"]
    assert tools.calls[3][-1] == str(out) and str(out) in tools.calls[4]
    assert "ebur128" in " ".join(tools.calls[4])


def test_result_shape(run_cli: Run, tools: Tools, source: Path) -> None:
    _, data = _master(run_cli, str(source), "--lufs", "-14")
    assert data["before"] == {"lufs": -16.3, "true_peak": -1.3, "lra": 6.0}
    assert data["after"] == {"lufs": -14.1, "true_peak": -1.6}
    assert (data["video"], data["audio"]) == ("copied", "aac 256 kbps 48 kHz (source 256 kbps)")
    assert data["mode"] == "linear" and data["audio_kbps_from"] == "source"
    assert data["loudnorm"] == {"I": -14.0, "TP": -1.8, "LRA": 11.0}
    assert (data["bytes"], data["mb"], data["duration_s"]) == (7195566, 7.2, 20.0)
    assert str(data["out"]).endswith("in-master.mp4")
    checks = data["checks"]
    assert isinstance(checks, list)
    assert {row["check"]: row["ok"] for row in checks} == {"loudness": True, "true_peak": True}
    text = _help(data)
    assert "render verify" in text and "--max-mb <mb>" in text and "--lra" not in text


def test_remeasure_off_target_fails_with_exit_one(run_cli: Run, tools: Tools, source: Path) -> None:
    tools.measured = ("-16.0", "-1.6")
    code, data = _master(run_cli, str(source), "--lufs", "-14")
    assert code == 1 and data["ok"] is False
    assert "--true-peak -2" in _help(data) and "lower the ceiling" in _help(data)


def test_true_peak_miss_fails(run_cli: Run, tools: Tools, source: Path) -> None:
    tools.measured = ("-14.0", "-0.4")
    code, data = _master(run_cli, str(source), "--lufs", "-14")
    assert code == 1 and data["ok"] is False and "--true-peak -2" in _help(data)


def test_dynamic_mode_adds_the_lra_hint(run_cli: Run, tools: Tools, source: Path) -> None:
    tools.pass1 = PASS1.replace('"linear"', '"dynamic"')
    code, data = _master(run_cli, str(source), "--lufs", "-14")
    assert code == 0 and data["mode"] == "dynamic"
    assert "--lufs -14 --lra 15" in _help(data)


def test_flags_reach_the_commands(run_cli: Run, tools: Tools, source: Path, tmp_path: Path) -> None:
    out = tmp_path / "x.wav"
    args = ["--lufs", "-16", "--true-peak", "-1", "--lra", "9", "--audio-kbps", "128"]
    _master(run_cli, str(source), *args, "--out", str(out))
    assert "loudnorm=I=-16:TP=-1.3:LRA=9:print_format=json" in tools.calls[1]
    second = tools.calls[2]
    assert second[second.index("-b:a") + 1] == "128k" and "-movflags" not in second
    assert second[-1] == str(out)


def test_true_peak_margin_applies_to_both_passes(run_cli: Run, tools: Tools, source: Path) -> None:
    _master(run_cli, str(source), "--lufs", "-14", "--true-peak", "-2")
    assert "TP=-2.3:" in tools.calls[1][tools.calls[1].index("-af") + 1]
    assert "TP=-2.3:" in tools.calls[2][tools.calls[2].index("-af") + 1]


@pytest.mark.parametrize(
    ("bit_rate", "kbps", "text", "origin"),
    [
        ("320000", "320k", "(source 320 kbps)", "source"),
        ("1536000", "320k", "(source 1536 kbps)", "source"),
        ("64000", "128k", "(source 64 kbps)", "source"),
        (None, "256k", "(source bit rate unknown)", "default"),
    ],
)
def test_default_kbps_follows_the_source(
    run_cli: Run,
    tools: Tools,
    source: Path,
    bit_rate: str | None,
    kbps: str,
    text: str,
    origin: str,
) -> None:
    audio = {k: v for k, v in AUDIO.items() if k != "bit_rate"}
    tools.streams = [VIDEO, {**audio, "bit_rate": bit_rate} if bit_rate else audio]
    _, data = _master(run_cli, str(source), "--lufs", "-14")
    second = tools.calls[2]
    assert second[second.index("-b:a") + 1] == kbps
    assert str(data["audio"]).endswith(text) and data["audio_kbps_from"] == origin


def test_audio_kbps_flag_wins_over_the_source(run_cli: Run, tools: Tools, source: Path) -> None:
    tools.streams = [VIDEO, {**AUDIO, "bit_rate": "320000"}]
    _, data = _master(run_cli, str(source), "--lufs", "-14", "--audio-kbps", "192")
    assert tools.calls[2][tools.calls[2].index("-b:a") + 1] == "192k"
    assert "aac 192 kbps" in str(data["audio"]) and data["audio_kbps_from"] == "flag"


def test_existing_output_is_refused_without_overwrite(
    run_cli: Run, tools: Tools, source: Path
) -> None:
    existing = source.with_name("in-master.mp4")
    existing.write_bytes(b"old")
    code, data = _master(run_cli, str(source), "--lufs", "-14")
    assert code == 1 and "--overwrite" in _help(data)
    assert existing.read_bytes() == b"old" and tools.calls == []
    code, data = _master(run_cli, str(source), "--lufs", "-14", "--overwrite")
    assert code == 0 and data["ok"] is True


def test_out_equal_to_input_exits_two(run_cli: Run, tools: Tools, source: Path) -> None:
    code, data = _master(run_cli, str(source), "--lufs", "-14", "--out", str(source))
    assert code == 2 and data["code"] == "VALIDATION_ERROR" and tools.calls == []
    assert source.read_bytes() == b"x"


@pytest.mark.parametrize(
    "extra",
    [
        ["--lufs", "3"],
        ["--lufs", "-70"],
        ["--lufs", "-14", "--true-peak", "1"],
        ["--lufs", "-14", "--lra", "0"],
        ["--lufs", "-14", "--lra", "60"],
        ["--lufs", "-14", "--audio-kbps", "16"],
        ["--lufs", "-14", "--audio-kbps", "600"],
        ["--lufs", "-14", "--lufs-tolerance", "-1"],
        ["--lufs", "-14", "--job", "job-1"],
        [],
    ],
)
def test_bad_arguments_exit_two_without_a_subprocess(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, source: Path, extra: list[str]
) -> None:
    code, out, _ = run_cli(["render", "master", str(source), *extra, "--json"])
    assert code == 2 and json.loads(out)["code"] == "VALIDATION_ERROR"
    assert tools.calls == [] and fake_world.calls == []


def test_no_file_and_no_job_exits_two(run_cli: Run, tools: Tools) -> None:
    code, data = _master(run_cli, "--lufs", "-14")
    assert code == 2 and "name a file" in str(data["error"])
    assert tools.calls == []


def test_missing_ffmpeg_exits_one_without_a_call(
    run_cli: Run, tools: Tools, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda name: None)
    code, data = _master(run_cli, str(source), "--lufs", "-14")
    assert code == 1 and data["code"] == "NOT_AVAILABLE" and tools.calls == []


def test_missing_file_is_not_found(run_cli: Run, tools: Tools, tmp_path: Path) -> None:
    code, data = _master(run_cli, str(tmp_path / "nope.mp4"), "--lufs", "-14")
    assert code == 1 and data["code"] == "NOT_FOUND" and tools.calls == []


def test_file_without_audio_is_a_validation_error(run_cli: Run, tools: Tools, source: Path) -> None:
    tools.streams = [VIDEO]
    code, data = _master(run_cli, str(source), "--lufs", "-14")
    assert code == 2 and "no audio stream to master" in str(data["error"])
    assert "render verify" in _help(data) and tools.names() == ["ffprobe"]


def test_pass_one_failure_names_the_pass_and_the_command(
    run_cli: Run, tools: Tools, source: Path
) -> None:
    tools.pass1_code = 1
    code, data = _master(run_cli, str(source), "--lufs", "-14")
    assert code == 1 and data["code"] == "API_FAILED"
    assert "pass 1 (measure)" in str(data["error"]) and "in.mp4" in str(data["error"])
    assert "Run the command by hand: " in _help(data) and "loudnorm" in _help(data)
    assert not source.with_name("in-master.mp4").exists()


def test_pass_two_failure_removes_the_half_written_file(
    run_cli: Run, tools: Tools, source: Path
) -> None:
    tools.pass2_code = 1
    code, data = _master(run_cli, str(source), "--lufs", "-14")
    assert code == 1 and "pass 2 (apply)" in str(data["error"])
    assert "Run the command by hand" in _help(data)
    assert not source.with_name("in-master.mp4").exists() and source.exists()


def test_silent_file_is_api_failed(run_cli: Run, tools: Tools, source: Path) -> None:
    tools.pass1 = PASS1.replace('"-16.30"', '"-inf"')
    code, data = _master(run_cli, str(source), "--lufs", "-14")
    assert code == 1 and data["code"] == "API_FAILED" and "silent" in str(data["error"])
    assert len(tools.calls) == 2  # probe + pass 1 only


def test_unreadable_pass_one_output_is_api_failed(run_cli: Run, tools: Tools, source: Path) -> None:
    tools.pass1 = "no json here"
    code, data = _master(run_cli, str(source), "--lufs", "-14")
    assert code == 1 and data["code"] == "API_FAILED" and _help(data)


def test_job_path_uses_the_job_target(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    (tmp_path / "job.mp4").write_bytes(b"x")
    project = fake_world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    project.render_jobs[0].update({"TargetDir": str(tmp_path), "OutputFilename": "job.mp4"})
    code, data = _master(run_cli, "--job", "job-1", "--lufs", "-14")
    assert code == 0 and str(data["file"]).endswith("job.mp4")
    assert str(data["out"]).endswith("job-master.mp4")
    assert len(fake_world.calls_to("Project.GetRenderJobList")) == 1
    assert fake_world.write_calls() == []


def test_unknown_job_is_not_found(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, data = _master(run_cli, "--job", "job-9", "--lufs", "-14")
    assert code == 1 and data["code"] == "NOT_FOUND" and tools.calls == []


def test_parse_loudnorm_takes_the_last_block() -> None:
    noisy = '{"decoy": 1}\nsome log {not json}\n' + PASS1
    assert parse_loudnorm(noisy) == Pass1(-16.3, -1.3, 6.0, -26.8, 0.3, "linear")
    with pytest.raises(AxiError, match="could not read"):
        parse_loudnorm("{ broken")


def test_commands_and_default_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda name: f"/bin/{name}")
    path = tmp_path / "a.mkv"
    assert default_out(path) == tmp_path / "a-master.mkv"
    caps = Caps(-14.0)
    p1 = Pass1(-20.0, -3.0, 5.0, -30.0, 1.5)
    assert pass1_command(path, caps)[-3:] == ["-f", "null", "-"]
    assert "-movflags" not in pass2_command(path, tmp_path / "o.mkv", caps, p1, 192)
    assert "+faststart" in pass2_command(path, tmp_path / "o.mov", caps, p1, 192)
    assert "+faststart" in pass2_command(path, tmp_path / "o.m4a", caps, p1, 192)


def test_help_text_lists_examples(run_cli: Run) -> None:
    code, out, _ = run_cli(["render", "master", "--help"])
    assert code == 0 and "examples[" in out and "48000" in out and "--lufs" in out
