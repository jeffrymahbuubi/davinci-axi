"""F24 U1: the insert-placement engine and the fake model of the live insert, no CLI."""

from __future__ import annotations

import pytest

from davinci_axi.commands.insert_place import (
    DEFAULT_FRAMES,
    INSERT_FLAGS,
    RECIPE,
    Landing,
    Victims,
    check_insert_flags,
    find_victims,
    landing_extra,
    landing_read,
    landing_warning,
    locate,
    marks_for,
    read_landing,
    refuse_ripple,
    timecode_to_frame,
)
from davinci_axi.commands.render_frame import frame_to_timecode
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeProject, FakeTimeline, FakeWorld, ext_edit_extra
from davinci_axi.fake.insert_place import place_insert
from davinci_axi.mutate import MutationPlan
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.spec import GlobalOpts, Invocation


@pytest.fixture(autouse=True)
def _extend() -> None:
    ext_edit_extra.extend()


def _edit(world: FakeWorld) -> FakeTimeline:
    project = world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    return next(t for t in project.timelines if t.name == "Edit v1")


def _inv(**args: object) -> Invocation:
    def no_connect() -> object:
        raise AssertionError("the engine must not connect")

    return Invocation(GlobalOpts(), dict(args), {}, no_connect)  # type: ignore[arg-type]


def _api(timeline: FakeTimeline) -> TimelineApi:
    return timeline  # type: ignore[return-value]


def _count(timeline: FakeTimeline, kind: str, index: int) -> int:
    return len(timeline.tracks[kind][index - 1].items)


def test_timecode_to_frame_counts_from_midnight() -> None:
    assert timecode_to_frame("01:00:02:00", 24) == 86448
    assert timecode_to_frame("01:00:02:00", 23.976) == 86448
    assert timecode_to_frame("00:00:00:05", 25) == 5


@pytest.mark.parametrize("text", ["1:2", "01:00:02", "abc", "01:00:02:24", "01:00:02;00"])
def test_timecode_to_frame_rejects_bad_text_and_drop_frame(text: str) -> None:
    with pytest.raises(AxiError) as caught:
        timecode_to_frame(text, 24)
    assert caught.value.code == "VALIDATION_ERROR"


def test_insert_flags_are_the_four_documented_ones() -> None:
    assert [f.name for f in INSERT_FLAGS] == ["at", "frame", "duration", "allow-ripple"]
    assert [f.kind for f in INSERT_FLAGS] == ["str", "int", "int", "bool"]
    assert "timeline compound" in RECIPE and "--record-frame" in RECIPE


@pytest.mark.parametrize(
    "args",
    [
        {"at": "01:00:02:00", "frame": 86448},
        {"at": "1:2"},
        {"at": "01:00:02;00"},
        {"frame": -1},
        {"duration": 0},
    ],
)
def test_check_insert_flags_fails_before_any_connection(args: dict[str, object]) -> None:
    with pytest.raises(AxiError) as caught:
        check_insert_flags(_inv(**args))
    assert caught.value.code == "VALIDATION_ERROR"


