"""`render sheet` with ffprobe/ffmpeg stubbed through `ffmpeg_common.run` (round 4, U1)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import ffmpeg_common, render
from davinci_axi.commands.render_sheet import SHEET_SPEC, default_out, grid_for
from davinci_axi.fake import FakeWorld, ext_render
from davinci_axi.fake.core import FakeProject
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

SPEC: CommandSpec = replace(render.SPEC, subcommands={**render.SUBCOMMANDS, "sheet": SHEET_SPEC})
VIDEO = {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920}
AUDIO = {"codec_type": "audio", "codec_name": "aac", "channels": 2}


class Tools:
    """A stand-in for `ffmpeg_common.run`: JSON for ffprobe, a written JPEG for the tile call."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.duration: str | None = "209.0"
        self.streams: list[dict[str, object]] = [VIDEO, AUDIO]
        self.code = 0
        self.write = True
        self.stderr = ""

    def names(self) -> list[str]:
        return [Path(c[0]).name for c in self.calls]

    def tile_calls(self) -> list[list[str]]:
        return [c for c in self.calls if "tile=" in " ".join(c)]

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if Path(command[0]).name == "ffprobe":
            fmt: dict[str, str] = {"size": "7195566", "bit_rate": "3075000"}
            if self.duration is not None:
                fmt["duration"] = self.duration
            body = {"format": fmt, "streams": self.streams}
            return subprocess.CompletedProcess(command, 0, json.dumps(body), "")
        if self.write:
            Path(command[-1]).write_bytes(b"jpeg-bytes")
        return subprocess.CompletedProcess(command, self.code, "", self.stderr)


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
    path = tmp_path / "edit.mp4"
    path.write_bytes(b"x")
    return path


def _sheet(run_cli: Run, *args: str) -> tuple[int, dict[str, object]]:
    code, out, _ = run_cli(["render", "sheet", *args, "--json"])
    return code, json.loads(out)


def _filter(tools: Tools) -> str:
    (call,) = tools.tile_calls()
    return call[call.index("-vf") + 1]


def _help(data: dict[str, object]) -> str:
    lines = data.get("help") or []
    assert isinstance(lines, list)
    return "\n".join(str(line) for line in lines)


@pytest.mark.parametrize(
    ("duration", "every", "columns", "expected"),
    [
        (209, 10, 6, (21, 6, 4)),
        (209, 30, 6, (7, 6, 2)),
        (209, 60, 3, (4, 3, 2)),
        (5, 10, 6, (1, 1, 1)),
    ],
)
def test_grid_for(
    duration: float, every: float, columns: int, expected: tuple[int, int, int]
) -> None:
    assert grid_for(duration, every, columns) == expected


def test_default_out_sits_beside_the_input() -> None:
    assert default_out(Path("/a/b/edit.mp4")) == Path("/a/b/edit-sheet.jpg")


def test_default_sheet_of_a_209_second_file(run_cli: Run, tools: Tools, source: Path) -> None:
    code, data = _sheet(run_cli, str(source))
    assert code == 0
    assert _filter(tools) == "fps=1/10,scale=270:-2,tile=6x4"
    assert (data["frames"], data["columns"], data["rows"]) == (21, 6, 4)
    assert (data["every_s"], data["cell_width"], data["duration_s"]) == (10.0, 270, 209.0)
    out = source.with_name("edit-sheet.jpg")
    assert out.read_bytes() == b"jpeg-bytes"
    assert str(data["out"]).endswith("edit-sheet.jpg") and data["bytes"] == len(b"jpeg-bytes")
    assert "t = n x 10 s" in str(data["grid"]) and "note" not in data
    call = tools.tile_calls()[0]
    assert call[-1] == str(out) and call[call.index("-q:v") + 1] == "3"
    assert call[call.index("-frames:v") + 1] == "1" and "-update" in call
    assert tools.names() == ["ffprobe", "ffmpeg"]


def test_every_30_gives_seven_frames(run_cli: Run, tools: Tools, source: Path) -> None:
    _, data = _sheet(run_cli, str(source), "--every", "30")
    assert data["frames"] == 7 and "tile=6x2" in _filter(tools)


def test_three_columns_every_60(run_cli: Run, tools: Tools, source: Path) -> None:
    _, data = _sheet(run_cli, str(source), "--columns", "3", "--every", "60", "--width", "100")
    assert data["frames"] == 4 and "scale=100:-2,tile=3x2" in _filter(tools)


def test_help_names_verify_a_finer_grid_and_no_job_line(
    run_cli: Run, tools: Tools, source: Path
) -> None:
    _, data = _sheet(run_cli, str(source))
    text = _help(data)
    assert "render verify" in text and "--max-mb <mb> --lufs -14" in text
    assert "render sheet" in text and "--every 5" in text and "render status" not in text


@pytest.mark.parametrize(
    "args",
    [
        ["--every", "0"],
        ["--every", "-3"],
        ["--columns", "0"],
        ["--columns", "13"],
        ["--width", "63"],
        ["--width", "1921"],
    ],
)
def test_out_of_range_numbers_exit_2_before_any_subprocess(
    run_cli: Run, tools: Tools, source: Path, args: list[str]
) -> None:
    code, data = _sheet(run_cli, str(source), *args)
    assert code == 2 and data["code"] == "VALIDATION_ERROR" and data["help"]
    assert tools.calls == []


