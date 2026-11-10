"""`ai_mutation`: Extras preflight, error memory and `ai status` last_error (F11 U2)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import ai_state, connect, registry
from davinci_axi.commands.ai_preflight import ai_mutation
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeWorld, ext_ai
from davinci_axi.fake.ext_extras import write_package
from davinci_axi.mutate import Change, MutationPlan
from davinci_axi.session import Session
from davinci_axi.spec import GlobalOpts, Invocation, Result

Run = Callable[[list[str]], tuple[int, str, str]]

SLATE = "AI Slate ID"
LANGS = "Extended AI Transcription Language Support 2"


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_ai.extend()
    monkeypatch.setitem(registry.COMMANDS, "ai", "davinci_axi.commands.ai:SPEC")
    yield


def _seed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *names: str) -> None:
    root = tmp_path / "extras-seed"
    root.mkdir()
    for name in names:
        write_package(root, name)
    monkeypatch.setenv("DAVINCI_AXI_EXTRAS_DIR", str(root))


def _inv(*, force: bool, ignore: bool = False) -> Invocation:
    return Invocation(
        opts=GlobalOpts(),
        args={
            "force": force,
            "ignore_preflight": ignore,
            "no_snapshot": False,
            "snapshot_out": None,
        },
        positionals={},
        connect=lambda: connect.connect(GlobalOpts()),
    )


class Probe:
    def __init__(self) -> None:
        self.applied = 0
        self.plan = MutationPlan(
            command="ai probe",
            target="the probe",
            changes=[Change("called", False, True)],
            force_cmd="davinci-axi ai probe --force",
            warning="takes a while",
        )

    def apply(self) -> None:
        self.applied += 1

    def run(
        self,
        inv: Invocation,
        *,
        package: str | None = None,
        advisory: bool = False,
    ) -> Result:
        return ai_mutation(
            inv,
            _session(),
            self.plan,
            self.apply,
            lambda: {"called": True},
            package=package,
            advisory=advisory,
        )


def _session() -> Session:
    return connect.connect(GlobalOpts())


def test_missing_package_refuses_before_any_write(
    fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(monkeypatch, tmp_path, "AI Motion Deblur")
    probe = Probe()
    with pytest.raises(AxiError) as caught:
        probe.run(_inv(force=True), package=SLATE)
    err = caught.value
    assert err.code == "NOT_AVAILABLE" and err.exit_code == 1
    assert err.detail["preflight"] == "missing" and err.detail["package"] == SLATE
    text = "\n".join(err.help)
    assert "Extras Download Manager" in text and "--ignore-preflight" in text
    assert fake_world.calls_to("ProjectManager.SaveProject") == []
    assert fake_world.calls_to("ProjectManager.ExportProject") == []
    assert not (tmp_path / "axi-home" / "snapshots").exists()
    assert probe.applied == 0
    assert ai_state.load()["ai probe"].code == "NOT_AVAILABLE"


def test_ignore_preflight_proceeds_and_snapshots(
    fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(monkeypatch, tmp_path, "AI Motion Deblur")
    probe = Probe()
    result = probe.run(_inv(force=True, ignore=True), package=SLATE)
    assert probe.applied == 1 and result.data["applied"] is True
    assert len(fake_world.calls_to("ProjectManager.ExportProject")) == 1
    assert "will fail" not in str(result.data.get("warning", ""))


def test_unknown_warns_but_proceeds(fake_world: FakeWorld) -> None:
    probe = Probe()
    dry = probe.run(_inv(force=False), package=SLATE)
    assert str(dry.data["ai_extras"]).startswith("unknown:")
    assert any("ai extras" in line for line in dry.help)
    assert probe.applied == 0 and fake_world.write_calls() == []
    done = probe.run(_inv(force=True), package=SLATE)
    assert probe.applied == 1 and done.data["applied"] is True
    assert len(fake_world.calls_to("ProjectManager.ExportProject")) == 1


def test_advisory_missing_never_refuses(
    fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(monkeypatch, tmp_path, "AI Motion Deblur")
    probe = Probe()
    dry = probe.run(_inv(force=False), package=LANGS, advisory=True)
    assert "built-in languages still work" in str(dry.data["ai_extras"])
    assert "will fail" not in str(dry.data["warning"])
    assert any(h.startswith("Optional, for more languages") for h in dry.help)
    assert not any("then retry" in h for h in dry.help)
    done = probe.run(_inv(force=True), package=LANGS, advisory=True)
    assert probe.applied == 1 and done.data["applied"] is True


def test_dry_run_says_will_fail(
    fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(monkeypatch, tmp_path, "AI Motion Deblur")
    probe = Probe()
    result = probe.run(_inv(force=False), package=SLATE)
    assert result.data["applied"] is False
    assert str(result.data["warning"]).startswith("will fail:")
    assert "takes a while" in str(result.data["warning"])
    assert result.data["ai_extras"] == f"missing: {SLATE} (will fail)"
    assert result.data["changes"]
    assert fake_world.write_calls() == [] and ai_state.load() == {}


def test_installed_package_has_no_hints_and_no_warning_prefix(
    fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(monkeypatch, tmp_path, SLATE)
    result = Probe().run(_inv(force=False), package=SLATE)
    assert result.data["ai_extras"] == f"installed: {SLATE}"
    assert result.data["warning"] == "takes a while"
    assert not any("ai extras" in line for line in result.help)


def test_no_package_means_no_field_but_memory(fake_world: FakeWorld) -> None:
    probe = Probe()
    result = probe.run(_inv(force=True))
    assert "ai_extras" not in result.data and probe.applied == 1

    def boom() -> None:
        raise AxiError("NOT_AVAILABLE", "nope")

    with pytest.raises(AxiError):
        ai_mutation(_inv(force=True), _session(), probe.plan, boom, lambda: {})
    assert ai_state.load()["ai probe"].message == "nope"


def test_live_false_is_remembered_and_success_forgets(run_cli: Run, fake_world: FakeWorld) -> None:
    ext_ai.set_ai_extras(fake_world, False)
    code, _, _ = run_cli(["ai", "magic-mask", "create", "V1:1", "--force"])
    assert code == 1
    assert ai_state.load()["ai magic-mask create"].code == "NOT_AVAILABLE"
    ext_ai.set_ai_extras(fake_world, True)
    code, _, _ = run_cli(["ai", "magic-mask", "create", "V1:1", "--force"])
    assert code == 0
    assert "ai magic-mask create" not in ai_state.load()


def test_validation_errors_are_not_remembered(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _, _ = run_cli(["ai", "magic-mask", "create", "V1:1", "--mode", "sideways", "--force"])
    assert code == 2
    assert ai_state.load() == {}


def test_status_shows_last_error_and_clears(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["ai", "status"])
    assert code == 0 and "other_errors" not in out and "clear-errors" not in out
    ext_ai.set_ai_extras(fake_world, False)
    run_cli(["ai", "magic-mask", "create", "V1:1", "--force"])
    ai_state.record("audio transcribe", AxiError("NOT_AVAILABLE", "x"), "p")
    code, out, _ = run_cli(["ai", "status"])
    assert code == 0
    assert "other_errors[1]{command,code,at}:" in out
    assert "ai magic-mask create,NOT_AVAILABLE," in out
    row = next(line for line in out.splitlines() if line.strip().startswith("transcribe,"))
    assert ',"NOT_AVAILABLE at 20' in row
    assert "ai status --clear-errors" in out
    code, out, _ = run_cli(["ai", "status", "--clear-errors"])
    assert code == 0 and "errors_cleared: 2" in out
    assert "other_errors" not in out and "NOT_AVAILABLE at" not in out
    assert ai_state.load() == {}
