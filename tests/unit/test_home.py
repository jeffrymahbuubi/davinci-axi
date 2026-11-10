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


def _help_lines(out: str) -> list[str]:
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("help["))
    return [line.strip() for line in lines[start + 1 :] if line.startswith("  ")]


def test_default_world_hints_follow_the_state(run_cli: Run, fake_world: FakeWorld) -> None:
    _, out, _ = run_cli([])
    text = "\n".join(_help_lines(out))
    assert "davinci-axi items` to list the items of Edit v1" in text
    assert "davinci-axi timeline check --target <length>`" in text
    assert "davinci-axi render start <job> --force` to start the 1 pending job(s)" in text
    assert "davinci-axi render verify --job <id> --max-mb <mb>`" in text
    assert "davinci-axi projects" not in text
    assert "help[5]:" in out


def test_no_project_hints_projects_and_open(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.resolve.pm.current_project = None
    _, out, _ = run_cli([])
    text = "\n".join(_help_lines(out))
    assert "davinci-axi projects`" in text
    assert "davinci-axi project open <name>`" in text
    assert "davinci-axi items" not in text and "render start" not in text
    assert "help[3]:" in out


def test_no_timelines_hints_create_and_import(run_cli: Run, fake_world: FakeWorld) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    project.timelines.clear()
    project.current_timeline = None
    _, out, _ = run_cli([])
    text = "\n".join(_help_lines(out))
    assert "davinci-axi timelines`" in text
    assert "davinci-axi timeline create <name> --force`" in text
    assert "davinci-axi media import <files> --force`" in text
    assert "davinci-axi items" not in text
    assert len(_help_lines(out)) <= 5


def test_registry_without_render_has_no_render_lines(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld
) -> None:
    monkeypatch.delitem(registry.COMMANDS, "render")
    _, out, _ = run_cli([])
    text = "\n".join(_help_lines(out))
    assert "render start" not in text and "render verify" not in text
    assert "davinci-axi items" in text


def test_hints_only_name_registered_commands(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld
) -> None:
    for name in ("items", "timeline"):
        monkeypatch.delitem(registry.COMMANDS, name)
    _, out, _ = run_cli([])
    text = "\n".join(_help_lines(out))
    assert "davinci-axi items" not in text and "davinci-axi timeline " not in text
    assert "davinci-axi render start" in text
    monkeypatch.setitem(registry.COMMANDS, "items", "x:y")
    assert "davinci-axi items" in "\n".join(_help_lines(run_cli([])[1]))


@pytest.mark.parametrize("world", ["default", "no_project", "no_timelines"])
def test_help_is_at_most_five_lines(run_cli: Run, fake_world: FakeWorld, world: str) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    if world == "no_project":
        fake_world.resolve.pm.current_project = None
    elif world == "no_timelines":
        project.timelines.clear()
        project.current_timeline = None
    lines = _help_lines(run_cli([])[1])
    assert 1 <= len(lines) <= 5
    assert (
        lines[-1].endswith("`davinci-axi --help` to list commands and flags")
        or "--help" in lines[-1]
    )


def test_next_steps_unit() -> None:
    from davinci_axi.commands.home import next_steps

    assert next_steps({"project": None})[0].startswith("Run `davinci-axi projects`")
    steps = next_steps(
        {
            "project": "p",
            "timeline": "T",
            "timeline_count": 1,
            "render_pending": 2,
            "render_complete": 3,
        }
    )
    assert len(steps) == 4
    assert "start the 2 pending job(s)" in steps[2]


def test_home_never_loads_the_real_module(run_cli: Run, fake_world: FakeWorld) -> None:
    import sys

    run_cli([])
    assert "DaVinciResolveScript" not in sys.modules


def test_starting_home_says_so(run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld) -> None:
    fake_world.connection = "starting"
    fake_world.starting_answers = 99
    exit_code, out, err = run_cli(["--timeout", "0.6"])
    keys = parse_toon_keys(out)
    assert (exit_code, err) == (0, "")
    assert keys["resolve"] == "starting"
    assert keys["code"] == "RESOLVE_STARTING"
    assert "loading" in keys["fix"]
    assert "\nhelp[" in out
    assert "edition" not in keys


def test_home_version_matches_the_version_flag(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("davinci_axi.version.describe", lambda: "0.1.0+abc1234")
    shown = parse_toon_keys(run_cli([])[1])["version"]
    assert shown == run_cli(["--version"])[1].strip() == "0.1.0+abc1234"
