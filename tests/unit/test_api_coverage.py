"""R36: every non-deprecated Resolve API method except `Quit` is declared by some command.

`tests/data/api_methods_21_1.txt` is the source of truth (one `Class.Method` per line, extracted
from the local `DaVinciResolveScript.pyi` for the 13 documented classes). The test unions the
`methods` of every spec in the registry and checks both directions.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from davinci_axi import registry
from davinci_axi.spec import CommandSpec

DATA = Path(__file__).resolve().parents[1] / "data" / "api_methods_21_1.txt"
EXCLUDED = {"Resolve.Quit"}


def _api_methods() -> list[str]:
    return [line for line in DATA.read_text(encoding="utf-8").splitlines() if line]


def _specs() -> Iterator[CommandSpec]:
    loaded = {name: registry.load(ref) for name, ref in registry.COMMANDS.items()}
    loaded["home"] = registry.load(registry.HOME)
    yield from loaded.values()


def _walk(spec: CommandSpec) -> Iterator[CommandSpec]:
    yield spec
    for child in spec.subcommands.values():
        yield from _walk(child)


def _declared() -> dict[str, list[str]]:
    """Method name -> the command paths that declare it."""
    found: dict[str, list[str]] = {}

    def visit(path: str, spec: CommandSpec) -> None:
        for method in spec.methods:
            found.setdefault(method, []).append(path)
        for name, child in spec.subcommands.items():
            visit(f"{path} {name}", child)

    for top in _specs():
        visit(top.name, top)
    return found


def test_data_file_has_416_sorted_unique_lines() -> None:
    lines = _api_methods()
    assert len(lines) == 416
    assert len(set(lines)) == len(lines)
    assert lines == sorted(lines)
    assert all(line.count(".") == 1 for line in lines)
    assert "Resolve.Quit" in lines


def _undeclared() -> set[str]:
    declared = _declared()
    return {m for m in _api_methods() if m not in EXCLUDED and m not in declared}


def test_r36_every_method_except_quit_is_declared() -> None:
    """The R36 acceptance gate: every method except `Quit` is declared, nothing waived."""
    assert _undeclared() == set()


def test_every_declared_method_is_in_the_api_file() -> None:
    known = set(_api_methods())
    unknown = {m: paths for m, paths in _declared().items() if m not in known}
    assert unknown == {}


def test_quit_is_declared_by_no_command() -> None:
    assert {m: p for m, p in _declared().items() if m in EXCLUDED} == {}


def test_page_and_app_are_in_the_walk() -> None:
    declared = _declared()
    assert "page set" in declared["Resolve.OpenPage"]
    assert "app priority" in declared["Resolve.SetHighPriority"]
    assert "app keyframe-mode set" in declared["Resolve.SetKeyframeMode"]
