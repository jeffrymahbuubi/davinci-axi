from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import skill as skill_module
from davinci_axi.fake import FakeWorld
from davinci_axi.spec import GLOBAL_FLAGS

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "skill", "davinci_axi.commands.skill:SPEC")


@pytest.fixture
def out_file(tmp_path: Path) -> Path:
    return tmp_path / "out" / "SKILL.md"


def test_gen_writes_then_reports_unchanged_and_check_passes(
    run_cli: Run, parse_toon_keys: Keys, out_file: Path
) -> None:
    code, out, err = run_cli(["skill", "gen", "--out", str(out_file)])
    assert (code, err) == (0, "")
    assert parse_toon_keys(out)["status"] == "written"
    assert out_file.is_file()

    code, out, _ = run_cli(["skill", "gen", "--out", str(out_file)])
    assert code == 0
    assert parse_toon_keys(out)["status"] == "unchanged"

    code, out, err = run_cli(["skill", "gen", "--check", "--out", str(out_file)])
    assert (code, err) == (0, "")
    assert parse_toon_keys(out)["status"] == "current"


def test_check_missing_exits_1_and_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, out_file: Path
) -> None:
    code, out, err = run_cli(["skill", "gen", "--check", "--out", str(out_file)])
    assert (code, err) == (1, "")
    assert "is missing" in parse_toon_keys(out)["error"]
    assert "davinci-axi skill gen" in out
    assert not out_file.exists()


def test_check_stale_exits_1_and_leaves_the_file_alone(
    run_cli: Run, parse_toon_keys: Keys, out_file: Path
) -> None:
    out_file.parent.mkdir(parents=True)
    out_file.write_text("old\n", encoding="utf-8")
    code, out, err = run_cli(["skill", "gen", "--check", "--out", str(out_file)])
    assert (code, err) == (1, "")
    assert "is stale" in parse_toon_keys(out)["error"]
    assert "davinci-axi skill gen" in out
    assert out_file.read_text(encoding="utf-8") == "old\n"


def test_check_tolerates_crlf_line_endings(run_cli: Run, out_file: Path) -> None:
    assert run_cli(["skill", "gen", "--out", str(out_file)])[0] == 0
    out_file.write_bytes(out_file.read_bytes().replace(b"\n", b"\r\n"))
    code, _, _ = run_cli(["skill", "gen", "--check", "--out", str(out_file)])
    assert code == 0


def test_gen_rewrites_a_crlf_file_with_lf_endings(run_cli: Run, out_file: Path) -> None:
    run_cli(["skill", "gen", "--out", str(out_file)])
    lf = out_file.read_bytes()
    out_file.write_bytes(lf.replace(b"\n", b"\r\n"))
    assert run_cli(["skill", "gen", "--out", str(out_file)])[0] == 0
    assert out_file.read_bytes() == lf
    assert b"\r" not in lf


def test_two_runs_are_byte_identical(run_cli: Run, tmp_path: Path) -> None:
    first, second = tmp_path / "a" / "SKILL.md", tmp_path / "b" / "SKILL.md"
    run_cli(["skill", "gen", "--out", str(first)])
    run_cli(["skill", "gen", "--out", str(second)])
    assert first.read_bytes() == second.read_bytes()


def test_out_accepts_an_existing_directory(run_cli: Run, tmp_path: Path) -> None:
    code, _, _ = run_cli(["skill", "gen", "--out", str(tmp_path)])
    assert code == 0
    assert (tmp_path / "SKILL.md").is_file()


def test_bare_skill_is_a_usage_error(run_cli: Run, parse_toon_keys: Keys) -> None:
    code, out, err = run_cli(["skill"])
    assert (code, err) == (2, "")
    assert parse_toon_keys(out)["code"] == "VALIDATION_ERROR"
    assert "gen" in out


def test_unknown_flag_is_a_usage_error(run_cli: Run) -> None:
    code, out, _ = run_cli(["skill", "gen", "--bogus"])
    assert code == 2
    assert "--check" in out


