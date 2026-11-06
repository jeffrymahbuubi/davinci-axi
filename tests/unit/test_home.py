from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


def test_reachable_home_view(run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld) -> None:
    code, out, err = run_cli([])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert list(keys)[:3] == ["bin", "version", "description"]
    assert keys["resolve"] == "reachable"
    assert keys["edition"] == "Studio"
    assert keys["resolve_version"] == "21.1.1"
    assert keys["page"] == "edit"
    assert keys["project"] == "davinci-axi-test"
    assert keys["timeline"] == "Edit v1"
    assert keys["timeline_count"] == "2"
    assert (keys["render_jobs"], keys["render_pending"], keys["render_complete"]) == ("2", "1", "1")
    assert "timelines[2]{name,frames,current}:" in out
    assert "  Edit v1,240,true" in out
    assert "\nhelp[" in out
    assert not out.endswith("\n\n")


def test_home_command_name_is_an_alias(run_cli: Run, fake_world: FakeWorld) -> None:
    assert run_cli(["home"])[1] == run_cli([])[1]


@pytest.mark.parametrize(
    ("connection", "code"),
    [
        ("not_running", "RESOLVE_NOT_RUNNING"),
        ("scripting_disabled", "SCRIPTING_DISABLED"),
        ("free", "FREE_EDITION"),
        ("not_installed", "RESOLVE_NOT_INSTALLED"),
    ],
)
def test_unreachable_home_exits_zero_with_code_and_fix(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, connection: str, code: str
) -> None:
    fake_world.connection = connection  # type: ignore[assignment]
    exit_code, out, err = run_cli([])
    keys = parse_toon_keys(out)
    assert (exit_code, err) == (0, "")
    assert keys["resolve"] == "unreachable"
    assert keys["code"] == code
    assert keys["fix"]
    assert "edition" not in keys and "project" not in keys
    assert "\nhelp[" in out


def test_hang_reports_timeout_within_budget(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.connection = "hang"
    exit_code, out, _ = run_cli(["--timeout", "0.2"])
    assert exit_code == 0
    assert parse_toon_keys(out)["code"] == "RESOLVE_TIMEOUT"


def test_home_json_has_the_same_keys(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _, toon_out, _ = run_cli([])
    code, json_out, _ = run_cli(["--json"])
    data = json.loads(json_out)
    assert code == 0
    assert set(parse_toon_keys(toon_out)) | {"timelines", "help"} == set(data)
    assert data["timelines"][1] == {"name": "Edit v2", "frames": 120, "current": False}
    assert data["resolve"] == "reachable"


def test_home_makes_no_write_calls_and_keeps_the_page(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.resolve.page = "color"
    run_cli([])
    run_cli(["--json"])
    assert fake_world.write_calls() == []
    assert fake_world.page_changes() == []
    assert fake_world.resolve.GetCurrentPage() == "color"


def test_home_without_a_project(run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld) -> None:
    fake_world.resolve.pm.current_project = None
    code, out, _ = run_cli([])
    keys = parse_toon_keys(out)
    assert code == 0
    assert keys["resolve"] == "reachable" and keys["project"] == "null"
    assert "timelines" not in keys and "timeline" not in keys


def test_project_without_timelines_states_the_scope(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    project.timelines.clear()
    project.current_timeline = None
    _, out, _ = run_cli([])
    keys = parse_toon_keys(out)
    assert keys["timelines"] == "0 timelines in project davinci-axi-test"
    assert keys["timeline"] == "null" and keys["timeline_count"] == "0"


def test_only_the_first_ten_timelines_are_listed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    for n in range(12):
        project.add_timeline(f"extra {n}")
    _, out, _ = run_cli([])
    assert parse_toon_keys(out)["timeline_count"] == "14"
    assert "timelines[10]{name,frames,current}:" in out


def test_hints_only_name_registered_commands(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld
) -> None:
    for name in ("projects", "timelines", "items"):
        monkeypatch.delitem(registry.COMMANDS, name, raising=False)
    _, out, _ = run_cli([])
    assert "davinci-axi projects" not in out
    monkeypatch.setitem(registry.COMMANDS, "projects", "x:y")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "x:y")
    monkeypatch.setitem(registry.COMMANDS, "items", "x:y")
    _, out, _ = run_cli([])
    assert "Run `davinci-axi projects` to list" in out
    assert "Run `davinci-axi timelines` to list" in out
    assert "davinci-axi items" not in out
    assert "help[3]:" in out


def test_home_never_loads_the_real_module(run_cli: Run, fake_world: FakeWorld) -> None:
    import sys

    run_cli([])
    assert "DaVinciResolveScript" not in sys.modules
