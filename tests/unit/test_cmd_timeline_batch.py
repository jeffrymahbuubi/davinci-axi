"""`timeline transitions` and `timeline fades`: many items, one snapshot, on the fake (U4)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import timeline_extra
from davinci_axi.commands.item_detail import Located
from davinci_axi.commands.timeline_batch import (
    FADES_SPEC,
    TRANSITIONS_SPEC,
    cuts_on,
    fit_fades,
    select_items,
)
from davinci_axi.fake import FakeProject, FakeWorld
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem
from davinci_axi.spec import CommandSpec, GlobalOpts, Invocation

Run = Callable[[list[str]], tuple[int, str, str]]

ADD = "TimelineItem.AddTransition"
SET_FADES = "TimelineItem.SetFades"
SPEC: CommandSpec = replace(
    timeline_extra.SPEC,
    subcommands={
        **timeline_extra.SPEC.subcommands,
        "transitions": TRANSITIONS_SPEC,
        "fades": FADES_SPEC,
    },
)
DISSOLVE = ["timeline", "transitions", "--type", "Cross Dissolve", "--duration", "12"]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "timeline", f"{__name__}:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")


def _edit(world: FakeWorld) -> FakeTimeline:
    project: FakeProject | None = world.resolve.pm.current_project
    assert project is not None
    return project.timelines[0]


def _track(world: FakeWorld, kind: str) -> list[FakeTimelineItem]:
    return _edit(world).tracks[kind][0].items


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _inv(world: FakeWorld, **args: object) -> Invocation:
    def connect() -> object:
        raise AssertionError("not connected")

    return Invocation(GlobalOpts(), dict(args), {}, connect)  # type: ignore[arg-type]


# ---- transitions ------------------------------------------------------------------------------


def test_dry_run_lists_the_cuts_and_writes_nothing(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli([*DISSOLVE, "--json"])
    assert (code, err) == (0, "")
    data = json.loads(out)
    assert data["applied"] is False
    assert data["cuts"] == [
        {"at": 86480, "left": "V1:1", "right": "V1:2"},
        {"at": 86560, "left": "V1:2", "right": "V1:3"},
    ]
    assert [row["field"] for row in data["changes"]] == ["V1:2 clip-2", "V1:3 clip-3"]
    assert "later item refs" in data["warning"]
    assert fake_world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


def test_force_adds_every_transition_under_one_snapshot(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli([*DISSOLVE, "--force", "--json"])
    assert (code, err) == (0, "")
    data = json.loads(out)
    assert data["applied"] is True
    assert (
        fake_world.calls_to(ADD)
        == [
            (
                {
                    "type": "Cross Dissolve",
                    "category": "simple",
                    "position": "start",
                    "alignment": "center",
                    "duration": 12,
                },
            )
        ]
        * 2
    )
    assert len(_track(fake_world, "video")) == 5
    assert [d.name for d in _snapshots(tmp_path).iterdir()] == [data["snapshot"]]


def test_running_it_again_finds_no_cut_to_dissolve(run_cli: Run) -> None:
    assert run_cli([*DISSOLVE, "--force"])[0] == 0
    code, out, _ = run_cli([*DISSOLVE, "--json"])
    assert code == 0
    data = json.loads(out)
    assert data["cuts"] == [] and data["changes"] == []
    assert "no cuts" in data["note"]


def test_a_refused_cut_does_not_stop_the_rest_and_ends_in_api_failed(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject(ADD, None)
    code, out, _ = run_cli([*DISSOLVE, "--force", "--json"])
    assert code == 1
    data = json.loads(out)
    assert data["code"] == "API_FAILED"
    assert data["detail"]["failed"] == ["V1:2", "V1:3"] and data["detail"]["added"] == []
    assert "snapshot" in data["detail"]
    assert len(fake_world.calls_to(ADD)) == 2


def test_items_limits_the_cuts_to_the_listed_right_hand_items(run_cli: Run) -> None:
    code, out, _ = run_cli([*DISSOLVE, "--items", "V1:3", "--json"])
    assert code == 0
    assert json.loads(out)["cuts"] == [{"at": 86560, "left": "V1:2", "right": "V1:3"}]


def test_a_track_without_cuts_is_a_no_op_with_a_note(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli([*DISSOLVE, "--track", "2", "--force", "--json"])
    assert code == 0
    data = json.loads(out)
    assert data["applied"] is False and data["changes"] == []
    assert "adjacent items must touch" in data["note"]
    assert fake_world.write_calls() == []


def test_bad_flags_are_refused_before_connecting(run_cli: Run, fake_world: FakeWorld) -> None:
    assert run_cli(["timeline", "transitions", "--type", ""])[0] == 2
    assert run_cli([*DISSOLVE[:-2], "--duration", "0"])[0] == 2
    assert run_cli([*DISSOLVE, "--track", "0"])[0] == 2
    assert fake_world.calls == []


# ---- fades ------------------------------------------------------------------------------------


def test_fades_dry_run_then_force_sets_every_audio_item(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "fades", "--in", "12", "--json"])
    assert code == 0
    data = json.loads(out)
    assert [row["field"] for row in data["changes"]] == ["A1:1 fade_in", "A1:2 fade_in"]
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["timeline", "fades", "--in", "12", "--force", "--json"])
    assert code == 0
    data = json.loads(out)
    assert data["applied"] is True
    assert fake_world.calls_to(SET_FADES) == [({"FadeIn": 12, "FadeOut": 0},)] * 2
    assert [i.GetFades()["FadeIn"] for i in _track(fake_world, "audio")] == [12, 12]
    assert [d.name for d in _snapshots(tmp_path).iterdir()] == [data["snapshot"]]


def test_fades_clamp_to_the_item_length_and_say_so(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timeline", "fades", "--in", "200", "--force", "--json"])
    assert code == 0
    data = json.loads(out)
    assert data["clamped"][0] == {
        "item": "A1:1",
        "fade": "fade_in",
        "asked": 200,
        "set": 119,
        "why": "item is 120 frames",
    }
    assert "clamped" in data["warning"]
    assert fake_world.calls_to(SET_FADES)[0] == ({"FadeIn": 119, "FadeOut": 0},)


def test_fades_clamp_is_verified_against_the_clamped_value(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "fades", "--in", "200", "--force"])
    assert code == 0 and "VERIFY_FAILED" not in out


def test_fades_video_out_on_listed_items(run_cli: Run, fake_world: FakeWorld) -> None:
    args = ["timeline", "fades", "--track-type", "video", "--out", "6", "--items", "V1:1"]
    code, out, _ = run_cli([*args, "--json"])
    assert code == 0
    assert [row["field"] for row in json.loads(out)["changes"]] == ["V1:1 fade_out"]
    assert run_cli([*args, "--force"])[0] == 0
    assert fake_world.calls_to(SET_FADES) == [({"FadeIn": 0, "FadeOut": 6},)]


def test_fades_already_set_is_a_no_op(run_cli: Run, fake_world: FakeWorld) -> None:
    assert run_cli(["timeline", "fades", "--in", "0", "--force"])[0] == 0
    code, out, _ = run_cli(["timeline", "fades", "--in", "0", "--force", "--json"])
    data = json.loads(out)
    assert code == 0 and data["applied"] is False and "already" in data["note"]
    assert fake_world.write_calls() == []


def test_fades_every_item_refused_is_api_failed_naming_the_first(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject(SET_FADES, False)
    code, out, _ = run_cli(["timeline", "fades", "--in", "12", "--force"])
    assert code == 1 and "API_FAILED" in out and "any of the 2 items" in out and "A1:1" in out
    assert len(fake_world.calls_to(SET_FADES)) == 2


def _short_audio(world: FakeWorld, fades: dict[str, int] | None = None) -> FakeTimelineItem:
    """A 15-frame audio item on A1 (A1:3), after the two 120-frame ones."""
    last = _track(world, "audio")[-1]
    item = _edit(world).add_item("audio", 1, "short", last.start + last.duration, 15)
    if fades is not None:
        item.fades = dict(fades)
    return item


def test_fades_fit_in_plus_out_to_the_item_and_say_so(run_cli: Run, fake_world: FakeWorld) -> None:
    _short_audio(fake_world)
    code, out, _ = run_cli(["timeline", "fades", "--in", "12", "--out", "12", "--json"])
    assert code == 0
    data = json.loads(out)
    assert {
        "item": "A1:3",
        "fade": "fade_in",
        "asked": 12,
        "set": 7,
        "why": "in + out must stay under the 15-frame item (Resolve keeps 1 frame)",
    } in data["clamped"]
    assert [r["set"] for r in data["clamped"] if r["item"] == "A1:3"] == [7, 7]
    assert "12+12 > 14" in data["warning"] and "clamped on 1 item(s)" in data["warning"]
    assert "1 item(s) (2 fade(s), listed under clamped)" in data["warning"]
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["timeline", "fades", "--in", "12", "--out", "12", "--force", "--json"])
    assert code == 0
    data = json.loads(out)
    assert data["set"] == ["A1:1", "A1:2", "A1:3"] and "failed" not in data
    assert ({"FadeIn": 7, "FadeOut": 7},) in fake_world.calls_to(SET_FADES)


def test_fades_one_sided_fit_keeps_the_other_fade(run_cli: Run, fake_world: FakeWorld) -> None:
    item = _short_audio(fake_world, {"FadeIn": 0, "FadeOut": 10})
    args = ["timeline", "fades", "--in", "12", "--items", "A1:3", "--force", "--json"]
    code, out, _ = run_cli(args)
    assert code == 0
    assert json.loads(out)["clamped"][0]["set"] == 4
    assert fake_world.calls_to(SET_FADES) == [({"FadeIn": 4, "FadeOut": 10},)]
    assert item.GetFades() == {"FadeIn": 4, "FadeOut": 10}


def test_fades_a_refused_item_is_skipped_and_listed(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _track(fake_world, "audio")[0]
    monkeypatch.setattr(first, "SetFades", lambda fades: False)
    code, out, _ = run_cli(["timeline", "fades", "--in", "12", "--force", "--json"])
    assert code == 0 and "VERIFY_FAILED" not in out
    data = json.loads(out)
    assert data["failed"] == ["A1:1"] and data["set"] == ["A1:2"]
    assert [row["field"] for row in data["changes"]] == ["A1:2 fade_in"]
    assert "1 item(s) refused SetFades and were skipped: A1:1" in data["warning"]
    assert len(list(_snapshots(tmp_path).iterdir())) == 1


def test_fades_help_names_the_default_scope(run_cli: Run) -> None:
    code, out, _ = run_cli(["timeline", "fades", "--help"])
    assert code == 0 and "every audio track" in out


def test_fake_set_fades_refuses_a_sum_over_the_item_and_trims_equality(
    fake_world: FakeWorld,
) -> None:
    item = _short_audio(fake_world)
    assert item.SetFades({"FadeIn": 7, "FadeOut": 9}) is False
    assert item.SetFades({"FadeIn": 7, "FadeOut": 8}) is True
    assert item.GetFades() == {"FadeIn": 7, "FadeOut": 7}


def test_fades_at_the_item_length_are_clamped_and_verified(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    item = _short_audio(fake_world)
    args = ["timeline", "fades", "--in", "7", "--out", "8", "--items", "A1:3", "--force", "--json"]
    code, out, _ = run_cli(args)
    assert code == 0 and "VERIFY_FAILED" not in out
    assert json.loads(out)["clamped"] == [
        {
            "item": "A1:3",
            "fade": "fade_out",
            "asked": 8,
            "set": 7,
            "why": "in + out must stay under the 15-frame item (Resolve keeps 1 frame)",
        }
    ]
    assert fake_world.calls_to(SET_FADES) == [({"FadeIn": 7, "FadeOut": 7},)]
    assert item.GetFades() == {"FadeIn": 7, "FadeOut": 7}


def test_fades_track_run_with_equal_sums_exits_zero(run_cli: Run, fake_world: FakeWorld) -> None:
    args = ["timeline", "fades", "--in", "60", "--out", "60", "--force", "--json"]
    code, out, _ = run_cli(args)
    assert code == 0 and "VERIFY_FAILED" not in out
    data = json.loads(out)
    assert {row["item"] for row in data["clamped"]} == {"A1:1", "A1:2"}
    assert {row["set"] for row in data["clamped"]} == {59}
    assert all(i.GetFades() == {"FadeIn": 60, "FadeOut": 59} for i in _track(fake_world, "audio"))


@pytest.mark.parametrize(
    ("wanted", "current", "duration", "full", "rows"),
    [
        ({"FadeIn": 12}, {}, 120, {"FadeIn": 12, "FadeOut": 0}, []),
        ({"FadeIn": 200}, {}, 120, {"FadeIn": 119, "FadeOut": 0}, [("fade_in", 200, 119)]),
        (
            {"FadeIn": 12, "FadeOut": 12},
            {},
            15,
            {"FadeIn": 7, "FadeOut": 7},
            [("fade_in", 12, 7), ("fade_out", 12, 7)],
        ),
        (
            {"FadeIn": 7, "FadeOut": 8},
            {},
            15,
            {"FadeIn": 7, "FadeOut": 7},
            [("fade_out", 8, 7)],
        ),
        ({"FadeIn": 12}, {"FadeOut": 10}, 15, {"FadeIn": 4, "FadeOut": 10}, [("fade_in", 12, 4)]),
        (
            {"FadeIn": 99, "FadeOut": 99},
            {},
            10,
            {"FadeIn": 4, "FadeOut": 5},
            [("fade_in", 99, 4), ("fade_out", 99, 5)],
        ),
        ({"FadeIn": 5}, {}, 0, {"FadeIn": 0, "FadeOut": 0}, [("fade_in", 5, 0)]),
    ],
)
def test_fit_fades_table(
    wanted: dict[str, int],
    current: dict[str, object],
    duration: int,
    full: dict[str, int],
    rows: list[tuple[str, int, int]],
) -> None:
    got_full, got_rows = fit_fades(wanted, current, duration)
    assert got_full == full
    assert [(r["fade"], r["asked"], r["set"]) for r in got_rows] == rows
    assert all("why" in r for r in got_rows)


def test_fades_need_a_length_and_a_valid_track(run_cli: Run, fake_world: FakeWorld) -> None:
    assert run_cli(["timeline", "fades"])[0] == 2
    assert run_cli(["timeline", "fades", "--in", "-1"])[0] == 2
    assert run_cli(["timeline", "fades", "--in", "3", "--track", "0"])[0] == 2
    assert fake_world.calls == []


def test_fades_need_resolve_21_1(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.resolve.version = [20, 3, 1, 1, ""]
    code, out, _ = run_cli(["timeline", "fades", "--in", "3"])
    assert code == 1 and "21.1" in out


# ---- helpers ----------------------------------------------------------------------------------


def test_cuts_on_pairs_touching_neighbours_in_start_order(fake_world: FakeWorld) -> None:
    items = [
        Located("V1", position + 1, item)
        for position, item in enumerate(_track(fake_world, "video"))
    ]
    cuts = cuts_on(list(reversed(items)))
    assert [(frame, left.position, right.position) for frame, left, right in cuts] == [
        (86480, 1, 2),
        (86560, 2, 3),
    ]


def test_select_items_defaults_to_video_track_one_or_all_audio(fake_world: FakeWorld) -> None:
    timeline = _edit(fake_world)
    video = select_items(_inv(fake_world), timeline, "video")
    assert [one.track for one in video] == ["V1", "V1", "V1"]
    audio = select_items(_inv(fake_world), timeline, "audio")
    assert [one.track for one in audio] == ["A1", "A1"]
    assert select_items(_inv(fake_world, track=2), timeline, "video")[0].item.GetName() == "Title"
