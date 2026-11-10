from __future__ import annotations

import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from davinci_axi import connect as connect_module
from davinci_axi import fake
from davinci_axi import probe as probe_module
from davinci_axi.connect import connect, probe
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeWorld
from davinci_axi.probe import (
    _parse_etime,
    is_fake,
    process_age_seconds,
    process_running,
    studio_licence_present,
)
from davinci_axi.spec import GlobalOpts

OPTS = GlobalOpts(timeout=2.0)


@pytest.mark.parametrize(
    ("connection", "code"),
    [
        ("not_installed", "RESOLVE_NOT_INSTALLED"),
        ("not_running", "RESOLVE_NOT_RUNNING"),
        ("free", "FREE_EDITION"),
        ("scripting_disabled", "SCRIPTING_DISABLED"),
    ],
)
def test_each_connection_failure_has_its_code(
    fake_world: FakeWorld, connection: str, code: str
) -> None:
    fake_world.connection = connection  # type: ignore[assignment]
    with pytest.raises(AxiError) as caught:
        connect(OPTS)
    assert caught.value.code == code
    assert caught.value.help, "every connection error names a fix"
    assert caught.value.exit_code == 1


def test_scripting_disabled_names_the_preference(fake_world: FakeWorld) -> None:
    fake_world.connection = "scripting_disabled"
    with pytest.raises(AxiError) as caught:
        connect(OPTS)
    text = " ".join(caught.value.help)
    assert "Preferences > System > General" in text
    assert "External scripting using" in text and "Local" in text


def test_free_edition_says_studio_only(fake_world: FakeWorld) -> None:
    fake_world.connection = "free"
    with pytest.raises(AxiError) as caught:
        connect(OPTS)
    assert "Studio" in " ".join(caught.value.help)


def test_hang_times_out(fake_world: FakeWorld) -> None:
    fake_world.connection = "hang"
    started = time.monotonic()
    with pytest.raises(AxiError) as caught:
        connect(GlobalOpts(timeout=0.3))
    assert caught.value.code == "RESOLVE_TIMEOUT"
    assert time.monotonic() - started < 0.8
    assert "--timeout" in " ".join(caught.value.help)


def test_connected_session_fields(fake_world: FakeWorld) -> None:
    session = connect(OPTS)
    assert session.version == (21, 1, 1)
    assert session.product == "DaVinci Resolve Studio"
    assert session.is_studio is True
    assert session.require_project().GetName() == "davinci-axi-test"
    assert session.current_timeline() is not None


def test_require_version_names_the_minimum(fake_world: FakeWorld) -> None:
    fake_world.resolve.version = [20, 3, 1, 1, ""]
    session = connect(OPTS)
    assert session.version == (20, 3, 1)
    with pytest.raises(AxiError) as caught:
        session.require_version("GetSelectedClips", (21, 0, 4))
    assert caught.value.code == "VERSION_TOO_OLD"
    assert "21.0.4" in caught.value.message
    session.require_version("GetName", (20, 0, 0))


def test_require_timeline_not_found_lists_timelines(fake_world: FakeWorld) -> None:
    session = connect(OPTS)
    assert session.require_timeline().GetName() == "Edit v1"
    assert session.require_timeline("Edit v2").GetName() == "Edit v2"
    with pytest.raises(AxiError) as caught:
        session.require_timeline("missing")
    assert caught.value.code == "NOT_FOUND"
    assert "Edit v1, Edit v2" in caught.value.help[0]


def test_no_project_and_no_timeline(fake_world: FakeWorld) -> None:
    session = connect(OPTS)
    project = session.require_project()
    project.current_timeline = None  # type: ignore[attr-defined]
    with pytest.raises(AxiError) as caught:
        session.require_timeline()
    assert caught.value.code == "NO_TIMELINE"
    fake_world.resolve.pm.current_project = None
    assert session.current_project() is None
    assert session.current_timeline() is None
    with pytest.raises(AxiError) as caught:
        session.require_project()
    assert caught.value.code == "NO_PROJECT"


def test_probe_never_raises(fake_world: FakeWorld) -> None:
    ok = probe(OPTS)
    assert (ok.ok, ok.code, ok.version, ok.is_studio) == (True, None, "21.1.1", True)
    fake_world.connection = "scripting_disabled"
    bad = probe(OPTS)
    assert bad.ok is False and bad.code == "SCRIPTING_DISABLED" and bad.fix
    fake_world.connection = "starting"
    fake_world.starting_answers = 99
    waiting = probe(GlobalOpts(timeout=0.6))
    assert waiting.ok is False and waiting.code == "RESOLVE_STARTING" and waiting.fix


def test_fake_probes_follow_the_world(fake_world: FakeWorld) -> None:
    assert is_fake() and process_running() and studio_licence_present()
    fake_world.connection = "not_running"
    assert not process_running()
    fake_world.connection = "free"
    assert process_running() and not studio_licence_present()


