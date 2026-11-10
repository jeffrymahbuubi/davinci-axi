from __future__ import annotations

from collections.abc import Callable

import pytest

from davinci_axi import registry
from davinci_axi.commands import timeline_track_lock
from davinci_axi.fake import FakeTimeline, FakeWorld, ext_timeline_edit

Run = Callable[[list[str]], tuple[int, str, str]]

GETTER = "Timeline.GetIsTrackLocked"


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_timeline_edit.extend()
    monkeypatch.setitem(registry.COMMANDS, "timeline", "davinci_axi.commands.timeline:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")
    monkeypatch.setattr(timeline_track_lock, "SETTLE_SECONDS", 0)


def _edit(world: FakeWorld) -> FakeTimeline:
    project = world.resolve.pm.current_project
    assert project is not None
    return next(t for t in project.timelines if t.name == "Edit v1")


def _lock(run_cli: Run, world: FakeWorld, kind: str) -> tuple[int, str, int]:
    """Run `lock <kind> 1 --force`; the third item is how many getter calls it made."""
    before = len(world.calls_to(GETTER))
    code, out, _ = run_cli(["timeline", "track", "lock", kind, "1", "--force"])
    return code, out, len(world.calls_to(GETTER)) - before


def test_lock_with_a_normal_read_back_does_not_retry(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = _lock(run_cli, fake_world, "video")
    assert code == 0 and "applied: true" in out
    assert "read_back_retried" not in out
    assert _edit(fake_world).tracks["video"][0].locked is True


def test_lock_read_back_stuck_false_fails_with_the_quirk_help(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _, _, normal_calls = _lock(run_cli, fake_world, "audio")
    fake_world.inject(GETTER, False)
    code, out, calls = _lock(run_cli, fake_world, "video")
    assert code == 1
    assert "code: VERIFY_FAILED" in out
    assert "GetIsTrackLocked" in out and "lock icon" in out
    assert calls == normal_calls + 1  # exactly one retry read
    assert _edit(fake_world).tracks["video"][0].locked is True  # the write stood


def test_lock_read_back_that_settles_passes_and_says_it_retried(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = FakeTimeline.GetIsTrackLocked
    reads_after_write: list[bool] = []

    def flaky(self: FakeTimeline, trackType: str, trackIndex: int) -> bool:
        value = real(self, trackType, trackIndex)
        if not self._world.calls_to("Timeline.SetTrackLock"):
            return value
        reads_after_write.append(value)
        return False if len(reads_after_write) == 1 else value  # False once, then the truth

    monkeypatch.setattr(FakeTimeline, "GetIsTrackLocked", flaky)
    code, out, _ = _lock(run_cli, fake_world, "video")
    assert code == 0 and "applied: true" in out
    assert "read_back_retried: true" in out


@pytest.mark.parametrize("kind", ["video", "audio"])
def test_disable_never_retries(run_cli: Run, fake_world: FakeWorld, kind: str) -> None:
    code, out, _ = run_cli(["timeline", "track", "disable", kind, "1", "--force"])
    assert code == 0 and "applied: true" in out
    assert "read_back_retried" not in out


def test_lock_dry_run_writes_nothing(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timeline", "track", "lock", "audio", "1"])
    assert code == 0 and "applied: false" in out
    assert fake_world.write_calls() == []


def test_lock_and_unlock_help_carry_the_note(run_cli: Run) -> None:
    for verb in ("lock", "unlock"):
        code, out, _ = run_cli(["timeline", "track", verb, "--help"])
        assert code == 0 and "GetIsTrackLocked" in out, verb
    code, out, _ = run_cli(["timeline", "track", "enable", "--help"])
    assert code == 0 and "GetIsTrackLocked" not in out
