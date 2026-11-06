"""`storage clone start|status|stop` (U21): polling, failure paths and the no-snapshot policy."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_storage, ext_writes_core

Run = Callable[[list[str]], tuple[int, str, str]]

START = ["storage", "clone", "start", "/src", "/dst1,/dst2"]
SETTINGS, BEGIN = "MediaStorage.SetCloneToolSettings", "MediaStorage.StartCloneMedia"
STATUS = "MediaStorage.GetCloneStatus"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext_storage.extend()
    monkeypatch.setitem(registry.COMMANDS, "storage", "davinci_axi.commands.storage:SPEC")
    yield


def _keys(out: str) -> dict[str, str]:
    found = (re.match(r"^([A-Za-z_][\w.-]*): (.*)$", line) for line in out.splitlines())
    return {m.group(1): m.group(2) for m in found if m}


def test_clone_start_dry_run_writes_nothing(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(START)
    keys = _keys(out)
    assert code == 0 and keys["applied"] == "false" and err == ""
    assert "clone,idle,Complete" in out and "--force" in out
    assert fake_world.write_calls() == [] and not (tmp_path / "axi-home" / "snapshots").exists()


def test_clone_start_polls_to_complete_with_the_checksum_enum(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = [*START, "--checksum", "sha256", "--preserve-folder-name", "--poll-interval", "0"]
    code, out, err = run_cli([*args, "--force"])
    keys = _keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] == "none"
    assert "clone: Cloning" in err and "clone: Complete 100.0%" in err
    assert "irreversible" not in keys and "outside the project" in keys["warning"]
    assert fake_world.write_calls() == [SETTINGS, BEGIN]
    assert not (tmp_path / "axi-home" / "snapshots").exists()
    sent = fake_world.calls_to(SETTINGS)[0][0]
    sha256 = fake_world.resolve.CLONE_CHECKSUM_TYPE_SHA256  # type: ignore[attr-defined]
    assert sent == {"PreserveFolderName": True, "ChecksumType": sha256}
    assert fake_world.calls_to(BEGIN) == [("/src", ["/dst1", "/dst2"])]
    store = ext_storage.storage_of(fake_world)
    assert store.clones == [("/src", ["/dst1", "/dst2"], sent)]


def test_clone_start_default_settings_leave_the_checksum_alone(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    assert run_cli([*START, "--poll-interval", "0", "--force"])[0] == 0
    assert fake_world.calls_to(SETTINGS)[0][0] == {"PreserveFolderName": False}


def _cloning(world: FakeWorld) -> None:
    """Reads advance the fake job, so put it back to a running clone before each command."""
    status = {"JobStatus": "Cloning", "CompletionPercentage": 0.0}
    ext_storage.storage_of(world).clone_status = status


def test_clone_no_wait_then_busy_then_stop(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, err = run_cli([*START, "--no-wait", "--force"])
    keys = _keys(out)
    assert code == 0 and keys["applied"] == "true" and "clone,idle,started" in out and err == ""
    _cloning(fake_world)
    code, out, _ = run_cli([*START, "--force"])
    assert code == 1 and "CLONE_BUSY" in out
    assert fake_world.calls_to(BEGIN) == [("/src", ["/dst1", "/dst2"])]
    _cloning(fake_world)
    code, out, _ = run_cli(["storage", "clone", "status"])
    assert code == 0 and "status: Cloning" in out and "storage clone stop" in out
    _cloning(fake_world)
    code, out, _ = run_cli(["storage", "clone", "stop"])
    assert code == 0 and _keys(out)["applied"] == "false" and "cloning,true,false" in out
    _cloning(fake_world)
    code, out, _ = run_cli(["storage", "clone", "stop", "--force"])
    keys = _keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] == "none"
    assert fake_world.write_calls()[-1] == "MediaStorage.StopCloneMedia"
    code, out, _ = run_cli(["storage", "clone", "status"])
    assert "status: Cancelled" in out


def test_clone_stop_when_idle_is_a_no_op(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["storage", "clone", "stop", "--force"])
    keys = _keys(out)
    assert code == 0 and keys["applied"] == "false" and keys["note"] == "no clone is running"
    assert fake_world.write_calls() == []


def test_clone_failed_status_is_job_failed(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject(STATUS, {"JobStatus": "Failed", "Error": "disk full"})
    code, out, err = run_cli([*START, "--poll-interval", "0", "--force"])
    assert code == 1 and "JOB_FAILED" in out and "disk full" in out
    assert "clone: Failed" in err


def test_clone_poll_timeout(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject(STATUS, {"JobStatus": "Queued", "CompletionPercentage": 1})
    args = [*START, "--poll-interval", "0", "--poll-timeout", "0.05", "--force"]
    code, out, _ = run_cli(args)
    assert code == 1 and "POLL_TIMEOUT" in out


def test_clone_no_wait_reports_a_failed_start_status(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject(STATUS, {"JobStatus": "Failed", "Error": "no space"})
    code, out, _ = run_cli([*START, "--no-wait", "--force"])
    assert code == 1 and "VERIFY_FAILED" in out


def test_clone_start_validation_is_exit_2_before_any_write(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    for args in (
        ["storage", "clone", "start", "/src", "/src/inside", "--force"],
        ["storage", "clone", "start", "/src", "/src", "--force"],
        [*START, "--checksum", "crc64", "--force"],
        [*START, "--poll-timeout", "0", "--force"],
        ["storage", "clone", "start", "/src", "/a,,/b", "--force"],
    ):
        assert run_cli(args)[0] == 2, args
    assert fake_world.write_calls() == []


def test_clone_start_refused_calls_report_api_failed(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject(SETTINGS, False)
    code, out, _ = run_cli([*START, "--force"])
    assert code == 1 and "API_FAILED" in out and fake_world.calls_to(BEGIN) == []
    fake_world.fail.clear()
    fake_world.inject(BEGIN, False)
    code, out, _ = run_cli([*START, "--force"])
    assert code == 1 and "API_FAILED" in out


def test_clone_missing_enum_is_api_failed(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(type(fake_world.resolve), "CLONE_CHECKSUM_TYPE_MD5")
    code, out, _ = run_cli([*START, "--checksum", "md5", "--force"])
    assert code == 1 and "this Resolve has no CLONE_CHECKSUM_TYPE_MD5" in out
    assert fake_world.write_calls() == []


def test_clone_stop_refusal_is_api_failed(run_cli: Run, fake_world: FakeWorld) -> None:
    _cloning(fake_world)
    fake_world.inject("MediaStorage.StopCloneMedia", False)
    code, out, _ = run_cli(["storage", "clone", "stop", "--force"])
    assert code == 1 and "API_FAILED" in out


def test_clone_status_shows_error_text(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject(STATUS, {"JobStatus": "Failed", "CompletionPercentage": 3.0, "Error": "boom"})
    code, out, _ = run_cli(["storage", "clone", "status"])
    assert code == 0 and "status: Failed" in out and "error: boom" in out
