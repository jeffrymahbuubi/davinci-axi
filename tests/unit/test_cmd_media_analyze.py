"""`media analyze` on the fake with ffprobe/ffmpeg stubbed (U1); nothing spawns a process."""

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
SUMMARY = (
    "[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n    I:         -23.1 LUFS\n"
    "\n  True peak:\n    Peak:       -1.3 dBFS\n"
)
SILENCE = (
    "  Duration: 00:00:05.00, start: 0.0\n"
    "[silencedetect @ 0x2] silence_start: 1.5\n"
    "[silencedetect @ 0x2] silence_end: 3.2 | silence_duration: 1.7\n"
)
VIDEO = {
    "codec_type": "video",
    "codec_name": "h264",
    "width": 1920,
    "height": 1080,
    "r_frame_rate": "30/1",
    "avg_frame_rate": "2920/100",  # 29.2: more than 0.5 % under 30, so variable frame rate
    "nb_frames": "120",
    "color_space": "bt709",
    "color_range": "tv",
}
ALL = (
    "name,duration_s,fps,fps_avg,vfr,resolution,lufs,silences,silences_list,yavg,note,"
    "resolve_fps,resolve_frames,error"
)
AUDIO = {"codec_type": "audio", "codec_name": "aac", "channels": 2}
# signalstats Y/U/V frames, means 112.4/98.6/80: RGB 26.2/144.1/50.14 under bt709 tv.
RGB_FRAMES = (
    "lavfi.signalstats.YAVG=112.0\nlavfi.signalstats.UAVG=98.0\nlavfi.signalstats.VAVG=79.0\n"
    "lavfi.signalstats.YAVG=112.8\nlavfi.signalstats.UAVG=99.2\nlavfi.signalstats.VAVG=81.0\n"
)


def _pts(deltas: list[float]) -> str:
    at, lines = 0.0, ["0.000000"]
    for step in deltas:
        at += step
        lines.append(f"{at:.6f}")
    return "\n".join(lines) + "\n"


UNIFORM_PTS = _pts([0.033] * 12)
UNEVEN_PTS = _pts([0.033, 0.050] * 6 + [0.033])  # 7 gaps of 0.033 and 6 of 0.050: median 0.033


def _probe(*streams: dict[str, object], duration: str = "5.0") -> str:
    return json.dumps({"format": {"duration": duration}, "streams": list(streams)})


