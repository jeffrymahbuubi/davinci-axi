"""`page` and `app` (U23), against the fake. Neither takes a snapshot: both change UI or session."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_app
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _registered(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_app.extend()
    ext_app.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "page", "davinci_axi.commands.page:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "app", "davinci_axi.commands.app:SPEC")
    yield


def _no_snapshots(tmp_path: Path) -> bool:
    return not (tmp_path / "axi-home" / "snapshots").exists()


# --- page -----------------------------------------------------------------------------------


def test_page_show_reads_the_open_page(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["page", "show"])
    assert (code, err) == (0, "")
    assert parse_toon_keys(out)["page"] == "edit" and "color" in out
    assert fake_world.write_calls() == []


def test_page_set_dry_run_changes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["page", "set", "color"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert "page set color --force" in out
    assert fake_world.write_calls() == [] and fake_world.page_changes() == []
    assert fake_world.resolve.page == "edit" and _no_snapshots(tmp_path)


def test_page_set_force_opens_the_page_without_a_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["page", "set", "color", "--force"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["applied"] == "true" and keys["snapshot"] == "none"
    assert fake_world.page_changes() == ["color"]
    assert fake_world.write_calls() == ["Resolve.OpenPage"]
    assert fake_world.resolve.GetCurrentPage() == "color" and _no_snapshots(tmp_path)


def test_page_set_is_case_insensitive(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _, _ = run_cli(["page", "set", "Fairlight", "--force"])
    assert code == 0 and fake_world.resolve.page == "fairlight"


def test_page_set_to_the_open_page_is_a_no_op(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.resolve.page = "color"
    code, out, _ = run_cli(["page", "set", "color", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert "already on this page" in out
    assert fake_world.write_calls() == []


def test_page_set_rejects_an_unknown_page_before_connecting(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["page", "set", "timeline", "--force"])
    assert code == 2 and "VALIDATION_ERROR" in out and "Valid pages" in out
    assert fake_world.calls == []


def test_page_set_api_refusal_is_api_failed(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("Resolve.OpenPage", False)
    code, out, _ = run_cli(["page", "set", "deliver", "--force"])
    assert code == 1 and "API_FAILED" in out
    assert fake_world.resolve.page == "edit"


def test_page_set_verify_failure_when_the_page_does_not_stick(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject("Resolve.GetCurrentPage", "edit")
    code, out, _ = run_cli(["page", "set", "deliver", "--force"])
    assert code == 1 and "VERIFY_FAILED" in out


def test_page_set_refuses_no_snapshot_without_the_opt_in(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["page", "set", "color", "--no-snapshot", "--force"])
    assert code == 1 and "WRITE_REFUSED" in out
    assert fake_world.write_calls() == []


# --- app priority ---------------------------------------------------------------------------


def test_app_priority_dry_run_then_force(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["app", "priority", "on"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == [] and fake_world.resolve.flags["high_priority"] is False
    code, out, _ = run_cli(["app", "priority", "on", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] == "none"
    assert "not read back" in out
    assert fake_world.calls_to("Resolve.SetHighPriority") == [(True,)]
    assert fake_world.write_calls() == ["Resolve.SetHighPriority"]
    assert fake_world.resolve.flags["high_priority"] is True and _no_snapshots(tmp_path)
    code, _, _ = run_cli(["app", "priority", "off", "--force"])
    assert code == 0 and fake_world.resolve.flags["high_priority"] is False


def test_app_priority_bad_state_exits_2(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["app", "priority", "high", "--force"])
    assert code == 2 and "Valid states" in out and fake_world.calls == []


def test_app_priority_refusal_is_api_failed(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("Resolve.SetHighPriority", False)
    code, out, _ = run_cli(["app", "priority", "on", "--force"])
    assert code == 1 and "API_FAILED" in out


# --- app keyframe-mode ----------------------------------------------------------------------


def test_keyframe_mode_show_names_the_mode(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["app", "keyframe-mode", "show"])
    assert code == 0 and parse_toon_keys(out)["keyframe_mode"] == "unknown (0)"
    fake_world.resolve.keyframe_mode = int(fake_world.resolve.KEYFRAME_MODE_SIZING)
    code, out, _ = run_cli(["app", "keyframe-mode", "show"])
    assert parse_toon_keys(out)["keyframe_mode"] == "sizing"
    assert fake_world.write_calls() == []


@pytest.mark.parametrize("mode", ["all", "color", "sizing"])
def test_keyframe_mode_set_force_reads_back_the_enum(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path, mode: str
) -> None:
    code, out, err = run_cli(["app", "keyframe-mode", "set", mode, "--force"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "") and keys["applied"] == "true" and keys["snapshot"] == "none"
    expected = getattr(fake_world.resolve, f"KEYFRAME_MODE_{mode.upper()}")
    assert fake_world.resolve.GetKeyframeMode() == int(expected)
    assert fake_world.calls_to("Resolve.SetKeyframeMode") == [(expected,)]
    assert fake_world.write_calls() == ["Resolve.SetKeyframeMode"] and _no_snapshots(tmp_path)


def test_keyframe_mode_set_dry_run_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["app", "keyframe-mode", "set", "color"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == [] and fake_world.resolve.keyframe_mode == 0


def test_keyframe_mode_set_to_the_current_mode_is_a_no_op(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    run_cli(["app", "keyframe-mode", "set", "color", "--force"])
    fake_world.calls.clear()
    code, out, _ = run_cli(["app", "keyframe-mode", "set", "color", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == []


def test_keyframe_mode_set_bad_mode_exits_2(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["app", "keyframe-mode", "set", "position", "--force"])
    assert code == 2 and "Valid modes" in out and fake_world.calls == []


def test_keyframe_mode_set_without_the_enum_is_api_failed(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    delattr(fake_world.resolve, "KEYFRAME_MODE_COLOR")
    code, out, _ = run_cli(["app", "keyframe-mode", "set", "color", "--force"])
    assert code == 1 and "this Resolve has no KEYFRAME_MODE_COLOR" in out
    assert fake_world.write_calls() == []


# --- app background-tasks -------------------------------------------------------------------


def test_background_tasks_disable_dry_run_then_force(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["app", "background-tasks", "disable"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["app", "background-tasks", "disable", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] == "none"
    assert "lasts until Resolve restarts" in out and "not read back" in out
    assert fake_world.write_calls() == ["Resolve.DisableBackgroundTasksForCurrentResolveSession"]
    assert fake_world.resolve.flags["background_tasks_disabled"] is True
    assert _no_snapshots(tmp_path)


def test_background_tasks_refusal_is_api_failed(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("Resolve.DisableBackgroundTasksForCurrentResolveSession", False)
    code, out, _ = run_cli(["app", "background-tasks", "disable", "--force"])
    assert code == 1 and "API_FAILED" in out


# --- specs ----------------------------------------------------------------------------------


def _paths(spec: CommandSpec, prefix: str = "") -> Iterator[tuple[str, CommandSpec]]:
    here = f"{prefix} {spec.name}".strip()
    yield here, spec
    for child in spec.subcommands.values():
        yield from _paths(child, here)


@pytest.mark.parametrize("name", ["page", "app"])
def test_every_runnable_spec_declares_methods_and_help_works(name: str, run_cli: Run) -> None:
    spec = registry.lookup(name)
    assert spec is not None
    for path, node in _paths(spec):
        if node.run is not None:
            assert node.methods, path
        code, out, err = run_cli([*path.split(), "--help"])
        assert (code, err) == (0, "") and "examples[" in out, path
