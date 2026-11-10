"""`timeline check --audio` on the fake, ffmpeg stubbed through `ffmpeg_common.run` (round 2, C)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.commands import ffmpeg_common, timeline_extra
from davinci_axi.commands.timeline_check import CHECK_SPEC
from davinci_axi.fake import FakeWorld
from davinci_axi.fake.timeline import FakeTimeline

Run = Callable[[list[str]], tuple[int, str, str]]
SPEC = replace(
    timeline_extra.SPEC, subcommands={**timeline_extra.SPEC.subcommands, "check": CHECK_SPEC}
)
SILENT = (
    "  Duration: 00:00:05.00, start: 0.0\n"
    "[silencedetect @ 0x2] silence_start: 0.0\n"
    "[silencedetect @ 0x2] silence_end: 4.5 | silence_duration: 4.5\n"
)
LIVE = "  Duration: 00:00:05.00, start: 0.0\n"


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
        self.silent: set[str] = set()
        self.broken: set[str] = set()

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        stem = Path(command[command.index("-i") + 1]).stem
        if stem in self.broken:
            return subprocess.CompletedProcess(command, 1, "", f"{stem}: Invalid data\n")
        tail = SILENT if stem in self.silent else LIVE
        text = _ebur(self.lufs.get(stem, -23.1)) + tail
        return subprocess.CompletedProcess(command, 0, "", text)


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "timeline", f"{__name__}:SPEC")
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


def _check(run_cli: Run, *flags: str) -> tuple[int, dict[str, Any]]:
    code, out, err = run_cli(["timeline", "check", "--json", *flags])
    assert err == ""
    return code, json.loads(out)


def _rows(data: dict[str, Any], check: str) -> list[dict[str, str]]:
    return [row for row in data["findings"] if row["check"] == check]


def _with_third_item(world: FakeWorld) -> FakeTimeline:
    project = world.resolve.pm.current_project
    timeline = next(t for t in project.timelines if t.name == "Edit v1")  # type: ignore[union-attr]
    assert isinstance(timeline, FakeTimeline)
    footage = project.media_pool.root.subfolders[0].clips  # type: ignore[union-attr]
    timeline.add_item("audio", 1, "A003_dusk", timeline.start_frame + 240, 24, media=footage[2])
    return timeline


def test_default_check_never_needs_ffmpeg(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda name: None)
    monkeypatch.setattr(ffmpeg_common, "run", lambda command: calls.append(command))  # type: ignore[arg-type,return-value]
    code, data = _check(run_cli)
    assert code == 0 and calls == []
    assert _rows(data, "loudness") == [] and _rows(data, "silent-item") == []


def test_audio_target_reports_the_program_gap_and_the_gain_hint(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, data = _check(run_cli, "--audio", "--lufs", "-14")
    assert code == 0 and len(tools.calls) == 2
    program, median = _rows(data, "loudness")  # warnings sort first
    assert (median["level"], median["where"]) == ("info", "A1")
    assert median["detail"] == (
        "median -23.1 LUFS est. across 2 items (before track effects; "
        "outliers: more than 6 LU away)"
    )
    assert (program["level"], program["where"]) == ("warn", "program")
    assert program["detail"].startswith("9.1 LU under the target -14")
    assert data["warnings"] == 2  # the orientation warning stays
    assert any("audio gain <items> --db +9.1" in line for line in data["help"])
    assert any("audio loudness --target -14" in line for line in data["help"])
    assert fake_world.write_calls() == []


def test_within_tolerance_is_info(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    _, data = _check(run_cli, "--audio", "--lufs", "-22.5", "--lufs-tolerance", "1")
    (program,) = [r for r in _rows(data, "loudness") if r["where"] == "program"]
    assert program["level"] == "info" and program["where"] == "program"
    _, data = _check(run_cli, "--audio", "--lufs", "-22.5", "--lufs-tolerance", "0.2")
    (program,) = [r for r in _rows(data, "loudness") if r["where"] == "program"]
    assert program["level"] == "warn"


def test_a_quiet_item_is_a_warn_that_counts(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    _with_third_item(fake_world)
    tools.lufs["music_bed"] = -40.0
    code, data = _check(run_cli, "--audio")
    (warn,) = [r for r in _rows(data, "loudness") if r["level"] == "warn"]
    assert warn["where"] == "A1:2"
    assert warn["detail"] == (
        "music_bed -40.0 LUFS est. (source -40.0, gain +0.0), 16.9 LU under the median -23.1"
    )
    assert code == 0 and data["warnings"] == 2
    assert any("audio gain A1:2 --db +16.9" in line for line in data["help"])


def test_a_loud_item_is_over_the_median(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    timeline = _with_third_item(fake_world)
    timeline.tracks["audio"][0].items[0].properties["AudioVolume"] = 9.0
    _, data = _check(run_cli, "--audio")
    (warn,) = [r for r in _rows(data, "loudness") if r["level"] == "warn"]
    assert warn["where"] == "A1:1" and "9.0 LU over the median -23.1" in warn["detail"]
    assert any("audio gain A1:1 --db -9.0" in line for line in data["help"])


def test_mostly_silent_items_are_info_and_group_in_toon(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    tools.silent.update({"A001_beach", "music_bed"})
    code, data = _check(run_cli, "--audio")
    rows = _rows(data, "silent-item")
    assert code == 0 and [r["where"] for r in rows] == ["A1:1", "A1:2"]
    assert rows[0]["detail"] == "A001_beach: 90% of its range is silent"
    assert all(r["level"] == "info" for r in rows)
    code, out, _ = run_cli(["timeline", "check", "--audio"])
    assert code == 0 and "2 items" in out and "mostly silent items" in out


def test_unmeasurable_items_are_info_not_failures(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    tools.broken.add("music_bed")
    code, data = _check(run_cli, "--audio")
    (row,) = [r for r in _rows(data, "loudness") if "not measured" in r["detail"]]
    assert code == 0 and row["level"] == "info" and row["where"] == "A1:2"
    assert "Invalid data" in row["detail"]
    assert any("across 1 items" in r["detail"] for r in _rows(data, "loudness"))


def test_disabled_items_are_not_measured(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    timeline = _with_third_item(fake_world)
    timeline.tracks["audio"][0].items[1].enabled = False
    _check(run_cli, "--audio")
    assert len(tools.calls) == 2


def test_lufs_without_audio_is_a_validation_error(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    for flags in (["--lufs", "-14"], ["--lufs-tolerance", "1"]):
        code, out, _ = run_cli(["timeline", "check", "--json", *flags])
        assert code == 2 and "needs --audio" in out
    assert tools.calls == [] and fake_world.calls == []


def test_missing_ffmpeg_fails_before_any_resolve_call(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda name: None)
    code, out, _ = run_cli(["timeline", "check", "--json", "--audio"])
    assert code == 1 and "NOT_AVAILABLE" in out
    assert fake_world.calls == []


def test_negative_tolerance_is_rejected(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    code, out, _ = run_cli(
        ["timeline", "check", "--json", "--audio", "--lufs", "-14", "--lufs-tolerance=-1"]
    )
    assert code == 2 and "0 or more" in out


def test_outlier_lu_lowers_the_threshold(run_cli: Run, fake_world: FakeWorld, tools: Tools) -> None:
    timeline = _with_third_item(fake_world)
    timeline.tracks["audio"][0].items[0].properties["AudioVolume"] = 1.5
    _, data = _check(run_cli, "--audio")
    assert [r for r in _rows(data, "loudness") if r["level"] == "warn"] == []
    _, data = _check(run_cli, "--audio", "--outlier-lu", "1")
    (warn,) = [r for r in _rows(data, "loudness") if r["level"] == "warn"]
    assert warn["where"] == "A1:1" and "1.5 LU over the median -23.1" in warn["detail"]
    (median,) = [r for r in _rows(data, "loudness") if r["detail"].startswith("median")]
    assert "outliers: more than 1 LU away" in median["detail"]


def test_outlier_lu_validation_exits_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tools: Tools
) -> None:
    code, out, _ = run_cli(["timeline", "check", "--json", "--audio", "--outlier-lu", "0"])
    assert code == 2 and "must be above 0" in out
    code, out, _ = run_cli(["timeline", "check", "--json", "--outlier-lu", "2"])
    assert code == 2 and "--outlier-lu needs --audio" in out
    assert tools.calls == [] and fake_world.calls == []


def test_help_lists_outlier_lu_and_the_thresholds(run_cli: Run) -> None:
    code, out, _ = run_cli(["timeline", "check", "--help"])
    assert code == 0 and "--outlier-lu" in out and "default 6" in out
    assert "80% of the used range" in out and "--lufs-tolerance" in out
