"""`render verify`: measure a rendered file against size, loudness and duration caps (U5).

Read-only: it runs `ffprobe` (and `ffmpeg` for loudness) on a file, or on the output of a render
job found through `--job`, and reports one pass/fail row per cap the user gave. Sizes are decimal
MB (1,000,000 bytes), the unit `render deliver --max-mb` uses. `ffprobe`/`ffmpeg` are optional
`PATH` programs: a missing one is `NOT_AVAILABLE` before Resolve is touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from davinci_axi.commands import ffmpeg_common
from davinci_axi.commands.render_common import methods_of, render_project
from davinci_axi.commands.render_deliver import encoder_floor_kbps
from davinci_axi.errors import AxiError, not_found
from davinci_axi.output import collapse_home, hint
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

BYTES_PER_MB = 1_000_000
DURATION_TOLERANCE_S = 0.5
DEFAULT_TOLERANCE = 1.0


def _bad(message: str, help_line: str) -> AxiError:
    return AxiError("VALIDATION_ERROR", message, help=[help_line])


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def checks_for(
    measured: dict[str, object], caps: dict[str, float | None]
) -> list[dict[str, object]]:
    """One row per cap that was given: `size`, `loudness`, `true_peak`, `duration`.

    `caps` keys: `max_mb`, `lufs`, `lufs_tolerance`, `true_peak`, `expected_s`; a missing or None
    cap adds no row. A cap whose number could not be measured fails with `measured` None.
    """
    rows: list[dict[str, object]] = []
    max_mb = caps.get("max_mb")
    if max_mb is not None:
        size = _number(measured.get("bytes"))
        mb = size / BYTES_PER_MB if size is not None else None
        rows.append(
            {
                "check": "size",
                "ok": mb is not None and mb <= max_mb,
                "measured": _number(measured.get("mb")),
                "limit": f"<= {max_mb:g} MB",
            }
        )
    target = caps.get("lufs")
    if target is not None:
        tolerance = caps.get("lufs_tolerance")
        spread = DEFAULT_TOLERANCE if tolerance is None else tolerance
        lufs = _number(measured.get("lufs"))
        rows.append(
            {
                "check": "loudness",
                "ok": lufs is not None and abs(lufs - target) <= spread,
                "measured": lufs,
                "limit": f"{target:g} LUFS +/- {spread:g}",
            }
        )
    ceiling = caps.get("true_peak")
    if ceiling is not None:
        peak = _number(measured.get("true_peak"))
        rows.append(
            {
                "check": "true_peak",
                "ok": peak is not None and peak <= ceiling,
                "measured": peak,
                "limit": f"<= {ceiling:g} dBTP",
            }
        )
    expected = caps.get("expected_s")
    if expected is not None:
        seconds = _number(measured.get("duration_s"))
        rows.append(
            {
                "check": "duration",
                "ok": seconds is not None and abs(seconds - expected) <= DURATION_TOLERANCE_S,
                "measured": seconds,
                "limit": f"{expected:g} s +/- {DURATION_TOLERANCE_S:g}",
            }
        )
    return rows


def caps_from(inv: Invocation) -> dict[str, float | None]:
    """The validated cap numbers from `--max-mb/--lufs/--lufs-tolerance/--true-peak`."""
    args = inv.args
    caps: dict[str, float | None] = {
        "max_mb": _number(args.get("max_mb")),
        "lufs": _number(args.get("lufs")),
        "lufs_tolerance": _number(args.get("lufs_tolerance", DEFAULT_TOLERANCE)),
        "true_peak": _number(args.get("true_peak")),
        "expected_s": None,
    }
    if caps["max_mb"] is not None and caps["max_mb"] <= 0:
        raise _bad("--max-mb must be above 0", "Valid: --max-mb 50 (decimal MB, 1,000,000 bytes)")
    tolerance = caps["lufs_tolerance"]
    if tolerance is None or tolerance < 0:
        raise _bad("--lufs-tolerance must be 0 or above", "Valid: --lufs-tolerance 1")
    if caps["true_peak"] is not None and caps["true_peak"] > 0:
        raise _bad("--true-peak must be 0 or below", "Valid: --true-peak -1")
    if args.get("no_loudness") and (caps["lufs"] is not None or caps["true_peak"] is not None):
        raise _bad("--lufs and --true-peak need the loudness pass", "Drop --no-loudness")
    if inv.positionals.get("file") and args.get("job"):
        raise _bad("give a file or --job, not both", "davinci-axi render verify --job <id>")
    return caps


def _timeline_rate(session: Session, name: str) -> float | None:
    project = session.require_project()
    sources: list[dict[str, Any]] = []
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is not None and timeline.GetName() == name:
            sources.append(timeline.GetSettings() or {})
            break
    sources.append(project.GetSettings() or {})
    for settings in sources:
        try:
            rate = float(settings.get("timelineFrameRate") or 0)
        except (TypeError, ValueError):
            continue
        if rate > 0:
            return rate
    return None


def _with_extension(path: Path) -> Path | None:
    """The one file beside `path` whose stem is `path`'s name (a job name without extension)."""
    if not path.parent.is_dir():
        return None
    found = [p for p in path.parent.iterdir() if p.is_file() and p.stem == path.name]
    return found[0] if len(found) == 1 else None


