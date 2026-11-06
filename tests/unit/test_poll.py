from __future__ import annotations

import io
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from davinci_axi import flags, poll
from davinci_axi.errors import AxiError
from davinci_axi.poll import (
    ANALYSIS,
    CLONE,
    POLL_FLAGS,
    QUICK_EXPORT,
    RENDER_JOB,
    PollResult,
    poll_args,
    poll_status,
    settle_start,
)
from davinci_axi.spec import CommandSpec, GlobalOpts, Invocation

Info = dict[str, Any]


def _reader(*infos: Info) -> Any:
    """A read() that returns each dict once, then repeats the last one."""
    queue = list(infos)

    def read() -> Info:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return read


def _run(*infos: Info, timeout: float = 5.0, interval: float = 0.0, kind: Any = RENDER_JOB) -> Any:
    err = io.StringIO()
    sleeps: list[float] = []
    result = poll_status(
        _reader(*infos), kind, timeout=timeout, interval=interval, sleep=sleeps.append, err=err
    )
    return result, err.getvalue().splitlines(), sleeps


def _invocation(argv: list[str]) -> Invocation:
    spec = CommandSpec("x", "x", flags=POLL_FLAGS)
    args, positionals = flags.parse(spec, argv)

    def connect() -> Any:
        raise AssertionError("poll_args must not connect")

    return Invocation(GlobalOpts(), args, positionals, connect)


def test_completes_after_three_reads_with_two_progress_lines() -> None:
    running = {"JobStatus": "Rendering", "CompletionPercentage": 50}
    done = {"JobStatus": "Complete", "CompletionPercentage": 100}
    result, lines, _ = _run(running, running, done)
    assert isinstance(result, PollResult)
    assert (result.status, result.percent, result.polls) == ("Complete", 100, 3)
    assert result.last == done
    assert result.elapsed >= 0
    assert lines == ["render: Rendering 50%", "render: Complete 100%"]


def test_a_job_that_is_already_done_returns_after_one_read_even_with_zero_timeout() -> None:
    result, lines, _ = _run({"JobStatus": "Complete", "CompletionPercentage": 100}, timeout=0)
    assert result.polls == 1
    assert lines == ["render: Complete 100%"]


def test_a_failed_status_raises_job_failed_with_the_error_text() -> None:
    with pytest.raises(AxiError) as caught:
        _run(
            {"JobStatus": "Rendering", "CompletionPercentage": 10},
            {"JobStatus": "Failed", "Error": "disk full"},
        )
    err = caught.value
    assert err.code == "JOB_FAILED"
    assert "disk full" in err.message
    assert err.detail == {"status": "Failed", "Error": "disk full"}
    assert err.exit_code == 1


def test_every_failed_status_of_every_kind_raises() -> None:
    for kind in (RENDER_JOB, QUICK_EXPORT, ANALYSIS, CLONE):
        for status in kind.failed:
            with pytest.raises(AxiError) as caught:
                _run({"JobStatus": status}, kind=kind)
            assert caught.value.code == "JOB_FAILED"
            assert kind.label in caught.value.message


def test_the_deadline_raises_poll_timeout_naming_the_last_status() -> None:
    with pytest.raises(AxiError) as caught:
        _run({"JobStatus": "Rendering", "CompletionPercentage": 10}, timeout=0)
    assert caught.value.code == "POLL_TIMEOUT"
    assert caught.value.detail["status"] == "Rendering"
    assert "--poll-timeout" in " ".join(caught.value.help)