def test_read_landing_uses_the_playhead_by_default(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    edit.current_tc = "01:00:00:15"
    landing = read_landing(_inv(), _api(edit))
    assert landing == Landing(86415, "01:00:00:15", None, "playhead")


def test_read_landing_follows_the_video_marks(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    edit.mark_in_out = {"video": {"in": 48, "out": 107}}  # relative to the 86400 start
    landing = read_landing(_inv(), _api(edit))
    assert landing == Landing(86448, "01:00:02:00", 60, "marks")


def test_read_landing_from_flags(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    by_at = read_landing(_inv(at="01:00:02:00", duration=60), _api(edit))
    assert by_at == Landing(86448, "01:00:02:00", 60, "flags")
    by_frame = read_landing(_inv(frame=86448), _api(edit))
    assert by_frame == Landing(86448, "01:00:02:00", DEFAULT_FRAMES, "flags")
    by_duration = read_landing(_inv(duration=30), _api(edit))  # playhead position, own length
    assert by_duration == Landing(86400, "01:00:00:00", 30, "flags")


def test_read_landing_duration_alone_starts_at_the_mark_in(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    edit.mark_in_out = {"video": {"in": 48, "out": 107}}
    assert read_landing(_inv(duration=10), _api(edit)) == Landing(86448, "01:00:02:00", 10, "flags")
    assert read_landing(_inv(frame=86400), _api(edit)).length == 60  # frame alone keeps mark length


def test_read_landing_refuses_flags_on_a_drop_frame_timeline(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    edit.start_tc = "01:00:00;00"
    with pytest.raises(AxiError) as caught:
        read_landing(_inv(at="01:00:02:00"), _api(edit))
    assert caught.value.code == "VALIDATION_ERROR"


def test_find_victims_at_a_mid_clip_frame(fake_world: FakeWorld) -> None:
    victims = find_victims(_api(_edit(fake_world)), 86415)
    assert victims.splits == ["V1:1", "A1:1"]
    assert victims.shifted == ["V1:2", "V1:3", "V2:1", "A1:2"]


def test_find_victims_past_the_last_item_is_empty(fake_world: FakeWorld) -> None:
    assert find_victims(_api(_edit(fake_world)), 90000) == Victims([], [])


def test_find_victims_at_a_cut_shifts_without_splitting(fake_world: FakeWorld) -> None:
    victims = find_victims(_api(_edit(fake_world)), 86480)
    assert victims.splits == ["V2:1", "A1:1"]
    assert "V1:2" in victims.shifted and "V1:1" not in victims.shifted


def test_find_victims_skips_locked_tracks(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    edit.tracks["audio"][0].locked = True
    victims = find_victims(_api(edit), 86415)
    assert not any(label.startswith("A") for label in victims.splits + victims.shifted)


def test_refuse_ripple_names_the_victims_and_the_recipe() -> None:
    landing = Landing(86415, "01:00:00:15", None, "playhead")
    with pytest.raises(AxiError) as caught:
        refuse_ripple(_inv(), landing, Victims(["V1:1"], ["V1:2", "A1:2"]), "fusion insert title X")
    err = caught.value
    assert err.code == "WRITE_REFUSED"
    assert "V1:1" in err.message and "86415" in err.message and "shift 2 items" in err.message
    assert err.help[0] == "Pass --allow-ripple to do it anyway"
    assert "fusion insert title X --force" in err.help[1]
    assert "timeline compound" in err.help[1] and "--record-frame" in err.help[1]


def test_refuse_ripple_passes_when_allowed_or_nothing_is_hit() -> None:
    landing = Landing(86415, "01:00:00:15", None, "playhead")
    refuse_ripple(_inv(allow_ripple=True), landing, Victims(["V1:1"], ["V1:2"]), "x")
    refuse_ripple(_inv(), landing, Victims([], []), "x")


def test_landing_warning_and_extra() -> None:
    playhead = Landing(86415, "01:00:00:15", None, "playhead")
    assert landing_warning(playhead) == (
        "lands on V1 at 86415 (01:00:00:15), about 150 frames (from playhead); "
        "the API takes no track and ripple-inserts across unlocked tracks"
    )
    extra = landing_extra(playhead, Victims(["V1:1", "A1:1"], ["V1:2"]), {})
    assert extra == {
        "landing": "V1@86415 (01:00:00:15)",
        "frames": "about 150 (Standard Generator Duration)",
        "splits": "V1:1, A1:1",
        "shifted": 1,
        "marks_before": "none",
    }
    marked = Landing(86448, "01:00:02:00", 60, "marks")
    extra = landing_extra(marked, Victims([], []), {"video": {"in": 48, "out": 107}})
    assert (extra["frames"], extra["splits"], extra["marks_before"]) == (
        60,
        "none",
        "48-107 (timeline-relative)",
    )
    assert "60 frames (from marks)" in landing_warning(marked)


def test_marks_for_is_a_no_op_unless_the_landing_came_from_flags(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    plan = MutationPlan("x", "t", [], "x --force")
    with marks_for(_api(edit), Landing(86448, "01:00:02:00", 60, "marks"), plan):
        pass
    methods = [m for _, m, _ in fake_world.calls]
    assert "SetMarkInOut" not in methods and "ClearMarkInOut" not in methods
    assert "marks_restored" not in plan.extra


def test_marks_for_sets_then_clears_when_there_were_no_marks(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    plan = MutationPlan("x", "t", [], "x --force")
    with marks_for(_api(edit), Landing(86448, "01:00:02:00", 60, "flags"), plan):
        assert edit.mark_in_out["video"] == {"in": 48, "out": 107}  # relative, not 86448
    assert edit.mark_in_out == {}
    assert plan.extra["marks_set"] == "48-107 (timeline-relative)"
    assert plan.extra["marks_restored"] is True
    calls = [(m, a) for _, m, a in fake_world.calls if m in ("SetMarkInOut", "ClearMarkInOut")]
    assert calls == [("SetMarkInOut", (48, 107, "video")), ("ClearMarkInOut", ("video",))]


def test_marks_for_puts_old_marks_back_even_when_the_call_raises(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    edit.mark_in_out = {"video": {"in": 86400, "out": 86500}}
    plan = MutationPlan("x", "t", [], "x --force")
    with (
        pytest.raises(RuntimeError),
        marks_for(_api(edit), Landing(86448, "01:00:02:00", 60, "flags"), plan),
    ):
        raise RuntimeError("boom")
    assert edit.mark_in_out == {"video": {"in": 86400, "out": 86500}}
    assert plan.extra["marks_restored"] is True


def test_marks_for_fails_when_the_marks_cannot_be_set(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    fake_world.inject("Timeline.SetMarkInOut", False)
    plan = MutationPlan("x", "t", [], "x --force")
    with (
        pytest.raises(AxiError) as caught,
        marks_for(_api(edit), Landing(86448, "01:00:02:00", 60, "flags"), plan),
    ):
        raise AssertionError("must not run")
    assert caught.value.code == "API_FAILED"
    assert "marks_restored" not in plan.extra


def test_place_insert_at_the_playhead_splits_and_ripples(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    edit.current_tc = "01:00:00:15"
    item = place_insert(edit, "Text+", "Title")
    assert item is not None
    assert (item.start, item.duration, item.kind) == (86415, 150, "Title")
    assert (_count(edit, "video", 1), _count(edit, "audio", 1)) == (3 + 2, 2 + 1)
    v1 = sorted(edit.tracks["video"][0].items, key=lambda i: i.start)
    assert [(i.name, i.start, i.duration) for i in v1] == [
        ("clip-1", 86400, 15),
        ("Text+", 86415, 150),
        ("clip-1", 86565, 65),
        ("clip-2", 86630, 80),
        ("clip-3", 86710, 80),
    ]
    assert v1[2].source_start == 15
    assert edit.tracks["video"][1].items[0].start == 86440 + 150  # V2 title shifted
    a1 = sorted(edit.tracks["audio"][0].items, key=lambda i: i.start)
    assert [(i.start, i.duration) for i in a1] == [(86400, 15), (86565, 105), (86670, 120)]
    assert edit.end_frame == 86400 + 240 + 150


def test_place_insert_follows_the_video_marks(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    edit.mark_in_out = {"video": {"in": 48, "out": 107}}
    item = place_insert(edit, "Text+", "Title")
    assert item is not None
    assert (item.start, item.duration) == (86448, 60)


def test_place_insert_on_an_empty_timeline_moves_nothing(fake_world: FakeWorld) -> None:
    project = fake_world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    scratch = project.add_timeline("scratch")
    scratch.add_track("video")
    scratch.add_track("audio")
    item = place_insert(scratch, "Text+", "Title")
    assert item is not None
    assert (item.start, item.duration) == (86400, 150)
    assert (_count(scratch, "video", 1), _count(scratch, "audio", 1)) == (1, 0)


def test_place_insert_on_a_locked_or_missing_v1_returns_none(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    edit.tracks["video"][0].locked = True
    before = _count(edit, "video", 1)
    assert place_insert(edit, "Text+", "Title") is None
    assert _count(edit, "video", 1) == before
    edit.tracks["video"][0].locked = False
    assert place_insert(edit, "", "Title") is None
    edit.tracks["video"].clear()
    assert place_insert(edit, "Text+", "Title") is None


def test_place_insert_leaves_locked_tracks_alone(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    edit.current_tc = "01:00:00:15"
    edit.tracks["audio"][0].locked = True
    place_insert(edit, "Text+", "Title")
    assert [(i.start, i.duration) for i in edit.tracks["audio"][0].items] == [
        (86400, 120),
        (86520, 120),
    ]


def test_landing_read_and_locate_find_the_new_item(fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    edit.current_tc = "01:00:00:15"
    item = place_insert(edit, "Text+", "Title")
    assert item is not None
    loc = locate(_api(edit), item.uid)
    assert loc is not None and loc.item.GetUniqueId() == item.uid
    assert landing_read(_api(edit), item.uid) == "V1@86415"
    item.track_index = 2  # as if Resolve had put it elsewhere
    edit.tracks["video"][0].items.remove(item)
    edit.tracks["video"][1].items.append(item)
    assert landing_read(_api(edit), item.uid) == "V2@86415"
    assert landing_read(_api(edit), "item-999") is None
    assert landing_read(_api(edit), None) is None


# F32: Resolve reads and writes marks relative to the timeline start. These run on a timeline that
# starts at 108000 (01:15:00:00 at 24 fps), where relative and absolute frames cannot coincide.
LATE_START = 108000


def _late(world: FakeWorld) -> FakeTimeline:
    project = world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    late = project.add_timeline(
        "late", current=True, start_frame=LATE_START, start_tc="01:15:00:00", frames=240
    )
    late.add_track("video")
    late.add_track("audio")
    return late


def test_read_landing_marks_are_relative_to_the_timeline_start(fake_world: FakeWorld) -> None:
    late = _late(fake_world)
    late.mark_in_out = {"video": {"in": 100, "out": 200}}
    landing = read_landing(_inv(), _api(late))
    assert landing == Landing(108100, frame_to_timecode(108100, 24), 101, "marks")
    assert landing.tc == "01:15:04:04"


def test_read_landing_from_flags_is_absolute(fake_world: FakeWorld) -> None:
    late = _late(fake_world)
    by_at = read_landing(_inv(at="01:15:02:00", duration=60), _api(late))
    assert by_at == Landing(108048, "01:15:02:00", 60, "flags")
    assert read_landing(_inv(frame=108048, duration=60), _api(late)).frame == 108048
    late.mark_in_out = {"video": {"in": 60, "out": 120}}
    assert read_landing(_inv(duration=10), _api(late)) == Landing(
        108060, frame_to_timecode(108060, 24), 10, "flags"
    )


@pytest.mark.parametrize("args", [{"frame": 107000}, {"at": "01:00:02:00", "duration": 60}])
def test_read_landing_before_the_timeline_start_is_a_validation_error(
    fake_world: FakeWorld, args: dict[str, object]
) -> None:
    late = _late(fake_world)
    with pytest.raises(AxiError) as caught:
        read_landing(_inv(**args), _api(late))
    assert caught.value.code == "VALIDATION_ERROR"
    assert "before the timeline start 108000" in caught.value.message
    assert fake_world.write_calls() == []


def test_marks_for_writes_relative_marks_and_the_insert_lands_where_predicted(
    fake_world: FakeWorld,
) -> None:
    late = _late(fake_world)
    landing = read_landing(_inv(at="01:15:02:00", duration=60), _api(late))
    plan = MutationPlan("x", "t", [], "x --force")
    with marks_for(_api(late), landing, plan):
        assert late.mark_in_out["video"] == {"in": 48, "out": 107}
        item = place_insert(late, "Text+", "Title")
    assert item is not None and (item.start, item.duration) == (108048, 60)
    assert landing_read(_api(late), item.uid) == "V1@108048"
    assert plan.extra["marks_set"] == "48-107 (timeline-relative)"
    assert plan.extra["marks_restored"] is True and late.mark_in_out == {}
    calls = [(m, a) for _, m, a in fake_world.calls if m in ("SetMarkInOut", "ClearMarkInOut")]
    assert calls == [("SetMarkInOut", (48, 107, "video")), ("ClearMarkInOut", ("video",))]


def test_marks_for_restores_the_previous_relative_marks_untouched(fake_world: FakeWorld) -> None:
    late = _late(fake_world)
    late.mark_in_out = {"video": {"in": 10, "out": 20}}
    plan = MutationPlan("x", "t", [], "x --force")
    landing = Landing(108048, "01:15:02:00", 60, "flags")
    with marks_for(_api(late), landing, plan):
        pass
    assert late.mark_in_out["video"] == {"in": 10, "out": 20}
    assert fake_world.calls_to("Timeline.SetMarkInOut") == [(48, 107, "video"), (10, 20, "video")]
    assert plan.extra["marks_restored"] is True


def test_hand_set_marks_60_to_120_land_at_108060_with_61_frames(fake_world: FakeWorld) -> None:
    late = _late(fake_world)
    late.mark_in_out = {"video": {"in": 60, "out": 120}}
    landing = read_landing(_inv(), _api(late))
    assert (landing.frame, landing.length, landing.source) == (108060, 61, "marks")
    plan = MutationPlan("x", "t", [], "x --force")
    with marks_for(_api(late), landing, plan):  # a no-op: the marks already say it
        item = place_insert(late, "Text+", "Title")
    assert item is not None and (item.start, item.duration) == (108060, 61)
    assert fake_world.calls_to("Timeline.SetMarkInOut") == []


def test_marks_for_refuses_a_landing_before_the_start(fake_world: FakeWorld) -> None:
    late = _late(fake_world)
    plan = MutationPlan("x", "t", [], "x --force")
    with (
        pytest.raises(AxiError) as caught,
        marks_for(_api(late), Landing(107000, "x", 60, "flags"), plan),
    ):
        raise AssertionError("must not run")
    assert caught.value.code == "VALIDATION_ERROR"
    assert fake_world.calls_to("Timeline.SetMarkInOut") == []
