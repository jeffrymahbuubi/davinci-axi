"""Polling for long-running Resolve jobs (KTD21): render, quick export, analysis, clone.

Every job status getter returns a dict with `JobStatus` and `CompletionPercentage` (and `Error` on
failure). `poll_status` reads it until the job reaches one of the kind's done or failed statuses,
writing a progress line to stderr whenever the status or percentage changes; the final result
block stays on stdout. Stable interface: extend it additively, never change what exists.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TextIO

from davinci_axi.errors import AxiError
from davinci_axi.spec import Flag, Invocation

DEFAULT_POLL_TIMEOUT = 600.0
DEFAULT_POLL_INTERVAL = 1.0
START_SETTLE_READS = 12  # extra reads after a start call, ~3 s at the interval below (B3)
START_SETTLE_INTERVAL = 0.25

POLL_FLAGS: tuple[Flag, ...] = (
    Flag("wait", "bool", True, help="wait for the job to finish (--no-wait returns at once)"),
    Flag(
        "poll-timeout",
        "float",
        DEFAULT_POLL_TIMEOUT,
        help="seconds to wait for the job before giving up",
    ),
    Flag("poll-interval", "float", DEFAULT_POLL_INTERVAL, help="seconds between status reads"),
)


@dataclass(frozen=True)
class JobKind:
    """The status vocabulary of one kind of job, taken from the `.pyi` docstrings."""

    label: str
    done: frozenset[str]
    failed: frozenset[str]
    idle: frozenset[str]


RENDER_JOB = JobKind(
    "render",
    frozenset({"Complete"}),
    frozenset({"Failed", "Cancelled", "Background Render Cancelled", "Remote Render Cancelled"}),
    frozenset({"Ready", "Ready for background render", "Ready to remotely render"}),
)
QUICK_EXPORT = JobKind(
    "quick export",
    frozenset({"Render Complete", "Upload Completed"}),
    frozenset(
        {"Render Failed", "Render Cancelled", "Upload Failed", "Upload Cancelled", "Unexpected"}
    ),
    frozenset({"Inactive"}),
)
# slate, transcribe, reframe, scene cuts and subtitles share one status vocabulary
ANALYSIS = JobKind(
    "analysis", frozenset({"Complete"}), frozenset({"Cancelled", "Failed"}), frozenset({"Inactive"})
)
# CloneStatus idles as 'Complete'
CLONE = JobKind(
    "clone", frozenset({"Complete"}), frozenset({"Cancelled", "Failed"}), frozenset({"Complete"})
)


@dataclass(frozen=True)
class PollResult:
    status: str
    percent: int | None
    polls: int
    elapsed: float
    last: dict[str, object]


def _line(label: str, status: str, percent: object) -> str:
    return f"{label}: {status}" if percent is None else f"{label}: {status} {percent}%"


def _failed(kind: JobKind, status: str, info: dict[str, Any]) -> AxiError:
    error = info.get("Error")
    message = f"{kind.label} job ended as {status}"
    detail: dict[str, object] = {"status": status}
    if error:
        message = f"{message}: {error}"
        detail["Error"] = str(error)
    return AxiError(
        "JOB_FAILED",
        message,
        help=["Check the job in Resolve (for example on the Deliver page), fix it, then retry"],
        detail=detail,
    )


def _timed_out(kind: JobKind, status: str, timeout: float) -> AxiError:
    return AxiError(
        "POLL_TIMEOUT",
        f"{kind.label} job was still {status} after {timeout:g}s",
        help=[
            "The job keeps running in Resolve; check its status later",
            "Pass a larger --poll-timeout to wait longer, or --no-wait to return at once",
        ],
        detail={"status": status, "timeout": timeout},
    )


def poll_status(
    read: Callable[[], dict[str, Any]],
    kind: JobKind,
    *,
    timeout: float,
    interval: float,
    sleep: Callable[[float], None] = time.sleep,
    err: TextIO | None = None,
) -> PollResult:
    """Read the job status until it is done; raise JOB_FAILED or POLL_TIMEOUT otherwise.

    `read` returns the status dict (`JobStatus`, `CompletionPercentage`, `Error`). An idle status
    (the job has not started yet) counts as progress until the deadline. The deadline is checked
    after each read, so a job that is already done is returned even with a zero timeout.
    `err` defaults to the stderr of the moment of the call, so captured streams work.
    """
    out = sys.stderr if err is None else err
    started = time.monotonic()
    polls = 0
    shown: str | None = None
    while True:
        info = dict(read() or {})
        polls += 1
        status = str(info.get("JobStatus") or "unknown")
        percent = info.get("CompletionPercentage")
        text = _line(kind.label, status, percent)
        if text != shown:
            out.write(text + "\n")
            out.flush()
            shown = text
        if status in kind.done:
            elapsed = time.monotonic() - started
            return PollResult(status, _percent(percent), polls, elapsed, info)
        if status in kind.failed:
            raise _failed(kind, status, info)
        if time.monotonic() - started >= timeout:
            raise _timed_out(kind, status, timeout)
        if interval > 0:
            sleep(interval)


def settle_start(
    read: Callable[[], dict[str, Any]],
    kind: JobKind,
    *,
    reads: int | None = None,
    interval: float | None = None,
) -> dict[str, Any]:
    """Re-read until the status leaves `kind.idle` or `reads` extra reads are spent.

    Resolve reports `Inactive` for a moment after a start call (live B3), so a `--no-wait` start
    reads the status again before it is verified. Returns the last status dict and writes nothing
    to stderr. The module constants are read at call time when the keywords are None.
    """
    budget = START_SETTLE_READS if reads is None else reads
    pause = START_SETTLE_INTERVAL if interval is None else interval
    info = dict(read() or {})
    for _ in range(budget):
        if str(info.get("JobStatus") or "unknown") not in kind.idle:
            break
        if pause > 0:
            time.sleep(pause)
        info = dict(read() or {})
    return info


def _percent(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def poll_args(inv: Invocation) -> tuple[bool, float, float]:
    """`(wait, timeout, interval)` from `POLL_FLAGS`; bad numbers are VALIDATION_ERROR."""
    wait = bool(inv.args.get("wait", True))
    timeout = _number(inv, "poll_timeout", DEFAULT_POLL_TIMEOUT)
    interval = _number(inv, "poll_interval", DEFAULT_POLL_INTERVAL)
    if timeout <= 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--poll-timeout must be above 0, got {timeout:g}",
            help=["Valid example: --poll-timeout 600"],
        )
    if interval < 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--poll-interval must be 0 or more, got {interval:g}",
            help=["Valid example: --poll-interval 1"],
        )
    return wait, timeout, interval


def _number(inv: Invocation, key: str, default: float) -> float:
    raw = inv.args.get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    return float(raw)
