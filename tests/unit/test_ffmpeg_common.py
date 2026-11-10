"""`ffmpeg_common`: parsers and tool lookup; no test spawns ffmpeg or ffprobe (U1)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from davinci_axi.commands import ffmpeg_common
from davinci_axi.commands.ffmpeg_common import (
    Colour,
    ToolMissing,
    colour_of,
    fps_of,
    parse_ebur128,
    parse_silences,
    parse_yavg,
)
from davinci_axi.errors import AxiError

EBUR128 = """\
[Parsed_ebur128_0 @ 0x1] t: 0.1  TARGET:-23 LUFS  M: -70.0 S: -70.0  I: -70.0 LUFS  LRA: 0.0 LU
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -23.1 LUFS
    Threshold: -33.1 LUFS

  Loudness range:
    LRA:         6.2 LU

  True peak:
    Peak:       -1.3 dBFS
"""
SILENCE = """\
  Duration: 00:00:05.00, start: 0.000000, bitrate: 128 kb/s
[silencedetect @ 0x2] silence_start: 1.5
[silencedetect @ 0x2] silence_end: 3.2 | silence_duration: 1.7
"""


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FFPROBE", raising=False)
    monkeypatch.delenv("FFMPEG", raising=False)


def _tools_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda name: f"/bin/{name}")


def test_fps_of() -> None:
    assert fps_of("30000/1001") == 29.97
    assert fps_of("25") == 25.0
    assert fps_of("0/0") is None
    assert fps_of("N/A") is None


def test_parse_ebur128_reads_the_summary_not_the_frame_lines() -> None:
    assert parse_ebur128(EBUR128) == (-23.1, -1.3)
    assert parse_ebur128("nothing here") == (None, None)
    silent = "Summary:\n  Integrated loudness:\n    I:         -inf LUFS\n"
    assert parse_ebur128(silent) == (None, None)


def test_parse_ebur128_tolerates_dbtp() -> None:
    text = (
        "Summary:\n  Integrated loudness:\n    I: -16.0 LUFS\n  True peak:\n    Peak: -0.5 dBTP\n"
    )
    assert parse_ebur128(text) == (-16.0, -0.5)


def test_parse_silences_pairs_and_unterminated() -> None:
    assert parse_silences(SILENCE) == [(1.5, 3.2)]
    open_ended = "[silencedetect @ 0x2] silence_start: 4.0\n"
    assert parse_silences(open_ended) == []  # no Duration line: dropped
    assert parse_silences("  Duration: 00:00:05.00,\n" + open_ended) == [(4.0, 5.0)]


def test_parse_yavg_averages() -> None:
    text = "frame:0\nlavfi.signalstats.YAVG=100.0\nframe:1\nlavfi.signalstats.YAVG=103.0\n"
    assert parse_yavg(text) == 101.5
    assert parse_yavg("") is None


def test_tool_path_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FFPROBE", "/custom/ffprobe")
    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda _: "/usr/bin/ffprobe")
    assert ffmpeg_common.tool_path("ffprobe") == "/custom/ffprobe"


def test_tool_path_missing_is_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda _: None)
    with pytest.raises(ToolMissing) as caught:
        ffmpeg_common.require_tools("ffprobe")
    err = caught.value
    assert (err.code, err.exit_code) == ("NOT_AVAILABLE", 1)
    assert any("brew install ffmpeg" in line for line in err.help)


def test_probe_reads_first_video_and_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "format": {"duration": "5.0", "size": "1000", "bit_rate": "1600000"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30000/1001",
                "avg_frame_rate": "30000/1001",
                "nb_frames": "150",
                "bit_rate": "1500000",
                "color_space": "bt709",
                "color_range": "tv",
            },
            {"codec_type": "audio", "codec_name": "aac", "channels": 2},
        ],
    }
    seen: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    _tools_on_path(monkeypatch)
    monkeypatch.setattr(ffmpeg_common, "run", fake_run)
    got = ffmpeg_common.probe(Path("/m/a.mov"))
    assert (got.duration_s, got.size, got.bit_rate_kbps) == (5.0, 1000, 1600)
    assert got.video == {
        "codec": "h264",
        "width": 1920,
        "height": 1080,
        "fps": 29.97,
        "fps_avg": 29.97,
        "frames": 150,
        "kbps": 1500,
        "color_space": "bt709",
        "color_range": "tv",
    }
    assert got.audio == {"codec": "aac", "channels": 2, "kbps": None}
    assert seen[0][0] == "/bin/ffprobe" and seen[0][-1] == "/m/a.mov"


def test_nonzero_exit_is_api_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "\nInvalid data found\nmore\n")

    _tools_on_path(monkeypatch)
    monkeypatch.setattr(ffmpeg_common, "run", fake_run)
    with pytest.raises(AxiError) as caught:
        ffmpeg_common.probe(Path("/m/a.mov"))
    assert caught.value.code == "API_FAILED"
    assert "a.mov" in caught.value.message and "Invalid data found" in caught.value.message


def test_loudness_and_luma_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        if "-af" in command:
            return subprocess.CompletedProcess(command, 0, "", EBUR128 + SILENCE)
        return subprocess.CompletedProcess(command, 0, "lavfi.signalstats.YAVG=90\n", "")

    _tools_on_path(monkeypatch)
    monkeypatch.setattr(ffmpeg_common, "run", fake_run)
    heard = ffmpeg_common.loudness(Path("/m/a.mov"), silence_db=-35.0, silence_s=0.7)
    assert (heard.lufs, heard.true_peak, heard.silences) == (-23.1, -1.3, [(1.5, 3.2)])
    assert "ebur128=peak=true,silencedetect=noise=-35dB:d=0.7" in seen[0]
    quiet = ffmpeg_common.loudness(Path("/m/a.mov"), silence_db=None, silence_s=0.7)
    assert quiet.silences == [] and "ebur128=peak=true" in seen[1]
    assert ffmpeg_common.luma_mean(Path("/m/a.mov")) == 90.0
    assert not any("fps=" in part for part in seen[2])
    assert any(part.startswith("signalstats,metadata") for part in seen[2])
    assert ffmpeg_common.luma_mean(Path("/m/a.mov"), sample_fps=1) == 90.0
    assert any(part.startswith("fps=1,signalstats") for part in seen[3])


def test_loudness_window_goes_before_the_input(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, "", EBUR128)

    _tools_on_path(monkeypatch)
    monkeypatch.setattr(ffmpeg_common, "run", fake_run)
    path = Path("/m/a.mov")
    ffmpeg_common.loudness(path, silence_db=None, silence_s=0.7)
    plain = seen[-1]
    assert "-ss" not in plain and "-t" not in plain
    ffmpeg_common.loudness(path, silence_db=None, silence_s=0.7, start_s=1.0, duration_s=3.6)
    windowed = seen[-1]
    at = windowed.index("-ss")
    assert windowed[at : at + 4] == ["-ss", "1.000", "-t", "3.600"] and at < windowed.index("-i")
    assert [p for p in windowed if p not in ("-ss", "1.000", "-t", "3.600")] == plain
    ffmpeg_common.loudness(path, silence_db=None, silence_s=0.7, start_s=1.0, duration_s=0.0)
    assert seen[-1] == plain


BT709_TV_VF = (
    "scale=in_color_matrix=bt709:in_range=tv:out_color_matrix=bt709:out_range=tv,"
    "format=yuv444p,signalstats,metadata=print:file=-"
)


def test_rgb_means_command_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    out = {
        "text": "lavfi.signalstats.YAVG=125.5\nlavfi.signalstats.UAVG=128\n"
        "lavfi.signalstats.VAVG=128\nlavfi.signalstats.YAVG=16\n"
        "lavfi.signalstats.UAVG=128\nlavfi.signalstats.VAVG=128\n"
    }

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, out["text"], "")

    _tools_on_path(monkeypatch)
    monkeypatch.setattr(ffmpeg_common, "run", fake_run)
    assert ffmpeg_common.rgb_means(Path("/m/a.mov")) == (63.75, 63.75, 63.75)
    assert seen[0][seen[0].index("-vf") + 1] == BT709_TV_VF
    ffmpeg_common.rgb_means(Path("/m/a.mov"), sample_fps=2)
    assert seen[1][seen[1].index("-vf") + 1].startswith("fps=2,scale=in_color_matrix=bt709")
    out["text"] = ""
    assert ffmpeg_common.rgb_means(Path("/m/a.mov")) == (None, None, None)
    assert ffmpeg_common.parse_plane_avgs("") == {"Y": None, "U": None, "V": None}


def test_probe_without_colour_tags_gives_none(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"streams": [{"codec_type": "video", "codec_name": "h264", "height": 576}]}
    _tools_on_path(monkeypatch)
    monkeypatch.setattr(
        ffmpeg_common,
        "run",
        lambda command: subprocess.CompletedProcess(command, 0, json.dumps(payload), ""),
    )
    video = ffmpeg_common.probe(Path("/m/a.mov")).video
    assert video is not None
    assert video["color_space"] is None and video["color_range"] is None


@pytest.mark.parametrize(
    ("space", "range_", "height", "label", "assumed"),
    [
        ("bt709", "tv", 1080, "bt709 tv", False),
        ("smpte170m", "tv", 576, "bt601 tv", False),
        ("bt2020nc", "pc", 2160, "bt2020 pc", False),
        (None, None, 1080, "bt709 tv (assumed)", True),
        (None, None, 576, "bt601 tv (assumed)", True),
        ("unknown", "tv", None, "bt709 tv (assumed)", True),
        ("reserved", "tv", 480, "bt601 tv (assumed)", True),
        ("bt709", None, 1080, "bt709 tv (assumed)", True),
        ("bt709", "unknown", 1080, "bt709 tv (assumed)", True),
        ("bt709", "pc", "inf", "bt709 pc", False),
        (None, "pc", "inf", "bt709 pc (assumed)", True),
    ],
)
def test_colour_of(
    space: object, range_: object, height: object, label: str, assumed: bool
) -> None:
    got = colour_of(space, range_, height)
    assert got.label() == label and got.assumed is assumed


def test_rgb_means_with_colour_names_the_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    _tools_on_path(monkeypatch)
    monkeypatch.setattr(ffmpeg_common, "run", fake_run)
    tail = ",format=yuv444p,signalstats,metadata=print:file=-"
    ffmpeg_common.rgb_means(Path("/m/a.mov"), colour=Colour("bt601", "tv", False))
    ffmpeg_common.rgb_means(Path("/m/a.mov"), colour=Colour("bt709", "pc", False))
    ffmpeg_common.rgb_means(Path("/m/a.mov"), sample_fps=2, colour=Colour("bt709", "tv", False))
    vfs = [call[call.index("-vf") + 1] for call in seen]
    assert vfs[0] == (
        "scale=in_color_matrix=bt601:in_range=tv:out_color_matrix=bt601:out_range=tv" + tail
    )
    assert vfs[1] == (
        "scale=in_color_matrix=bt709:in_range=pc:out_color_matrix=bt709:out_range=pc" + tail
    )
    assert vfs[2] == "fps=2," + BT709_TV_VF
    for call in seen:
        assert call[call.index("-i") + 2] == "-an" and call[-3:] == ["-f", "null", "-"]


@pytest.mark.parametrize(
    ("yuv", "colour", "expected"),
    [
        ((16, 128, 128), Colour("bt709", "tv", False), (0.0, 0.0, 0.0)),
        ((235, 128, 128), Colour("bt709", "tv", False), (255.0, 255.0, 255.0)),
        ((125.5, 128, 128), Colour("bt709", "tv", False), (127.5, 127.5, 127.5)),
        ((128, 128, 128), Colour("bt709", "pc", False), (128.0, 128.0, 128.0)),
        ((128, 128, 128), Colour("bt601", "pc", False), (128.0, 128.0, 128.0)),
        ((112.4, 98.6, 80), Colour("bt709", "tv", False), (26.2, 144.1, 50.14)),
        ((112.4, 98.6, 80), Colour("bt601", "tv", False), (35.64, 162.79, 52.94)),
        ((112.4, 98.6, 80), Colour("bt709", "pc", False), (36.81, 140.38, 57.85)),
        ((81, 90, 240), Colour("bt709", "tv", False), (255.0, 24.1, 0.0)),
        ((100, 200, 100), Colour("bt2020", "tv", False), (50.81, 102.53, 252.02)),
        ((60, 128, 128), Colour("smpte240m", "tv", False), (51.23, 51.23, 51.23)),
        ((60, 128, 128), Colour("nonsense", "tv", True), (51.23, 51.23, 51.23)),
    ],
)
def test_yuv_to_rgb_hand_values(
    yuv: tuple[float, float, float], colour: Colour, expected: tuple[float, float, float]
) -> None:
    assert ffmpeg_common.yuv_to_rgb(*yuv, colour) == pytest.approx(expected, abs=0.011)