def test_rendered_file_mentions_every_registry_command(run_cli: Run, out_file: Path) -> None:
    run_cli(["skill", "gen", "--out", str(out_file)])
    text = out_file.read_text(encoding="utf-8")
    for name in registry.command_names():
        assert f"`davinci-axi {name}" in text, name
    assert "`davinci-axi render presets`" in text
    assert "`davinci-axi skill gen`" in text


def test_rendered_file_follows_the_registry_at_run_time(
    run_cli: Run, monkeypatch: pytest.MonkeyPatch, out_file: Path
) -> None:
    run_cli(["skill", "gen", "--out", str(out_file)])
    monkeypatch.delitem(registry.COMMANDS, "timelines")
    code, out, _ = run_cli(["skill", "gen", "--check", "--out", str(out_file)])
    assert code == 1
    assert "is stale" in out
    run_cli(["skill", "gen", "--out", str(out_file)])
    assert "`davinci-axi timelines" not in out_file.read_text(encoding="utf-8")


def test_rendered_file_has_the_required_sections(run_cli: Run, out_file: Path) -> None:
    run_cli(["skill", "gen", "--out", str(out_file)])
    text = out_file.read_text(encoding="utf-8")
    assert text.startswith("---\nname: davinci-axi\ndescription: ")
    assert "uv tool install git+https://github.com/jeffrymahbuubi/davinci-axi" in text
    assert "uvx --from git+https://github.com/jeffrymahbuubi/davinci-axi davinci-axi" in text
    assert "| Command | What it does | Example |" in text
    for flag in GLOBAL_FLAGS:
        assert f"`--{flag.name}" in text
    assert "## Safety" in text
    assert "\r" not in text
    assert text.endswith("\n") and not text.endswith("\n\n")


def test_table_uses_the_first_example_of_each_command(run_cli: Run, out_file: Path) -> None:
    run_cli(["skill", "gen", "--out", str(out_file)])
    lines = out_file.read_text(encoding="utf-8").splitlines()
    row = next(line for line in lines if line.startswith("| `davinci-axi render status`"))
    assert row.endswith("| `davinci-axi render status` |")
    other = next(line for line in lines if line.startswith("| `davinci-axi render presets`"))
    assert other.endswith("| `davinci-axi render presets` |")


def test_default_target_is_the_checkout_skill_path(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(skill_module.__file__).resolve().parents[3]
    assert (root / "pyproject.toml").is_file()
    assert skill_module.target_path(None) == root / "skills" / "davinci-axi" / "SKILL.md"
    monkeypatch.setattr(skill_module, "checkout_root", lambda: Path("/nowhere"))
    assert skill_module.target_path(None) == Path("/nowhere/skills/davinci-axi/SKILL.md")


def test_default_target_falls_back_to_cwd_without_pyproject(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(skill_module.Path, "is_file", lambda self: False)
    monkeypatch.chdir(tmp_path)
    assert skill_module.checkout_root() == Path.cwd()


def test_unwritable_target_is_a_coded_error(
    run_cli: Run, parse_toon_keys: Keys, tmp_path: Path
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    code, out, _ = run_cli(["skill", "gen", "--out", str(blocker / "SKILL.md")])
    assert code == 1
    assert parse_toon_keys(out)["code"] == "SKILL_WRITE_FAILED"


def test_json_output_has_status_and_path(run_cli: Run, out_file: Path) -> None:
    code, out, _ = run_cli(["skill", "gen", "--json", "--out", str(out_file)])
    body = json.loads(out)
    assert code == 0
    assert body["status"] == "written"
    assert body["path"].endswith("SKILL.md")


def test_gen_never_connects_to_resolve(run_cli: Run, fake_world: FakeWorld, out_file: Path) -> None:
    assert run_cli(["skill", "gen", "--out", str(out_file)])[0] == 0
    assert run_cli(["skill", "gen", "--check", "--out", str(out_file)])[0] == 0
    assert fake_world.calls == []


def test_gen_works_when_resolve_is_unreachable(
    run_cli: Run, fake_world: FakeWorld, out_file: Path
) -> None:
    fake_world.connection = "not_running"
    assert run_cli(["skill", "gen", "--out", str(out_file)])[0] == 0
    assert fake_world.calls == []