def job_target(session: Session, job_id: str) -> tuple[Path, float | None, str | None]:
    """`(output file, expected seconds, note)` for render job `job_id`."""
    project = render_project(session)
    jobs: list[dict[str, Any]] = project.GetRenderJobList() or []
    job = next((j for j in jobs if str(j.get("JobId", "")) == job_id), None)
    if job is None:
        known = ", ".join(str(j.get("JobId", "")) for j in jobs) or "none queued"
        raise not_found("render job", job_id, f"Queued jobs: {known}")
    path = Path(str(job.get("TargetDir") or "")) / str(job.get("OutputFilename") or "")
    if not path.is_file():
        path = _with_extension(path) or path
    low, high = job.get("MarkIn"), job.get("MarkOut")
    expected: float | None = None
    if isinstance(low, int) and isinstance(high, int) and high >= low:
        rate = _timeline_rate(session, str(job.get("TimelineName") or ""))
        expected = (high - low + 1) / rate if rate else None
    status = str((project.GetRenderJobStatus(job_id) or {}).get("JobStatus") or "")
    note = (
        f"job {job_id} is {status}, not Complete: the file may be partial"
        if (status and status != "Complete")
        else None
    )
    return path, expected, note


_job_target = job_target  # the pre-round-3 private name


def measure_file(path: Path, loud: bool) -> dict[str, object]:
    probed = ffmpeg_common.probe(path)
    size = probed.size if probed.size is not None else path.stat().st_size
    measured: dict[str, object] = {
        "file": collapse_home(path),
        "bytes": size,
        "mb": round(size / BYTES_PER_MB, 2),
        "duration_s": round(probed.duration_s, 3) if probed.duration_s is not None else None,
        "video": probed.video,
        "audio": probed.audio,
    }
    if loud and probed.audio is not None:
        heard = ffmpeg_common.loudness(path, silence_db=None, silence_s=0)
        measured["lufs"] = heard.lufs
        measured["true_peak"] = heard.true_peak
    return measured


def _video_dims(measured: dict[str, object]) -> tuple[int, int, int | None]:
    video = measured.get("video")
    if not isinstance(video, dict):
        return 0, 0, None
    width, height, kbps = video.get("width"), video.get("height"), video.get("kbps")
    return (
        width if isinstance(width, int) else 0,
        height if isinstance(height, int) else 0,
        kbps if isinstance(kbps, int) else None,
    )


def _help(
    measured: dict[str, object],
    checks: list[dict[str, object]],
    caps: dict[str, float | None],
    job: str | None,
) -> list[str]:
    lines: list[str] = []
    failed = {str(row["check"]) for row in checks if not row["ok"]}
    max_mb = caps.get("max_mb")
    mb = _number(measured.get("mb"))
    if "size" in failed and max_mb and mb is not None:
        lines.append(
            hint(
                f"render deliver --max-mb {max_mb:g} ...",
                f"render again; the encoder overshot the cap by {mb / max_mb:.2f}x",
            )
        )
        width, height, kbps = _video_dims(measured)
        if width and height and kbps is not None and kbps < encoder_floor_kbps(width, height):
            lines.append(
                f"The video rate ({kbps} kbps) is under Resolve's H.264 floor of about "
                f"{encoder_floor_kbps(width, height)} kbps for {width}x{height}: lower the "
                "resolution or raise --max-mb"
            )
    lufs, target = _number(measured.get("lufs")), caps.get("lufs")
    if "loudness" in failed and lufs is not None and target is not None:
        lines.append(
            hint(
                f"render master {measured['file']} --lufs {target:g}",
                "fix the file in seconds (video copied)",
            )
        )
        lines.append(
            hint(
                f"audio gain <items> --db {target - lufs:+.1f}",
                f"move the mix from {lufs:g} to {target:g} LUFS, then render again",
            )
        )
    lines.append(hint(f"render sheet {measured['file']} --every 10", "look at a frame every 10 s"))
    if job:
        lines.append(hint(f"render status {job}", "see that job's state and settings"))
    if not checks:
        lines.append("Pass --max-mb, --lufs or --true-peak to check the file against a cap")
    return lines


