"""`audio loudness` on the fake with ffmpeg stubbed through `ffmpeg_common.run` (round 2, A)."""

from __future__ import annotations

import json
import statistics
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.commands import audio, ffmpeg_common
from davinci_axi.commands.audio_loudness import LOUDNESS_SPEC
from davinci_axi.fake import FakeWorld
from davinci_axi.fake.timeline import FakeTimeline
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

SPEC: CommandSpec = replace(
    audio.SPEC, subcommands={**audio.SPEC.subcommands, "loudness": LOUDNESS_SPEC}
)
ALL = (
    "item,name,id,start,duration_s,source_lufs,volume_db,lufs,true_peak,delta,silent_pct,"
    "disabled,note,error,path"
)
SILENCE = (
    "  Duration: 00:00:05.00, start: 0.0\n"
    "[silencedetect @ 0x2] silence_start: 1.0\n"
    "[silencedetect @ 0x2] silence_end: 2.0 | silence_duration: 1.0\n"
)


def _ebur(lufs: float, peak: float = -1.3) -> str:
    return (
        f"Summary:\n\n  Integrated loudness:\n    I:         {lufs} LUFS\n"
        f"\n  True peak:\n    Peak:       {peak} dBFS\n"
    )


class Tools:
    """A stand-in for `ffmpeg_common.run`: canned ebur128 text by file stem."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.lufs: dict[str, float] = {}
        self.broken: set[str] = set()

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        stem = Path(command[command.index("-i") + 1]).stem
        if stem in self.broken:
            return subprocess.CompletedProcess(command, 1, "", f"{stem}: Invalid data\n")
        text = _ebur(self.lufs.get(stem, -23.1)) + SILENCE
        return subprocess.CompletedProcess(command, 0, "", text)


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "audio", f"{__name__}:SPEC")
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


def _data(out: str) -> dict[str, Any]:
    data = json.loads(out)
    assert isinstance(data, dict)
    return data


def _rows(out: str) -> dict[str, dict[str, Any]]:
    return {str(row["item"]): row for row in _data(out)["rows"]}


def _items(world: FakeWorld) -> list[Any]:
    project = world.resolve.pm.current_project
    timeline = next(t for t in project.timelines if t.name == "Edit v1")  # type: ignore[union-attr]
    assert isinstance(timeline, FakeTimeline)
    return timeline.tracks["audio"][0].items


def test_rows_for_every_audio_item(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, err = run_cli(["audio", "loudness", "--json", "--fields", ALL])
    assert (code, err) == (0, "")
    rows, data = _rows(out), _data(out)
    assert list(rows) == ["A1:1", "A1:2"]
    first = rows["A1:1"]
    assert (first["name"], first["source_lufs"], first["volume_db"], first["lufs"]) == (
        "A001_beach",
        -23.1,
        0.0,
        -23.1,
    )
    assert (first["duration_s"], first["delta"], first["silent_pct"]) == (5.0, None, 20.0)
    assert (data["items"], data["measured"], data["errors"]) == (2, 2, 0)
    assert (data["median_lufs"], data["range"], data["track"]) == (-23.1, "-23.1..-23.1", "all")
    assert "target" not in data and "program_hint" not in data
    assert fake_world.write_calls() == []


def test_stub_gets_the_source_window(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    run_cli(["audio", "loudness", "A1:1", "--json", "--fields", ALL])
    (call,) = tools.calls
    at = call.index("-ss")
    assert call[at : at + 4] == ["-ss", "0.000", "-t", "5.000"] and at < call.index("-i")
    _items(fake_world)[0].source_start = 48
    run_cli(["audio", "loudness", "A1:1", "--json", "--fields", ALL])
    assert tools.calls[-1][tools.calls[-1].index("-ss") + 1] == "2.000"


def test_target_adds_delta_and_the_program_hint(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, out, _ = run_cli(["audio", "loudness", "--target", "-14", "--json"])
    data = _data(out)
    assert code == 0 and data["target"] == -14.0
    assert _rows(out)["A1:1"]["delta"] == 9.1
    assert "--db 9.1" in data["program_hint"] and "--loudness -14" in data["program_hint"]
    assert any("audio normalize A1:1,A1:2 --loudness -14" in h for h in data["help"])


def test_volume_moves_the_estimate_and_flags_an_outlier(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    _items(fake_world)[0].properties["AudioVolume"] = 6.0
    code, out, _ = run_cli(["audio", "loudness", "--outlier-lu", "2", "--json"])
    rows = _rows(out)
    assert code == 0 and rows["A1:1"]["lufs"] == -17.1 and rows["A1:2"]["lufs"] == -23.1
    assert rows["A1:1"]["note"] == "3 LU over median"
    assert rows["A1:2"]["note"] == "3 LU under median"
    assert any("audio gain A1:1 --db -3.0" in h for h in _data(out)["help"])
    _, quiet, _ = run_cli(["audio", "loudness", "--json", "--fields", ALL])
    assert _rows(quiet)["A1:1"]["note"] is None


def test_unreadable_volume_leaves_the_estimate_empty(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    _items(fake_world)[0].properties.pop("AudioVolume")
    code, out, _ = run_cli(["audio", "loudness", "--json", "--target", "-14"])
    first = _rows(out)["A1:1"]
    assert code == 0 and first["volume_db"] is None and first["lufs"] is None
    assert first["delta"] is None and first["note"] == "volume not readable"
    assert first["source_lufs"] == -23.1
    assert _data(out)["median_lufs"] == -23.1  # only A1:2 has an estimate


def test_one_failed_item_is_an_error_row_and_all_failed_exits_one(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    tools.broken.add("music_bed")
    code, out, _ = run_cli(["audio", "loudness", "--json", "--fields", ALL])
    data = _data(out)
    assert code == 0 and (data["measured"], data["errors"]) == (1, 1)
    assert "Invalid data" in _rows(out)["A1:2"]["error"]
    tools.broken.add("A001_beach")
    code, out, _ = run_cli(["audio", "loudness", "--json", "--fields", ALL])
    assert code == 1 and "every audio item failed" in out


def test_no_audio_stream_and_missing_path(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    items = _items(fake_world)
    items[0].media.properties["Type"] = "Video"
    items[1].media.properties["File Path"] = ""
    items[1].enabled = False
    code, out, _ = run_cli(["audio", "loudness", "A1:1", "--json", "--fields", ALL])
    first = _rows(out)["A1:1"]
    assert code == 0 and first["source_lufs"] is None and first["note"] == "no audio stream"
    assert first["error"] is None
    code, out, _ = run_cli(["audio", "loudness", "--json", "--fields", ALL])
    assert code == 0 and _rows(out)["A1:2"]["error"] == "no file path"


def test_toon_shows_the_default_columns(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, _ = run_cli(["audio", "loudness"])
    assert code == 0
    assert "item,name,duration_s,source_lufs,volume_db,lufs" in out
    assert "delta" not in out


def test_missing_ffmpeg_fails_before_any_resolve_call(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda name: None)
    code, out, _ = run_cli(["audio", "loudness", "--json", "--fields", ALL])
    assert code == 1 and "NOT_AVAILABLE" in out
    assert fake_world.calls == []


def test_track_past_the_last_is_not_found(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, out, _ = run_cli(["audio", "loudness", "--track", "9", "--json"])
    assert code == 1 and "NOT_FOUND" in out
    code, out, _ = run_cli(["audio", "loudness", "--track", "1", "--json"])
    assert code == 0 and _data(out)["track"] == "A1"


def test_a_video_item_is_refused(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, _ = run_cli(["audio", "loudness", "A1:1,V1:1", "--json"])
    assert "VALIDATION_ERROR" in out and "V1:1 is a video item" in out and code != 0
    assert tools.calls == []


@pytest.mark.parametrize(
    "bad",
    [
        ["--jobs", "0"],
        ["--jobs", "17"],
        ["--fields", "bogus"],
        ["--target", "5"],
        ["--target", "-70"],
        ["--tolerance", "-1"],
        ["--outlier-lu", "0"],
        ["--track", "0"],
        ["--limit", "0"],
    ],
)
def test_bad_flags_exit_two_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, bad: list[str]
) -> None:
    code, out, _ = run_cli(["audio", "loudness", *bad, "--json"])
    assert code == 2 and "VALIDATION_ERROR" in out
    assert fake_world.calls == [] and tools.calls == []


def test_one_get_properties_call_per_item(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    run_cli(["audio", "loudness", "--json", "--fields", ALL])
    assert len(fake_world.calls_to("TimelineItem.GetProperties")) == 2


def test_empty_timeline_says_so(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, _ = run_cli(["audio", "loudness", "--timeline", "Edit v2", "--json"])
    assert code == 0 and "0 audio items" in _data(out)["rows"]
    assert tools.calls == []


def _decimals(value: float) -> int:
    text = repr(value)
    return len(text.split(".")[1]) if "." in text else 0


def test_volume_and_median_are_rounded(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    _items(fake_world)[0].properties["AudioVolume"] = 10.600000000000001
    tools.lufs.update({"A001_beach": -23.1, "music_bed": -23.3})
    code, out, _ = run_cli(["audio", "loudness", "--json"])
    data = _data(out)
    first = _rows(out)["A1:1"]
    assert code == 0 and first["volume_db"] == 10.6 and first["lufs"] == -12.5
    assert data["median_lufs"] == -17.9
    for value in (first["volume_db"], first["lufs"], data["median_lufs"]):
        assert _decimals(value) <= 2
    _, toon, _ = run_cli(["audio", "loudness", "--fields", "item,volume_db,lufs"])
    assert "00000" not in toon
    assert round(statistics.median([-16.1, -16.3]), 2) == -16.2


def test_old_outlier_flag_is_unknown(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, err = run_cli(["audio", "loudness", "--outlier", "3", "--json"])
    assert code == 2 and tools.calls == [] and fake_world.calls == []
    _, helped, _ = run_cli(["audio", "loudness", "--help"])
    assert "--outlier-lu" in out + err + helped
