from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import hook as hook_module
from davinci_axi.fake import FakeWorld

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

COMMAND = "davinci-axi # davinci-axi"
REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_HOOK_COMMAND = hook_module.hook_command


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(registry.COMMANDS, "hook", "davinci_axi.commands.hook:SPEC")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(hook_module, "hook_command", lambda: COMMAND)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def claude_file(project: Path) -> Path:
    return project / ".claude" / "settings.json"


def read(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def install(run_cli: Run, project: Path, *extra: str) -> tuple[int, str, str]:
    return run_cli(["hook", "install", "--dir", str(project), *extra])


def test_fresh_install_writes_the_entry(run_cli: Run, parse_toon_keys: Keys, project: Path) -> None:
    code, out, err = install(run_cli, project)
    assert (code, err) == (0, "")
    keys = parse_toon_keys(out)
    assert keys["status"] == "installed"
    assert keys["modified"] == "true"
    assert read(claude_file(project)) == {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "",
                    "hooks": [{"type": "command", "command": COMMAND, "timeout": 10}],
                }
            ]
        }
    }


def test_second_install_is_a_no_op(run_cli: Run, parse_toon_keys: Keys, project: Path) -> None:
    install(run_cli, project)
    before = claude_file(project).read_bytes()
    code, out, _ = install(run_cli, project)
    keys = parse_toon_keys(out)
    assert code == 0
    assert (keys["status"], keys["modified"]) == ("unchanged", "false")
    assert claude_file(project).read_bytes() == before


def test_stale_command_is_repaired_in_place(
    run_cli: Run, parse_toon_keys: Keys, project: Path
) -> None:
    install(run_cli, project)
    path = claude_file(project)
    data = read(path)
    entry = data["hooks"]["SessionStart"][0]  # type: ignore[index]
    entry["hooks"][0]["command"] = "/old/place/davinci-axi # davinci-axi"
    entry["hooks"][0]["timeout"] = 3
    entry["extra"] = "kept"
    path.write_text(json.dumps(data), encoding="utf-8")

    code, out, _ = install(run_cli, project)
    assert code == 0
    assert parse_toon_keys(out)["status"] == "repaired"
    fixed = read(path)["hooks"]["SessionStart"]  # type: ignore[index]
    assert len(fixed) == 1
    assert fixed[0]["hooks"] == [{"type": "command", "command": COMMAND, "timeout": 10}]
    assert fixed[0]["extra"] == "kept"


def test_install_keeps_unrelated_settings_and_hooks(run_cli: Run, project: Path) -> None:
    path = claude_file(project)
    path.parent.mkdir()
    other = {"matcher": "", "hooks": [{"type": "command", "command": "echo hi"}]}
    path.write_text(
        json.dumps({"model": "opus", "hooks": {"SessionStart": [other], "Stop": []}}),
        encoding="utf-8",
    )
    assert install(run_cli, project)[0] == 0
    data = read(path)
    assert data["model"] == "opus"
    hooks = data["hooks"]
    assert hooks["Stop"] == []  # type: ignore[index]
    assert hooks["SessionStart"][0] == other  # type: ignore[index]
    assert len(hooks["SessionStart"]) == 2  # type: ignore[index]


def test_remove_deletes_only_the_marked_entry(
    run_cli: Run, parse_toon_keys: Keys, project: Path
) -> None:
    path = claude_file(project)
    path.parent.mkdir()
    other = {"matcher": "", "hooks": [{"type": "command", "command": "echo hi"}]}
    path.write_text(json.dumps({"hooks": {"SessionStart": [other]}}), encoding="utf-8")
    install(run_cli, project)
    code, out, _ = install(run_cli, project, "--remove")
    keys = parse_toon_keys(out)
    assert code == 0
    assert (keys["status"], keys["modified"]) == ("removed", "true")
    assert read(path) == {"hooks": {"SessionStart": [other]}}


def test_remove_prunes_containers_it_emptied(run_cli: Run, project: Path) -> None:
    install(run_cli, project)
    assert install(run_cli, project, "--remove")[0] == 0
    assert read(claude_file(project)) == {}


def test_remove_removes_only_our_hook_from_a_shared_entry(run_cli: Run, project: Path) -> None:
    path = claude_file(project)
    path.parent.mkdir()
    mine = {"type": "command", "command": COMMAND, "timeout": 10}
    theirs = {"type": "command", "command": "echo hi"}
    path.write_text(
        json.dumps({"hooks": {"SessionStart": [{"matcher": "", "hooks": [theirs, mine]}]}}),
        encoding="utf-8",
    )
    assert install(run_cli, project, "--remove")[0] == 0
    assert read(path) == {"hooks": {"SessionStart": [{"matcher": "", "hooks": [theirs]}]}}


def test_remove_when_absent_is_a_no_op_and_creates_nothing(
    run_cli: Run, parse_toon_keys: Keys, project: Path
) -> None:
    code, out, _ = install(run_cli, project, "--remove")
    keys = parse_toon_keys(out)
    assert code == 0
    assert (keys["status"], keys["modified"]) == ("absent", "false")
    assert not (project / ".claude").exists()


def test_check_passes_when_installed_and_fails_when_missing_or_stale(
    run_cli: Run, parse_toon_keys: Keys, project: Path
) -> None:
    code, out, err = install(run_cli, project, "--check")
    assert (code, err) == (1, "")
    assert parse_toon_keys(out)["code"] == "HOOK_MISSING"
    assert "davinci-axi hook install" in out
    assert not (project / ".claude").exists()

    install(run_cli, project)
    code, out, _ = install(run_cli, project, "--check")
    assert code == 0
    assert parse_toon_keys(out)["status"] == "installed"

    path = claude_file(project)
    path.write_text(path.read_text(encoding="utf-8").replace(COMMAND, "old # davinci-axi"))
    code, out, _ = install(run_cli, project, "--check")
    assert code == 1
    assert parse_toon_keys(out)["code"] == "HOOK_STALE"
    assert "old # davinci-axi" in path.read_text(encoding="utf-8")


