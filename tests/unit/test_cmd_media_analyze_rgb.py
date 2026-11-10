"""`media analyze --rgb`: the stream colour, `rgb_matrix` and `sampled` (F53); nothing spawns."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import ffmpeg_common, media_extra
from davinci_axi.commands.media_analyze import ANALYZE_SPEC
from davinci_axi.fake import FakeWorld
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

SPEC: CommandSpec = replace(
    media_extra.SPEC,
    subcommands={**media_extra.SPEC.subcommands, "analyze": ANALYZE_SPEC},
)
AUDIO = {"codec_type": "audio", "codec_name": "aac", "channels": 2}
RGB_FRAMES = (
    "lavfi.signalstats.YAVG=112.0\nlavfi.signalstats.UAVG=98.0\nlavfi.signalstats.VAVG=79.0\n"
    "lavfi.signalstats.YAVG=112.8\nlavfi.signalstats.UAVG=99.2\nlavfi.signalstats.VAVG=81.0\n"
)
BT709_TV = (
    "scale=in_color_matrix=bt709:in_range=tv:out_color_matrix=bt709:out_range=tv,format=yuv444p"
)
SAMPLED = "2 fps (estimate: yavg, ravg, gavg, bavg)"


def _video(height: int, **colour: str) -> dict[str, object]:
    return {
        "codec_type": "video",
        "codec_name": "h264",
        "width": height * 16 // 9,
        "height": height,
        "r_frame_rate": "30/1",
        "avg_frame_rate": "30/1",
        "nb_frames": "120",
        **colour,
    }


def _probe(video: dict[str, object]) -> str:
    return json.dumps({"format": {"duration": "5.0"}, "streams": [video, AUDIO]})


class Stub:
    """A stand-in for `ffmpeg_common.run`: ffprobe JSON per clip, canned ffmpeg output."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.probes = {
            "A001_beach": _probe(_video(1080, color_space="bt709", color_range="tv")),
            "A002_city": _probe(_video(1080)),
            "A003_dusk": _probe(_video(576, color_space="smpte170m", color_range="tv")),
        }

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        stem = Path(command[command.index("-i") + 1] if "-i" in command else command[-1]).stem
        if Path(command[0]).name == "ffprobe":
            return subprocess.CompletedProcess(command, 0, self.probes[stem], "")
        if "-af" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        if "format=yuv444p" in " ".join(command):
            return subprocess.CompletedProcess(command, 0, RGB_FRAMES, "")
        return subprocess.CompletedProcess(command, 0, "lavfi.signalstats.YAVG=100\n", "")

    def rgb_vf(self, name: str) -> list[str]:
        vfs = [c[c.index("-vf") + 1] for c in self.calls if "-vf" in c and name in " ".join(c)]
        return [vf for vf in vfs if "format=yuv444p" in vf]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "media", f"{__name__}:SPEC")
    monkeypatch.delenv("FFPROBE", raising=False)
    monkeypatch.delenv("FFMPEG", raising=False)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Stub:
    fake = Stub()
    original = Path.is_file

    def is_file(self: Path) -> bool:
        return str(self).startswith("/media/davinci-axi-test/") or original(self)

    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda name: f"/opt/homebrew/bin/{name}")
    monkeypatch.setattr(ffmpeg_common, "run", fake)
    monkeypatch.setattr(Path, "is_file", is_file)
    return fake


def _rows(out: str) -> dict[str, dict[str, object]]:
    return {row["name"]: row for row in json.loads(out)["rows"]}


def test_a_tagged_stream_uses_its_own_matrix(
    run_cli: Run, fake_world: FakeWorld, stub: Stub
) -> None:
    code, out, _ = run_cli(["media", "analyze", "A001_beach", "--rgb", "--json"])
    assert code == 0
    (vf,) = stub.rgb_vf("A001_beach")
    assert vf.startswith(BT709_TV)
    assert _rows(out)["A001_beach"]["rgb_matrix"] == "bt709 tv"


