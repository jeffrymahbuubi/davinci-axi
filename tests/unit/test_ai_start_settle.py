"""`--no-wait` AI starts tolerate a short `Inactive` lag before the first status (B3)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_ai, ext_audio
from davinci_axi.fake.ext_ai import set_start_lag

Run = Callable[[list[str]], tuple[int, str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_ai.extend()
    ext_audio.extend()
    monkeypatch.setitem(registry.COMMANDS, "ai", "davinci_axi.commands.ai:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "audio", "davinci_axi.commands.audio:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")
    monkeypatch.setattr("davinci_axi.poll.START_SETTLE_INTERVAL", 0)  # never sleep in tests
    yield


def test_scene_cuts_no_wait_tolerates_a_start_lag(run_cli: Run, fake_world: FakeWorld) -> None:
    set_start_lag(fake_world, 2)
    code, out, err = run_cli(["ai", "scene-cuts", "--no-wait", "--force"])
    assert code == 0 and err == "", out
    assert "status,Inactive,started" in out
    # the busy check, then two lagging reads and the first real one
    assert len(fake_world.calls_to("Project.GetDetectSceneCutsStatus")) == 1 + 3


def test_no_wait_still_fails_when_the_job_never_starts(run_cli: Run, fake_world: FakeWorld) -> None:
    set_start_lag(fake_world, 100)
    code, out, _ = run_cli(["ai", "scene-cuts", "--no-wait", "--force"])
    assert code == 1 and "VERIFY_FAILED" in out and "Inactive" in out


def test_slate_and_transcribe_no_wait_tolerate_a_start_lag(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    set_start_lag(fake_world, 2)
    code, out, err = run_cli(["ai", "slate", "A001_beach", "--no-wait", "--force"])
    assert code == 0 and err == "" and "started" in out, out
    code, out, err = run_cli(["audio", "transcribe", "A001_beach", "--no-wait", "--force"])
    assert code == 0 and err == "" and "started" in out, out
    assert len(fake_world.calls_to("Project.GetTranscribeAudioStatus")) == 1 + 3
