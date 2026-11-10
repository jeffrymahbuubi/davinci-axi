from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import render
from davinci_axi.commands.render_settings import SETTINGS_SPEC
from davinci_axi.fake import FakeWorld, ext_render
from davinci_axi.fake.core import FakeProject

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_render.extend()
    monkeypatch.setitem(registry.COMMANDS, "render", "davinci_axi.commands.render:SPEC")
    monkeypatch.setitem(render.SPEC.subcommands, "settings", SETTINGS_SPEC)


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _sent(world: FakeWorld) -> dict[str, object]:
    sent = world.calls_to("Project.SetRenderSettings")[0][0]
    assert isinstance(sent, dict)
    return sent


def test_dry_run_lists_the_settings_and_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["render", "settings", "set", "TargetDir=/tmp/x,CustomName=p"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys.get("applied") in (None, "false")
    assert "TargetDir=" in out and "CustomName=p" in out
    assert "render settings set" in out and "--force" in out
    assert fake_world.write_calls() == []


def test_force_calls_set_render_settings_with_typed_values_and_no_job(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path.resolve()
    arg = f"TargetDir={target},CustomName=edit,MarkIn=108100,MarkOut=108200,SelectAllFrames=false"
    code, out, _ = run_cli(["render", "settings", "set", arg, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true"
    assert fake_world.calls_to("Project.SetRenderSettings") == [
        (
            {
                "TargetDir": str(target),
                "CustomName": "edit",
                "MarkIn": 108100,
                "MarkOut": 108200,
                "SelectAllFrames": False,
            },
        )
    ]
    writes = fake_world.write_calls()
    assert "Project.AddRenderJob" not in writes
    assert "ProjectManager.ExportProject" not in writes
    assert "no GetRenderSettings" in keys["note"]
    assert "  called,false,true" in out
    assert _project(fake_world).__dict__["pending_settings"]["CustomName"] == "edit"


def test_target_dir_expands_home_and_is_not_checked(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(
        ["render", "settings", "set", "TargetDir=~/does-not-exist-f36b", "--force"]
    )
    assert code == 0 and "NOT_FOUND" not in out
    sent = str(_sent(fake_world)["TargetDir"])
    assert Path(sent).is_absolute() and sent.startswith(str(Path.home().resolve()))


def test_unknown_key_exits_2_before_connecting(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, err = run_cli(["render", "settings", "set", "Nope=1", "--force"])
    assert code == 2
    assert "VALIDATION_ERROR" in out + err and "Valid settings:" in out + err
    assert fake_world.calls == []


@pytest.mark.parametrize(("arg", "message"), [("MarkIn=abc", "cannot read abc"), ("", "")])
def test_bad_value_and_empty_text_exit_2(
    run_cli: Run, fake_world: FakeWorld, arg: str, message: str
) -> None:
    code, out, err = run_cli(["render", "settings", "set", arg, "--force"])
    assert code == 2 and message in out + err
    assert fake_world.write_calls() == []


def test_video_quality_bits_per_second_warns(run_cli: Run, parse_toon_keys: Keys) -> None:
    code, out, _ = run_cli(["render", "settings", "set", "VideoQuality=20000000"])
    assert code == 0 and "kbps" in parse_toon_keys(out)["warning"]


def test_api_refusal_is_api_failed(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("Project.SetRenderSettings", False)
    code, out, err = run_cli(["render", "settings", "set", "CustomName=x", "--force"])
    assert code == 1
    assert "API_FAILED" in out + err and "SetRenderSettings" in out + err


def test_help_lists_keys_and_the_no_getter_note(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "settings", "set", "--help"])
    assert code == 0 and "TargetDir" in out and "GetRenderSettings" in out
    assert fake_world.calls == []


def test_settings_set_puts_the_page_back(run_cli: Run, fake_world: FakeWorld) -> None:
    ext_render.add_opens_deliver(_project(fake_world))
    code, out, _ = run_cli(["render", "settings", "set", "CustomName=p", "--force", "--json"])
    assert code == 0
    assert json.loads(out)["page_restored"] is True
    assert fake_world.resolve.page == "edit"
    assert fake_world.page_changes() == ["edit"]


def test_settings_set_notes_the_page(run_cli: Run) -> None:
    code, out, _ = run_cli(["render", "settings", "set", "--help"])
    assert code == 0 and "page_restored" in out