def test_the_deadline_follows_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks: Iterator[float] = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    monkeypatch.setattr(poll, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    reads = 0

    def read() -> Info:
        nonlocal reads
        reads += 1
        return {"JobStatus": "Rendering", "CompletionPercentage": 1}

    with pytest.raises(AxiError) as caught:
        poll_status(
            read, RENDER_JOB, timeout=2.5, interval=0, sleep=lambda _: None, err=io.StringIO()
        )
    assert caught.value.code == "POLL_TIMEOUT"
    assert reads == 3


def test_an_idle_status_counts_as_progress_until_the_deadline() -> None:
    ready = {"JobStatus": "Ready", "CompletionPercentage": 0}
    result, lines, _ = _run(
        ready,
        ready,
        {"JobStatus": "Rendering", "CompletionPercentage": 50},
        {"JobStatus": "Complete", "CompletionPercentage": 100},
    )
    assert result.polls == 4
    assert lines[0] == "render: Ready 0%"
    with pytest.raises(AxiError) as caught:
        _run(ready, timeout=0)
    assert caught.value.code == "POLL_TIMEOUT"
    assert caught.value.detail["status"] == "Ready"


def test_interval_zero_never_sleeps_and_a_positive_interval_sleeps_between_reads() -> None:
    running = {"JobStatus": "Rendering", "CompletionPercentage": 5}
    done = {"JobStatus": "Complete", "CompletionPercentage": 100}
    _, _, sleeps = _run(running, running, done, interval=0)
    assert sleeps == []
    _, _, sleeps = _run(running, running, done, interval=0.25)
    assert sleeps == [0.25, 0.25]


def test_a_missing_percentage_prints_the_status_alone() -> None:
    result, lines, _ = _run({"JobStatus": "Complete"}, kind=ANALYSIS)
    assert result.percent is None
    assert lines == ["analysis: Complete"]


def test_an_empty_status_dict_reads_as_unknown_and_keeps_polling() -> None:
    result, lines, _ = _run({}, {"JobStatus": "Complete", "CompletionPercentage": 100})
    assert result.polls == 2
    assert lines[0] == "render: unknown"


def test_default_stream_is_the_stderr_of_the_moment(capsys: pytest.CaptureFixture[str]) -> None:
    poll_status(
        _reader({"JobStatus": "Complete", "CompletionPercentage": 100}),
        RENDER_JOB,
        timeout=1,
        interval=0,
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "render: Complete 100%\n"


def test_the_job_kinds_carry_the_pyi_vocabularies() -> None:
    assert RENDER_JOB.done == {"Complete"}
    assert "Remote Render Cancelled" in RENDER_JOB.failed
    assert "Ready for background render" in RENDER_JOB.idle
    assert QUICK_EXPORT.done == {"Render Complete", "Upload Completed"}
    assert QUICK_EXPORT.idle == {"Inactive"}
    assert ANALYSIS.failed == {"Cancelled", "Failed"}
    assert CLONE.idle == CLONE.done == {"Complete"}


def test_poll_flags_and_defaults() -> None:
    assert [(f.name, f.kind, f.default) for f in POLL_FLAGS] == [
        ("wait", "bool", True),
        ("poll-timeout", "float", 600.0),
        ("poll-interval", "float", 1.0),
    ]
    assert poll_args(_invocation([])) == (True, 600.0, 1.0)


def test_poll_args_reads_no_wait_and_the_numbers() -> None:
    inv = _invocation(["--no-wait", "--poll-timeout", "12.5", "--poll-interval", "0"])
    assert poll_args(inv) == (False, 12.5, 0.0)


@pytest.mark.parametrize(
    "argv", [["--poll-timeout", "0"], ["--poll-timeout", "-1"], ["--poll-interval", "-0.5"]]
)
def test_poll_args_rejects_out_of_range_numbers(argv: list[str]) -> None:
    with pytest.raises(AxiError) as caught:
        poll_args(_invocation(argv))
    assert caught.value.code == "VALIDATION_ERROR"
    assert caught.value.exit_code == 2


def test_settle_start_returns_when_the_status_leaves_idle() -> None:
    read = _reader({"JobStatus": "Inactive"}, {"JobStatus": "Inactive"}, {"JobStatus": "Analyzing"})
    calls: list[int] = []

    def counted() -> Info:
        calls.append(1)
        return read()  # type: ignore[no-any-return]

    info = settle_start(counted, ANALYSIS, reads=10, interval=0)
    assert info["JobStatus"] == "Analyzing" and len(calls) == 3


def test_settle_start_spends_its_budget() -> None:
    calls: list[int] = []

    def idle() -> Info:
        calls.append(1)
        return {"JobStatus": "Inactive"}

    info = settle_start(idle, ANALYSIS, reads=4, interval=0)
    assert info["JobStatus"] == "Inactive" and len(calls) == 5
