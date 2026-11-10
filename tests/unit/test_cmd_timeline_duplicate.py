from __future__ import annotations

from collections.abc import Callable

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_timeline_edit

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_timeline_edit.extend()
    monkeypatch.setitem(registry.COMMANDS, "timeline", "davinci_axi.commands.timeline:SPEC")


def _current(world: FakeWorld) -> str | None:
    project = world.resolve.pm.current_project
    assert project is not None
    return project.current_timeline.name if project.current_timeline else None


def test_dry_run_shows_the_switch_to_the_copy(run_cli: Run, fake_world: FakeWorld) -> None:
    before = _current(fake_world)
    code, out, _ = run_cli(["timeline", "duplicate", "Edit v1", "--to", "Edit v1 copy"])
    assert code == 0 and "applied: false" in out
    assert f"  current,{before},Edit v1 copy" in out
    assert _current(fake_world) == before


def test_force_makes_the_copy_current_and_reads_it_back(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["timeline", "duplicate", "Edit v1", "--to", "Edit v1 copy", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true"
    assert _current(fake_world) == "Edit v1 copy"
    assert "  current,Edit v1,Edit v1 copy" in out


def test_positional_new_name_is_refused_naming_to(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, err = run_cli(["timeline", "duplicate", "Edit v1", "copy"])
    assert code == 2 and "--to" in out + err
    assert fake_world.write_calls() == []


def test_help_says_the_copy_becomes_current(run_cli: Run) -> None:
    code, out, _ = run_cli(["timeline", "duplicate", "--help"])
    assert code == 0 and "becomes current" in out
