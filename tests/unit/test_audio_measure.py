"""`audio_measure`: the pure estimate/window/outlier functions and `read_item` on the fake."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from davinci_axi.commands import audio_measure as am
from davinci_axi.commands import ffmpeg_common
from davinci_axi.commands.item_detail import Located
from davinci_axi.fake import FakeWorld
from davinci_axi.fake.timeline import FakeTimeline


def _info(**over: object) -> am.ItemInfo:
    base = am.ItemInfo(
        ref="A1:1",
        id="u1",
        name="clip",
        start=100,
        end=220,
        enabled=True,
        source_start=30,
        source_end=138,
        clip_fps=30.0,
        path="/m/clip.wav",
        volume_db=0.0,
        has_audio=True,
    )
    return replace(base, **over)  # type: ignore[arg-type]


def _measured(
    ref: str = "A1:1", lufs: float | None = -23.0, volume: float | None = 0.0, **over: object
) -> am.Measured:
    m = am.Measured(_info(ref=ref, volume_db=volume), lufs, -1.0, [], None)
    return replace(m, **over)  # type: ignore[arg-type]


def _a1_first(world: FakeWorld) -> object:
    project = world.resolve.pm.current_project
    timeline = next(t for t in project.timelines if t.name == "Edit v1")  # type: ignore[union-attr]
    assert isinstance(timeline, FakeTimeline)
    return timeline.tracks["audio"][0].items[0]


def test_window_uses_source_frames_at_the_clip_rate() -> None:
    assert am.window(_info()) == (1.0, 3.6)
    assert am.window(_info(clip_fps=None)) is None
    assert am.window(_info(source_end=30)) is None


def test_estimate_adds_the_volume_and_needs_both() -> None:
    assert am.estimate(_measured(lufs=-23.1, volume=6.0)) == -17.1
    assert am.estimate(_measured(volume=None)) is None
    assert am.estimate(_measured(lufs=None)) is None
    assert am.estimate_peak(_measured(volume=-3.0)) == -4.0
    assert am.estimate_peak(_measured(volume=None)) is None


def test_silent_share_is_the_silent_fraction_of_the_window() -> None:
    quiet = _measured(silences=[(0.0, 1.8), (3.0, 9.0)])  # the second is cut at the 3.6 s window
    assert am.silent_share(quiet) == pytest.approx(0.6667, abs=1e-4)
    assert am.silent_share(_measured(error="boom")) is None
    assert am.silent_share(replace(_measured(), info=_info(clip_fps=None))) is None


def test_on_timeline_maps_and_clamps_to_the_item_span() -> None:
    m = _measured(silences=[(0.0, 1.0), (3.0, 99.0), (-5.0, -1.0)])
    assert am.on_timeline(m, 30.0) == [(100, 130), (190, 220)]
    assert am.on_timeline(m, 24.0) == [(100, 124), (172, 220)]


def test_median_and_outliers() -> None:
    assert am.median([]) is None
    assert am.median([-20.0, -22.0]) == -21.0
    group = [_measured("A1:1", -23), _measured("A1:2", -23), _measured("A1:3", -32)]
    assert am.outliers(group) == {"A1:3": -9.0}
    assert am.outliers(group, 10.0) == {}
    assert am.outliers([_measured("A1:1", volume=None)]) == {}
    over = [_measured("A1:1", -23), _measured("A1:2", -23), _measured("A1:3", -14)]
    assert am.outliers(over) == {"A1:3": 9.0}


def test_gain_hint_names_the_refs_and_a_signed_amount() -> None:
    assert "audio gain A1:3,A1:4 --db +9.0" in am.gain_hint(["A1:3", "A1:4"], 9.0)


def test_read_item_on_a_fake_audio_item(fake_world: FakeWorld) -> None:
    item = _a1_first(fake_world)
    info = am.read_item(Located("A1", 1, item))  # type: ignore[arg-type]
    assert (info.ref, info.name, info.volume_db, info.has_audio) == (
        "A1:1",
        "A001_beach",
        0.0,
        True,
    )
    assert info.path.endswith("A001_beach.mov") and info.clip_fps == 24.0
    assert (info.start, info.end) == (item.start, item.start + 120)  # type: ignore[attr-defined]
    assert am.window(info) == (0.0, 5.0)


def test_read_item_tolerates_missing_properties_and_media(fake_world: FakeWorld) -> None:
    item = _a1_first(fake_world)
    item.properties.pop("AudioVolume")  # type: ignore[attr-defined]
    assert am.read_item(Located("A1", 1, item)).volume_db is None  # type: ignore[arg-type]
    item.media = None  # type: ignore[attr-defined]
    bare = am.read_item(Located("A1", 1, item))  # type: ignore[arg-type]
    assert (bare.path, bare.has_audio, bare.clip_fps) == ("", False, None)


def test_measure_items_errors_and_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def stub(command: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        if "bad.wav" in command[command.index("-i") + 1]:
            return subprocess.CompletedProcess(command, 1, "", "bad.wav: Invalid data\n")
        text = "Summary:\n  Integrated loudness:\n    I: -20.0 LUFS\n"
        return subprocess.CompletedProcess(command, 0, "", text)

    monkeypatch.setattr(ffmpeg_common.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(ffmpeg_common, "run", stub)
    monkeypatch.setattr(Path, "is_file", lambda self: not str(self).endswith("gone.wav"))
    infos = [
        _info(ref="A1:1"),
        _info(ref="A1:2", path=""),
        _info(ref="A1:3", path="/m/gone.wav"),
        _info(ref="A1:4", path="/m/bad.wav"),
        _info(ref="A1:5", has_audio=False),
    ]
    got = am.measure_items(infos, silence_db=-35.0, silence_s=0.7, jobs=2)
    assert [(m.info.ref, m.lufs, m.error) for m in got] == [
        ("A1:1", -20.0, None),
        ("A1:2", None, "no file path"),
        ("A1:3", None, "file not found"),
        ("A1:4", None, "ffmpeg failed on bad.wav: bad.wav: Invalid data"),
        ("A1:5", None, None),
    ]
    assert len(seen) == 2  # A1:1 and A1:4 only
    first = seen[0]
    assert first[first.index("-ss") : first.index("-ss") + 4] == ["-ss", "1.000", "-t", "3.600"]
