"""Round 2 U4: `timeline title add` runs the scratch-timeline recipe as one mutation.

`timeline_extra.NEW` does not carry `title` until the lead splices it, so the tests wrap the spec
locally and point the registry at this module's `SPEC`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from davinci_axi import registry
from davinci_axi.commands import timeline_extra
from davinci_axi.commands.timeline_title import TITLE_SPEC, free_span
from davinci_axi.commands.timeline_title_style import TEXT_INPUT_IDS, point_of, read_inputs
from davinci_axi.fake import FakeProject, FakeTimeline, FakeWorld

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]
SPEC = replace(
    timeline_extra.SPEC, subcommands={**timeline_extra.SPEC.subcommands, "title": TITLE_SPEC}
)
BASE = ["timeline", "title", "add", "Sunday at the Aquarium", "--at", "01:00:10:00"]
CALLS = (
    "Timeline.InsertFusionTitleIntoTimeline",
    "FusionTool.SetInput",
    "Timeline.CreateCompoundClip",
    "MediaPool.AppendToTimeline",
    "MediaPool.DeleteTimelines",
)


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "timeline", f"{__name__}:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    return project


def _timeline(world: FakeWorld, name: str) -> FakeTimeline:
    return next(t for t in _project(world).timelines if t.name == name)


def _count(world: FakeWorld, method: str) -> int:
    return len(world.calls_to(method))


def test_the_dry_run_validates_and_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli([*BASE, "--duration", "90"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false"
    assert "title,null,V2@86640 (90 frames)" in out
    assert keys["text"] == "Sunday at the Aquarium" and keys["track"] == "V2"
    assert keys["scratch"].startswith("davinci-axi-title-")
    assert "compound clip" in keys["warning"] and "--force" in out
    assert fake_world.write_calls() == []


def test_force_runs_the_recipe_once_and_reads_the_title_back(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    before = _project(fake_world).GetTimelineCount()
    code, out, _ = run_cli([*BASE, "--duration", "90", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true"
    assert "title,null,V2@86640 (90 frames)" in out
    for method in CALLS:
        assert _count(fake_world, method) == 1, method
    assert fake_world.calls_to("FusionTool.SetInput") == [
        ("StyledText", "Sunday at the Aquarium", None)
    ]
    for step in (
        "scratch created",
        "title inserted 90 frames",
        "text set",
        "compound title: Sunday at the Aquarium",
        "appended V2@86640",
        "scratch deleted",
    ):
        assert step in out, step
    assert "snapshot" in out
    edit = _timeline(fake_world, "Edit v1")
    placed = [i for i in edit.tracks["video"][1].items if i.start == 86640]
    assert (
        len(placed) == 1
        and placed[0].duration == 90
        and placed[0].name == "title: Sunday at the Aquarium"
    )
    assert keys["item"] == '"V2:2"'
    assert any(
        c.GetName() == "title: Sunday at the Aquarium"
        for c in _project(fake_world).media_pool.current.clips
    )
    assert _project(fake_world).GetTimelineCount() == before == int(keys["timeline_count"])
    assert _project(fake_world).current_timeline is edit


def test_keep_scratch_keeps_the_timeline_and_omits_the_delete_step(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    before = _project(fake_world).GetTimelineCount()
    assert "--keep-scratch --force" in run_cli([*BASE, "--keep-scratch"])[1]
    code, out, _ = run_cli([*BASE, "--keep-scratch", "--force"])
    assert code == 0 and "scratch deleted" not in out
    assert _count(fake_world, "MediaPool.DeleteTimelines") == 0
    assert _project(fake_world).GetTimelineCount() == before + 1


def test_an_occupied_span_is_refused_with_the_occupants_and_a_way_out(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "title", "add", "Card", "--at", "01:00:01:16"])
    assert code == 1 and parse_toon_keys(out)["code"] == "WRITE_REFUSED"
    assert "V2:1 'Title' 86440-86500" in out
    assert "--frame 86500" in out and "--track 3" in out
    assert fake_world.write_calls() == []


def test_a_missing_track_is_not_found(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli([*BASE, "--track", "5"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert "2 video tracks" in out and "timeline track add video" in out
    assert fake_world.write_calls() == []


def test_at_and_frame_together_or_neither_are_usage_errors(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    both = run_cli([*BASE, "--frame", "86700"])
    neither = run_cli(["timeline", "title", "add", "Card"])
    assert both[0] == 2 and "mutually exclusive" in both[1]
    assert neither[0] == 2 and "--at" in neither[1]
    assert fake_world.calls == [] or fake_world.write_calls() == []


def test_text_over_200_characters_or_empty_is_exit_2(run_cli: Run, fake_world: FakeWorld) -> None:
    long = run_cli(["timeline", "title", "add", "x" * 201, "--at", "01:00:10:00"])
    empty = run_cli(["timeline", "title", "add", "  ", "--at", "01:00:10:00"])
    assert long[0] == 2 and "201 characters" in long[1]
    assert empty[0] == 2 and "empty" in empty[1]
    assert fake_world.calls == []


def test_a_frame_before_the_timeline_start_is_refused(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "title", "add", "Card", "--frame", "100"])
    assert code == 2 and "before the timeline start (86400" in out


def test_a_taken_compound_name_is_refused_before_any_write(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    clip = _project(fake_world).media_pool.current.clips[0]
    code, out, _ = run_cli([*BASE, "--name", clip.name])
    assert code == 2 and "already exists" in out
    assert fake_world.write_calls() == []


def test_the_other_timeline_is_a_valid_target_and_stays_unopened(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    other = _timeline(fake_world, "Edit v2")
    argv = ["timeline", "title", "add", "Card", "--frame", "86500", "--track", "1"]
    code, out, _ = run_cli([*argv, "--timeline", "Edit v2"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert "--timeline 'Edit v2' --force" in out or "--timeline Edit v2 --force" in out
    code, out, _ = run_cli([*argv, "--timeline", "Edit v2", "--force"])
    assert code == 0 and "V1@86500 (90 frames)" in out
    assert [(i.start, i.duration) for i in other.tracks["video"][0].items] == [(86500, 90)]
    assert _project(fake_world).current_timeline is _timeline(fake_world, "Edit v1")
    assert parse_toon_keys(out)["timeline_restored"] == "true"


def test_a_failed_compound_ends_in_api_failed_naming_the_steps(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("Timeline.CreateCompoundClip", None)
    code, out, _ = run_cli([*BASE, "--force"])
    keys = parse_toon_keys(out)
    assert code == 1 and keys["code"] == "API_FAILED"
    assert "done so far: scratch created, title inserted 90 frames, text set" in out
    assert _count(fake_world, "MediaPool.AppendToTimeline") == 0
    assert _project(fake_world).current_timeline is _timeline(fake_world, "Edit v1")


def test_the_fake_title_comp_has_a_template_tool_with_styled_text(fake_world: FakeWorld) -> None:
    scratch = _project(fake_world).add_timeline("scratch", current=True)
    scratch.add_track("video")
    item = scratch.InsertFusionTitleIntoTimeline("Text+")
    assert item is not None
    tool = item.GetFusionCompByIndex(1).FindTool("Template")
    assert tool is not None and tool.GetInput("StyledText") == "Text"


def test_free_span_lists_only_overlapping_items(fake_world: FakeWorld) -> None:
    edit = _timeline(fake_world, "Edit v1")
    assert [o.position for o in free_span(edit, 2, 86400, 40)] == []
    assert [o.position for o in free_span(edit, 2, 86400, 41)] == [1]
    assert [o.position for o in free_span(edit, 2, 86499, 5)] == [1]
    assert [o.position for o in free_span(edit, 2, 86500, 5)] == []


STYLE = ["--size", "0.12", "--font", "Open Sans", "--color", "yellow", "--y", "0.2"]


def test_the_style_flags_set_each_input_after_the_text_and_read_it_back(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli([*BASE, *STYLE, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and "style_read_back" not in keys
    calls = fake_world.calls_to("FusionTool.SetInput")
    assert [(c[0], c[1]) for c in calls] == [
        ("StyledText", "Sunday at the Aquarium"),
        ("Size", 0.12),
        ("Font", "Open Sans"),
        ("Red1", 1.0),
        ("Green1", 1.0),
        ("Blue1", 0.0),
        ("Center", {1: 0.5, 2: 0.2}),
    ]
    assert "style set (6 inputs)" in out
    assert "size_source" in out and "flag" in out and "#ffff00" in out


def test_landscape_without_style_flags_sets_only_the_text(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli([*BASE, "--force"])
    assert code == 0 and "style set" not in out and "style_read_back" not in out
    assert [c[0] for c in fake_world.calls_to("FusionTool.SetInput")] == ["StyledText"]


def test_a_portrait_timeline_gets_the_scaled_default_size(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _project(fake_world).settings.update(
        timelineResolutionWidth="1080", timelineResolutionHeight="1920"
    )
    dry = run_cli(BASE)[1]
    assert "auto (1080x1920)" in dry
    code, out, _ = run_cli([*BASE, "--force"])
    assert code == 0 and "style set (1 inputs)" in out
    sizes = [c for c in fake_world.calls_to("FusionTool.SetInput") if c[0] == "Size"]
    assert [c[1] for c in sizes] == [0.142]


def test_a_style_read_back_mismatch_is_reported_and_does_not_fail(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("FusionTool.GetInput", 0.5)
    code, out, _ = run_cli([*BASE, "--size", "0.12", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert "style_read_back" in out and "Size" in out


@pytest.mark.parametrize(
    "bad", [["--color", "teal"], ["--size", "2"], ["--y", "1.5"], ["--font", " "]]
)
def test_a_bad_style_flag_is_exit_2_before_connecting(
    run_cli: Run, fake_world: FakeWorld, bad: list[str]
) -> None:
    code, out, _ = run_cli([*BASE, *bad, "--force"])
    assert code == 2 and "Valid" in out
    assert fake_world.calls == []


def test_the_dry_run_carries_the_style_words_and_the_applied_run_hints_the_cleanup(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli([*BASE, *STYLE, "--name", "Card A"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert "--size 0.12 --font 'Open Sans' --color yellow --y 0.2" in out
    assert fake_world.write_calls() == []
    code, out, _ = run_cli([*BASE, *STYLE, "--name", "Card A", "--force"])
    assert code == 0 and "media delete 'Card A' --force" in out


def test_help_lists_the_four_style_flags(run_cli: Run) -> None:
    out = run_cli(["timeline", "title", "add", "--help"])[1]
    for flag in ("--size", "--font", "--color", "--y"):
        assert flag in out


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ({1: 0.5, 2: 0.2, 3: 0.0}, (0.5, 0.2)),
        ({"1": 0.5, "2": 0.2}, (0.5, 0.2)),
        ([0.5, 0.2], (0.5, 0.2)),
        ((0.5, 0.2, 0.0), (0.5, 0.2)),
        ("0.5 0.2", (0.5, 0.2)),
        ({1: 0.5}, None),
        (None, None),
        ("x", None),
        (0.5, None),
    ],
)
def test_point_of_normalises_every_form_resolve_returns(
    value: object, want: tuple[float, float] | None
) -> None:
    assert point_of(value) == want


def _center_run(run_cli: Run, fake_world: FakeWorld, got: object) -> str:
    fake_world.inject("FusionTool.GetInput", got)
    code, out, _ = run_cli([*BASE, "--y", "0.2", "--force"])
    assert code == 0
    return out


@pytest.mark.parametrize("got", [{1: 0.5, 2: 0.2, 3: 0.0}, "0.5 0.2", [0.5, 0.2]])
def test_a_matching_center_in_any_form_is_not_a_read_back_mismatch(
    run_cli: Run, fake_world: FakeWorld, got: object
) -> None:
    assert "style_read_back" not in _center_run(run_cli, fake_world, got)


def test_a_real_center_difference_reports_the_point_as_text(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    out = _center_run(run_cli, fake_world, {1: 0.5, 2: 0.3})
    assert "style_read_back" in out and "0.5 0.3" in out


def test_text_inputs_are_reported_on_a_force_run_without_style_flags(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli([*BASE, "--force"])
    assert code == 0 and "inputs read" in out
    assert "text_inputs" in out and "StyledText" in out and "0.5 0.5" in out
    read = [c[0] for c in fake_world.calls_to("FusionTool.GetInput")]
    assert all(read.count(key) >= 1 for key in TEXT_INPUT_IDS)


def test_read_inputs_has_the_eight_keys_and_nulls_for_unreadable_ids(
    fake_world: FakeWorld,
) -> None:
    scratch = _project(fake_world).add_timeline("scratch", current=True)
    scratch.add_track("video")
    item = scratch.InsertFusionTitleIntoTimeline("Text+")
    assert item is not None
    tool = item.GetFusionCompByIndex(1).FindTool("Template")
    got = read_inputs(tool)
    assert tuple(got) == TEXT_INPUT_IDS and len(got) == 8
    assert got["StyledText"] == "Text" and got["Center"] == "0.5 0.5"
    fake_world.inject("FusionTool.GetInput", RuntimeError("boom"))
    assert read_inputs(tool) == dict.fromkeys(TEXT_INPUT_IDS)


def test_the_dry_run_has_no_text_inputs_and_help_explains_size(run_cli: Run) -> None:
    assert "text_inputs" not in run_cli(BASE)[1]
    out = run_cli(["timeline", "title", "add", "--help"])[1]
    assert "glyph scale" in out and "0.142" in out