def test_an_untagged_hd_stream_is_assumed_bt709(
    run_cli: Run, fake_world: FakeWorld, stub: Stub
) -> None:
    code, out, _ = run_cli(["media", "analyze", "A002_city", "--rgb", "--json"])
    assert code == 0
    assert stub.rgb_vf("A002_city")[0].startswith(BT709_TV)
    assert _rows(out)["A002_city"]["rgb_matrix"] == "bt709 tv (assumed)"


def test_an_sd_stream_maps_smpte170m_to_bt601(
    run_cli: Run, fake_world: FakeWorld, stub: Stub
) -> None:
    code, out, _ = run_cli(["media", "analyze", "A003_dusk", "--rgb", "--json"])
    assert code == 0
    assert stub.rgb_vf("A003_dusk")[0].startswith("scale=in_color_matrix=bt601:in_range=tv")
    assert _rows(out)["A003_dusk"]["rgb_matrix"] == "bt601 tv"


def test_fields_name_and_matrix_run_the_pass_once(
    run_cli: Run, fake_world: FakeWorld, stub: Stub
) -> None:
    code, out, _ = run_cli(["media", "analyze", "A001_beach", "--fields", "name,rgb_matrix"])
    assert code == 0
    assert len(stub.rgb_vf("A001_beach")) == 1
    assert "name,rgb_matrix" in out and "bt709 tv" in out and "ravg" not in out


def test_rgb_shows_the_matrix_column_by_default(
    run_cli: Run, fake_world: FakeWorld, stub: Stub
) -> None:
    code, out, _ = run_cli(["media", "analyze", "A001_beach", "--rgb"])
    assert code == 0
    assert "ravg,gavg,bavg,rgb_matrix" in out


def test_sample_adds_the_sampled_line_before_rows(
    run_cli: Run, fake_world: FakeWorld, stub: Stub
) -> None:
    code, out, _ = run_cli(["media", "analyze", "A001_beach", "--rgb", "--sample", "2", "--json"])
    assert code == 0
    assert json.loads(out)["sampled"] == SAMPLED
    assert list(json.loads(out)).index("sampled") < list(json.loads(out)).index("rows")
    assert stub.rgb_vf("A001_beach")[0].startswith(f"fps=2,{BT709_TV}")
    code, toon, _ = run_cli(["media", "analyze", "A001_beach", "--rgb", "--sample", "2"])
    assert code == 0
    assert f'sampled: "{SAMPLED}"' in toon and toon.index("sampled:") < toon.index("rows")


def test_no_sample_means_no_sampled_key(run_cli: Run, fake_world: FakeWorld, stub: Stub) -> None:
    _, out, _ = run_cli(["media", "analyze", "A001_beach", "--rgb", "--json"])
    assert "sampled" not in json.loads(out)


def test_sample_with_quick_means_no_sampled_key(
    run_cli: Run, fake_world: FakeWorld, stub: Stub
) -> None:
    _, out, _ = run_cli(["media", "analyze", "A001_beach", "--sample", "2", "--quick", "--json"])
    assert "sampled" not in json.loads(out)


def test_out_file_rows_carry_the_matrix(
    run_cli: Run, fake_world: FakeWorld, stub: Stub, tmp_path: Path
) -> None:
    target = tmp_path / "rgb.json"
    args = ["media", "analyze", "A001_beach,A003_dusk", "--rgb", "--out", str(target)]
    code, _, _ = run_cli(args)
    assert code == 0
    rows = {r["name"]: r for r in json.loads(target.read_text())}
    assert rows["A001_beach"]["rgb_matrix"] == "bt709 tv"
    assert rows["A003_dusk"]["rgb_matrix"] == "bt601 tv"


def test_help_names_the_timing_and_the_assumption(run_cli: Run) -> None:
    code, out, _ = run_cli(["media", "analyze", "--help"])
    assert code == 0
    assert "about 6 s" in out and "assumed" in out and "rgb_matrix" in out
