from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.spec import CommandSpec

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "davinci_axi"
Run = Callable[[list[str]], tuple[int, str, str]]


def _python_files() -> list[Path]:
    return sorted([*(ROOT / "src").rglob("*.py"), *(ROOT / "tests").rglob("*.py")])


def test_every_file_is_under_500_lines() -> None:
    too_long = {
        str(path.relative_to(ROOT)): n
        for path in _python_files()
        if (n := len(path.read_text(encoding="utf-8").splitlines())) >= 500
    }
    assert too_long == {}


def test_every_registry_entry_exposes_a_spec() -> None:
    for name, ref in {"home": registry.HOME, **registry.COMMANDS}.items():
        assert isinstance(registry.load(ref), CommandSpec), name


def _walk(path: tuple[str, ...], spec: CommandSpec) -> Iterator[tuple[str, ...]]:
    yield path
    for name, child in spec.subcommands.items():
        yield from _walk((*path, name), child)


def test_every_command_and_subcommand_help_exits_zero_with_empty_stderr(run_cli: Run) -> None:
    paths = [
        path
        for name in registry.command_names()
        if (spec := registry.lookup(name)) is not None
        for path in _walk((name,), spec)
    ]
    assert ("home",) in paths
    for path in paths:
        code, out, err = run_cli([*path, "--help"])
        assert code == 0, path
        assert err == "", path
        assert "examples[" in out, path


def test_every_command_rejects_an_unknown_flag_with_exit_2(run_cli: Run) -> None:
    for name in registry.command_names():
        code, out, err = run_cli([name, "--axi-probe-unknown-flag"])
        assert code == 2, name
        assert "valid" in out.lower() and err == "", name


def test_commands_never_import_the_resolve_module_at_module_level() -> None:
    for path in (SRC / "commands").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any("DaVinciResolveScript" in n for n in names), path.name


def test_no_source_file_imports_the_resolve_module_statically() -> None:
    """The real module is loaded only by name inside connect.py, never with an import statement."""
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all("DaVinciResolveScript" not in a.name for a in node.names), path.name
            if isinstance(node, ast.ImportFrom):
                assert "DaVinciResolveScript" not in (node.module or ""), path.name


def test_runtime_has_no_third_party_dependencies() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r"^dependencies = \[\]$", text, re.MULTILINE)


@pytest.mark.live
def test_live_marked_tests_are_skipped_without_the_flag(request: pytest.FixtureRequest) -> None:
    assert request.config.getoption("--live")