class Tools:
    """A stand-in for `ffmpeg_common.run`: JSON for ffprobe, canned text for the ffmpeg passes."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.probes: dict[str, str] = {
            "A001_beach": _probe(VIDEO, AUDIO),
            "A002_city": _probe({**VIDEO, "nb_frames": "192"}, AUDIO),
            "A003_dusk": _probe({**VIDEO, "nb_frames": "144"}, AUDIO),
            "music_bed": _probe(AUDIO, duration="60.0"),
        }
        self.broken: set[str] = set()
        self.pts: dict[str, str] = {}

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        stem = Path(command[command.index("-i") + 1] if "-i" in command else command[-1]).stem
        if stem in self.broken:
            return subprocess.CompletedProcess(command, 1, "", f"{stem}.mov: Invalid data\n")
        if Path(command[0]).name == "ffprobe" and "packet=pts_time" in command:
            return subprocess.CompletedProcess(command, 0, self.pts.get(stem, UNIFORM_PTS), "")
        if Path(command[0]).name == "ffprobe":
            return subprocess.CompletedProcess(command, 0, self.probes[stem], "")
        if "-af" in command:
            return subprocess.CompletedProcess(command, 0, "", SUMMARY + SILENCE)
        if "format=yuv444p" in " ".join(command):
            return subprocess.CompletedProcess(command, 0, RGB_FRAMES, "")
        yavg = "lavfi.signalstats.YAVG=100\nlavfi.signalstats.YAVG=103\n"
        return subprocess.CompletedProcess(command, 0, yavg, "")

    def for_clip(self, name: str) -> list[list[str]]:
        return [c for c in self.calls if any(Path(p).stem == name for p in c)]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "media", f"{__name__}:SPEC")
    monkeypatch.delenv("FFPROBE", raising=False)
    monkeypatch.delenv("FFMPEG", raising=False)


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch) -> Tools:
    fake = Tools()
    original = Path.is_file

    def is_file(self: Path) -> bool:
        return str(self).startswith("/media/davinci-axi-test/") or original(self)

    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda name: f"/opt/homebrew/bin/{name}")
    monkeypatch.setattr(ffmpeg_common, "run", fake)
    monkeypatch.setattr(Path, "is_file", is_file)
    return fake


def _rows(out: str) -> dict[str, dict[str, object]]:
    return {row["name"]: row for row in json.loads(out)["rows"]}


def test_two_clips_measured(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, err = run_cli(
        ["media", "analyze", "A001_beach,music_bed", "--json", "--fields", ALL]
    )
    assert (code, err) == (0, "")
    data = json.loads(out)
    assert (data["clips"], data["analyzed"], data["errors"]) == (2, 2, 0)
    assert data["tools"] == {
        "ffprobe": "/opt/homebrew/bin/ffprobe",
        "ffmpeg": "/opt/homebrew/bin/ffmpeg",
    }
    beach, music = _rows(out)["A001_beach"], _rows(out)["music_bed"]
    assert (beach["fps"], beach["fps_avg"], beach["vfr"]) == (30.0, 29.2, True)
    assert (beach["lufs"], beach["silences"], beach["silences_list"]) == (-23.1, 1, [[1.5, 3.2]])
    assert (beach["yavg"], beach["resolution"], beach["duration_s"]) == (101.5, "1920x1080", 5.0)
    assert beach["note"] is None
    assert music["resolution"] is None and music["yavg"] is None
    assert music["lufs"] == -23.1
    assert all("-vf" not in call for call in tools.for_clip("music_bed"))


def test_toon_shows_the_default_columns(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, _ = run_cli(["media", "analyze", "A001_beach"])
    assert code == 0
    assert "name,duration_s,fps,vfr,resolution,lufs,silences,yavg" in out
    assert "silences_list" not in out
    assert ",error" not in out


def test_quick_makes_one_call_per_clip(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, _ = run_cli(["media", "analyze", "A001_beach,music_bed", "--quick", "--json"])
    assert code == 0
    assert len(tools.calls) == 2
    assert all(Path(call[0]).name == "ffprobe" for call in tools.calls)
    assert _rows(out)["A001_beach"]["lufs"] is None


def test_no_audio_and_no_video_skip_one_pass(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    run_cli(["media", "analyze", "A001_beach", "--no-audio"])
    assert not any("-af" in call for call in tools.calls)
    assert any("-vf" in call for call in tools.calls)
    tools.calls.clear()
    run_cli(["media", "analyze", "A001_beach", "--no-video"])
    assert any("-af" in call for call in tools.calls)
    assert not any("-vf" in call for call in tools.calls)


def test_container_and_resolve_frame_counts_disagree(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    tools.probes["A001_beach"] = _probe({**VIDEO, "nb_frames": "163"}, AUDIO)
    _, out, _ = run_cli(["media", "analyze", "A001_beach", "--json", "--fields", ALL])
    row = _rows(out)["A001_beach"]
    assert row["note"] == "container 163 frames, Resolve 120"
    assert (row["resolve_fps"], row["resolve_frames"]) == (24.0, 120)


def test_one_failing_clip_gets_an_error_row(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    tools.broken.add("A002_city")
    code, out, _ = run_cli(["media", "analyze", "A001_beach,A002_city"])
    assert code == 0
    assert "error" in out and "Invalid data" in out
    code, out, _ = run_cli(["media", "analyze", "A001_beach,A002_city", "--json"])
    data = json.loads(out)
    assert (data["analyzed"], data["errors"]) == (1, 1)
    assert "Invalid data" in str(_rows(out)["A002_city"]["error"])
    assert _rows(out)["A001_beach"]["error"] is None


def test_every_clip_failing_is_api_failed(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    tools.broken.update({"A001_beach", "A002_city"})
    code, out, _ = run_cli(["media", "analyze", "A001_beach,A002_city", "--json"])
    assert code == 1
    assert json.loads(out)["code"] == "API_FAILED"


def test_missing_file_is_a_row_error(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda self: "A003_dusk" not in str(self) and "davinci-axi-test" in str(self),
    )
    code, out, _ = run_cli(["media", "analyze", "A001_beach,A003_dusk", "--json"])
    assert code == 0
    assert _rows(out)["A003_dusk"]["error"] == "file not found"
    assert tools.for_clip("A003_dusk") == []


def test_missing_tool_fails_before_any_resolve_call(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda _: None)
    code, out, _ = run_cli(["media", "analyze", "A001_beach", "--json"])
    assert code == 1
    data = json.loads(out)
    assert data["code"] == "NOT_AVAILABLE"
    assert any("brew install ffmpeg" in line for line in data["help"])
    assert fake_world.calls == []


def test_quick_needs_only_ffprobe(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ffmpeg_common.shutil, "which", lambda n: "/bin/ffprobe" if n == "ffprobe" else None
    )
    code, out, _ = run_cli(["media", "analyze", "A001_beach", "--quick", "--json"])
    assert code == 0
    assert json.loads(out)["tools"] == {"ffprobe": "/bin/ffprobe", "ffmpeg": None}


def test_out_writes_every_row_with_silences(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    target = tmp_path / "a.json"
    code, out, _ = run_cli(["media", "analyze", "A001_beach,music_bed", "--out", str(target)])
    assert code == 0 and "out:" in out
    written = json.loads(target.read_text(encoding="utf-8"))
    assert [row["name"] for row in written] == ["A001_beach", "music_bed"]
    assert written[0]["silences_list"] == [[1.5, 3.2]]
    code, out, _ = run_cli(["media", "analyze", "A001_beach", "--out", str(target), "--json"])
    assert (code, json.loads(out)["code"]) == (1, "FILE_EXISTS")
    code, _, _ = run_cli(
        ["media", "analyze", "A001_beach", "--out", str(target), "--overwrite", "--quick"]
    )
    assert code == 0
    assert len(json.loads(target.read_text(encoding="utf-8"))) == 1


def test_out_into_a_missing_folder_is_refused(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    code, _, _ = run_cli(
        ["media", "analyze", "A001_beach", "--out", str(tmp_path / "no" / "a.json")]
    )
    assert code == 2
    assert fake_world.calls == []


def test_folder_scope_includes_all_clips(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, _ = run_cli(["media", "analyze", "--folder", "Master/Footage", "--json"])
    assert code == 0
    assert sorted(_rows(out)) == ["A001_beach", "A002_city", "A003_dusk"]


def test_folder_and_clips_together_are_refused(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, _, _ = run_cli(["media", "analyze", "A001_beach", "--folder", "Master/Footage"])
    assert code == 2
    assert tools.calls == []


@pytest.mark.parametrize(
    "flags",
    [
        ["--jobs", "0"],
        ["--jobs", "17"],
        ["--fields", "bogus"],
        ["--silence-db", "5"],
        ["--silence-s", "0"],
        ["--sample", "-1"],
    ],
)
def test_bad_flags_are_refused_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, flags: list[str]
) -> None:
    code, _, _ = run_cli(["media", "analyze", "A001_beach", *flags])
    assert code == 2
    assert fake_world.calls == [] and tools.calls == []


def test_fields_select_other_columns(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, _ = run_cli(
        ["media", "analyze", "A001_beach", "--fields", "name,true_peak,audio_ch,id", "--json"]
    )
    assert code == 0
    assert list(json.loads(out)["rows"][0]) == ["name", "true_peak", "audio_ch", "id"]


def test_silence_flags_reach_ffmpeg(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    run_cli(["media", "analyze", "A001_beach", "--silence-db", "-40", "--silence-s", "1.5"])
    chain = next(call[call.index("-af") + 1] for call in tools.calls if "-af" in call)
    assert chain == "ebur128=peak=true,silencedetect=noise=-40dB:d=1.5"


def test_the_verb_is_read_only(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    run_cli(["media", "analyze", "--folder", "Master", "--json"])
    assert fake_world.write_calls() == []
    assert ANALYZE_SPEC.reads_only is True


def test_help_has_examples(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["media", "analyze", "--help"])
    assert code == 0
    assert "examples[" in out


def test_limit_shows_fewer_rows_but_counts_every_clip(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    target = tmp_path / "all.json"
    args = ["media", "analyze", "--folder", "Master/Footage", "--limit", "2", "--json"]
    code, out, _ = run_cli([*args, "--out", str(target)])
    data = json.loads(out)
    assert code == 0
    assert (data["clips"], len(data["rows"]), data["shown"]) == (3, 2, "2 of 3")
    assert any("Showing 2 of 3 rows" in line for line in data["help"])
    assert len(json.loads(target.read_text(encoding="utf-8"))) == 3


def test_limit_above_the_clip_count_adds_no_shown_note(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    _, out, _ = run_cli(["media", "analyze", "A001_beach", "--limit", "5", "--json"])
    assert "shown" not in json.loads(out)


def test_limit_below_one_is_refused_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, _, _ = run_cli(["media", "analyze", "A001_beach", "--limit", "0"])
    assert code == 2
    assert fake_world.calls == [] and tools.calls == []


def _vf(call: list[str]) -> str:
    return call[call.index("-vf") + 1]


def _rgb_calls(tools: Tools) -> list[list[str]]:
    return [c for c in tools.calls if "-vf" in c and "format=yuv444p" in _vf(c)]


def _luma_calls(tools: Tools) -> list[list[str]]:
    return [c for c in tools.calls if "-vf" in c and "format=yuv444p" not in _vf(c)]


def test_rgb_is_one_extra_pass_over_every_frame(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, out, _ = run_cli(["media", "analyze", "A001_beach", "--rgb"])
    assert code == 0
    (call,) = _rgb_calls(tools)
    vf = _vf(call)
    assert vf.startswith("scale=in_color_matrix=bt709:in_range=tv:") and "fps=" not in vf
    assert vf.endswith("out_range=tv,format=yuv444p,signalstats,metadata=print:file=-")
    assert "fps=" not in _vf(_luma_calls(tools)[0])
    assert "ravg,gavg,bavg" in out and "26.2,144.1,50.14" in out
    assert "color cdl set --edl grades.csv" in out


def test_sample_puts_fps_in_front_of_both_passes(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, _, _ = run_cli(["media", "analyze", "A001_beach", "--rgb", "--sample", "2"])
    assert code == 0
    assert _vf(_rgb_calls(tools)[0]).startswith("fps=2,scale=in_color_matrix=bt709:in_range=tv")
    assert _vf(_luma_calls(tools)[0]).startswith("fps=2,signalstats")


def test_without_rgb_there_is_no_rgb_pass(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, out, _ = run_cli(["media", "analyze", "A001_beach"])
    assert code == 0
    assert _rgb_calls(tools) == [] and "ravg" not in out


def test_naming_a_channel_field_runs_the_pass_once(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, out, _ = run_cli(["media", "analyze", "A001_beach", "--fields", "name,ravg"])
    assert code == 0
    assert len(_rgb_calls(tools)) == 1
    assert "name,ravg" in out and "gavg" not in out


def test_rgb_with_quick_runs_no_ffmpeg_pass(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, out, _ = run_cli(["media", "analyze", "A001_beach", "--rgb", "--quick", "--json"])
    assert code == 0
    assert _rgb_calls(tools) == []
    assert _rows(out)["A001_beach"]["ravg"] is None


def test_rgb_rows_reach_the_out_file(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    target = tmp_path / "rgb.json"
    code, _, _ = run_cli(
        ["media", "analyze", "A001_beach,music_bed", "--rgb", "--out", str(target)]
    )
    assert code == 0
    rows = {r["name"]: r for r in json.loads(target.read_text())}
    assert (rows["A001_beach"]["ravg"], rows["A001_beach"]["gavg"]) == (26.2, 144.1)
    assert rows["A001_beach"]["bavg"] == 50.14
    assert rows["music_bed"]["ravg"] is None


def _scans(tools: Tools) -> list[list[str]]:
    return [c for c in tools.calls if "packet=pts_time" in c]


def test_vfr_scan_flags_uneven_frames_that_the_rates_miss(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    tools.probes["A001_beach"] = _probe({**VIDEO, "avg_frame_rate": "30/1"}, AUDIO)
    tools.pts["A001_beach"] = UNEVEN_PTS
    tools.pts["A002_city"] = UNIFORM_PTS
    code, out, _ = run_cli(
        ["media", "analyze", "A001_beach,A002_city,music_bed", "--vfr-scan", "--json"]
    )
    assert code == 0
    assert len(_scans(tools)) == 2  # one per video clip, none for music_bed
    assert all(c[c.index("-read_intervals") + 1] == "%+10" for c in _scans(tools))
    beach, city = _rows(out)["A001_beach"], _rows(out)["A002_city"]
    assert (beach["vfr_scan"], beach["frame_spread_pct"], beach["vfr"]) == (True, 51.5, True)
    assert city["vfr_scan"] is False
    assert _rows(out)["music_bed"]["vfr_scan"] is None


def test_vfr_scan_still_runs_with_quick_but_not_with_no_video(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, out, _ = run_cli(["media", "analyze", "A001_beach", "--vfr-scan", "--quick", "--json"])
    assert code == 0 and len(_scans(tools)) == 1
    assert _rows(out)["A001_beach"]["vfr_scan"] is False
    tools.calls.clear()
    run_cli(["media", "analyze", "A001_beach", "--vfr-scan", "--no-video"])
    assert _scans(tools) == []


def test_without_vfr_scan_there_is_no_scan_and_no_columns(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, out, _ = run_cli(["media", "analyze", "A001_beach"])
    assert code == 0 and _scans(tools) == []
    assert "vfr_scan" not in out and "frame_spread_pct" not in out
    _, out, _ = run_cli(["media", "analyze", "A001_beach", "--vfr-scan"])
    assert "vfr_scan,frame_spread_pct" in out


def test_vfr_scan_fields_reach_the_out_file(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    tools.pts["A001_beach"] = UNEVEN_PTS
    target = tmp_path / "vfr.json"
    code, _, _ = run_cli(["media", "analyze", "A001_beach", "--vfr-scan", "--out", str(target)])
    assert code == 0
    row = json.loads(target.read_text())[0]
    assert (row["vfr_scan"], row["frame_spread_pct"]) == (True, 51.5)


def test_help_lists_the_new_flags_and_says_every_frame(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["media", "analyze", "--help"])
    assert code == 0
    assert "--sample" in out and "--vfr-scan" in out and "every frame" in out