def test_file_xor_job(run_cli: Run, fake_world: FakeWorld, tools: Tools, source: Path) -> None:
    code, data = _sheet(run_cli, str(source), "--job", "job-1")
    assert code == 2 and "not both" in str(data["error"]) and data["help"]
    code, data = _sheet(run_cli)
    assert code == 2 and "--job" in str(data["error"]) and data["help"]
    assert tools.calls == [] and fake_world.calls == []


def test_missing_ffmpeg_fails_before_anything(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda name: None)
    code, data = _sheet(run_cli, str(source))
    assert code == 1 and data["code"] == "NOT_AVAILABLE"
    assert tools.calls == [] and fake_world.calls == []


def test_existing_out_is_refused_without_overwrite(
    run_cli: Run, tools: Tools, source: Path
) -> None:
    out = source.with_name("edit-sheet.jpg")
    out.write_bytes(b"old")
    code, data = _sheet(run_cli, str(source))
    assert code != 0 and "--overwrite" in _help(data) and tools.calls == []
    assert out.read_bytes() == b"old"
    code, data = _sheet(run_cli, str(source), "--overwrite")
    assert code == 0 and out.read_bytes() == b"jpeg-bytes"


def test_out_equal_to_the_input_exits_2(run_cli: Run, tools: Tools, source: Path) -> None:
    code, data = _sheet(run_cli, str(source), "--out", str(source), "--overwrite")
    assert code == 2 and "input" in str(data["error"]) and data["help"]
    assert tools.calls == [] and source.read_bytes() == b"x"


def test_custom_out(run_cli: Run, tools: Tools, source: Path, tmp_path: Path) -> None:
    out = tmp_path / "qa.jpg"
    code, data = _sheet(run_cli, str(source), "--out", str(out))
    assert code == 0 and out.is_file() and str(data["out"]).endswith("qa.jpg")


def test_missing_file_is_not_found(run_cli: Run, tools: Tools, tmp_path: Path) -> None:
    code, data = _sheet(run_cli, str(tmp_path / "nope.mp4"))
    assert code == 1 and data["code"] == "NOT_FOUND" and tools.calls == []


def test_audio_only_file_has_no_video_to_tile(run_cli: Run, tools: Tools, source: Path) -> None:
    tools.streams = [AUDIO]
    code, data = _sheet(run_cli, str(source))
    assert code != 0 and data["code"] == "VALIDATION_ERROR"
    assert "no video stream to tile" in str(data["error"]) and "render verify" in _help(data)
    assert tools.tile_calls() == []


def test_missing_duration_is_api_failed(run_cli: Run, tools: Tools, source: Path) -> None:
    tools.duration = None
    code, data = _sheet(run_cli, str(source))
    assert code == 1 and data["code"] == "API_FAILED" and "no duration" in str(data["error"])
    assert "ffprobe" in _help(data) and tools.tile_calls() == []


def test_ffmpeg_failure_removes_the_partial_and_shows_the_command(
    run_cli: Run, tools: Tools, source: Path
) -> None:
    tools.code, tools.stderr = 1, "Error: tile too big\nsecond line"
    code, data = _sheet(run_cli, str(source))
    assert code == 1 and data["code"] == "API_FAILED"
    assert "Error: tile too big" in str(data["error"]) and "second line" not in str(data["error"])
    assert "Run the command by hand" in _help(data) and "tile=6x4" in _help(data)
    assert not source.with_name("edit-sheet.jpg").exists()


def test_ffmpeg_that_writes_nothing_is_api_failed(run_cli: Run, tools: Tools, source: Path) -> None:
    tools.write = False
    code, data = _sheet(run_cli, str(source))
    assert code == 1 and "wrote nothing" in str(data["error"]) and data["help"]


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def test_job_resolves_its_file_and_reads_the_list_once(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    (tmp_path / "job.mp4").write_bytes(b"x")
    _project(fake_world).render_jobs[0].update(
        {"TargetDir": str(tmp_path), "OutputFilename": "job.mp4"}
    )
    code, data = _sheet(run_cli, "--job", "job-1")
    assert code == 0 and str(data["file"]).endswith("job.mp4") and "note" not in data
    assert (tmp_path / "job-sheet.jpg").is_file()
    assert len(fake_world.calls_to("Project.GetRenderJobList")) == 1
    assert fake_world.write_calls() == []
    assert "render status job-1" in _help(data)
    assert not [c for c in fake_world.calls if c[0] in ("MediaPool", "Timeline")]


def test_job_not_complete_adds_a_note(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    (tmp_path / "job.mp4").write_bytes(b"x")
    project = _project(fake_world)
    project.render_jobs[0].update({"TargetDir": str(tmp_path), "OutputFilename": "job.mp4"})
    project.render_status["job-1"] = {"JobStatus": "Rendering", "CompletionPercentage": 10}
    code, data = _sheet(run_cli, "--job", "job-1")
    assert code == 0 and "Rendering, not Complete" in str(data["note"])


def test_unknown_job_is_not_found(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, data = _sheet(run_cli, "--job", "job-9")
    assert code == 1 and data["code"] == "NOT_FOUND" and tools.calls == []


def test_the_verb_has_no_force_flag(run_cli: Run, tools: Tools, source: Path) -> None:
    assert run_cli(["render", "sheet", str(source), "--force"])[0] == 2
