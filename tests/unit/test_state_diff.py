"""Pure-dict tests for `state_diff` (U2): no Resolve, no files."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from davinci_axi.commands.state_diff import (
    Delta,
    Row,
    comparable,
    delta_result,
    diff_project,
    diff_timeline,
)
from davinci_axi.errors import AxiError


def _item(uid: str, name: str, start: int, duration: int = 80) -> dict[str, Any]:
    return {
        "id": uid,
        "name": name,
        "type": "Clip",
        "start": start,
        "end": start + duration,
        "duration": duration,
        "source_start": 0,
        "source_end": duration,
        "enabled": True,
        "color": "Blue",
        "flags": [],
        "markers": [],
        "media": {"name": f"{name}.mov", "id": f"m-{uid}", "path": "/x"},
    }


def _timeline(name: str = "Edit", prefix: str = "i", length: int = 240) -> dict[str, Any]:
    items = [_item(f"{prefix}{n}", f"clip-{n}", 86400 + 80 * (n - 1)) for n in (1, 2, 3)]
    return {
        "index": 1,
        "name": name,
        "id": f"{prefix}-tl",
        "start_frame": 86400,
        "end_frame": 86400 + length,
        "settings": {"timelineFrameRate": "24"},
        "markers": [{"frame": 100, "name": "M1", "color": "Red", "note": "review"}],
        "tracks": [
            {
                "type": "video",
                "index": 1,
                "name": "V",
                "enabled": True,
                "locked": False,
                "items": items,
            }
        ],
    }


def _project(*timelines: dict[str, Any], project: str = "p") -> dict[str, Any]:
    return {
        "project": project,
        "id": "pid",
        "settings": {"timelineFrameRate": "24"},
        "current_timeline": timelines[0]["name"],
        "timelines": list(timelines),
    }


def _kinds(delta: Delta) -> list[str]:
    return [r.kind for r in delta.rows]


def test_identical_states_have_no_rows() -> None:
    state = _project(_timeline())
    delta = diff_project(state, copy.deepcopy(state), timeline=None)
    assert delta.rows == []
    assert delta.summary["items_removed"] == 0
    assert "frames" not in delta.summary


def test_removed_item_and_shift_become_removed_and_moved() -> None:
    before = _project(_timeline())
    after = copy.deepcopy(before)
    items = after["timelines"][0]["tracks"][0]["items"]
    del items[1]
    items[1]["start"], items[1]["end"] = 86480, 86560
    after["timelines"][0]["end_frame"] = 86400 + 160
    delta = diff_project(before, after, timeline=None)
    assert sorted(_kinds(delta)) == ["moved", "removed"]
    assert delta.summary["items_removed"] == 1
    assert delta.summary["items_moved"] == 1
    assert delta.summary["frames[Edit]"] == "240 → 160"
    moved = next(r for r in delta.rows if r.kind == "moved")
    assert (moved.field, moved.before, moved.after) == ("start", 86560, 86480)
    assert moved.where == "Edit: V1:2 clip-3"


def test_trim_is_changed_not_moved() -> None:
    before = _timeline()
    after = copy.deepcopy(before)
    item = after["tracks"][0]["items"][0]
    item["end"], item["duration"] = item["end"] - 10, item["duration"] - 10
    fields = {r.field for r in diff_timeline(before, after, match="id").rows}
    assert fields == {"end", "duration"}


def test_media_is_compared_by_name() -> None:
    before = _timeline()
    after = copy.deepcopy(before)
    after["tracks"][0]["items"][0]["media"]["id"] = "other-id"
    assert diff_timeline(before, after, match="id").rows == []
    after["tracks"][0]["items"][0]["media"]["name"] = "swap.mov"
    row = diff_timeline(before, after, match="id").rows[0]
    assert (row.kind, row.field, row.after) == ("changed", "media", "swap.mov")


def test_project_setting_and_current_timeline_rows() -> None:
    before = _project(_timeline("A"), _timeline("B"))
    after = copy.deepcopy(before)
    after["settings"]["timelineFrameRate"] = "30"
    after["current_timeline"] = "B"
    delta = diff_project(before, after, timeline=None)
    assert _kinds(delta) == ["setting", "setting"]
    assert delta.summary["settings_changed"] == 2


def test_timelines_added_and_removed_by_name() -> None:
    before = _project(_timeline("A"), _timeline("B"))
    after = _project(_timeline("A"), _timeline("C"))
    delta = diff_project(before, after, timeline=None)
    assert delta.summary["timelines_added"] == 1
    assert delta.summary["timelines_removed"] == 1
    assert {(r.kind, r.where) for r in delta.rows} == {
        ("added", "timeline C"),
        ("removed", "timeline B"),
    }


def test_marker_added_removed_and_changed() -> None:
    before = _timeline()
    after = copy.deepcopy(before)
    after["markers"] = [
        {"frame": 100, "name": "M1", "color": "Blue", "note": "review"},
        {"frame": 200, "name": "M2", "color": "Red", "note": ""},
    ]
    delta = diff_timeline(before, after, match="id")
    assert delta.summary["markers_changed"] == 2
    assert {(r.field, r.before, r.after) for r in delta.rows} == {
        ("color", "Red", "Blue"),
        ("frame", None, 200),
    }
    gone = diff_timeline(after, before, match="id")
    assert ("frame", 200, None) in {(r.field, r.before, r.after) for r in gone.rows}


def test_track_count_and_flag_rows() -> None:
    before = _timeline()
    after = copy.deepcopy(before)
    after["tracks"][0]["locked"] = True
    after["tracks"].append(
        {"type": "video", "index": 2, "name": "V2", "enabled": True, "locked": False, "items": []}
    )
    delta = diff_timeline(before, after, match="id")
    assert delta.summary["tracks_changed"] == 2
    assert {(r.where, r.field) for r in delta.rows} == {("V1", "locked"), ("video tracks", "count")}


def test_timeline_setting_rows() -> None:
    before = _timeline()
    after = copy.deepcopy(before)
    after["settings"]["timelineFrameRate"] = "25"
    row = diff_timeline(before, after, match="id").rows[0]
    assert (row.kind, row.field, row.before, row.after) == (
        "setting",
        "timelineFrameRate",
        "24",
        "25",
    )


def test_position_matching_ignores_ids() -> None:
    a, b = _timeline("A", prefix="a"), _timeline("B", prefix="b")
    assert diff_timeline(a, b, match="position").rows == []
    assert len(diff_timeline(a, b, match="id").rows) == 6  # 3 removed + 3 added by id


def test_position_matching_extra_items_are_added_or_removed() -> None:
    a, b = _timeline("A", prefix="a"), _timeline("B", prefix="b")
    del b["tracks"][0]["items"][2]
    delta = diff_timeline(a, b, match="position")
    assert delta.summary["items_removed"] == 1
    assert delta.rows[0].where == "V1:3 clip-3"


def test_unknown_timeline_filter_is_not_found() -> None:
    state = _project(_timeline("A"))
    with pytest.raises(AxiError) as err:
        diff_project(state, state, timeline="nope")
    assert err.value.code == "NOT_FOUND"
    assert "A" in err.value.help[0]


def test_timeline_filter_limits_item_diff() -> None:
    before = _project(_timeline("A"), _timeline("B"))
    after = copy.deepcopy(before)
    del after["timelines"][0]["tracks"][0]["items"][0]
    del after["timelines"][1]["tracks"][0]["items"][0]
    only_b = diff_project(before, after, timeline="B")
    assert only_b.summary["items_removed"] == 1
    assert all(r.where.startswith("B: ") for r in only_b.rows)


def test_delta_result_caps_rows_and_names_full_command() -> None:
    rows = [Row("changed", f"V1:{n} c", "name", "a", "b") for n in range(5)]
    result = delta_result(
        Delta({"items_changed": 5}, rows), limit=2, full_cmd="snapshot diff x --full", header={}
    )
    assert len(result.data["changes"]) == 2  # type: ignore[arg-type]
    assert result.data["note"] == "showing 2 of 5 changes"
    assert "snapshot diff x --full" in result.help[0]


def test_delta_result_empty_text() -> None:
    result = delta_result(
        Delta({"items_changed": 0}, []),
        limit=100,
        full_cmd="x",
        header={"before": "a", "after": "b"},
    )
    assert result.data["changes"] == "no differences between a and b"
    assert result.help == []


def _with(tl: dict[str, Any], **fields: Any) -> dict[str, Any]:
    out = copy.deepcopy(tl)
    out["tracks"][0]["items"][0].update(fields)
    return out


def test_a_fade_change_is_one_changed_row() -> None:
    base = _with(_timeline(), fade_in=0, fade_out=0)
    delta = diff_timeline(base, _with(base, fade_in=60), match="id")
    assert delta.summary["items_changed"] == 1
    assert delta.summary["fields_changed"] == 1
    assert [(r.kind, r.field, r.before, r.after) for r in delta.rows] == [
        ("changed", "fade_in", 0, 60)
    ]


def test_two_fields_on_one_item_count_one_item_two_fields() -> None:
    base = _with(_timeline(), fade_in=0, fade_out=0)
    delta = diff_timeline(base, _with(base, fade_in=60, fade_out=60), match="id")
    assert delta.summary["items_changed"] == 1
    assert delta.summary["fields_changed"] == 2


def test_diff_project_adds_items_and_fields_across_timelines() -> None:
    def state(fade: int) -> dict[str, Any]:
        a = _with(_timeline("A", "a"), fade_in=0, fade_out=0)
        b = _with(_timeline("B", "b"), fade_in=0, fade_out=0)
        if fade:
            a, b = _with(a, fade_in=fade, fade_out=fade), _with(b, fade_in=fade)
        return {"timelines": [a, b]}

    delta = diff_project(state(0), state(60), timeline=None)
    assert delta.summary["items_changed"] == 2
    assert delta.summary["fields_changed"] == 3


def test_keys_missing_on_one_side_are_unknown_not_changes() -> None:
    old = _timeline()
    new = _with(old, fade_in=0, fade_out=0, volume=0.0, zoom=1.0, pan=0.0, tilt=0.0)
    assert diff_timeline(old, new, match="id").rows == []
    assert diff_timeline(new, old, match="id").rows == []
    assert diff_timeline(_with(old, volume=None), _with(old, volume=3.0), match="id").rows == []


def test_float_noise_is_not_a_change() -> None:
    a, b = _with(_timeline(), volume=0.0), _with(_timeline(), volume=0)
    assert diff_timeline(a, b, match="id").rows == []
    assert diff_timeline(a, _with(a, volume=0.0000001), match="id").rows == []
    assert len(diff_timeline(a, _with(a, volume=1.5), match="id").rows) == 1


def test_track_state_none_is_unknown_and_real_flips_are_rows() -> None:
    known = _timeline()
    unknown = copy.deepcopy(known)
    unknown["tracks"][0]["enabled"] = unknown["tracks"][0]["locked"] = None
    assert diff_timeline(known, unknown, match="id").rows == []
    assert diff_timeline(unknown, known, match="id").rows == []
    flipped = copy.deepcopy(known)
    flipped["tracks"][0]["enabled"] = False
    (row,) = diff_timeline(known, flipped, match="id").rows
    assert (row.kind, row.field, row.before, row.after) == ("track", "enabled", True, False)
    assert comparable(known["tracks"][0], flipped["tracks"][0], "enabled")
