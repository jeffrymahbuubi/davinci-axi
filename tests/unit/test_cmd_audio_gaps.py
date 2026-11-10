"""`audio gaps` on the fake with ffmpeg stubbed through `ffmpeg_common.run` (round 2, B)."""

from __future__ import annotations

import csv
import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.commands import audio, ffmpeg_common, timeline_extra
from davinci_axi.commands.audio_gaps import GAPS_SPEC
from davinci_axi.commands.timeline_marker_edl import MARKER_ADD_EDL_SPEC
from davinci_axi.fake import FakeWorld, ext_timeline_edit
from davinci_axi.fake.timeline import FakeTimeline, FakeTrack
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

SPEC: CommandSpec = replace(audio.SPEC, subcommands={**audio.SPEC.subcommands, "gaps": GAPS_SPEC})
SILENCE = (
    "Summary:\n\n  Integrated loudness:\n    I:         -23.1 LUFS\n"
    "\n  True peak:\n    Peak:       -1.3 dBFS\n"
    "  Duration: 00:00:05.00, start: 0.0\n"
    "[silencedetect @ 0x2] silence_start: 1.5\n"
    "[silencedetect @ 0x2] silence_end: 3.2 | silence_duration: 1.7\n"
)


class Tools:
    """A stand-in for `ffmpeg_common.run`: the same canned text for every file."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.broken: set[str] = set()

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        stem = Path(command[command.index("-i") + 1]).stem
        if stem in self.broken:
            return subprocess.CompletedProcess(command, 1, "", f"{stem}: Invalid data\n")
        return subprocess.CompletedProcess(command, 0, "", SILENCE)


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "audio", f"{__name__}:SPEC")
    monkeypatch.delenv("FFMPEG", raising=False)


@pytest.fixture(autouse=True)
def _thirty_fps(fake_world: FakeWorld) -> None:
    """The default world runs at 24 fps; the plan's numbers are worked out at 30."""
    settings = _timeline(fake_world).settings
    settings.update({"useCustomSettings": "1", "timelineFrameRate": 30})


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


def _timeline(world: FakeWorld, name: str = "Edit v1") -> FakeTimeline:
    project = world.resolve.pm.current_project
    timeline = next(t for t in project.timelines if t.name == name)  # type: ignore[union-attr]
    assert isinstance(timeline, FakeTimeline)
    return timeline


def _drop_music(world: FakeWorld) -> None:
    """A1 keeps only A001_beach (86400..86520); the picture runs to 86640."""
    track = _timeline(world).tracks["audio"][0]
    track.items = track.items[:1]