def _report(
    path: Path, caps: dict[str, float | None], loud: bool, job: str | None, note: str | None
) -> tuple[dict[str, object], list[str]]:
    measured = measure_file(path, loud)
    checks = checks_for(measured, caps)
    ok = all(bool(row["ok"]) for row in checks)
    data: dict[str, object] = {**measured, "ok": ok, "checks": checks}
    if note:
        data["note"] = note
    return data, _help(measured, checks, caps, job)


def measure_job(
    session: Session, job_id: str, caps: dict[str, float | None], *, loud: bool
) -> tuple[dict[str, object], list[str]]:
    """Measure render job `job_id`'s output file: `(data, help lines)`, as `render verify --job`.

    The duration cap is the job's mark range. A job or file that cannot be found is NOT_FOUND.
    """
    path, expected, note = job_target(session, job_id)
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path; for a job use --job <id>")
    return _report(path, {**caps, "expected_s": expected}, loud, job_id, note)


def run_verify(inv: Invocation) -> Result:
    caps = caps_from(inv)
    loud = not inv.args.get("no_loudness")
    ffmpeg_common.require_tools("ffprobe", *(("ffmpeg",) if loud else ()))
    given = inv.positionals.get("file")
    job = str(inv.args["job"]) if inv.args.get("job") else None
    if not given and not job:
        raise _bad("name a file or --job", "davinci-axi render verify ~/out/edit.mp4 --max-mb 50")
    if job:
        data, help_lines = measure_job(inv.connect(), job, caps, loud=loud)
    else:
        path = Path(str(given)).expanduser().resolve()
        if not path.is_file():
            raise not_found("file", collapse_home(path), "Check the path; for a job use --job <id>")
        data, help_lines = _report(path, caps, loud, None, None)
    return Result(data, help_lines, exit_code=0 if data["ok"] else 1)


VERIFY_SPEC = CommandSpec(
    name="verify",
    summary="Measure a rendered file (size, bit rates, LUFS, duration) against caps",
    run=run_verify,
    positionals=(Positional("file", required=False, help="a rendered media file"),),
    flags=(
        Flag("job", "str", None, help="verify the output of this render job instead of a file"),
        Flag("max-mb", "float", None, help="size cap in decimal MB (1,000,000 bytes)"),
        Flag("lufs", "float", None, help="target integrated loudness in LUFS (EBU R128)"),
        Flag(
            "lufs-tolerance", "float", DEFAULT_TOLERANCE, help="allowed LUFS distance (0 or more)"
        ),
        Flag("true-peak", "float", None, help="true-peak ceiling in dBTP (0 or below)"),
        Flag("no-loudness", "bool", False, help="ffprobe only: skip the loudness pass"),
    ),
    examples=(
        "davinci-axi render verify ~/out/edit.mp4 --max-mb 50 --lufs -14",
        "davinci-axi render verify --job job-1 --max-mb 6 --true-peak -1",
        "davinci-axi render verify ~/out/edit.mp4 --no-loudness --max-mb 50",
    ),
    reads_only=True,
    methods=methods_of(
        Project=(
            "GetRenderJobList GetRenderJobStatus GetTimelineCount GetTimelineByIndex GetSettings"
        ),
        Timeline="GetName GetSettings",
    ),
    notes=(
        "Exit 1 when any check fails (the data block is still printed), so the command gates "
        "scripts; exit 0 when every check passes or no cap was given",
        "ffprobe and ffmpeg are optional programs on PATH (FFPROBE/FFMPEG name a binary); "
        "without them this verb fails with NOT_AVAILABLE before it touches Resolve",
        "Sizes are decimal MB (1,000,000 bytes), the same unit as render deliver --max-mb",
        "Loudness is the file's integrated LUFS (EBU R128), the number YouTube normalises on; "
        "the true peak is in dBTP",
        "With --job the duration is checked against the job's mark range (MarkOut - MarkIn + 1 "
        "frames at the timeline rate, within 0.5 s); the job must be Complete to trust the file",
        "When the video rate is under Resolve's H.264 floor for the frame size (see "
        "render deliver), a failed size check says so",
    ),
)
