"""U26: the 16 read methods that closed the R36 gap, against the fake. All reads, none writes."""

from __future__ import annotations

import json
from collections.abc import Callable

from davinci_axi.fake import FakeWorld

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


def _project(world: FakeWorld):  # type: ignore[no-untyped-def]
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _clean(world: FakeWorld) -> None:
    assert world.write_calls() == []
    assert world.page_changes() == []


# --- app show -------------------------------------------------------------------------------


def test_app_show_reports_what_resolve_considers_current(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["app", "show"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["version"] == "21.1.1.10010"
    assert keys["product"] == "DaVinci Resolve Studio"
    assert keys["edition"] == "Studio"
    assert keys["page"] == "edit"
    assert keys["project"] == "davinci-axi-test"
    assert keys["timeline"] == "Edit v1"
    assert keys["media_pool_id"] == _project(fake_world).media_pool.uid
    assert "gallery_album" in keys
    assert fake_world.calls_to("Resolve.GetGallery") != []
    _clean(fake_world)


def test_app_show_without_a_project_answers_null(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.resolve.pm.current_project = None
    code, out, _ = run_cli(["app", "show"])
    keys = parse_toon_keys(out)
    assert code == 0
    for key in ("project", "timeline", "media_pool_id", "gallery_album"):
        assert keys[key] == "null"
    _clean(fake_world)


def test_app_show_without_a_gallery_answers_null(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("Resolve.GetGallery", None)
    code, out, _ = run_cli(["app", "show"])
    assert code == 0
    assert parse_toon_keys(out)["gallery_album"] == "null"


def test_app_show_json_has_the_same_keys_plus_help(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _, toon, _ = run_cli(["app", "show"])
    code, out, _ = run_cli(["app", "show", "--json"])
    payload = json.loads(out)
    assert code == 0
    assert set(payload) == {*parse_toon_keys(toon), "help"}


# --- timeline show --------------------------------------------------------------------------


def test_timeline_show_details_the_current_timeline(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["timeline", "show"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["name"] == "Edit v1"
    assert keys["current"] == "true"
    assert keys["frames"] == "240"
    assert keys["start_frame"] == "86400"
    assert keys["end_frame"] == "86640"
    assert keys["start_tc"] == '"01:00:00:00"'
    assert keys["fps"] == "24"
    assert (keys["video"], keys["audio"], keys["subtitle"]) == ("2", "1", "0")
    assert keys["markers"] == "1"
    assert keys["pool_clip"] == "null"
    assert "current_video_item:\n  id: item-1\n  name: clip-1\n  track: V1\n" in out
    assert "item V1:1" in out
    _clean(fake_world)


def test_timeline_show_of_another_timeline_skips_the_playhead_item(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "show", "--timeline", "Edit v2"])
    keys = parse_toon_keys(out)
    assert code == 0
    assert keys["current"] == "false"
    assert keys["current_video_item"] == "null"
    assert fake_world.calls_to("Timeline.GetCurrentVideoItem") == []
    assert fake_world.calls_to("Project.SetCurrentTimeline") == []
    _clean(fake_world)


def test_timeline_show_names_the_pool_clip(run_cli: Run, fake_world: FakeWorld) -> None:
    clip = _project(fake_world).media_pool.root.subfolders[0].clips[0]
    fake_world.inject("Timeline.GetMediaPoolItem", clip)
    code, out, _ = run_cli(["timeline", "show"])
    assert code == 0
    assert f"pool_clip:\n  id: {clip.uid}\n  name: A001_beach\n" in out


def test_timeline_show_errors(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timeline", "show", "--timeline", "nope"])
    assert code == 1
    assert "code: NOT_FOUND" in out
    _project(fake_world).current_timeline = None
    code, out, _ = run_cli(["timeline", "show"])
    assert code == 1
    assert "code: NO_TIMELINE" in out


# --- item offsets and source times ----------------------------------------------------------


def test_item_reports_offsets_and_source_times(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["item", "V1:1"])
    keys = parse_toon_keys(out)
    assert code == 0
    assert keys["source_start_time"] == "0"  # TOON drops a trailing .0
    assert keys["left_offset"] == "0"
    assert keys["right_offset"] == "0"
    _clean(fake_world)


def test_item_offsets_follow_the_api(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("TimelineItem.GetLeftOffset", 12.0)
    fake_world.inject("TimelineItem.GetRightOffset", 7.0)
    fake_world.inject("TimelineItem.GetSourceEndTime", 3.5)
    code, out, _ = run_cli(["item", "V1:1"])
    keys = parse_toon_keys(out)
    assert code == 0
    assert (keys["left_offset"], keys["right_offset"], keys["source_end_time"]) == (
        "12",
        "7",
        "3.5",
    )
    code, out, _ = run_cli(["item", "V1:1", "--json"])
    payload = json.loads(out)
    assert isinstance(payload["left_offset"], int)
    assert isinstance(payload["right_offset"], int)
    assert isinstance(payload["source_start_time"], float)
    assert payload["source_end_time"] == 3.5


def test_items_list_does_not_call_the_item_only_reads(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _, _ = run_cli(["items"])
    assert code == 0
    for method in ("GetLeftOffset", "GetRightOffset", "GetSourceStartTime", "GetSourceEndTime"):
        assert fake_world.calls_to(f"TimelineItem.{method}") == []


# --- projects --fields modified_epoch -------------------------------------------------------


def test_projects_modified_epoch_is_opt_in(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["projects", "--fields", "name,modified_epoch"])
    assert code == 0
    assert "projects[2]{name,modified_epoch}:" in out
    assert out.count("1760000000") == 2
    calls = fake_world.calls_to("ProjectManager.GetProjectLastModifiedTime")
    assert sorted(str(args[0]) for args in calls) == ["davinci-axi-test", "sample-project"]


def test_projects_default_makes_no_epoch_call(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _, _ = run_cli(["projects"])
    assert code == 0
    assert fake_world.calls_to("ProjectManager.GetProjectLastModifiedTime") == []


# --- media show and media -------------------------------------------------------------------


def test_media_show_reports_media_id_and_timeline(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["media", "show", "A001_beach"])
    keys = parse_toon_keys(out)
    assert code == 0
    assert keys["media_id"] == "media-1"
    assert keys["timeline"] == "null"
    _clean(fake_world)


def test_media_show_names_the_timeline_of_a_timeline_clip(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    timeline = _project(fake_world).current_timeline
    assert timeline is not None
    fake_world.inject("MediaPoolItem.GetTimeline", timeline)
    code, out, _ = run_cli(["media", "show", "A001_beach"])
    assert code == 0
    assert parse_toon_keys(out)["timeline"] == "Edit v1"


def test_media_show_survives_a_clip_whose_timeline_read_raises(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("MediaPoolItem.GetTimeline", RuntimeError("not a timeline clip"))
    code, out, _ = run_cli(["media", "show", "A001_beach"])
    assert code == 0
    assert parse_toon_keys(out)["timeline"] == "null"


def test_media_list_reports_folder_staleness(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["media"])
    assert code == 0
    assert parse_toon_keys(out)["folder_stale"] == "false"
    assert "folders[1]{path,clips,subfolders}:" in out
    fake_world.inject("Folder.GetIsFolderStale", True)
    _, out, _ = run_cli(["media"])
    assert parse_toon_keys(out)["folder_stale"] == "true"
    code, out, _ = run_cli(["media", "--folder", "Master/Footage"])
    assert code == 0
    assert "folder_stale" in parse_toon_keys(out)


# --- the new leaves are documented ----------------------------------------------------------


def test_new_leaves_have_help(run_cli: Run) -> None:
    for argv in (["app", "show", "--help"], ["timeline", "show", "--help"]):
        code, out, _ = run_cli(argv)
        assert code == 0
        assert "examples[" in out
