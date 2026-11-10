"""Round 3 U2: `timeline music add` lays a music bed in one mutation (fake world, 30 fps).

`timeline_extra.NEW` does not carry `music` until the lead splices it, so the tests wrap the spec
locally and point the registry at this module's `SPEC`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.commands import timeline_extra, timeline_music, timeline_music_pieces
from davinci_axi.commands.timeline_music import MUSIC_SPEC, holes_in, next_piece, occupied, pieces
from davinci_axi.commands.timeline_music_length import clip_length, timecode_frames
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
    """The default world runs at 24 fps; the numbers here are worked out at 30."""
    _timeline(fake_world).settings.update({"useCustomSettings": "1", "timelineFrameRate": 30})
    _timeline(fake_world).add_track("audio")  # A2, empty


def _timeline(world: FakeWorld) -> FakeTimeline:
    project = world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    return next(t for t in project.timelines if t.name == "Edit v1")


def _song(
    world: FakeWorld, name: str, frames: int, *, fps: str = "30", kind: str = "Audio"
) -> FakeMediaPoolItem:
    project = world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    return project.media_pool.root.add_clip(
        name,
        f"/media/davinci-axi-test/{name}.wav",
        clip_type=kind,
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


def test_pieces_table() -> None:
    assert pieces(300, 240, False) == [(0, 240)]
    assert pieces(100, 240, False) == [(0, 100)]
    assert pieces(300, 240, True) == [(0, 240)]
    assert pieces(100, 240, True) == [(0, 100), (0, 100), (0, 40)]
    assert pieces(100, 200, True) == [(0, 100), (0, 100)]
    assert pieces(0, 240, True) == [] and pieces(100, 0, True) == []


def test_pieces_min_tail_and_the_split() -> None:
    assert pieces(100, 301, True, 30) == [(0, 100)] * 3
    assert len(pieces(100, 390, True, 90)) == 4 and len(pieces(100, 389, True, 90)) == 3
    assert pieces(100, 40, True, 90) == [(0, 40)]  # the first piece is always placed
    assert pieces(100, 240, True) == [(0, 100), (0, 100), (0, 40)]  # default min_tail=1
    assert timeline_music.next_piece is timeline_music_pieces.next_piece
    assert timeline_music.holes_in is timeline_music_pieces.holes_in
    assert (timeline_music_pieces.stub_frames(0), timeline_music_pieces.stub_frames(90)) == (30, 90)


def test_force_appends_one_trimmed_piece_with_volume_and_fades(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _song(fake_world, "song", 300)
    code, out, err = run_cli([*ADD, "song", "--force", "--json"])
    data = _json(out)
    assert (code, err) == (0, "") and data["applied"] is True
    calls = fake_world.calls_to("MediaPool.AppendToTimeline")
    assert len(calls) == 1
    (infos,) = calls[0]
    assert len(infos) == 1
    info = infos[0]
    assert {k: v for k, v in info.items() if k != "mediaPoolItem"} == {
        "trackIndex": 2,
        "recordFrame": 86400,
        "startFrame": 0,
        "endFrame": 240,
        "mediaType": 2,
    }
    item = _a2(fake_world)[0]
    assert (item.start, item.duration) == (86400, 240)
    assert item.properties["AudioVolume"] == -18.0
    assert (item.fades["FadeIn"], item.fades["FadeOut"]) == (30, 90)
    assert data["covered_pct"] == 100.0 and data["track"] == "A2" and data["clip"] == "song"
    assert _changes(data) == {
        "pieces": 1,
        "A2 frames": 240,
        "AudioVolume": -18.0,
        "FadeIn": 30,
        "FadeOut": 90,
    }
    assert data["items"][0]["item"] == "A2:1" and data["items"][0]["end"] == 86640


def test_dry_run_shows_the_plan_and_writes_nothing(run_cli: Run, fake_world: FakeWorld) -> None:
    _song(fake_world, "song", 300)
    code, out, _ = run_cli([*ADD, "song", "--json"])
    data = _json(out)
    assert code == 0 and data["applied"] is False
    assert _changes(data) == {
        "pieces": 1,
        "A2 frames": 240,
        "AudioVolume": -18.0,
        "FadeIn": 30,
        "FadeOut": 90,
    }
    assert (data["clip_frames"], data["picture_frames"], data["loop"]) == (300, 240, False)
    assert "timeline music add song --track 2 --db -18" in data["help"][0]
    assert fake_world.write_calls() == []
    code, out, _ = run_cli([*ADD, "song", "--force", "--json"])
    helps = " ".join(_json(out)["help"])
    assert "audio loudness --track 2" in helps and "audio gaps" in helps
    assert "timeline check --audio --lufs -14" in helps


def test_loop_makes_three_pieces_with_fades_only_at_the_ends(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _song(fake_world, "short", 100)
    code, out, _ = run_cli([*ADD, "short", "--loop", "--fade-out", "30", "--force", "--json"])
    data = _json(out)
    assert code == 0 and _changes(data)["pieces"] == 3
    assert _changes(data)["FadeOut"] == 30
    calls = fake_world.calls_to("MediaPool.AppendToTimeline")
    assert [len(c[0]) for c in calls] == [1, 1, 1]
    assert [(i["recordFrame"], i["startFrame"], i["endFrame"]) for (c,) in calls for i in c] == [
        (86400, 0, 100),
        (86500, 0, 100),
        (86600, 0, 40),
    ]
    assert data["holes"] == 0
    assert (_changes(data)["pieces"], _changes(data)["A2 frames"]) == (3, 240)
    first, middle, last = _a2(fake_world)
    assert first.fades["FadeIn"] == 30 and first.fades.get("FadeOut", 0) == 0
    assert middle.fades.get("FadeIn", 0) == 0 and middle.fades.get("FadeOut", 0) == 0
    assert last.fades["FadeOut"] == 30 and last.fades.get("FadeIn", 0) == 0
    assert all(i.properties["AudioVolume"] == -18.0 for i in (first, middle, last))
    assert [row["item"] for row in data["items"]] == ["A2:1", "A2:2", "A2:3"]
    assert "clamped" not in data and data["tail_frames"] == 0


def test_without_loop_a_short_song_covers_part_and_hints_the_loop(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _song(fake_world, "short", 100)
    code, out, _ = run_cli([*ADD, "short", "--json"])
    data = _json(out)
    assert code == 0 and data["covered_pct"] == 41.7
    assert "--loop" in data["warning"] and "41.7%" in data["warning"]
    code, out, _ = run_cli([*ADD, "short", "--force", "--json"])
    assert any("timeline music add short --loop" in h for h in _json(out)["help"])


def test_an_occupied_track_is_refused_before_any_write(run_cli: Run, fake_world: FakeWorld) -> None:
    _song(fake_world, "song", 300)
    code, out, err = run_cli([*ADD, "song", "--track", "1", "--force", "--json"])
    assert code == 1 and "WRITE_REFUSED" in out + err
    assert "A1 is occupied" in out + err and "--track 2" in out + err
    assert fake_world.calls_to("MediaPool.AppendToTimeline") == []
    assert fake_world.write_calls() == []


def test_a_span_that_ends_before_the_occupant_is_free(run_cli: Run, fake_world: FakeWorld) -> None:
    _song(fake_world, "song", 100)
    _timeline(fake_world).add_item("audio", 2, "later", 86500, 100)
    code, _, _ = run_cli([*ADD, "song", "--force", "--json"])
    assert code == 0
    assert occupied(_timeline(fake_world), 2, 86400, 86500) != []
    code, out, err = run_cli([*ADD, "song", "--loop", "--json"])
    assert code == 1 and "WRITE_REFUSED" in out + err


def test_a_missing_track_is_not_found(run_cli: Run, fake_world: FakeWorld) -> None:
    _song(fake_world, "song", 300)
    code, out, err = run_cli([*ADD, "song", "--track", "9", "--force"])
    assert code == 1 and "NOT_FOUND" in out + err
    assert "timeline track add audio" in out + err
    assert fake_world.write_calls() == []


def test_bad_flags_exit_two_before_connecting(run_cli: Run, fake_world: FakeWorld) -> None:
    for argv in (
        ["song", "--db", "40"],
        ["song", "--db", "-101"],
        ["song", "--track", "0"],
        ["song", "--fade-in", "-1"],
        ["song", "--at", "01:00:02:00", "--frame", "86460"],
        ["song", "--at", "1:2"],
    ):
        code, out, err = run_cli([*ADD, *argv, "--force"])
        assert code == 2, argv
        assert "help" in out + err
    assert fake_world.write_calls() == []


def test_a_clip_without_audio_is_refused(run_cli: Run, fake_world: FakeWorld) -> None:
    _song(fake_world, "still", 100, kind="Still")
    code, out, err = run_cli([*ADD, "still", "--force"])
    assert code == 2 and "has no audio (Type Still)" in out + err
    assert "media --fields name,type" in out + err
    assert fake_world.write_calls() == []


def test_two_clips_are_refused(run_cli: Run, fake_world: FakeWorld) -> None:
    _song(fake_world, "one", 100)
    _song(fake_world, "two", 100)
    code, out, err = run_cli([*ADD, "one,two", "--force"])
    assert code == 2 and "name one clip" in out + err


def test_no_video_means_nothing_to_lay_music_under(run_cli: Run, fake_world: FakeWorld) -> None:
    _song(fake_world, "song", 300)
    for track in _timeline(fake_world).tracks["video"]:
        track.items = []
    code, out, err = run_cli([*ADD, "song", "--force"])
    assert code == 2 and "nothing to lay music under" in out + err
    assert "timeline append" in out + err


def test_at_sets_the_record_frame_and_shortens_the_bed(run_cli: Run, fake_world: FakeWorld) -> None:
    _song(fake_world, "song", 300)
    _, out, _ = run_cli([*ADD, "song", "--at", "00:48:02:00", "--json"])
    assert "--at 00:48:02:00" in _json(out)["help"][0]
    code, out, _ = run_cli([*ADD, "song", "--at", "00:48:02:00", "--force", "--json"])
    data = _json(out)
    assert code == 0 and data["covered_pct"] == 75.0
    (infos,) = fake_world.calls_to("MediaPool.AppendToTimeline")[0]
    assert (infos[0]["recordFrame"], infos[0]["endFrame"]) == (86400 + 60, 180)


def test_a_frame_past_the_picture_is_refused(run_cli: Run, fake_world: FakeWorld) -> None:
    _song(fake_world, "song", 300)
    code, out, err = run_cli([*ADD, "song", "--frame", "86640", "--force"])
    assert code == 2 and "outside the picture" in out + err
    assert fake_world.write_calls() == []
    code, out, err = run_cli([*ADD, "song", "--frame", "86000", "--force"])
    assert code == 2 and "86400-86640" in out + err


def test_a_clip_at_another_rate_is_scaled(run_cli: Run, fake_world: FakeWorld) -> None:
    _song(fake_world, "slow", 300, fps="24")
    _, out, _ = run_cli([*ADD, "slow", "--json"])
    dry = _json(out)
    assert "rate_scaled" in dry and dry["covered_pct"] == 100.0
    assert _changes(dry)["A2 frames"] == 240
    code, out, _ = run_cli([*ADD, "slow", "--force", "--json"])
    data = _json(out)
    assert code == 0 and data["planned_frames"] == 240
    (infos,) = fake_world.calls_to("MediaPool.AppendToTimeline")[0]
    assert infos[0]["endFrame"] == 192
    # the fake keeps the clip's frames; live Resolve conforms the piece to 240
    assert _changes(data)["A2 frames"] == 192 and "warning" in data


def _conform(monkeypatch: pytest.MonkeyPatch, ratio: float) -> None:
    """Make the fake conform each appended piece to the timeline rate, as live Resolve does."""
    original = FakeMediaPool.AppendToTimeline

    def conformed(self: FakeMediaPool, infos: list[dict[str, Any]]) -> list[Any]:
        made = original(self, infos)
        for item in made:
            item.duration = round(item.duration * ratio)
        return made

    monkeypatch.setattr(FakeMediaPool, "AppendToTimeline", conformed)


def test_a_looped_clip_at_another_rate_chains_pieces_on_the_read_back_end(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    _conform(monkeypatch, 1.25)
    _song(fake_world, "slow", 80, fps="24")
    code, out, _ = run_cli([*ADD, "slow", "--loop", "--fade-out", "30", "--force", "--json"])
    data = _json(out)
    assert code == 0
    calls = fake_world.calls_to("MediaPool.AppendToTimeline")
    assert [len(c[0]) for c in calls] == [1, 1, 1]
    infos = [c[0][0] for c in calls]
    assert [(i["recordFrame"], i["startFrame"], i["endFrame"]) for i in infos] == [
        (86400, 0, 80),
        (86500, 0, 80),
        (86600, 0, 32),
    ]
    pieces_made = _a2(fake_world)
    assert [i.start for i in pieces_made[1:]] == [p.start + p.duration for p in pieces_made[:-1]]
    assert (data["holes"], _changes(data)["pieces"], data["covered_pct"]) == (0, 3, 100.0)
    assert _changes(data)["A2 frames"] == pieces_made[-1].start + pieces_made[-1].duration - 86400
    assert _changes(data)["A2 frames"] == 240
    assert "warning" not in data


def test_an_empty_second_append_names_the_piece_and_frame(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    _song(fake_world, "short", 100)
    original, count = FakeMediaPool.AppendToTimeline, []

    def second_fails(self: FakeMediaPool, infos: list[dict[str, Any]]) -> list[Any]:
        count.append(1)
        return [] if len(count) == 2 else original(self, infos)

    monkeypatch.setattr(FakeMediaPool, "AppendToTimeline", second_fails)
    code, out, err = run_cli([*ADD, "short", "--loop", "--force"])
    assert code == 1 and "API_FAILED" in out + err
    assert "piece 2 at 86500" in out + err and "snapshot restore" in out + err


def test_a_conform_that_never_reaches_the_end_stops_at_the_piece_cap(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    _conform(monkeypatch, 0.2)
    _song(fake_world, "short", 100)
    code, out, err = run_cli([*ADD, "short", "--loop", "--force"])
    assert code == 1 and "more pieces than planned" in out + err


def test_an_audio_only_clip_takes_its_length_from_duration_and_fps(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    project = fake_world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    project.media_pool.root.add_clip(
        "wav",
        "/media/davinci-axi-test/wav.wav",
        clip_type="Audio",
        fps="30",
        duration="00:01:00:00",
    )
    assert project.media_pool.root.clips[-1].GetClipProperty("Frames") == ""
    code, out, _ = run_cli([*ADD, "wav", "--force", "--json"])
    data = _json(out)
    assert code == 0
    assert (data["clip_frames"], data["clip_frames_from"]) == (1800, "Duration x FPS")
    (infos,) = fake_world.calls_to("MediaPool.AppendToTimeline")[0]
    assert (infos[0]["startFrame"], infos[0]["endFrame"]) == (0, 240)
    assert data["covered_pct"] == 100.0 and "rate_scaled" not in data


def test_an_audio_only_clip_at_24_fps_is_rate_scaled(run_cli: Run, fake_world: FakeWorld) -> None:
    project = fake_world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    project.media_pool.root.add_clip(
        "wav",
        "/media/davinci-axi-test/wav.wav",
        clip_type="Audio",
        fps="24",
        duration="00:00:10:00",
    )
    code, out, _ = run_cli([*ADD, "wav", "--json"])
    data = _json(out)
    assert code == 0 and (data["clip_frames"], data["clip_frames_from"]) == (240, "Duration x FPS")
    assert "rate_scaled" in data and data["covered_pct"] == 100.0
    assert fake_world.calls_to("MediaPool.AppendToTimeline") == []


def test_a_drop_frame_duration_is_read(run_cli: Run, fake_world: FakeWorld) -> None:
    project = fake_world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    project.media_pool.root.add_clip(
        "wav",
        "/media/davinci-axi-test/wav.wav",
        clip_type="Audio",
        fps="30",
        duration="00;00;10;00",
    )
    _, out, _ = run_cli([*ADD, "wav", "--json"])
    assert _json(out)["clip_frames"] == 300


def test_a_clip_with_no_length_anywhere_points_at_media_show(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    project = fake_world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    project.media_pool.root.add_clip(
        "wav", "/media/davinci-axi-test/wav.wav", clip_type="Audio", fps="30", duration=""
    )
    code, out, err = run_cli([*ADD, "wav", "--force"])
    assert code == 2 and "cannot read the length of wav" in out + err
    assert "Frames is empty" in out + err and "media show" in out + err
    assert fake_world.write_calls() == []


def test_timecode_frames_and_clip_length_table() -> None:
    assert timecode_frames("00:01:00:00", 30) == 1800
    assert timecode_frames("00;00;10;05", 29.97) == 305
    assert timecode_frames("00:00:10:00", 23.976) == 240
    assert timecode_frames("junk", 30) is None and timecode_frames("00:00:10:00", 0) is None
    video = SimpleNamespace(GetClipProperty=lambda key: {"Frames": "240"}.get(key, ""))
    assert clip_length(video) == (240, "Frames")


def test_next_piece_and_holes_table() -> None:
    assert next_piece(100, 240, 1.0) == (0, 100)
    assert next_piece(100, 40, 1.0) == (0, 40)
    assert next_piece(96, 240, 1.25) == (0, 96)
    assert next_piece(96, 10, 1.25) == (0, 8)
    assert next_piece(96, 0, 1.25) == (0, 1)

    def one(start: int, end: int) -> Any:
        return SimpleNamespace(GetStart=lambda: start, GetEnd=lambda: end)

    assert holes_in([one(0, 10), one(10, 20)]) == 0
    assert holes_in([one(11, 20), one(0, 10)]) == 1
    assert holes_in([one(0, 10), one(12, 20), one(25, 30)]) == 7
    assert holes_in([]) == 0


def test_refused_append_is_an_api_error(run_cli: Run, fake_world: FakeWorld) -> None:
    _song(fake_world, "song", 300)
    fake_world.inject("MediaPool.AppendToTimeline", [])
    code, out, err = run_cli([*ADD, "song", "--force"])
    assert code == 1 and "API_FAILED" in out + err
    assert "appended nothing" in out + err


def test_refused_volume_points_at_audio_gain(run_cli: Run, fake_world: FakeWorld) -> None:
    _song(fake_world, "song", 300)
    fake_world.inject("TimelineItem.SetProperties", False)
    code, out, err = run_cli([*ADD, "song", "--force"])
    assert code == 1 and "API_FAILED" in out + err
    assert "audio gain" in out + err
