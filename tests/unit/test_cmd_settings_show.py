"""`project settings show` and `timeline settings show` (F15a), against the fake."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from davinci_axi import registry
from davinci_axi.commands import timeline
from davinci_axi.commands.settings_show import TIMELINE_SETTINGS_SHOW_SPEC
from davinci_axi.fake import FakeProject, FakeTimeline, FakeWorld, ext_timeline_edit

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

NOTE = "timeline follows the project settings (useCustomSettings is 0)"


@pytest.fixture(autouse=True)
def _registered(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_timeline_edit.extend()
    # The lead wires this in `timeline_extra.py`; the test builds the same spec locally.
    wrapped = replace(
        timeline.SETTINGS_SPEC,
        summary="Show or change timeline settings",
        subcommands={**timeline.SETTINGS_SPEC.subcommands, "show": TIMELINE_SETTINGS_SHOW_SPEC},
        methods=tuple(
            dict.fromkeys((*timeline.SETTINGS_SPEC.methods, *TIMELINE_SETTINGS_SHOW_SPEC.methods))
        ),
    )
    monkeypatch.setattr(
        timeline,
        "SPEC",
        replace(timeline.SPEC, subcommands={**timeline.SPEC.subcommands, "settings": wrapped}),
    )
    monkeypatch.setitem(registry.COMMANDS, "project", "davinci_axi.commands.project_extra:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timeline", "davinci_axi.commands.timeline:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    current = world.resolve.pm.current_project
    assert current is not None
    return current


def _timeline(world: FakeWorld, name: str) -> FakeTimeline:
    return next(t for t in _project(world).timelines if t.name == name)


def test_project_show_lists_every_setting(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["project", "settings", "show"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["count"] == "4"
    assert "timelineFrameRate" in out and "24" in out
    assert "colorScienceMode" in out and "davinciYRGB" in out
    assert fake_world.write_calls() == []


def test_project_show_picks_the_named_keys(run_cli: Run, parse_toon_keys: Keys) -> None:
    code, out, _ = run_cli(
        ["project", "settings", "show", "timelineResolutionWidth, timelineResolutionHeight"]
    )
    assert code == 0 and parse_toon_keys(out)["count"] == "2"
    assert "timelineResolutionWidth" in out and "1920" in out and "1080" in out
    assert "timelineFrameRate" not in out


def test_project_show_unknown_key_lists_the_valid_ones(run_cli: Run) -> None:
    code, out, err = run_cli(["project", "settings", "show", "nope"])
    assert code == 2
    text = out + err
    assert "unknown project setting nope" in text
    assert "Valid settings (4)" in text and "timelineFrameRate" in text


def test_project_show_is_listed_in_help(run_cli: Run) -> None:
    code, out, _ = run_cli(["project", "settings", "--help"])
    assert code == 0 and "show" in out and "preset" in out and "set" in out


def test_timeline_show_on_the_default_timeline_notes_inherited_settings(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["timeline", "settings", "show"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["custom"] == "false" and keys["timeline"] == "Edit v1"
    assert NOTE in out
    assert fake_world.write_calls() == []


def test_timeline_show_custom_settings_has_no_note(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _timeline(fake_world, "Edit v1").settings["useCustomSettings"] = "1"
    code, out, _ = run_cli(["timeline", "settings", "show"])
    assert code == 0 and parse_toon_keys(out)["custom"] == "true"
    assert NOTE not in out


def test_timeline_show_reads_the_named_timeline(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _timeline(fake_world, "Edit v2").settings["timelineFrameRate"] = 30.0
    code, out, _ = run_cli(
        ["timeline", "settings", "show", "timelineFrameRate", "--timeline", "Edit v2"]
    )
    keys = parse_toon_keys(out)
    assert code == 0 and keys["timeline"] == "Edit v2" and keys["count"] == "1"
    assert "timelineFrameRate: 30" in out and "useCustomSettings:" not in out


def test_timeline_show_unknown_key_exits_two(run_cli: Run) -> None:
    code, out, err = run_cli(["timeline", "settings", "show", "bogus"])
    assert code == 2
    assert "unknown timeline setting bogus" in out + err
