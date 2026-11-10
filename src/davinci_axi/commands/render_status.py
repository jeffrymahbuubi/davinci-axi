"""`render status`: the queue list, and one detail row per job when ids are given (F28a/b)."""

from __future__ import annotations

from typing import Any, cast

from davinci_axi.commands.render_common import render_project
from davinci_axi.commands.render_frame import frame_to_timecode
from davinci_axi.commands.render_jobs import JOB_ID, selected_jobs
from davinci_axi.errors import AxiError
from davinci_axi.output import apply_limit, empty_state, hint, page_meta, select_fields
from davinci_axi.resolve_types import ProjectApi
from davinci_axi.spec import DEFAULT_LIMIT, LIST_FLAGS, CommandSpec, Invocation, Result

SCOPE = "current project"
STATUS_FIELDS = ("id", "name", "timeline", "status", "percent")
EXTRA_FIELDS: dict[str, str] = {
    "target_dir": "TargetDir",
    "file": "OutputFilename",
    "format": "VideoFormat",
    "codec": "VideoCodec",
    "width": "FormatWidth",
    "height": "FormatHeight",
    "frame_rate": "FrameRate",
    "mark_in": "MarkIn",
    "mark_out": "MarkOut",
    "audio_codec": "AudioCodec",
    "preset": "PresetName",
    "render_mode": "RenderMode",
}
# The remaining RenderJobInfo keys that only the detail row shows.
DETAIL_FIELDS: dict[str, str] = {
    "pixel_aspect": "PixelAspectRatio",
    "export_video": "IsExportVideo",
    "export_audio": "IsExportAudio",
    "audio_bit_depth": "AudioBitDepth",
    "audio_sample_rate": "AudioSampleRate",
    "encoding_profile": "EncodingProfile",
    "multi_pass": "MultiPassEncode",
    "network_optimization": "NetworkOptimization",
}
# RenderJobStatus keys the detail row adds.
STATUS_DETAIL: dict[str, str] = {
    "time_taken_ms": "TimeTakenToRenderInMs",
    "time_remaining_ms": "EstimatedTimeRemainingInMs",
    "error": "Error",
}
DETAIL_FIELD_NAMES: tuple[str, ...] = (
    *STATUS_FIELDS,
    *EXTRA_FIELDS,
    "frames",
    "duration",
    *DETAIL_FIELDS,
    *STATUS_DETAIL,
    "alpha",
)
DETAIL_NOTE = (
    "Resolve's job info carries no bit rate or quality; set VideoQuality (kbps) on render add and "
    "check the file; mark_in/mark_out are absolute timeline frames (live: 108100-108200 for a "
    "101-frame job), the same values --set MarkIn/MarkOut take; timeline mark show reads the "
    "timeline's own marks relative to its start"
)


def _job_row(project: ProjectApi, job: dict[str, Any]) -> dict[str, object]:
    job_id = str(job.get("JobId", ""))
    status = project.GetRenderJobStatus(job_id) or {}
    row: dict[str, object] = {
        "id": job_id,
        "name": job.get("RenderJobName"),
        "timeline": job.get("TimelineName"),
        "status": status.get("JobStatus") or "unknown",
        "percent": status.get("CompletionPercentage"),
    }
    for field, key in EXTRA_FIELDS.items():
        row[field] = job.get(key)
    return row


def _range(info: dict[str, Any]) -> dict[str, object]:
    """`frames` and `duration` from MarkIn/MarkOut (inclusive); empty when unreadable."""
    try:
        frames = int(info["MarkOut"]) - int(info["MarkIn"]) + 1
        return {"frames": frames, "duration": frame_to_timecode(frames, float(info["FrameRate"]))}
    except (KeyError, TypeError, ValueError, AxiError):
        return {}


def _job_detail(project: ProjectApi, job: dict[str, Any]) -> dict[str, object]:
    """Every RenderJobInfo/RenderJobStatus field Resolve echoed for `job`; absent ones omitted."""
    row = _job_row(project, job)
    status = project.GetRenderJobStatus(str(job.get("JobId", ""))) or {}
    extra: dict[str, object] = {f: job.get(k) for f, k in DETAIL_FIELDS.items()}
    extra.update({f: status.get(k) for f, k in STATUS_DETAIL.items()})
    if job.get("ExportAlpha"):
        extra["alpha"] = {"export": job.get("ExportAlpha"), "mode": job.get("AlphaMode")}
    detail = {**row, **_range(job), **extra}
    return {k: v for k, v in detail.items() if v is not None}


def _list(inv: Invocation, project: ProjectApi, jobs: list[dict[str, Any]]) -> Result:
    rows = [_job_row(project, job) for job in jobs]
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        counts[status] = counts.get(status, 0) + 1
    data: dict[str, object] = {
        "scope": SCOPE,
        "rendering": bool(project.IsRenderingInProgress()),
        "total": len(rows),
    }
    next_step = hint("render presets", "list the render presets")
    if not rows:
        data["jobs"] = empty_state("jobs", f"in project {project.GetName()}", {})
        return Result(data, [next_step])
    data["by_status"] = [{"status": s, "count": n} for s, n in counts.items()]
    requested = cast("list[str] | None", inv.args.get("fields"))
    page, total = apply_limit(rows, cast(int, inv.args.get("limit")))
    data.update(page_meta(len(page), total))
    data["jobs"] = select_fields(page, STATUS_FIELDS, requested)
    help_lines = [next_step]
    if total > len(page):
        help_lines.insert(0, f"Showing {len(page)} of {total} jobs; raise --limit to see more")
    return Result(data, help_lines)


def run_status(inv: Invocation) -> Result:
    project = render_project(inv.connect())
    jobs: list[dict[str, Any]] = project.GetRenderJobList() or []
    raw = inv.positionals.get("jobs")
    if raw is None:
        return _list(inv, project, jobs)
    refs = list(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    ids, _ = selected_jobs(project, refs or None)
    by_id = {str(job.get("JobId", "")): job for job in jobs}
    requested = cast("list[str] | None", inv.args.get("fields"))
    rows = [_job_detail(project, by_id[job_id]) for job_id in ids]
    if requested is not None:
        rows = select_fields(rows, DETAIL_FIELD_NAMES, requested)
    data: dict[str, object] = {
        "scope": SCOPE,
        "rendering": bool(project.IsRenderingInProgress()),
        "jobs": rows,
        "note": DETAIL_NOTE,
    }
    help_lines = [hint("render status", "list the whole queue")]
    if inv.args.get("limit") != DEFAULT_LIMIT:
        help_lines.insert(0, "--limit applies to the queue list, not to a job's detail")
    return Result(data, help_lines)


STATUS_SPEC = CommandSpec(
    name="status",
    summary="List render queue jobs with status and completion, or show one job's settings",
    run=run_status,
    positionals=(JOB_ID,),
    flags=LIST_FLAGS,
    examples=(
        "davinci-axi render status",
        "davinci-axi render status job-2",
        "davinci-axi render status job-2 --fields id,status,percent",
        "davinci-axi render status --fields id,status,target_dir,file",
        "davinci-axi render status --limit 5",
    ),
    notes=(
        "With job ids, each job shows its range (mark_in, mark_out, frames, duration), rate, "
        "audio settings and timings; --fields picks columns in both views, --limit only shapes "
        "the queue list",
        DETAIL_NOTE,
    ),
    methods=(
        "Project.GetName",
        "Project.GetRenderJobList",
        "Project.GetRenderJobStatus",
        "Project.IsRenderingInProgress",
    ),
)
