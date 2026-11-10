from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeProject, FakeWorld
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "item", "davinci_axi.commands.items:ITEM_SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _edit(world: FakeWorld) -> FakeTimeline:
    timeline = _project(world).timelines[0]
    assert timeline.name == "Edit v1"
    return timeline


def _clip(world: FakeWorld, number: int) -> FakeTimelineItem:
    return _edit(world).tracks["video"][0].items[number - 1]


def test_lists_all_six_items_across_tracks(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["items"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["timeline"] == "Edit v1" and keys["total"] == "6"
    assert "items[6]{id,name,track,start,duration}:" in out
    assert "  item-1,clip-1,V1,86400,80" in out
    assert "  item-4,Title,V2,86440,60" in out
    assert "  item-6,music_bed,A1,86520,120" in out


def test_start_and_duration_are_integers_even_though_the_api_returns_floats(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    assert isinstance(_clip(fake_world, 1).GetStart(), float)
    _, out, _ = run_cli(["items", "--json", "--fields", "start,duration,end"])
    for row in json.loads(out)["items"]:
        assert all(type(value) is int for value in row.values())


def test_track_type_audio_returns_only_a1(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["items", "--track-type", "audio"])
    assert code == 0
    assert "items[2]{" in out and "total: 2" in out
    assert "A001_beach,A1,86400,120" in out
    assert ",V1," not in out and ",V2," not in out


def test_track_filter_combines_with_track_type(run_cli: Run, fake_world: FakeWorld) -> None:
    _, out, _ = run_cli(["items", "--track-type", "video", "--track", "2"])
    assert "items[1]{" in out and "item-4,Title,V2,86440,60" in out
    _, out, _ = run_cli(["items", "--track-type", "video", "--track", "7"])
    assert "items: 0 items on video track 7 in timeline Edit v1" in out
    _, out, _ = run_cli(["items", "--track", "1"])
    assert "total: 5" in out  # V1 (3 items) and A1 (2 items)


def test_empty_audio_states_the_scope_and_exits_0(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["items", "--timeline", "Edit v2", "--track-type", "audio"])
    keys = parse_toon_keys(out)
    assert code == 0
    assert keys["items"] == "0 items on audio tracks in timeline Edit v2"
    assert keys["total"] == "0"
    assert "item V" not in out


def test_empty_timeline_without_filters(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["items", "--timeline", "Edit v2"])
    assert code == 0
    assert parse_toon_keys(out)["items"] == "0 items in timeline Edit v2"


def test_unknown_timeline_is_not_found_and_lists_the_names(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["items", "--timeline", "nope"])
    assert code == 1
    assert parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert "Edit v1, Edit v2" in out


def test_no_current_timeline_is_a_coded_error(run_cli: Run, fake_world: FakeWorld) -> None:
    _project(fake_world).current_timeline = None
    code, out, _ = run_cli(["items"])
    assert code == 1 and "NO_TIMELINE" in out


def test_no_project_is_a_coded_error(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.resolve.pm.current_project = None
    code, out, _ = run_cli(["items"])
    assert code == 1 and "NO_PROJECT" in out
    code, out, _ = run_cli(["item", "V1:1"])
    assert code == 1 and "NO_PROJECT" in out


def test_fields_adds_the_extra_columns(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(
        [
            "items",
            "--fields",
            "name,end,type,enabled,color,source_in,source_out,media",
            "--track-type",
            "video",
            "--track",
            "1",
        ]
    )
    assert code == 0
    assert "items[3]{name,end,type,enabled,color,source_in,source_out,media}:" in out
    assert "  clip-1,86480,Video,true,Blue,0,80,A001_beach" in out


def test_extras_are_not_read_unless_requested(run_cli: Run, fake_world: FakeWorld) -> None:
    run_cli(["items"])
    for method in ("GetEnd", "GetType", "GetClipColor", "GetMediaPoolItem"):
        assert fake_world.calls_to(f"TimelineItem.{method}") == []


def test_unknown_field_exits_2_listing_valid_fields_without_a_resolve_call(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["items", "--fields", "name,bogus"])
    assert code == 2
    assert "VALIDATION_ERROR" in out and "bogus" in out and "source_out" in out
    assert fake_world.calls == []


def test_unknown_flag_exits_2_without_a_resolve_call(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["items", "--stat"])
    assert code == 2
    assert "--track-type" in out and "--timeline" in out and "--limit" in out
    assert fake_world.calls == []


@pytest.mark.parametrize(
    ("argv", "needle"),
    [
        (["items", "--track-type", "sound"], "audio"),
        (["items", "--track", "0"], "--track must be 1 or more"),
        (["items", "--limit", "0"], "--limit must be 1 or more"),
    ],
)
def test_bad_filter_values_exit_2_without_a_resolve_call(
    run_cli: Run, fake_world: FakeWorld, argv: list[str], needle: str
) -> None:
    code, out, _ = run_cli(argv)
    assert code == 2 and needle in out
    assert fake_world.calls == []


def test_limit_shows_two_of_six_and_hints_the_full_command(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["items", "--limit", "2", "--track-type", "video"])
    assert code == 0
    assert "items[2]{" in out and parse_toon_keys(out)["total"] == "4"
    assert "Run `davinci-axi items --track-type video --limit 4` to list all 4 items" in out
    code, out, _ = run_cli(["items", "--limit", "2"])
    assert "items[2]{" in out and "total: 6" in out
    assert "Run `davinci-axi items --limit 6` to list all 6 items" in out


def test_cut_list_is_marked_truncated_in_json(run_cli: Run, fake_world: FakeWorld) -> None:
    cut = json.loads(run_cli(["--json", "items", "--limit", "2"])[1])
    assert cut["total"] == 6 and cut["truncated"] is True
    assert "truncated" not in json.loads(run_cli(["--json", "items"])[1])


def test_item_hint_points_at_the_first_listed_item(run_cli: Run, fake_world: FakeWorld) -> None:
    _, out, _ = run_cli(["items", "--track-type", "audio"])
    assert "Run `davinci-axi item A1:1` to show that item in detail" in out
    _, out, _ = run_cli(["items", "--timeline", "Edit v1", "--limit", "1"])
    assert "item V1:1 --timeline 'Edit v1'" in out


def test_hints_are_hidden_when_the_commands_are_not_registered(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld
) -> None:
    monkeypatch.delitem(registry.COMMANDS, "item", raising=False)
    monkeypatch.delitem(registry.COMMANDS, "timelines", raising=False)
    _, out, _ = run_cli(["items"])
    assert "davinci-axi item " not in out and "davinci-axi timelines" not in out
    assert "help" not in out


def test_json_matches_the_toon_content(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["items", "--json", "--track-type", "audio"])
    data = json.loads(out)
    assert code == 0
    assert data["timeline"] == "Edit v1" and data["total"] == 2
    assert data["items"][0] == {
        "id": "item-5",
        "name": "A001_beach",
        "track": "A1",
        "start": 86400,
        "duration": 120,
    }


@pytest.mark.parametrize(
    ("connection", "code"),
    [("not_running", "RESOLVE_NOT_RUNNING"), ("free", "FREE_EDITION")],
)
def test_connection_failure_exits_1_with_the_code(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, connection: str, code: str
) -> None:
    fake_world.connection = connection  # type: ignore[assignment]
    for argv in (["items"], ["item", "V1:1"]):
        exit_code, out, _ = run_cli(argv)
        assert exit_code == 1
        assert parse_toon_keys(out)["code"] == code


def test_help_exits_0_with_examples(run_cli: Run, fake_world: FakeWorld) -> None:
    for argv, example in (
        (["items", "--help"], "davinci-axi items --timeline 'Edit v2' --track-type audio"),
        (["item", "--help"], "davinci-axi item V1:2"),
    ):
        code, out, err = run_cli(argv)
        assert (code, err) == (0, "")
        assert example in out
    assert fake_world.calls == []


# --- item <id> -----------------------------------------------------------------------------


def test_item_by_shorthand_and_by_id_return_the_same_detail(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, by_shorthand, err = run_cli(["item", "V1:2"])
    assert (code, err) == (0, "")
    _, by_id, _ = run_cli(["item", "item-2"])
    assert by_shorthand == by_id
    _, lower, _ = run_cli(["item", "v1:2"])
    assert lower == by_shorthand


def test_item_detail_view_for_a_linked_clip_with_a_marker(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["item", "V1:1"])
    keys = parse_toon_keys(out)
    assert code == 0
    assert keys["timeline"] == "Edit v1" and keys["id"] == "item-1" and keys["name"] == "clip-1"
    assert keys["type"] == "Video" and keys["track"] == "V1"
    assert (keys["start"], keys["end"], keys["duration"]) == ("86400", "86480", "80")
    assert keys["enabled"] == "true" and keys["color"] == "Blue"
    assert "flags[1]: Red" in out
    assert "media:\n  name: A001_beach\n  path: /media/davinci-axi-test/A001_beach.mov" in out
    assert "properties:\n  Pan: 0\n" in out and "  Opacity: 100\n" in out
    assert "markers[1]{frame,color,name,duration,note}:\n  10,Blue,Cut,1,check cut" in out
    assert "linked[1]{id,name,track}:\n  item-5,A001_beach,A1" in out
    assert "takes: []" in out and "fusion_comps: []" in out
    assert "help" not in out


def test_item_detail_shows_fusion_comps_and_empty_lists(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _, out, _ = run_cli(["item", "V1:2"])
    assert "fusion_comps[1]: Composition 1" in out
    assert "flags: []" in out and "markers: []" in out and "linked: []" in out


def test_item_without_media_and_color_shows_nulls(run_cli: Run, fake_world: FakeWorld) -> None:
    _, out, _ = run_cli(["item", "V2:1"])
    assert "type: Generator" in out and "color: null" in out and "media: null" in out
    assert "  Opacity: 80" in out


def test_item_takes_are_listed(run_cli: Run, fake_world: FakeWorld) -> None:
    clip = _clip(fake_world, 1)
    clip.takes = [{"startFrame": 0, "endFrame": 79}, {"startFrame": 10, "endFrame": 99}]
    clip.selected_take = 2
    _, out, _ = run_cli(["item", "V1:1"])
    assert "takes[2]{index,selected,start,end}:\n  1,false,0,79\n  2,true,10,99" in out


def test_item_audio_shorthand(run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld) -> None:
    _, out, _ = run_cli(["item", "A1:2"])
    keys = parse_toon_keys(out)
    assert keys["name"] == "music_bed" and keys["track"] == "A1" and keys["type"] == "Audio"
    assert "  AudioVolume: 0" in out


def test_audio_item_reads_back_volume_and_fades_without_a_take_selector(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    """F25: Resolve returns None from GetTakesCount on audio items; `item A1:1` used to crash."""
    code, out, err = run_cli(["item", "A1:1"])
    assert (code, err) == (0, "")
    assert "takes: []" in out and "  AudioVolume: 0" in out
    assert "fades:\n  in: 0\n  out: 0" in out
    assert fake_world.calls_to("TimelineItem.GetSelectedTakeIndex") == []
    _, by_id, _ = run_cli(["item", parse_toon_keys(out)["id"]])
    assert by_id == out


def test_item_fades_are_hidden_before_resolve_21_1(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.resolve.version = [20, 3, 1, 1, ""]
    code, out, _ = run_cli(["item", "A1:1"])
    assert code == 0 and "fades" not in out
    assert fake_world.calls_to("TimelineItem.GetFades") == []


def test_video_item_with_a_none_takes_count_lists_no_takes(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject("TimelineItem.GetTakesCount", None)
    code, out, _ = run_cli(["item", "V1:1"])
    assert code == 0 and "takes: []" in out


def test_item_timeline_flag_scopes_the_lookup(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    second = _project(fake_world).timelines[1]
    second.add_item("video", 1, "other", 5, 10)
    code, out, _ = run_cli(["item", "V1:1", "--timeline", "Edit v2"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["timeline"] == "Edit v2" and keys["name"] == "other"
    code, out, _ = run_cli(["item", "item-1", "--timeline", "Edit v2"])
    assert code == 1 and "NOT_FOUND" in out


@pytest.mark.parametrize(
    ("ref", "hint"),
    [
        ("V9:1", "This timeline has 2 video tracks"),
        ("A2:1", "This timeline has 1 audio tracks"),
        ("S1:1", "This timeline has 0 subtitle tracks"),
        ("V1:9", "Track V1 has 3 items"),
        ("V1:0", "Track V1 has 3 items"),
        ("V0:1", "This timeline has 2 video tracks"),
        ("nonsense", "Item ids come from `davinci-axi items`"),
        ("item", "Item ids come from"),
    ],
)
def test_unknown_item_is_not_found_with_a_specific_hint(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, ref: str, hint: str
) -> None:
    code, out, _ = run_cli(["item", ref])
    assert code == 1
    assert parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert hint in out


def test_item_id_prefixes_are_not_matched(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["item", "item-"])
    assert code == 1 and "NOT_FOUND" in out


def test_item_without_an_id_exits_2_without_a_resolve_call(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["item"])
    assert code == 2 and "missing <id>" in out
    assert fake_world.calls == []


def test_item_unknown_flag_exits_2_without_a_resolve_call(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["item", "V1:1", "--ful"])
    assert code == 2 and "--full" in out
    assert fake_world.calls == []


def _big_properties(world: FakeWorld) -> FakeTimelineItem:
    clip = _clip(world, 1)
    clip.properties = {f"Property{n:03d}": f"value-{n:03d}-padding" for n in range(120)}
    return clip


def test_long_properties_are_cut_at_1500_characters_with_the_full_hint(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _big_properties(fake_world)
    code, out, _ = run_cli(["item", "V1:1"])
    assert code == 0
    block = out.split("properties:\n", 1)[1].split("\nproperties_note", 1)[0]
    assert 1000 < len(block) <= 1500 + 2 * block.count("\n")
    assert "  Property000: value-000-padding" in out and "Property119" not in out
    note = parse_toon_keys(out)["properties_note"]
    assert "truncated at 1500 of" in note and "davinci-axi item V1:1 --full" in note
    assert all(line.split(":")[0].startswith("  Property") for line in block.splitlines())


def test_full_shows_every_property_without_a_note(run_cli: Run, fake_world: FakeWorld) -> None:
    _big_properties(fake_world)
    code, out, _ = run_cli(["item", "V1:1", "--full"])
    assert code == 0
    assert "  Property119: value-119-padding" in out
    assert "properties_note" not in out


def test_truncation_hint_keeps_the_timeline_flag(run_cli: Run, fake_world: FakeWorld) -> None:
    _big_properties(fake_world)
    _, out, _ = run_cli(["item", "V1:1", "--timeline", "Edit v1"])
    assert "davinci-axi item V1:1 --timeline 'Edit v1' --full" in out


def test_item_json_has_nested_properties_and_markers(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["item", "V1:1", "--json"])
    data = json.loads(out)
    assert code == 0
    assert data["properties"]["ZoomGang"] is True
    assert data["markers"] == [
        {"frame": 10, "color": "Blue", "name": "Cut", "duration": 1, "note": "check cut"}
    ]
    assert data["media"] == {
        "name": "A001_beach",
        "path": "/media/davinci-axi-test/A001_beach.mov",
    }
    assert data["start"] == 86400 and type(data["end"]) is int
    assert data["linked"][0]["track"] == "A1" and data["flags"] == ["Red"]


def test_read_commands_make_no_write_calls_and_keep_the_page(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.resolve.page = "color"
    current = _project(fake_world).current_timeline
    run_cli(["items"])
    run_cli(["items", "--timeline", "Edit v2", "--fields", "name,media,source_in", "--json"])
    run_cli(["item", "V1:1"])
    run_cli(["item", "item-5", "--full", "--json"])
    run_cli(["item", "V9:1"])
    assert fake_world.write_calls() == []
    assert fake_world.page_changes() == []
    assert fake_world.resolve.page == "color"
    assert _project(fake_world).current_timeline is current


def test_audit_fields_on_audio_items(run_cli: Run) -> None:
    fields = "name,volume,fade_in,fade_out"
    code, out, _ = run_cli(["items", "--track-type", "audio", "--fields", fields, "--json"])
    rows = json.loads(out)["items"]
    assert code == 0 and len(rows) == 2
    assert all(r["volume"] == 0.0 and r["fade_in"] == 0 and r["fade_out"] == 0 for r in rows)


def test_audit_fields_on_video_items(run_cli: Run) -> None:
    fields = "name,zoom,pan,tilt,resolution,super_scale"
    _, out, _ = run_cli(["items", "--track-type", "video", "--fields", fields, "--json"])
    rows = {r["name"]: r for r in json.loads(out)["items"]}
    assert rows["clip-1"]["zoom"] == 1.0 and rows["clip-1"]["resolution"] == "1920x1080"
    assert rows["clip-1"]["pan"] == 0.0 and rows["clip-1"]["super_scale"] == 1
    assert rows["Title"]["resolution"] is None and rows["Title"]["super_scale"] is None


def test_fps_is_a_float_and_volume_is_null_on_video(run_cli: Run, fake_world: FakeWorld) -> None:
    media = _clip(fake_world, 1).GetMediaPoolItem()
    assert media is not None
    media.properties["FPS"] = "24"
    _, out, _ = run_cli(["items", "--track-type", "video", "--fields", "fps,volume", "--json"])
    first = json.loads(out)["items"][0]
    assert first["fps"] == 24.0 and type(first["fps"]) is float and first["volume"] is None


def test_default_items_reads_no_audit_data(run_cli: Run, fake_world: FakeWorld) -> None:
    run_cli(["items"])
    run_cli(["items", "--fields", "name,media,end"])
    for method in ("TimelineItem.GetFades", "TimelineItem.GetProperties"):
        assert fake_world.calls_to(method) == []
    assert fake_world.calls_to("MediaPoolItem.GetClipProperty") == []


def test_bogus_field_lists_the_audit_fields_before_any_resolve_call(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["items", "--fields", "bogus"])
    assert code == 2 and "fade_in" in out + err and "super_scale" in out + err
    assert fake_world.calls_to("Project.GetCurrentTimeline") == []
