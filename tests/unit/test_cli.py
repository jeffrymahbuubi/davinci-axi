from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable

import pytest

from davinci_axi import __version__, registry
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeWorld
from davinci_axi.spec import CommandSpec, Flag, Invocation, Result

Run = Callable[[list[str]], tuple[int, str, str]]


def _boom(_inv: Invocation) -> Result:
    raise RuntimeError("kaboom")


def _needs_resolve(inv: Invocation) -> Result:
    inv.connect()
    return Result({"ok": True})


def _install(monkeypatch: pytest.MonkeyPatch, specs: dict[str, CommandSpec]) -> None:
    original = registry.lookup
    monkeypatch.setattr(
        registry, "lookup", lambda name: specs[name] if name in specs else original(name)
    )


def test_version_prints_bare_version(run_cli: Run) -> None:
    for flag in ("--version", "-V", "-v"):
        code, out, err = run_cli([flag])
        assert (code, out, err) == (0, f"{__version__}\n", "")


def test_version_fast_path_imports_nothing_heavy() -> None:
    probe = (
        "import sys; from davinci_axi.cli import main; code = main(['--version']);"
        "bad = [m for m in sys.modules if m.startswith('davinci_axi.commands.')"
        " or m in ('davinci_axi.connect', 'davinci_axi.fake', 'DaVinciResolveScript')];"
        "print(code, bad)"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, timeout=30
    )
    assert done.stdout.splitlines()[-1] == "0 []"


def test_top_level_help(run_cli: Run) -> None:
    code, out, err = run_cli(["--help"])
    assert code == 0
    assert err == ""
    assert "commands[" in out
    assert "--json" in out
    assert "examples[" in out
    assert "home" in out


def test_help_flag_at_any_position(run_cli: Run) -> None:
    code, out, _ = run_cli(["home", "--help"])
    assert code == 0
    assert out.startswith("usage: davinci-axi home")


def test_unknown_command_exits_2(run_cli: Run) -> None:
    code, out, _ = run_cli(["nope"])
    assert code == 2
    assert "code: VALIDATION_ERROR" in out
    assert "Valid commands: home" in out


def test_unknown_top_level_flag_exits_2(run_cli: Run) -> None:
    code, out, err = run_cli(["--axi-probe-unknown-flag"])
    assert code == 2
    assert "Valid flags" in out and "--json" in out
    assert err == ""


def test_internal_error_hides_traceback_by_default(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run
) -> None:
    _install(monkeypatch, {"boom": CommandSpec("boom", "Raises", run=_boom)})
    code, out, err = run_cli(["boom"])
    assert code == 1
    assert "code: INTERNAL" in out
    assert "kaboom" in out
    assert "Traceback" not in out
    assert err == ""


def test_internal_error_prints_traceback_to_stderr_with_debug(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run
) -> None:
    _install(monkeypatch, {"boom": CommandSpec("boom", "Raises", run=_boom)})
    code, out, err = run_cli(["boom", "--debug"])
    assert code == 1
    assert "Traceback" not in out
    assert "Traceback" in err and "RuntimeError: kaboom" in err


def test_bare_parent_needs_a_subcommand(monkeypatch: pytest.MonkeyPatch, run_cli: Run) -> None:
    child = CommandSpec("presets", "List presets", run=lambda _inv: Result({"n": 1}))
    parent = CommandSpec("render", "Render", subcommands={"presets": child})
    _install(monkeypatch, {"render": parent})
    code, out, _ = run_cli(["render"])
    assert code == 2
    assert "Valid subcommands for `render`: presets" in out
    code, out, _ = run_cli(["render", "bogus"])
    assert code == 2 and "unknown subcommand bogus" in out
    code, out, _ = run_cli(["render", "presets"])
    assert code == 0 and "n: 1" in out
    code, out, err = run_cli(["render", "--help"])
    assert code == 0 and "subcommands[1]{name,summary}" in out and err == ""
    code, out, _ = run_cli(["render", "presets", "--help"])
    assert code == 0 and out.startswith("usage: davinci-axi render presets")


def test_resolve_error_renders_code_and_exit_1(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld
) -> None:
    _install(monkeypatch, {"needs": CommandSpec("needs", "Connects", run=_needs_resolve)})
    fake_world.connection = "not_running"
    code, out, _ = run_cli(["needs"])
    assert code == 1
    assert "code: RESOLVE_NOT_RUNNING" in out
    code, out, _ = run_cli(["needs", "--json"])
    assert code == 1
    assert json.loads(out)["code"] == "RESOLVE_NOT_RUNNING"


def test_flags_are_validated_before_connecting(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld
) -> None:
    spec = CommandSpec("needs", "Connects", run=_needs_resolve, flags=(Flag("limit", "int", 5),))
    _install(monkeypatch, {"needs": spec})
    code, _, _ = run_cli(["needs", "--stat"])
    assert code == 2
    assert fake_world.calls == []


def test_timeout_flag_validation(run_cli: Run) -> None:
    code, out, _ = run_cli(["--timeout", "fast"])
    assert code == 2 and "--timeout" in out
    code, out, _ = run_cli(["home", "--timeout"])
    assert code == 2 and "needs a value" in out


def test_timeout_env_default(monkeypatch: pytest.MonkeyPatch, run_cli: Run) -> None:
    monkeypatch.setenv("DAVINCI_AXI_TIMEOUT", "-3")
    code, out, _ = run_cli(["home"])
    assert code == 2 and "DAVINCI_AXI_TIMEOUT" in out


def test_axi_error_is_importable_with_defaults() -> None:
    err = AxiError("X", "m")
    assert err.help == [] and err.detail == {} and err.exit_code == 1
