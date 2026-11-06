from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "projects", "davinci_axi.commands.projects:SPEC")


def test_default_lists_projects_and_marks_the_current(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["projects"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["database"] == "Local Database"
    assert keys["db_type"] == "Disk"
    assert keys["folder"] == "Root"
    assert keys["total"] == "2"
    assert keys["folders"] == "[]"
    assert "projects[2]{name,modified,current}:" in out
    assert '  sample-project,"2026-09-15T12:00:00+00:00",false' in out
    assert '  davinci-axi-test,"2026-10-27T09:30:00+00:00",true' in out
    assert not out.endswith("\n\n")


def test_no_project_open_is_not_an_error(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.resolve.pm.current_project = None
    code, out, _ = run_cli(["projects"])
    assert code == 0
    assert ",true" not in out
    assert "projects[2]{name,modified,current}:" in out


def test_fields_adds_created_and_notes(run_cli: Run, fake_world: FakeWorld) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    project.attributes["notes"] = "hello"
    code, out, _ = run_cli(["projects", "--fields", "name,created,notes,collab"])
    assert code == 0
    assert "projects[2]{name,created,notes,collab}:" in out
    assert '  davinci-axi-test,"2026-10-01T09:30:00+00:00",hello,single_user' in out


def test_unknown_field_exits_2_listing_valid_fields(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["projects", "--fields", "name,size"])
    assert code == 2
    assert "code: VALIDATION_ERROR" in out
    assert "Valid fields: name, modified, current, created, notes, collab, modified_epoch" in out


def test_unknown_field_is_rejected_even_when_the_folder_is_empty(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.resolve.pm.folder.projects.clear()
    code, out, _ = run_cli(["projects", "--fields", "size"])
    assert code == 2
    assert "Valid fields:" in out


def test_limit_truncates_and_hints_the_full_listing(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["projects", "--limit", "1"])
    assert code == 0
    assert "total: 2" in out
    assert "projects[1]{name,modified,current}:" in out
    assert "Run `davinci-axi projects --limit 2` to list all 2 projects" in out


@pytest.mark.parametrize("bad", ["0", "-3"])
def test_bad_limit_exits_2_without_connecting(
    run_cli: Run, fake_world: FakeWorld, bad: str
) -> None:
    fake_world.connection = "not_running"
    code, out, _ = run_cli(["projects", f"--limit={bad}"])
    assert code == 2
    assert "code: VALIDATION_ERROR" in out


def test_empty_folder_states_the_scope(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.resolve.pm.folder.projects.clear()
    fake_world.resolve.pm.current_project = None
    code, out, _ = run_cli(["projects"])
    keys = parse_toon_keys(out)
    assert code == 0
    assert keys["projects"] == "0 projects in folder Root of Local Database"
    assert keys["total"] == "0"


def test_subfolders_are_listed(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.resolve.pm.add_folder("Client A")
    fake_world.resolve.pm.add_folder("Archive")
    _, out, _ = run_cli(["projects"])
    assert "folders[2]: Client A,Archive" in out


def test_help_names_only_registered_next_steps(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld
) -> None:
    for name in ("timelines", "project"):
        monkeypatch.delitem(registry.COMMANDS, name, raising=False)
    _, out, _ = run_cli(["projects"])
    assert "davinci-axi timelines" not in out and "project snapshot" not in out
    monkeypatch.setitem(registry.COMMANDS, "timelines", "x:y")
    monkeypatch.setitem(registry.COMMANDS, "project", "x:y")
    _, out, _ = run_cli(["projects"])
    assert "Run `davinci-axi timelines` to list" in out
    assert "Run `davinci-axi project snapshot` to" in out


def test_json_has_the_same_content_as_toon(run_cli: Run, fake_world: FakeWorld) -> None:
    code, json_out, _ = run_cli(["projects", "--json"])
    data = json.loads(json_out)
    assert code == 0
    assert data["database"] == "Local Database" and data["total"] == 2
    assert data["projects"] == [
        {"name": "sample-project", "modified": "2026-09-15T12:00:00+00:00", "current": False},
        {"name": "davinci-axi-test", "modified": "2026-10-27T09:30:00+00:00", "current": True},
    ]


def test_makes_no_write_calls_and_opens_no_project(run_cli: Run, fake_world: FakeWorld) -> None:
    run_cli(["projects"])
    run_cli(["projects", "--json"])
    assert fake_world.write_calls() == []
    assert fake_world.page_changes() == []
    current = fake_world.resolve.pm.current_project
    assert current is not None and current.name == "davinci-axi-test"


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
    run_cli: Run, fake_world: FakeWorld, connection: str, code: str
) -> None:
    fake_world.connection = connection  # type: ignore[assignment]
    exit_code, out, _ = run_cli(["projects"])
    assert exit_code == 1
    assert f"code: {code}" in out
    assert "error:" in out and "help[" in out


def test_help_exits_0_with_examples(run_cli: Run) -> None:
    code, out, err = run_cli(["projects", "--help"])
    assert (code, err) == (0, "")
    assert "davinci-axi projects" in out
    assert "--fields" in out and "--limit" in out
