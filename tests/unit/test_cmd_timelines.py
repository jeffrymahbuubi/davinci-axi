from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeProject, FakeWorld

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def test_lists_both_timelines_and_marks_the_current(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["timelines"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["project"] == "davinci-axi-test"
    assert keys["total"] == "2"
    assert keys["current"] == "Edit v1"
    assert "timelines[2]{index,name,frames,video,audio,subtitle,current}:" in out
    assert "  1,Edit v1,240,2,1,0,true" in out
    assert "  2,Edit v2,120,1,1,0,false" in out
    assert "warnings" not in keys


def test_fields_adds_id_start_tc_and_fps(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timelines", "--fields", "name,id,start_tc,fps"])
    assert code == 0
    assert "timelines[2]{name,id,start_tc,fps}:" in out
    assert '  Edit v1,timeline-1,"01:00:00:00",24' in out
    assert fake_world.calls_to("Timeline.GetSettings")


def test_default_view_does_not_read_the_extra_fields(run_cli: Run, fake_world: FakeWorld) -> None:
    run_cli(["timelines"])
    assert fake_world.calls_to("Timeline.GetSettings") == []
    assert fake_world.calls_to("Timeline.GetUniqueId") == []


def test_unknown_field_exits_2_listing_valid_fields_without_a_resolve_call(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timelines", "--fields", "name,bogus"])
    assert code == 2
    assert "VALIDATION_ERROR" in out
    assert "bogus" in out and "start_tc" in out
    assert fake_world.calls == []


def test_unknown_field_is_rejected_even_when_there_are_no_timelines(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _project(fake_world).timelines.clear()
    code, _, _ = run_cli(["timelines", "--fields", "bogus"])
    assert code == 2


def test_unknown_flag_exits_2_without_a_resolve_call(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timelines", "--stat"])
    assert code == 2
    assert "--limit" in out and "--fields" in out
    assert fake_world.calls == []


def test_limit_truncates_but_states_the_total(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timelines", "--limit", "1"])
    keys = parse_toon_keys(out)
    assert code == 0
    assert keys["total"] == "2"
    assert "timelines[1]{" in out
    assert "--limit 2" in out


def test_limit_zero_exits_2(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timelines", "--limit", "0"])
    assert code == 2
    assert "--limit must be 1 or more" in out
    assert fake_world.calls == []


def test_unreadable_timeline_is_skipped_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld
) -> None:
    project = _project(fake_world)
    original = project.GetTimelineByIndex

    def flaky(idx: int) -> object:
        return None if idx == 1 else original(idx)

    monkeypatch.setattr(project, "GetTimelineByIndex", flaky)
    code, out, _ = run_cli(["timelines"])
    assert code == 0
    assert "timelines[1]{" in out
    assert "  2,Edit v2,120,1,1,0,false" in out
    assert "warnings[1]: timeline at index 1 could not be read and was skipped" in out
    assert "total: 1" in out


def test_project_without_timelines_states_the_scope(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    project = _project(fake_world)
    project.timelines.clear()
    project.current_timeline = None
    code, out, _ = run_cli(["timelines"])
    keys = parse_toon_keys(out)
    assert code == 0
    assert keys["timelines"] == "0 timelines in project davinci-axi-test"
    assert keys["total"] == "0" and keys["current"] == "null"


def test_no_project_is_a_coded_error_with_a_fix(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.resolve.pm.current_project = None
    code, out, _ = run_cli(["timelines"])
    assert code == 1
    assert parse_toon_keys(out)["code"] == "NO_PROJECT"
    assert "Open a project in Resolve" in out


@pytest.mark.parametrize(
    ("connection", "code"),
    [
        ("not_running", "RESOLVE_NOT_RUNNING"),
        ("scripting_disabled", "SCRIPTING_DISABLED"),
        ("free", "FREE_EDITION"),
        ("not_installed", "RESOLVE_NOT_INSTALLED"),
    ],
)
def test_connection_failure_exits_1_with_the_code(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, connection: str, code: str
) -> None:
    fake_world.connection = connection  # type: ignore[assignment]
    exit_code, out, _ = run_cli(["timelines"])
    assert exit_code == 1
    assert parse_toon_keys(out)["code"] == code


def test_json_matches_the_toon_content(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timelines", "--json"])
    data = json.loads(out)
    assert code == 0
    assert data["project"] == "davinci-axi-test" and data["total"] == 2
    assert data["current"] == "Edit v1"
    assert data["timelines"][0] == {
        "index": 1,
        "name": "Edit v1",
        "frames": 240,
        "video": 2,
        "audio": 1,
        "subtitle": 0,
        "current": True,
    }
    assert [t["current"] for t in data["timelines"]] == [True, False]


def test_hints_name_only_registered_commands(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld
) -> None:
    monkeypatch.delitem(registry.COMMANDS, "items", raising=False)
    monkeypatch.delitem(registry.COMMANDS, "item", raising=False)
    _, out, _ = run_cli(["timelines"])
    assert "davinci-axi items" not in out and "davinci-axi item " not in out
    monkeypatch.setitem(registry.COMMANDS, "items", "x:y")
    monkeypatch.setitem(registry.COMMANDS, "item", "x:y")
    _, out, _ = run_cli(["timelines"])
    assert "Run `davinci-axi items --timeline 'Edit v1'` to list the items" in out
    assert "Run `davinci-axi item <id>` to show one timeline item in detail" in out


def test_help_exits_0_with_examples(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timelines", "--help"])
    assert code == 0
    assert "--fields" in out and "--limit" in out
    assert "davinci-axi timelines --fields name,id,fps" in out
    assert fake_world.calls == []


def test_makes_no_write_calls_and_keeps_the_page(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.resolve.page = "color"
    current = _project(fake_world).current_timeline
    run_cli(["timelines"])
    run_cli(["timelines", "--fields", "name,id,start_tc,fps", "--json"])
    assert fake_world.write_calls() == []
    assert fake_world.page_changes() == []
    assert fake_world.resolve.GetCurrentPage() == "color"
    assert _project(fake_world).current_timeline is current
