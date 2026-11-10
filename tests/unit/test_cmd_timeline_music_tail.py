"""F51 a: `timeline music add --loop` never ends in a stub piece (fake world, 30 fps)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.commands import timeline_extra
from davinci_axi.commands.timeline_music import MUSIC_SPEC
from davinci_axi.fake import FakeProject, FakeTimeline, FakeWorld
from davinci_axi.fake.media import FakeMediaPool, FakeMediaPoolItem

Run = Callable[[list[str]], tuple[int, str, str]]
SPEC = replace(
    timeline_extra.SPEC, subcommands={**timeline_extra.SPEC.subcommands, "music": MUSIC_SPEC}
)
ADD = ["timeline", "music", "add"]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "timeline", f"{__name__}:SPEC")


@pytest.fixture(autouse=True)
def _thirty_fps(fake_world: FakeWorld) -> None:
    _timeline(fake_world).settings.update({"useCustomSettings": "1", "timelineFrameRate": 30})
    _timeline(fake_world).add_track("audio")  # A2, empty


def _timeline(world: FakeWorld) -> FakeTimeline:
    project = world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    return next(t for t in project.timelines if t.name == "Edit v1")


def _song(world: FakeWorld, name: str, frames: int, *, fps: str = "30") -> FakeMediaPoolItem:
    project = world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    return project.media_pool.root.add_clip(
        name,
        f"/media/x/{name}.wav",
        clip_type="Audio",
        frames=str(frames),
        fps=fps,
        duration="00:00:10:00",
    )


def _json(out: str) -> dict[str, Any]:
    data = json.loads(out)
    assert isinstance(data, dict)
    return data


def _changes(data: dict[str, Any]) -> dict[str, Any]:
    return {c["field"]: c["after"] for c in data["changes"]}


def _a2(world: FakeWorld) -> list[Any]:
    return _timeline(world).tracks["audio"][1].items


def _picture(world: FakeWorld, frames: int) -> None:
    """Make the picture exactly `frames` long: one V1 item from the timeline start."""
    _timeline(world).tracks["video"][0].items = []
    _timeline(world).add_item("video", 1, "picture", 86400, frames)


@pytest.mark.parametrize(
    ("picture", "extra", "durations", "tail"),
    [
        (301, [], [100, 100, 100], 1),
        (389, [], [100, 100, 100], 89),
        (390, [], [100, 100, 100, 90], 0),
        (329, ["--fade-out", "0"], [100, 100, 100], 29),
        (330, ["--fade-out", "0"], [100, 100, 100, 30], 0),
    ],
)
def test_a_looped_bed_never_ends_in_a_stub_piece(
    run_cli: Run,
    fake_world: FakeWorld,
    picture: int,
    extra: list[str],
    durations: list[int],
    tail: int,
) -> None:
    _song(fake_world, "song", 100)
    _picture(fake_world, picture)
    _, out, _ = run_cli([*ADD, "song", "--loop", *extra, "--json"])
    dry = _json(out)
    code, out, err = run_cli([*ADD, "song", "--loop", *extra, "--force", "--json"])
    data = _json(out)
    assert (code, err) == (0, "") and len(fake_world.calls_to("MediaPool.AppendToTimeline")) == len(
        durations
    )
    assert _changes(dry)["pieces"] == _changes(data)["pieces"] == len(durations)
    assert [i.duration for i in _a2(fake_world)] == durations and data["holes"] == 0
    assert (dry["tail_frames"], data["tail_frames"]) == (tail, tail)
    if tail:
        assert f"stops {tail} frame(s) before the picture end" in dry["warning"]
        assert f"fade-out is on piece {len(durations)}" in data["warning"]
    else:
        assert "warning" not in dry and "warning" not in data
    want = 0 if extra else min(90, durations[-1] - 1)  # fit_fades keeps one frame of the piece
    assert _a2(fake_world)[-1].fades.get("FadeOut", 0) == want


def test_without_loop_the_tail_rule_does_not_apply(run_cli: Run, fake_world: FakeWorld) -> None:
    _song(fake_world, "song", 100)
    _picture(fake_world, 301)
    code, out, _ = run_cli([*ADD, "song", "--force", "--json"])
    data = _json(out)
    assert code == 0 and _changes(data)["pieces"] == 1 and data["tail_frames"] == 0
    assert "--loop" in data["warning"]


def test_a_flooring_conform_ends_on_a_long_piece_and_reports_the_tail(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = FakeMediaPool.AppendToTimeline

    def flooring(self: FakeMediaPool, infos: list[dict[str, Any]]) -> list[Any]:
        made = original(self, infos)
        for item in made:
            item.duration = int(item.duration * 30 / 23.976)
        return made

    monkeypatch.setattr(FakeMediaPool, "AppendToTimeline", flooring)
    _song(fake_world, "song", 189, fps="23.976")
    _picture(fake_world, 6270)
    dry = _json(run_cli([*ADD, "song", "--loop", "--json"])[1])
    code, out, _ = run_cli([*ADD, "song", "--loop", "--force", "--json"])
    data, calls = _json(out), fake_world.calls_to("MediaPool.AppendToTimeline")
    assert code == 0 and len(calls) == 27 == _changes(dry)["pieces"] == _changes(data)["pieces"]
    last = _a2(fake_world)[-1]
    assert calls[-1][0][0]["endFrame"] == 107 and last.duration == 133
    assert last.fades["FadeOut"] == 90 and (data["tail_frames"], data["holes"]) == (1, 0)


def test_a_wav_bed_over_a_long_picture_keeps_four_pieces(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    project = fake_world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    project.media_pool.root.add_clip(
        "wav", "/media/x/wav.wav", clip_type="Audio", fps="30", duration="00:01:00:00"
    )
    _picture(fake_world, 6270)
    code, out, _ = run_cli([*ADD, "wav", "--loop", "--force", "--json"])
    data, calls = _json(out), fake_world.calls_to("MediaPool.AppendToTimeline")
    assert code == 0 and len(calls) == 4 and calls[-1][0][0]["endFrame"] == 870
    assert data["tail_frames"] == 0 and "warning" not in data


def test_a_single_piece_still_clamps_its_fades(run_cli: Run, fake_world: FakeWorld) -> None:
    _song(fake_world, "short", 100)
    _, out, _ = run_cli([*ADD, "short", "--force", "--json"])
    assert any(c["fade"] == "fade_out" for c in _json(out)["clamped"])
