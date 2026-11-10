"""`ffmpeg_vfr` with `ffmpeg_common.run` stubbed (U3); nothing spawns a process."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from davinci_axi.commands import ffmpeg_common, ffmpeg_vfr
from davinci_axi.errors import AxiError


@pytest.fixture(autouse=True)
def _ffprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FFPROBE", "/x/ffprobe")


def _stub(
    monkeypatch: pytest.MonkeyPatch, stdout: str = "", returncode: int = 0, stderr: str = ""
) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr(ffmpeg_common, "run", fake)
    return calls


def _lines(pts: list[float]) -> str:
    return "".join(f"{p:.6f}\n" for p in pts)


def test_scan_command_text() -> None:
    cmd = ffmpeg_vfr.scan_command(Path("/m/a.mov"), 10.0)
    assert cmd[0] == "/x/ffprobe"
    assert cmd[3:5] == ["-select_streams", "v:0"]
    assert "%+10" in cmd
    assert "packet=pts_time" in cmd
    assert "csv=p=0" in cmd
    assert cmd[-1] == "/m/a.mov"


def test_parse_pts_sorts_and_skips_na() -> None:
    assert ffmpeg_vfr.parse_pts("0.066\nN/A\n\n0.000\n0.033,\n") == [0.0, 0.033, 0.066]


def test_uniform_is_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, _lines([i / 30 for i in range(300)]))
    scan = ffmpeg_vfr.vfr_scan(Path("a.mov"))
    assert len(calls) == 1
    assert scan.packets == 300
    assert scan.spread_pct == 0.0
    assert scan.variable is False
    assert scan.median_s == pytest.approx(1 / 30, abs=1e-6)


def test_alternating_is_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    pts, t = [], 0.0
    for i in range(40):
        pts.append(t)
        t += 0.033 if i % 2 == 0 else 0.050
    _stub(monkeypatch, _lines(pts))
    scan = ffmpeg_vfr.vfr_scan(Path("a.mov"), seconds=5.0)
    assert scan.variable is True
    assert scan.spread_pct == pytest.approx(51.5, abs=1.0)
    assert scan.min_s == pytest.approx(0.033, abs=1e-6)
    assert scan.max_s == pytest.approx(0.050, abs=1e-6)


def test_few_packets_undecided(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, _lines([0.0, 0.033, 0.066, 0.1, 0.133]))
    scan = ffmpeg_vfr.vfr_scan(Path("a.mov"))
    assert scan.packets == 5
    assert scan.variable is None
    assert scan.spread_pct is not None


def test_audio_only_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, "")
    scan = ffmpeg_vfr.vfr_scan(Path("a.wav"))
    assert scan == ffmpeg_vfr.VfrScan(0, None, None, None, None, None)


def test_non_zero_exit_raises_with_help(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, returncode=1, stderr="\nno such file\n")
    with pytest.raises(AxiError) as err:
        ffmpeg_vfr.vfr_scan(Path("/m/a.mov"))
    assert err.value.code == "API_FAILED"
    assert "a.mov" in str(err.value) and "no such file" in str(err.value)
    assert err.value.help