def test_no_measure_finds_the_uncovered_span_with_zero_ffmpeg_calls(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    _drop_music(fake_world)
    code, out, err = run_cli(["audio", "gaps", "--no-measure", "--json"])
    data = _data(out)
    assert (code, err) == (0, "")
    assert data["rows"] == [
        {
            "kind": "no-audio",
            "where": "-",
            "start": 86520,
            "end": 86640,
            "tc": "00:48:04:00",
            "duration_s": 4.0,
        }
    ]
    assert (data["gaps"], data["no_audio_s"], data["silent_s"]) == (1, 4.0, 0.0)
    assert (data["covered_pct"], data["measured"], data["errors"]) == (50.0, 0, 0)
    assert tools.calls == [] and fake_world.write_calls() == []


def test_fully_covered_picture_has_no_structural_rows(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, out, _ = run_cli(["audio", "gaps", "--no-measure", "--json"])
    data = _data(out)
    assert code == 0 and data["gaps"] == 0 and data["covered_pct"] == 100.0
    assert "0 gaps in timeline Edit v1" in data["rows"] and "min_s=1.0" in data["rows"]


def test_silences_map_to_timeline_frames(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, _ = run_cli(["audio", "gaps", "--json"])
    data = _data(out)
    assert code == 0
    assert [(r["kind"], r["where"], r["start"], r["end"]) for r in data["rows"]] == [
        ("silent", "A1:1", 86445, 86496),
        ("silent", "A1:2", 86565, 86616),
    ]
    assert data["rows"][0]["duration_s"] == 1.7 and data["rows"][0]["tc"] == "00:48:01:15"
    assert (data["gaps"], data["silent_s"], data["measured"], data["errors"]) == (2, 3.4, 2, 0)
    assert data["covered_pct"] == round(100 * (240 - 102) / 240, 1)
    assert len(tools.calls) == 2


def test_min_s_drops_short_rows(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, _ = run_cli(["audio", "gaps", "--min-s", "2", "--json"])
    data = _data(out)
    assert code == 0 and data["gaps"] == 0 and data["covered_pct"] == 100.0
    assert "0 gaps" in data["rows"]


def test_rows_sort_by_start_across_kinds(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    _drop_music(fake_world)
    _, out, _ = run_cli(["audio", "gaps", "--json"])
    rows = _data(out)["rows"]
    assert [r["kind"] for r in rows] == ["silent", "no-audio"]
    assert [r["start"] for r in rows] == sorted(r["start"] for r in rows)


def test_one_failed_item_is_counted_and_exits_zero(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    tools.broken.add("music_bed")
    code, out, _ = run_cli(["audio", "gaps", "--json"])
    data = _data(out)
    assert code == 0 and (data["measured"], data["errors"]) == (1, 1)
    assert [r["where"] for r in data["rows"]] == ["A1:1"]
    assert any("A1:2" in h and "Invalid data" in h for h in data["help"])


def test_disabled_items_are_neither_measured_nor_cover_the_picture(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    _timeline(fake_world).tracks["audio"][0].items[1].enabled = False
    code, out, _ = run_cli(["audio", "gaps", "--json", "--min-s", "2"])
    data = _data(out)
    assert code == 0 and len(tools.calls) == 1
    assert [(r["kind"], r["start"], r["end"]) for r in data["rows"]] == [("no-audio", 86520, 86640)]


def test_hand_built_timeline_covered_pct(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    timeline = _timeline(fake_world)
    video = timeline.tracks["video"][0]
    video.items = video.items[:1]
    video.items[0].duration = 240
    timeline.tracks["video"][1].items = []
    _drop_music(fake_world)
    code, out, _ = run_cli(["audio", "gaps", "--no-measure", "--json"])
    data = _data(out)
    assert code == 0 and [(r["start"], r["end"]) for r in data["rows"]] == [(86520, 86640)]
    assert data["covered_pct"] == 50.0


def test_empty_audio_track_is_one_span_with_the_track_hint(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    _timeline(fake_world).tracks["audio"][0].items = []
    _, out, _ = run_cli(["audio", "gaps", "--no-measure", "--json"])
    data = _data(out)
    assert [(r["start"], r["end"]) for r in data["rows"]] == [(86400, 86640)]
    assert data["covered_pct"] == 0.0
    assert any("timeline track add audio" in h for h in data["help"])
    assert any("audio loudness" in h for h in data["help"])
    assert any("desyncs" in h for h in data["help"])


def test_second_audio_track_changes_the_music_hint(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    timeline = _timeline(fake_world)
    _drop_music(fake_world)
    timeline.tracks["audio"].append(FakeTrack("Audio 2", "stereo"))
    _, out, _ = run_cli(["audio", "gaps", "--no-measure", "--json"])
    help_lines = _data(out)["help"]
    assert any(
        "--track 2 --frame 86520" in h or "--track 2 --record-frame 86520" in h for h in help_lines
    )
    assert any("on A2 (Audio 2)" in h for h in help_lines)


def test_music_hint_names_a_custom_track(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    _drop_music(fake_world)
    _timeline(fake_world).tracks["audio"].append(FakeTrack("Music", "stereo"))
    _, out, _ = run_cli(["audio", "gaps", "--no-measure", "--json"])
    assert any("on A2 (Music)" in h for h in _data(out)["help"])


def test_music_hint_without_a_track_name_has_no_parenthesis(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    _drop_music(fake_world)
    _timeline(fake_world).tracks["audio"].append(FakeTrack("Audio 2", "stereo"))
    fake_world.inject("Timeline.GetTrackName", "")
    _, out, _ = run_cli(["audio", "gaps", "--no-measure", "--json"])
    music = [h for h in _data(out)["help"] if "music bed" in h]
    assert music and "on A2" in music[0] and "(" not in music[0]


def test_one_audio_track_never_reads_a_track_name(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    _timeline(fake_world).tracks["audio"][0].items = []
    run_cli(["audio", "gaps", "--no-measure", "--json"])
    assert fake_world.calls_to("Timeline.GetTrackName") == []


def test_no_video_says_so(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, _ = run_cli(["audio", "gaps", "--timeline", "Edit v2", "--json"])
    assert code == 0 and "0 video items" in _data(out)["rows"] and tools.calls == []


def test_track_limits_the_audio_considered(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, out, _ = run_cli(["audio", "gaps", "--track", "1", "--no-measure", "--json"])
    assert code == 0 and _data(out)["track"] == "A1"
    code, out, _ = run_cli(["audio", "gaps", "--track", "9", "--no-measure", "--json"])
    assert code == 1 and "NOT_FOUND" in out


def test_limit_trims_rows_and_says_so(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, _ = run_cli(["audio", "gaps", "--limit", "1", "--json"])
    data = _data(out)
    assert code == 0 and len(data["rows"]) == 1 and data["gaps"] == 2
    assert any("1 more rows" in h for h in data["help"])


def test_missing_ffmpeg_fails_before_any_resolve_call(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda name: None)
    code, out, _ = run_cli(["audio", "gaps", "--json"])
    assert code == 1 and "NOT_AVAILABLE" in out and fake_world.calls == []
    code, _, _ = run_cli(["audio", "gaps", "--no-measure", "--json"])
    assert code == 0


@pytest.mark.parametrize(
    "bad",
    [
        ["--silence-db", "3"],
        ["--silence-s", "0"],
        ["--min-s", "-1"],
        ["--jobs", "0"],
        ["--jobs", "17"],
        ["--track", "0"],
        ["--limit", "0"],
    ],
)
def test_bad_flags_exit_two_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, bad: list[str]
) -> None:
    code, out, _ = run_cli(["audio", "gaps", *bad, "--json"])
    assert code == 2 and "VALIDATION_ERROR" in out
    assert fake_world.calls == [] and tools.calls == []


def test_toon_output_lists_the_rows(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, _ = run_cli(["audio", "gaps"])
    assert code == 0 and "silent" in out and "covered_pct" in out


# --- round 3, D: --out marker rows and the music hint --------------------------------------------

CSV_HEAD = "frame,color,name,note,duration"


def _gap_rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))


def test_out_csv_holds_the_no_audio_gap_as_a_marker_row(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    _drop_music(fake_world)
    out = tmp_path / "g.csv"
    code, text, err = run_cli(["audio", "gaps", "--no-measure", "--out", str(out), "--json"])
    data = _data(text)
    assert (code, err) == (0, "") and data["out"] == str(out) and data["out_rows"] == 1
    assert out.read_text(encoding="utf-8").splitlines()[0] == CSV_HEAD
    assert _gap_rows(out) == [
        {
            "frame": "120",
            "color": "Red",
            "name": "no-audio 4.0 s",
            "note": "00:48:04:00 frames 86520-86640",
            "duration": "120",
        }
    ]
    assert any(f"timeline marker add --edl {out} --force" in h for h in data["help"])
    assert any("mark the gaps on the timeline" in h for h in data["help"])
    assert fake_world.write_calls() == []


def test_out_json_writes_a_marker_row_array_with_silent_rows(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    out = tmp_path / "g.json"
    code, _, _ = run_cli(["audio", "gaps", "--out", str(out), "--json"])
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0 and [r["frame"] for r in rows] == [45, 165]
    assert rows[0] == {
        "frame": 45,
        "color": "Yellow",
        "name": "silent A1:1 1.7 s",
        "note": "00:48:01:15 frames 86445-86496",
        "duration": 51,
    }


def test_out_writes_every_row_not_only_the_limit(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    out = tmp_path / "g.csv"
    _, text, _ = run_cli(["audio", "gaps", "--limit", "1", "--out", str(out), "--json"])
    data = _data(text)
    assert len(data["rows"]) == 1 and data["out_rows"] == 2 and len(_gap_rows(out)) == 2


def test_out_with_a_bad_suffix_exits_two_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    code, text, _ = run_cli(["audio", "gaps", "--out", str(tmp_path / "g.txt"), "--json"])
    data = _data(text)
    assert code == 2 and "Valid: --out gaps.csv" in data["help"]
    assert fake_world.calls == [] and tools.calls == []


def test_out_into_a_missing_folder_exits_two(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    code, text, _ = run_cli(["audio", "gaps", "--out", str(tmp_path / "no" / "g.csv"), "--json"])
    assert code == 2 and "is not a folder" in _data(text)["error"] and fake_world.calls == []


def test_an_existing_out_file_is_refused_until_overwrite(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    out = tmp_path / "g.csv"
    out.write_text("keep me", encoding="utf-8")
    code, text, _ = run_cli(["audio", "gaps", "--no-measure", "--out", str(out), "--json"])
    assert code == 1 and "FILE_EXISTS" in text and _data(text)["help"]
    assert fake_world.calls == [] and out.read_text(encoding="utf-8") == "keep me"
    _drop_music(fake_world)
    args = ["audio", "gaps", "--no-measure", "--out", str(out), "--overwrite", "--json"]
    code, _, _ = run_cli(args)
    assert code == 0 and out.read_text(encoding="utf-8").startswith(CSV_HEAD)


def test_no_gaps_writes_no_file(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, tmp_path: Path
) -> None:
    out = tmp_path / "g.csv"
    code, text, _ = run_cli(["audio", "gaps", "--no-measure", "--out", str(out), "--json"])
    data = _data(text)
    assert code == 0 and data["out_rows"] == 0 and "out" not in data and not out.exists()
    assert not any("marker add --edl" in h for h in data["help"])


ROUND_TRIP = replace(
    timeline_extra.SPEC,
    subcommands={
        **timeline_extra.SPEC.subcommands,
        "marker": replace(
            timeline_extra.WRAPPED_MARKER,
            subcommands={**timeline_extra.WRAPPED_MARKER.subcommands, "add": MARKER_ADD_EDL_SPEC},
        ),
    },
)
WITH_MUSIC = replace(
    timeline_extra.SPEC,
    subcommands={
        **timeline_extra.SPEC.subcommands,
        "music": CommandSpec(name="music", summary="x"),
    },
)
WITHOUT_MUSIC = replace(
    timeline_extra.SPEC,
    subcommands={k: v for k, v in timeline_extra.SPEC.subcommands.items() if k != "music"},
)


def test_out_rows_load_back_through_marker_add_edl(
    run_cli: Run,
    fake_world: FakeWorld,
    tools: Tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ext_timeline_edit.extend()
    monkeypatch.setitem(registry.COMMANDS, "timeline", f"{__name__}:ROUND_TRIP")
    _drop_music(fake_world)
    out = tmp_path / "g.csv"
    run_cli(["audio", "gaps", "--no-measure", "--out", str(out), "--json"])
    code, _, _ = run_cli(["timeline", "marker", "add", "--edl", str(out), "--force", "--json"])
    marks = _timeline(fake_world).markers
    assert code == 0 and marks[120]["name"] == "no-audio 4.0 s" and marks[120]["duration"] == 120


def test_music_hint_points_at_timeline_music_when_registered(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(registry.COMMANDS, "timeline", f"{__name__}:WITH_MUSIC")
    _drop_music(fake_world)
    _timeline(fake_world).tracks["audio"].append(FakeTrack("Music", "stereo"))
    _, text, _ = run_cli(["audio", "gaps", "--no-measure", "--json"])
    help_lines = _data(text)["help"]
    assert any(
        "timeline music add <music clip> --track 2 --frame 86520" in h
        and "lay a music bed on A2 (Music)" in h
        for h in help_lines
    )
    assert not any("timeline append" in h for h in help_lines)


def test_music_hint_keeps_the_append_line_when_music_is_absent(
    run_cli: Run, fake_world: FakeWorld, tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(registry.COMMANDS, "timeline", f"{__name__}:WITHOUT_MUSIC")
    _drop_music(fake_world)
    _timeline(fake_world).tracks["audio"].append(FakeTrack("Audio 2", "stereo"))
    _, text, _ = run_cli(["audio", "gaps", "--no-measure", "--json"])
    help_lines = _data(text)["help"]
    assert any(
        "timeline append <music clip> --track 2 --record-frame 86520" in h for h in help_lines
    )
    assert not any("timeline music" in h for h in help_lines)
