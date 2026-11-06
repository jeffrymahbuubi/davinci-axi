"""Shared fixtures: fake mode is forced for every test so no test can reach a real Resolve."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import fake
from davinci_axi.fake import FakeWorld, default_world


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run tests marked `live` against a running DaVinci Resolve",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="needs --live and a running DaVinci Resolve")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def _fake_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Every test runs against the fake with an isolated state directory.

    Live tests (tests/live) must undo this in their own fixture with `monkeypatch.delenv`.
    """
    monkeypatch.setenv("DAVINCI_AXI_FAKE", "1")
    monkeypatch.setenv("DAVINCI_AXI_HOME", str(tmp_path / "axi-home"))
    monkeypatch.delenv("DAVINCI_AXI_TIMEOUT", raising=False)
    yield
    world = fake._world
    if world is not None:
        world.hang_gate.set()
    fake.set_world(None)


@pytest.fixture
def fake_world() -> FakeWorld:
    """A fresh `default_world()` installed as the world the fake serves."""
    world = default_world()
    fake.set_world(world)
    return world


@pytest.fixture
def run_cli(capsys: pytest.CaptureFixture[str]) -> Callable[[list[str]], tuple[int, str, str]]:
    """Run `main(argv)` in-process and return (exit code, stdout, stderr)."""

    def run(argv: list[str]) -> tuple[int, str, str]:
        from davinci_axi.cli import main

        code = main(argv)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return run


@pytest.fixture
def parse_toon_keys() -> Callable[[str], dict[str, str]]:
    """Return a helper that maps each top-level scalar `key: value` line to its value."""

    def parse(out: str) -> dict[str, str]:
        keys: dict[str, str] = {}
        for line in out.splitlines():
            match = re.match(r"^([A-Za-z_][\w.-]*): (.*)$", line)
            if match:
                keys[match.group(1)] = match.group(2)
        return keys

    return parse