def test_codex_writes_hooks_json_with_a_config_note(run_cli: Run, project: Path) -> None:
    code, out, _ = install(run_cli, project, "--agent", "codex")
    assert code == 0
    assert "features" in out and "hooks = true" in out
    data = read(project / ".codex" / "hooks.json")
    assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == COMMAND  # type: ignore[index]
    assert not (project / ".claude").exists()


def test_user_scope_writes_under_the_home_directory(
    run_cli: Run, tmp_path: Path, project: Path
) -> None:
    code, _, _ = run_cli(["hook", "install", "--scope", "user"])
    assert code == 0
    target = tmp_path / "home" / ".claude" / "settings.json"
    assert read(target)["hooks"]
    assert not (project / ".claude").exists()
    code, _, _ = run_cli(["hook", "install", "--scope", "user", "--agent", "codex"])
    assert code == 0
    assert (tmp_path / "home" / ".codex" / "hooks.json").is_file()


def test_dir_with_user_scope_is_a_usage_error(run_cli: Run, project: Path) -> None:
    code, out, _ = run_cli(["hook", "install", "--scope", "user", "--dir", str(project)])
    assert code == 2
    assert "VALIDATION_ERROR" in out


def test_remove_and_check_together_is_a_usage_error(run_cli: Run, project: Path) -> None:
    code, _, _ = install(run_cli, project, "--remove", "--check")
    assert code == 2


@pytest.mark.parametrize(
    "content", ["{not json", "[]", '{"hooks": []}', '{"hooks": {"SessionStart": {}}}']
)
@pytest.mark.parametrize("extra", [[], ["--remove"], ["--check"]])
def test_invalid_settings_exit_1_and_leave_the_file_untouched(
    run_cli: Run, parse_toon_keys: Keys, project: Path, content: str, extra: list[str]
) -> None:
    path = claude_file(project)
    path.parent.mkdir()
    path.write_text(content, encoding="utf-8")
    code, out, err = install(run_cli, project, *extra)
    assert (code, err) == (1, "")
    assert parse_toon_keys(out)["code"] == "HOOK_FILE_INVALID"
    assert path.read_text(encoding="utf-8") == content
    assert list(path.parent.iterdir()) == [path]


def test_blank_settings_file_is_treated_as_empty(run_cli: Run, project: Path) -> None:
    path = claude_file(project)
    path.parent.mkdir()
    path.write_text("\n", encoding="utf-8")
    assert install(run_cli, project)[0] == 0
    assert read(path)["hooks"]


def test_unwritable_target_is_a_coded_error(
    run_cli: Run, parse_toon_keys: Keys, project: Path
) -> None:
    (project / ".claude").write_text("a file, not a directory", encoding="utf-8")
    code, out, _ = install(run_cli, project)
    assert code == 1
    assert parse_toon_keys(out)["code"] in ("HOOK_WRITE_FAILED", "HOOK_FILE_INVALID")


def test_unknown_agent_and_bare_hook_are_usage_errors(run_cli: Run, parse_toon_keys: Keys) -> None:
    code, out, _ = run_cli(["hook", "install", "--agent", "vim"])
    assert code == 2
    assert "claude-code" in out
    code, out, _ = run_cli(["hook"])
    assert code == 2
    assert "install" in out
    assert parse_toon_keys(out)["code"] == "VALIDATION_ERROR"


def test_json_output(run_cli: Run, project: Path) -> None:
    code, out, _ = install(run_cli, project, "--json")
    body = json.loads(out)
    assert code == 0
    assert body["status"] == "installed"
    assert body["modified"] is True
    assert body["agent"] == "claude-code"


def test_help_lists_examples(run_cli: Run) -> None:
    code, out, _ = run_cli(["hook", "install", "--help"])
    assert code == 0
    assert "davinci-axi hook install --check" in out


def test_never_connects_to_resolve(run_cli: Run, fake_world: FakeWorld, project: Path) -> None:
    fake_world.connection = "not_running"
    install(run_cli, project)
    install(run_cli, project, "--check")
    install(run_cli, project, "--remove")
    assert fake_world.calls == []


def test_never_touches_the_repos_own_claude_directory(run_cli: Run, project: Path) -> None:
    repo_settings = REPO_ROOT / ".claude" / "settings.json"
    before = repo_settings.read_bytes() if repo_settings.exists() else None
    install(run_cli, project)
    install(run_cli, project, "--remove")
    after = repo_settings.read_bytes() if repo_settings.exists() else None
    assert before == after


def test_hook_command_bare_when_path_resolves_to_this_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "bin" / "davinci-axi"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(hook_module.sys, "argv", [str(binary)])
    monkeypatch.setattr(hook_module.shutil, "which", lambda name: str(binary))
    assert REAL_HOOK_COMMAND() == "davinci-axi # davinci-axi"


def test_hook_command_absolute_when_path_resolves_elsewhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "my bin" / "davinci-axi"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(hook_module.sys, "argv", [str(binary)])
    monkeypatch.setattr(hook_module.shutil, "which", lambda name: None)
    command = REAL_HOOK_COMMAND()
    assert command == f"'{binary.resolve()}' # davinci-axi"


def test_hook_command_falls_back_to_python_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook_module.sys, "argv", ["/x/__main__.py"])
    monkeypatch.setattr(hook_module.shutil, "which", lambda name: None)
    assert REAL_HOOK_COMMAND().endswith("-m davinci_axi # davinci-axi")
