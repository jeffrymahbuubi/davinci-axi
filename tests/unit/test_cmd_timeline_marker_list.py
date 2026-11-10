"""`timeline marker list` and the `timeline show` hint to it, on the fake (F50f1)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.commands import timeline_extra
from davinci_axi.commands.timeline_marker_list import MARKER_LIST_SPEC
from davinci_axi.fake import FakeProject, FakeTimeline, FakeWorld, ext_timeline_edit
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

MARKER = replace(
    timeline_extra.WRAPPED_MARKER,
    subcommands={**timeline_extra.WRAPPED_MARKER.subcommands, "list": MARKER_LIST_SPEC},
)
SPEC: CommandSpec = replace(
    timeline_extra.SPEC, subcommands={**timeline_extra.SPEC.subcommands, "marker": MARKER}
)
LIST = ["timeline", "marker", "list"]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_timeline_edit.extend()
    monkeypatch.setitem(registry.COMMANDS, "timeline", f"{__name__}:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")


def _timeline(world: FakeWorld, name: str = "Edit v1") -> FakeTimeline:
    project: FakeProject | None = world.resolve.pm.current_project
    assert project is not None
    return next(t for t in project.timelines if t.name == name)


def _mark(
    world: FakeWorld, frame: int, color: str, name: str, timeline: str = "Edit v1", **more: Any
) -> None:
    marker = {"color": color, "name": name, "note": "", "duration": 1, "customData": ""}
    _timeline(world, timeline).markers[frame] = {**marker, **more}


@pytest.fixture
def bare(fake_world: FakeWorld) -> FakeWorld:
    """The fake world with the default timeline's seeded marker removed."""
    _timeline(fake_world).markers.clear()
    return fake_world


@pytest.fixture
def three(bare: FakeWorld) -> FakeWorld:
    _mark(bare, 50, "Green", "b", note="second", customData="tag-b")
    _mark(bare, 10, "Red", "a", duration=4)
    _mark(bare, 70, "Red", "c")
    return bare


def _json(out: str) -> dict[str, Any]:
    data = json.loads(out)
    assert isinstance(data, dict)
    return data


def test_lists_markers_sorted_with_the_default_fields(run_cli: Run, three: FakeWorld) -> None:
    code, out, err = run_cli([*LIST, "--json"])
    data = _json(out)
    assert (code, err) == (0, "")
    assert data["total"] == 3 and "truncated" not in data
    assert [m["frame"] for m in data["markers"]] == [10, 50, 70]
    assert set(data["markers"][0]) == {"frame", "color", "name", "note", "duration", "custom"}
    assert data["markers"][0]["duration"] == 4 and data["markers"][1]["note"] == "second"
    assert any("timeline marker delete --frame 10" in h for h in data["help"])
    assert any("timeline marker add --frame" in h for h in data["help"])
    assert three.write_calls() == []


def test_custom_column_appears_only_when_a_marker_has_custom_data(
    run_cli: Run, three: FakeWorld
) -> None:
    _, out, _ = run_cli([*LIST, "--json"])
    rows = _json(out)["markers"]
    assert [r["custom"] for r in rows] == ["", "tag-b", ""]
    assert "abs_frame" not in rows[0]
    _timeline(three).markers[50]["customData"] = ""
    _, out, _ = run_cli([*LIST, "--json"])
    rows = _json(out)["markers"]
    assert all("custom" not in r for r in rows)


def test_custom_column_in_text_output_and_fields_override(run_cli: Run, three: FakeWorld) -> None:
    _, out, _ = run_cli(LIST)
    assert "custom" in out and "tag-b" in out
    _, out, _ = run_cli([*LIST, "--fields", "frame,name", "--json"])
    assert set(_json(out)["markers"][0]) == {"frame", "name"}


def test_help_notes_custom_shown_by_default(run_cli: Run) -> None:
    _, out, _ = run_cli([*LIST, "--help"])
    assert "shown by default when" in out and "custom data when any marker has it" in out


def test_extra_fields_show_abs_frame_and_custom(run_cli: Run, three: FakeWorld) -> None:
    start = _timeline(three).start_frame
    _, out, _ = run_cli([*LIST, "--fields", "frame,abs_frame,custom", "--json"])
    rows = _json(out)["markers"]
    assert rows[1] == {"frame": 50, "abs_frame": start + 50, "custom": "tag-b"}


def test_color_filter_and_its_empty_state(run_cli: Run, three: FakeWorld) -> None:
    _, out, _ = run_cli([*LIST, "--color", "Red", "--json"])
    data = _json(out)
    assert data["total"] == 2 and [m["frame"] for m in data["markers"]] == [10, 70]
    _, out, _ = run_cli([*LIST, "--color", "Cyan", "--json"])
    data = _json(out)
    assert data["total"] == 0
    assert data["markers"] == "0 markers in timeline Edit v1 (filters: color=Cyan)"


def test_no_markers_is_an_empty_state(run_cli: Run, bare: FakeWorld) -> None:
    _, out, _ = run_cli([*LIST, "--json"])
    assert _json(out)["markers"] == "0 markers in timeline Edit v1"


def test_limit_marks_the_list_truncated_and_hints_the_full_list(
    run_cli: Run, three: FakeWorld
) -> None:
    _, out, _ = run_cli([*LIST, "--limit", "1", "--json"])
    data = _json(out)
    assert data["total"] == 3 and data["truncated"] is True and len(data["markers"]) == 1
    assert any("timeline marker list --limit 3" in h for h in data["help"])
    assert any("list all 3 markers" in h for h in data["help"])


@pytest.mark.parametrize(
    "args",
    [["--color", "Mauve"], ["--fields", "mood"], ["--limit", "0"]],
)
def test_bad_flags_exit_two_without_a_resolve_call(
    run_cli: Run, fake_world: FakeWorld, args: list[str]
) -> None:
    code, out, _ = run_cli([*LIST, *args, "--json"])
    assert code == 2 and _json(out)["help"] and fake_world.calls == []


def test_timeline_flag_targets_another_timeline(run_cli: Run, three: FakeWorld) -> None:
    _mark(three, 20, "Blue", "other", timeline="Edit v2")
    _, out, _ = run_cli([*LIST, "--timeline", "Edit v2", "--json"])
    data = _json(out)
    assert data["timeline"] == "Edit v2" and [m["name"] for m in data["markers"]] == ["other"]


def test_show_names_the_list_command_only_when_markers_exist(run_cli: Run, bare: FakeWorld) -> None:
    _, out, _ = run_cli(["timeline", "show", "--json"])
    data = _json(out)
    assert data["markers"] == 0
    assert not any("timeline marker list" in h for h in data["help"])
    _mark(bare, 10, "Red", "a")
    _, out, _ = run_cli(["timeline", "show", "--json"])
    data = _json(out)
    assert data["markers"] == 1
    assert any("timeline marker list" in h for h in data["help"])


def test_help_lists_the_flags(run_cli: Run) -> None:
    code, out, err = run_cli([*LIST, "--help"])
    assert code == 0 and err == ""
    assert "--color" in out and "--fields" in out and "--limit" in out
