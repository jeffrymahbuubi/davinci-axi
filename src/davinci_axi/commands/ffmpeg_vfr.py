"""A packet pts-delta scan that tells constant from variable frame rate (U3).

`ffprobe` reads the first few seconds of video packets (about 0.1 s of work) and prints their
presentation times. The gaps between sorted times are the frame durations: a constant-rate clip has
near-equal gaps, a variable one does not. Pure module, no Resolve import; every subprocess goes
through `ffmpeg_common.run`, so tests replace that one function.
"""

from __future__ import annotations

import itertools
import shlex
import statistics
from dataclasses import dataclass
from pathlib import Path

from davinci_axi.commands import ffmpeg_common
from davinci_axi.errors import AxiError

DEFAULT_SCAN_S, MIN_PACKETS, VFR_SPREAD_PCT = 10.0, 10, 10.0


@dataclass(frozen=True)
class VfrScan:
    packets: int
    median_s: float | None
    min_s: float | None
    max_s: float | None
    spread_pct: float | None  # (max - min) / median * 100, 1 decimal
    variable: bool | None  # None when packets < MIN_PACKETS or no durations


def scan_command(path: Path, seconds: float) -> list[str]:
    """The ffprobe command that prints `pts_time` for the first `seconds` of video packets."""
    return [
        ffmpeg_common.tool_path("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-read_intervals",
        f"%+{seconds:g}",
        "-show_entries",
        "packet=pts_time",
        "-of",
        "csv=p=0",
        str(path),
    ]


def parse_pts(stdout: str) -> list[float]:
    """Sorted pts values (packets come in decode order); blank and `N/A` lines are skipped."""
    values: list[float] = []
    for line in stdout.splitlines():
        text = line.strip().split(",")[0].strip()
        if not text:
            continue
        try:
            values.append(float(text))
        except ValueError:
            continue
    return sorted(values)


def summarize(pts: list[float]) -> VfrScan:
    """Frame-duration statistics from sorted pts; `variable` needs `MIN_PACKETS` packets."""
    packets = len(pts)
    deltas = [b - a for a, b in itertools.pairwise(pts) if b - a > 0]
    if len(deltas) < 2:
        return VfrScan(packets, None, None, None, None, None)
    median = statistics.median(deltas)
    low, high = min(deltas), max(deltas)
    spread = round((high - low) / median * 100, 1) if median > 0 else None
    variable = None if spread is None or packets < MIN_PACKETS else spread > VFR_SPREAD_PCT
    return VfrScan(packets, round(median, 6), round(low, 6), round(high, 6), spread, variable)


def vfr_scan(path: Path, *, seconds: float = DEFAULT_SCAN_S) -> VfrScan:
    """Scan the first `seconds` of `path`; `API_FAILED` (with help) when ffprobe exits non-zero."""
    command = scan_command(path, seconds)
    done = ffmpeg_common.run(command)
    if done.returncode != 0:
        lines = [ln.strip() for ln in (done.stderr or "").splitlines() if ln.strip()]
        raise AxiError(
            "API_FAILED",
            f"ffprobe failed on {path.name}: {lines[0] if lines else f'exit {done.returncode}'}",
            help=[
                f"Run it by hand: {shlex.join(command)}",
                "Check the file plays in ffplay",
            ],
        )
    return summarize(parse_pts(done.stdout or ""))