def _real_mode(monkeypatch: pytest.MonkeyPatch, module: object) -> None:
    """Exercise the real code path with the import stubbed: no DaVinciResolveScript is loaded."""
    monkeypatch.setenv("DAVINCI_AXI_FAKE", "0")
    monkeypatch.setattr(connect_module, "_prepare_sys_path", lambda: None)

    def fake_import(name: str) -> object:
        assert name == "DaVinciResolveScript"
        if isinstance(module, BaseException):
            raise module
        return module

    monkeypatch.setattr(connect_module.importlib, "import_module", fake_import)


def test_real_path_connects_through_the_stubbed_module(
    monkeypatch: pytest.MonkeyPatch, fake_world: FakeWorld
) -> None:
    module = types.SimpleNamespace(scriptapp=lambda name: fake_world.resolve)
    _real_mode(monkeypatch, module)
    assert connect(OPTS).product == "DaVinci Resolve Studio"
    assert "DaVinciResolveScript" not in sys.modules


def test_real_path_import_error_means_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_mode(monkeypatch, ImportError("Could not locate module dependencies"))
    with pytest.raises(AxiError) as caught:
        connect(OPTS)
    assert caught.value.code == "RESOLVE_NOT_INSTALLED"


@pytest.mark.parametrize(
    ("running", "studio", "code"),
    [
        (False, True, "RESOLVE_NOT_RUNNING"),
        (True, False, "FREE_EDITION"),
        (True, True, "SCRIPTING_DISABLED"),
    ],
)
def test_real_path_classifies_a_none_app(
    monkeypatch: pytest.MonkeyPatch, running: bool, studio: bool, code: str
) -> None:
    _real_mode(monkeypatch, types.SimpleNamespace(scriptapp=lambda name: None))
    monkeypatch.setattr(connect_module, "process_running", lambda: running)
    monkeypatch.setattr(connect_module, "studio_licence_present", lambda: studio)
    with pytest.raises(AxiError) as caught:
        connect(OPTS)
    assert caught.value.code == code


def test_prepare_sys_path_honours_resolve_script_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("RESOLVE_SCRIPT_API", str(tmp_path))
    connect_module._prepare_sys_path()
    assert str(tmp_path / "Modules") in sys.path


def test_scriptapp_ignores_other_names(fake_world: FakeWorld) -> None:
    assert fake.scriptapp("Fusion") is None
    assert fake.scriptapp("Resolve") is fake_world.resolve


def test_starting_resolve_is_retried_within_the_timeout(fake_world: FakeWorld) -> None:
    fake_world.connection = "starting"
    fake_world.starting_answers = 2
    started = time.monotonic()
    session = connect(GlobalOpts(timeout=3.0))
    elapsed = time.monotonic() - started
    assert session.product == "DaVinci Resolve Studio"
    assert 1.0 <= elapsed < 2.5


def test_starting_resolve_past_the_timeout_is_resolve_starting(fake_world: FakeWorld) -> None:
    fake_world.connection = "starting"
    fake_world.starting_answers = 99
    with pytest.raises(AxiError) as caught:
        connect(GlobalOpts(timeout=1.2))
    assert caught.value.code == "RESOLVE_STARTING"
    text = " ".join(caught.value.help)
    assert "still loading" in text and "Preferences" not in text.split("still fails")[0]
    assert caught.value.exit_code == 1


def test_an_old_process_refusing_is_scripting_disabled_without_retry(
    fake_world: FakeWorld,
) -> None:
    fake_world.connection = "scripting_disabled"
    started = time.monotonic()
    with pytest.raises(AxiError) as caught:
        connect(OPTS)
    assert caught.value.code == "SCRIPTING_DISABLED"
    assert time.monotonic() - started < 0.4
    assert process_age_seconds() == 600.0


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("05", 5), ("01:05", 65), ("02:03:04", 7384), ("1-02:03:04", 93784), ("  00:09\n", 9)],
)
def test_parse_etime(text: str, seconds: float) -> None:
    assert _parse_etime(text) == seconds


@pytest.mark.parametrize("text", ["x", "", "1-x", "a:b"])
def test_parse_etime_rejects_garbage(text: str) -> None:
    assert _parse_etime(text) is None


def test_process_age_reads_ps_etime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAVINCI_AXI_FAKE", "0")
    monkeypatch.setattr(probe_module.sys, "platform", "darwin")
    seen: list[list[str]] = []

    def run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        out = "4242\n" if cmd[0] == "pgrep" else "  00:07\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(probe_module.subprocess, "run", run)
    assert process_age_seconds() == 7.0
    assert seen[1] == ["ps", "-o", "etime=", "-p", "4242"]


def test_process_age_is_none_when_pgrep_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAVINCI_AXI_FAKE", "0")
    monkeypatch.setattr(probe_module.sys, "platform", "darwin")

    def run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(probe_module.subprocess, "run", run)
    assert process_age_seconds() is None
    monkeypatch.setattr(probe_module.sys, "platform", "linux")
    assert process_age_seconds() is None
