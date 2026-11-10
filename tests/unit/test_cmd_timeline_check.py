"""`timeline check` (U1): rules, `--target`, `--strict` and the read-only guarantee.

`timeline_extra.NEW` does not carry `check` until the lead splices it, so the tests wrap the spec
locally and point the registry at this module's `SPEC`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace

import pytest

from davinci_axi import registry
from davinci_axi.commands import timeline_extra
from davinci_axi.commands.timeline_check import CHECK_SPEC, parse_target
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeProject, FakeTimeline, FakeTimelineItem, FakeWorld, ext_render

Run = Callable[[list[str]], tuple[int, str, str]]
SPEC = replace(
    timeline_extra.SPEC, subcommands={**timeline_extra.SPEC.subcommands, "check": CHECK_SPEC}
)
CHECK = ["timeline", "check", "--json"]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "timeline", f"{__name__}:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _edit(world: FakeWorld) -> FakeTimeline:
    return next(t for t in _project(world).timelines if t.name == "Edit v1")


def _clip(world: FakeWorld, number: int) -> FakeTimelineItem:
    return _edit(world).tracks["video"][0].items[number - 1]


def _check(run_cli: Run, *flags: str) -> tuple[int, dict[str, object]]:
    code, out, err = run_cli([*CHECK, *flags])
    assert err == ""
    return code, json.loads(out)


def _toon(run_cli: Run, *flags: str) -> str:
    code, out, err = run_cli(["timeline", "check", *flags])
    assert code == 0 and err == ""
    return out


def _set(world: FakeWorld, number: int, **props: object) -> str:
    media = _clip(world, number).media
    assert media is not None
    media.properties.update({key.replace("_", " "): value for key, value in props.items()})
    return media.name


def _rows(data: dict[str, object], check: str | None = None) -> list[dict[str, str]]:
    rows = data["findings"]
    assert isinstance(rows, list)
    return [row for row in rows if check is None or row["check"] == check]


def test_default_world_is_ok_with_one_orientation_warning(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, data = _check(run_cli)
    assert code == 0 and data["ok"] is True and data["errors"] == 0 and data["warnings"] == 1
    assert (data["frames"], data["duration"], data["fps"]) == (240, "10.0 s", 24.0)
    assert data["resolution"] == "1920x1080" and data["timeline"] == "Edit v1"
    assert "target" not in data
    (row,) = _rows(data, "resolution")
    assert (row["level"], row["where"]) == ("warn", "V1:2")
    assert "1080x1920" in row["detail"] and "landscape" in row["detail"]
    assert fake_world.write_calls() == []


def test_gap_between_clips_reports_frames_and_timecode(run_cli: Run, fake_world: FakeWorld) -> None:
    _clip(fake_world, 3).start += 10
    _, data = _check(run_cli)
    (row,) = _rows(data, "gaps")
    assert (row["level"], row["where"]) == ("warn", "V1:2→V1:3")
    assert "10 frames" in row["detail"] and "01:00:07:02" in row["detail"]
    assert "86560-86570" in row["detail"]


def test_leading_gap_is_found_against_the_timeline_start(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    for number in (1, 2, 3):
        _clip(fake_world, number).start += 5
    _, data = _check(run_cli)
    (row,) = _rows(data, "gaps")
    assert row["where"] == "V1:1" and "leading gap of 5 frames" in row["detail"]


def test_offline_media_is_an_error_but_exit_zero_unless_strict(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    ext_render.mark_offline(_project(fake_world), "A001_beach")
    code, data = _check(run_cli)
    assert code == 0 and data["ok"] is False and data["errors"] == 1
    (row,) = _rows(data, "offline")
    assert row["level"] == "error" and row["where"] == "V1:1"
    assert "A001_beach is offline" in row["detail"] and "A1:1" in row["detail"]
    assert _rows(data)[0]["check"] == "offline"  # errors sort first
    strict_code, strict = _check(run_cli, "--strict")
    assert strict_code == 1 and strict["ok"] is False


def test_strict_exits_zero_when_there_are_no_errors(run_cli: Run, fake_world: FakeWorld) -> None:
    code, data = _check(run_cli, "--strict")
    assert code == 0 and data["ok"] is True


def test_target_maximum_is_an_error_when_exceeded(run_cli: Run, fake_world: FakeWorld) -> None:
    code, data = _check(run_cli, "--target", "5s")
    assert code == 0 and data["ok"] is False and data["target"] == "5s"
    (row,) = _rows(data, "length")
    assert (row["level"], row["detail"]) == ("error", "10.0 s is over the 5 s maximum")


def test_target_minimum_and_range(run_cli: Run, fake_world: FakeWorld) -> None:
    _, under = _check(run_cli, "--target", "30s-1m")
    (row,) = _rows(under, "length")
    assert row["level"] == "error" and row["detail"] == "10.0 s is under the 30 s minimum"
    _, within = _check(run_cli, "--target", "5s-20s")
    (row,) = _rows(within, "length")
    assert row["level"] == "info" and "within" in row["detail"] and within["ok"] is True


def test_disabled_item_is_info(run_cli: Run, fake_world: FakeWorld) -> None:
    _edit(fake_world).tracks["video"][0].items[1].enabled = False
    _, data = _check(run_cli)
    (row,) = _rows(data, "disabled")
    assert (row["level"], row["where"]) == ("info", "V1:2")


def test_track_flag_limits_the_video_rules(run_cli: Run, fake_world: FakeWorld) -> None:
    _, data = _check(run_cli, "--track", "2")
    assert data["track"] == "V2"
    assert _rows(data, "resolution") == [] and _rows(data, "silence") == []
    _, missing = _check(run_cli, "--track", "5")
    (row,) = _rows(missing, "empty")
    assert row["level"] == "error" and row["where"] == "V5"


def test_video_without_audio_is_a_silence_span(run_cli: Run, fake_world: FakeWorld) -> None:
    _edit(fake_world).tracks["audio"][0].items.clear()
    _, data = _check(run_cli)
    (row,) = _rows(data, "silence")
    assert row["level"] == "info" and "86400-86640" in row["detail"]
    assert "240 frames" in row["detail"]


def test_partial_audio_leaves_the_uncovered_span(run_cli: Run, fake_world: FakeWorld) -> None:
    _edit(fake_world).tracks["audio"][0].items.pop()  # music_bed covered 120-240
    _, data = _check(run_cli)
    (row,) = _rows(data, "silence")
    assert "86520-86640" in row["detail"]


def test_empty_timeline_is_an_error(run_cli: Run, fake_world: FakeWorld) -> None:
    code, data = _check(run_cli, "--timeline", "Edit v2")
    assert code == 0 and data["ok"] is False
    (row,) = _rows(data, "empty")
    assert row["where"] == "timeline"
    assert data["timeline"] == "Edit v2"


def test_transition_is_info_not_an_overlap(run_cli: Run, fake_world: FakeWorld) -> None:
    edit = _edit(fake_world)
    edit.add_item("video", 1, "Cross Dissolve", edit.start_frame + 70, 20, kind="Transition")
    edit.tracks["video"][0].items.sort(key=lambda item: item.start)
    _, data = _check(run_cli)
    assert _rows(data, "overlap") == []
    (row,) = _rows(data, "transition")
    assert row["level"] == "info" and row["where"] == "V1:2"
    assert data["errors"] == 0 and data["warnings"] == 1  # the orientation warning only


def test_overlapping_clips_are_an_error(run_cli: Run, fake_world: FakeWorld) -> None:
    _clip(fake_world, 2).start -= 8
    _, data = _check(run_cli)
    (row,) = _rows(data, "overlap")
    assert row["level"] == "error" and row["where"] == "V1:1→V1:2"
    assert "8 frames" in row["detail"]
    assert _rows(data, "transition") == []


def test_fps_mismatch_warns(run_cli: Run, fake_world: FakeWorld) -> None:
    media = _clip(fake_world, 3).media
    assert media is not None
    media.properties["FPS"] = "29.97"
    _, data = _check(run_cli)
    (row,) = _rows(data, "fps")
    assert row["where"] == "V1:3" and "29.97 fps on a 24 fps timeline" in row["detail"]
    media.properties["FPS"] = "not-a-number"
    _, skipped = _check(run_cli)
    assert _rows(skipped, "fps") == []


def test_upscaled_clip_warns_with_the_factor(run_cli: Run, fake_world: FakeWorld) -> None:
    media = _clip(fake_world, 3).media
    assert media is not None
    media.properties["Resolution"] = "1280x720"
    _, data = _check(run_cli)
    row = next(r for r in _rows(data, "resolution") if r["where"] == "V1:3")
    assert "upscaled 1.5x" in row["detail"]


def test_custom_timeline_settings_win_over_the_project(run_cli: Run, fake_world: FakeWorld) -> None:
    _edit(fake_world).settings.update(
        useCustomSettings="1",
        timelineResolutionWidth="3840",
        timelineResolutionHeight="2160",
        timelineFrameRate="25",
    )
    _, data = _check(run_cli)
    assert data["resolution"] == "3840x2160" and data["fps"] == 25.0
    assert any("upscaled 2.0x" in row["detail"] for row in _rows(data, "resolution"))


def test_findings_empty_state_and_hints(
    run_cli: Run, parse_toon_keys: Callable[[str], dict[str, str]], fake_world: FakeWorld
) -> None:
    media = _clip(fake_world, 2).media
    assert media is not None
    media.properties["Resolution"] = "1920x1080"
    _, data = _check(run_cli)
    assert data["findings"] == "0 findings in timeline 'Edit v1'"
    ext_render.mark_offline(_project(fake_world), "A001_beach")
    code, out, _ = run_cli(["timeline", "check"])
    assert code == 0 and "media relink" in out and "item delete" not in out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2m-5m", (120.0, 300.0)),
        ("90s", (None, 90.0)),
        ("3:30", (None, 210.0)),
        ("1m30s", (None, 90.0)),
        ("1:02:03", (None, 3723.0)),
        ("45", (None, 45.0)),
        ("30s-1:30", (30.0, 90.0)),
    ],
)
def test_parse_target(text: str, expected: tuple[float | None, float | None]) -> None:
    assert parse_target(text) == expected


@pytest.mark.parametrize("text", ["abc", "", "5m-2m", "0s", "3:75", "2x", "-5m"])
def test_parse_target_rejects(text: str) -> None:
    with pytest.raises(AxiError) as caught:
        parse_target(text)
    assert caught.value.code == "VALIDATION_ERROR"


@pytest.mark.parametrize("flags", [["--track", "0"], ["--target", "abc"], ["--target", "5m-2m"]])
def test_bad_flags_exit_two_before_connecting(
    run_cli: Run, fake_world: FakeWorld, flags: list[str]
) -> None:
    code, out, _ = run_cli([*CHECK, *flags])
    assert code == 2 and "VALIDATION_ERROR" in out
    assert fake_world.calls == []


def test_check_never_writes(run_cli: Run, fake_world: FakeWorld) -> None:
    ext_render.mark_offline(_project(fake_world), "A001_beach")
    _check(run_cli, "--target", "2m-5m", "--strict")
    assert fake_world.write_calls() == []


def test_repeated_upscales_collapse_into_one_toon_row(run_cli: Run, fake_world: FakeWorld) -> None:
    for number in (1, 2, 3):
        _set(fake_world, number, Resolution="1280x720")
    out = _toon(run_cli)
    assert "3 items" in out and out.count("upscaled 1.5x") == 1
    assert "3 findings shown as 1 row; pass --verbose to list every item" in out
    verbose = _toon(run_cli, "--verbose")
    assert verbose.count("upscaled 1.5x") == 3 and "findings shown as" not in verbose
    _, data = _check(run_cli)
    assert [row["where"] for row in _rows(data, "resolution")] == ["V1:1", "V1:2", "V1:3"]
    assert all(set(row) == {"level", "check", "where", "detail"} for row in _rows(data))
    assert data["warnings"] == 3


def test_help_line_counts_findings_and_rows(run_cli: Run, fake_world: FakeWorld) -> None:
    for number in (1, 2, 3):
        _set(fake_world, number, Resolution="1280x720")
    ext_render.mark_offline(_project(fake_world), "A001_beach")
    _, data = _check(run_cli)
    total = len(_rows(data))
    out = _toon(run_cli)
    assert f"{total} findings shown as {total - 2} rows; pass --verbose" in out


def test_super_scale_covering_the_upscale_is_info(run_cli: Run, fake_world: FakeWorld) -> None:
    _set(fake_world, 3, Resolution="1280x720", Super_Scale=2)
    _, data = _check(run_cli)
    row = next(r for r in _rows(data, "resolution") if r["where"] == "V1:3")
    assert row["level"] == "info" and "covered by Super Scale 2x" in row["detail"]
    assert data["warnings"] == 1  # the orientation warning only
    assert fake_world.write_calls() == []


def test_super_scale_too_small_stays_warn(run_cli: Run, fake_world: FakeWorld) -> None:
    _set(fake_world, 3, Resolution="1280x720", Super_Scale=2)
    _edit(fake_world).settings.update(
        useCustomSettings="1", timelineResolutionWidth="3840", timelineResolutionHeight="2160"
    )
    _, data = _check(run_cli)
    row = next(r for r in _rows(data, "resolution") if r["where"] == "V1:3")
    assert row["level"] == "warn"
    assert "upscaled 3.0x" in row["detail"] and "does not cover" in row["detail"]


@pytest.mark.parametrize("value", ["x", 7, 0, None])
def test_super_scale_unreadable_is_treated_as_off(
    run_cli: Run, fake_world: FakeWorld, value: object
) -> None:
    _set(fake_world, 3, Resolution="1280x720", Super_Scale=value)
    _, data = _check(run_cli)
    row = next(r for r in _rows(data, "resolution") if r["where"] == "V1:3")
    assert row["level"] == "warn" and "Super Scale" not in row["detail"]


def test_upscale_hint_names_media_property_set(run_cli: Run, fake_world: FakeWorld) -> None:
    name = _set(fake_world, 3, Resolution="1280x720")
    out = _toon(run_cli)
    assert "media property set" in out and "'Super Scale=2x-enhanced'" in out
    assert "40x" in out and name in out and "ZoomX" not in out
    _set(fake_world, 3, Super_Scale=2)
    assert "'Super Scale=2x-enhanced'" not in _toon(run_cli)
    _set(fake_world, 3, Resolution="1920x1080", Super_Scale=1)
    assert "media property set" not in _toon(run_cli)  # orientation only


def test_fps_findings_group_by_source_rate(run_cli: Run, fake_world: FakeWorld) -> None:
    _set(fake_world, 1, FPS="29.97")
    _set(fake_world, 2, FPS="29.97")
    _set(fake_world, 3, FPS="25")
    out = _toon(run_cli)
    assert "2 items" in out and "29.97 fps" in out and "conforms" in out
    assert "V1:3" in out and "25 fps" in out
    _, data = _check(run_cli)
    assert len(_rows(data, "fps")) == 3


def test_grouped_rows_keep_level_order(run_cli: Run, fake_world: FakeWorld) -> None:
    for number in (1, 2, 3):
        _set(fake_world, number, Resolution="1280x720")
    ext_render.mark_offline(_project(fake_world), "A001_beach")
    out = _toon(run_cli)
    assert out.index("offline") < out.index("3 items")


def _cost(data: dict[str, object]) -> list[dict[str, str]]:
    return _rows(data, "render_cost")


def test_render_cost_counts_super_scale_items(run_cli: Run, fake_world: FakeWorld) -> None:
    _set(fake_world, 1, Super_Scale=2)
    _set(fake_world, 2, Super_Scale=4)
    code, data = _check(run_cli)
    (row,) = _cost(data)
    assert code == 0 and row["level"] == "info" and row["where"] == "2 items"
    assert row["detail"].startswith("2 of 4 video items use Super Scale (2x-4x)")
    assert "about 40x longer" in row["detail"] and "turn it off for drafts" in row["detail"]
    assert data["warnings"] == 1  # the orientation warning only; info does not count
    assert fake_world.write_calls() == []


def test_render_cost_hint_names_used_flag(run_cli: Run, fake_world: FakeWorld) -> None:
    _set(fake_world, 1, Super_Scale=2)
    out = _toon(run_cli)
    assert "media property set --used 'Super Scale=1' --save-restore ~/super-scale.json" in out
    assert "draft renders take seconds" in out
    assert "media property set --restore ~/super-scale.json --force" in out


@pytest.mark.parametrize("value", [1, "x", 7, 0, None])
def test_render_cost_absent_when_super_scale_is_off(
    run_cli: Run, fake_world: FakeWorld, value: object
) -> None:
    _set(fake_world, 1, Super_Scale=value)
    _, data = _check(run_cli)
    assert _cost(data) == []
    assert "--used" not in _toon(run_cli)


def test_render_cost_default_world_has_no_row(run_cli: Run, fake_world: FakeWorld) -> None:
    _, data = _check(run_cli)
    assert _cost(data) == []


def test_render_cost_sorts_after_warnings_and_keeps_strict_exit(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _set(fake_world, 1, Super_Scale=2)
    _, data = _check(run_cli, "--strict")
    levels = [row["level"] for row in _rows(data)]
    assert levels == sorted(levels, key=["error", "warn", "info"].index)
    code, _ = _check(run_cli, "--strict")
    assert code == 0
    assert _rows(data).index(_cost(data)[0]) > max(
        i for i, row in enumerate(_rows(data)) if row["level"] == "warn"
    )


def test_render_cost_hint_absent_without_media_verb(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set(fake_world, 1, Super_Scale=2)
    monkeypatch.delitem(registry.COMMANDS, "media")
    out = _toon(run_cli)
    assert "render_cost" in out and "--used" not in out
