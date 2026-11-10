from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest

from davinci_axi import helptext, registry
from davinci_axi.spec import GLOBAL_FLAGS, CommandSpec, Flag

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


def _error_calls() -> Iterator[tuple[str, int, ast.Call]]:
    """Every `AxiError("CODE", ...)` call outside the fake, as (file, line, call)."""
    for path in sorted(SRC.rglob("*.py")):
        if "fake" in path.relative_to(SRC).parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "AxiError" and isinstance(node.args[0], ast.Constant):
                yield str(path.relative_to(ROOT)), node.lineno, node


def test_every_inline_error_carries_help() -> None:
    missing = [
        f"{file}:{line}"
        for file, line, call in _error_calls()
        if len(call.args) < 3 and not any(kw.arg == "help" for kw in call.keywords)
    ]
    assert missing == [], "AxiError with no help= (name a valid value, the fix or what to check)"


def _specs() -> Iterator[tuple[str, CommandSpec]]:
    """Every runnable command and subcommand of the registry tree, as (path, spec)."""
    for name in registry.command_names():
        spec = registry.lookup(name)
        assert spec is not None, name
        stack = [(name, spec)]
        while stack:
            path, one = stack.pop()
            if one.run is not None:
                yield path, one
            stack.extend((f"{path} {n}", child) for n, child in one.subcommands.items())


KIND_EXCEPTIONS = {
    "out": "a frame on append/fades/marks/take, a file on analyze/snapshot/skill gen",
    "track": "audio align takes mix|auto, the rest take a track number",
    "target": "a length on some verbs, a LUFS value on others",
    "size": "a LUT size on color lut, a share on others",
}


def test_shared_flag_names_keep_one_kind() -> None:
    kinds: dict[str, dict[str, list[str]]] = {}
    for path, spec in _specs():
        for flag in spec.flags:
            kinds.setdefault(flag.name.lstrip("-"), {}).setdefault(flag.kind, []).append(path)
    for flag in GLOBAL_FLAGS:
        kinds.setdefault(flag.name.lstrip("-"), {}).setdefault(flag.kind, []).append("(global)")
    split = {
        name: {kind: paths[:3] for kind, paths in by_kind.items()}
        for name, by_kind in kinds.items()
        if len(by_kind) > 1 and name not in KIND_EXCEPTIONS
    }
    assert split == {}, "one flag name, two kinds: rename one, or add it to KIND_EXCEPTIONS"
    stale = [name for name in KIND_EXCEPTIONS if len(kinds.get(name, {})) < 2]
    assert stale == [], "KIND_EXCEPTIONS entries that no longer split kinds"


PAIR_EXCEPTIONS = {
    "timeline diff": "fixed narrow columns",
    "snapshot diff": "fixed narrow columns",
    "audio gaps": "fixed narrow columns",
    "storage ls": "fixed narrow columns",
    "help": "fixed 3 columns",
}


def test_list_flags_come_in_pairs() -> None:
    lopsided = []
    for path, spec in _specs():
        names = {flag.name.lstrip("-") for flag in spec.flags}
        if ("fields" in names) != ("limit" in names) and path not in PAIR_EXCEPTIONS:
            lopsided.append(path)
    assert lopsided == [], (
        "--limit without --fields (or the reverse): add the pair or PAIR_EXCEPTIONS"
    )


def test_default_true_bools_show_their_negation() -> None:
    flagged: list[Flag] = [f for _, spec in _specs() for f in spec.flags if f.default is True]
    assert flagged, "expected at least one default-true bool flag (render start --wait)"
    for path, spec in _specs():
        rows = cast(
            list[dict[str, object]], helptext.render(spec, tuple(path.split())).get("flags", [])
        )
        for flag, row in zip(spec.flags, rows, strict=True):
            if flag.kind == "bool" and flag.default is True:
                assert "/--no-" in str(row["flag"]), (path, flag.name)


@pytest.mark.live
def test_live_marked_tests_are_skipped_without_the_flag(request: pytest.FixtureRequest) -> None:
    assert request.config.getoption("--live")
