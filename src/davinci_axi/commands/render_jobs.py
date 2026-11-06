"""`render add|start|stop|delete`: the render queue (U16, R33).

Every verb is a mutation built on `mutate.run_mutation`: a dry run until `--force`, then save,
snapshot, write and read-back. Render settings are applied inside `render add` only, because
`SetRenderSettings` has no getter; the job that `AddRenderJob` creates is what gets verified
(decision 17). Long jobs poll with `poll.py`: progress on stderr, the result block on stdout.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from davinci_axi.commands.render_common import (
    DIR_BOOLS,
    DIR_FLAGS,
    JOB_INFO_KEYS,
    RenderProject,
    force_words,
    methods_of,
    parse_settings,
    refuse_offline,
    render_project,
    target_dir,
)
from davinci_axi.commands.timeline_tracks import TIMELINE_FLAG, text_arg
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
    with_current_timeline,
)
from davinci_axi.output import collapse_home, empty_state, hint
from davinci_axi.poll import POLL_FLAGS, RENDER_JOB, poll_args, poll_status
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

STOP_WAIT_TRIES = 10
STOP_WAIT_SECONDS = 0.2


def _job_ids(project: RenderProject) -> list[str]:
    return [str(job.get("JobId", "")) for job in project.GetRenderJobList() or []]


def _job_refs(inv: Invocation) -> list[str] | None:
    """The comma-separated job ids, or None for `--all`; neither or both is VALIDATION_ERROR."""
    raw = inv.positionals.get("jobs")
    everything = bool(inv.args.get("all"))
    if raw is not None and everything:
        raise AxiError(
            "VALIDATION_ERROR",
            "pass job ids or --all, not both",
            help=["Valid example: davinci-axi render start job-2,job-3 --force"],
        )
    if raw is None and not everything:
        raise AxiError(
            "VALIDATION_ERROR",
            "name the jobs to act on, or pass --all",
            help=[
                hint("render status", "list the job ids"),
                "Valid example: davinci-axi render start job-2,job-3 --force",
            ],
        )
    if raw is None:
        return None
    refs = list(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if not refs:
        raise AxiError("VALIDATION_ERROR", "the job list is empty", help=["Pass job ids"])
    return refs


def _selected(project: RenderProject, refs: list[str] | None) -> tuple[list[str], list[str]]:
    """(selected ids, all ids in the queue); an unknown id is NOT_FOUND listing the queue."""
    queue = _job_ids(project)
    if refs is None:
        return list(queue), queue
    missing = [ref for ref in refs if ref not in queue]
    if missing:
        listing = ", ".join(queue) if queue else "(none)"
        raise not_found("render job", ", ".join(missing), f"Jobs in the queue: {listing}")
    return refs, queue


def _reader(project: RenderProject, job_id: str) -> Callable[[], dict[str, Any]]:
    def read() -> dict[str, Any]:
        return dict(project.GetRenderJobStatus(job_id) or {})

    return read


def _status_of(project: RenderProject, job_id: str) -> dict[str, object]:
    info = project.GetRenderJobStatus(job_id) or {}
    return {
        "id": job_id,
        "status": info.get("JobStatus") or "unknown",
        "percent": info.get("CompletionPercentage"),
    }


def _lenient(key: str, live: object, wanted: object) -> object:
    """Resolve may echo a directory with a trailing slash or a file name with its extension."""
    if key == "TargetDir" and str(live).rstrip("/") == str(wanted).rstrip("/"):
        return wanted
    if key == "CustomName" and (str(live) == str(wanted) or Path(str(live)).stem == str(wanted)):
        return wanted
    return live


def run_add(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    settings = parse_settings(text_arg(inv, "set"))
    name = text_arg(inv, "name")
    path = target_dir(inv)
    session = inv.connect()
    project = render_project(session)
    preset = text_arg(inv, "preset")
    if preset is not None:
        presets = project.GetRenderPresetList() or []
        if preset not in presets:
            listing = ", ".join(presets) if presets else "(none)"
            raise not_found("render preset", preset, f"Render presets in this project: {listing}")
    timeline = session.require_timeline(text_arg(inv, "timeline"))
    refuse_offline(timeline, inv)
    wanted: dict[str, object] = {"TargetDir": str(path)}
    if name is not None:
        wanted["CustomName"] = name
    wanted.update(settings)
    cmd = ["render", "add", "--target-dir", str(inv.args["target_dir"])]
    for flag, key in (("--preset", "preset"), ("--name", "name"), ("--set", "set")):
        if text_arg(inv, key) is not None:
            cmd.extend([flag, str(text_arg(inv, key))])
    changes = [Change("job", None, "(new job)"), Change("timeline", None, timeline.GetName())]
    changes += [Change(key, None, value) for key, value in wanted.items() if key in JOB_INFO_KEYS]
    unmapped = [f"{key}={value}" for key, value in wanted.items() if key not in JOB_INFO_KEYS]
    plan = MutationPlan(
        command="render add",
        target=f"project {project.GetName()}",
        changes=changes,
        force_cmd=force_words(inv, cmd, DIR_BOOLS),
        extra={"target_dir": collapse_home(path)},
        next=[hint("render status", "list the queue and the new job")],
    )
    if preset is not None:
        plan.extra["preset"] = preset
    if unmapped:
        plan.extra["applied_settings"] = unmapped
    created: dict[str, str] = {}

    def apply() -> None:
        if not path.exists() and inv.args.get("mkdir"):
            path.mkdir(parents=True, exist_ok=True)
        before = set(_job_ids(project))
        with with_current_timeline(session, timeline) as switched:
            if switched:
                plan.extra["timeline_restored"] = True
            if preset is not None and not project.LoadRenderPreset(preset):
                raise api_failed("LoadRenderPreset", f"could not load the preset {preset}")
            if not project.SetRenderSettings(dict(wanted)):
                raise api_failed("SetRenderSettings", "Resolve rejected the render settings")
            added = project.AddRenderJob()
        if not added:
            fresh = [job for job in _job_ids(project) if job not in before]
            added = fresh[-1] if fresh else ""
        if not added:
            raise api_failed("AddRenderJob", "no job was added; check that Resolve shows no dialog")
        created["id"] = str(added)
        plan.changes[0] = Change("job", None, created["id"])
        plan.extra["job"] = created["id"]
        plan.next[:] = [
            hint(f"render start {created['id']}", "render this job (add --force to confirm)"),
            hint("render status", "list the queue"),
        ]

    def verify() -> dict[str, object]:
        live: dict[str, object] = {"job": None}
        for job in project.GetRenderJobList() or []:
            if str(job.get("JobId", "")) != created["id"]:
                continue
            live["job"] = created["id"]
            live["timeline"] = job.get("TimelineName")
            for key, info_key in JOB_INFO_KEYS.items():
                if key in wanted:
                    live[key] = _lenient(key, job.get(info_key), wanted[key])
        return live

    return run_mutation(inv, session, plan, apply, verify)


def run_start(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    wait, timeout, interval = poll_args(inv)
    refs = _job_refs(inv)
    session = inv.connect()
    project = render_project(session)
    ids, queue = _selected(project, refs)
    statuses = {job_id: _status_of(project, job_id) for job_id in ids}
    done = [j for j in ids if statuses[j]["status"] in RENDER_JOB.done]
    skipped: list[str] = []
    if done and not inv.args.get("rerender"):
        if refs is not None:
            raise AxiError(
                "JOB_COMPLETE",
                f"{', '.join(done)} already rendered; Resolve would ask before rendering again",
                help=[
                    "Pass --rerender to render it again",
                    hint("render status", "check the jobs"),
                ],
            )
        skipped = done
        ids = [job_id for job_id in ids if job_id not in done]
    cmd = ["render", "start", *([inv.positionals["jobs"]] if refs is not None else ["--all"])]
    plan = MutationPlan(
        command="render start",
        target=f"project {project.GetName()}",
        changes=[],
        force_cmd=force_words(inv, cmd, ("interactive", "rerender")),
        next=[hint("render status --fields id,status,target_dir,file", "list the output paths")],
    )
    if skipped:
        plan.extra["skipped"] = [f"{j} (already Complete)" for j in skipped]
    if not ids:
        why = "the queue is empty" if not queue else "every selected job is already Complete"
        plan.extra["note"] = f"nothing to render: {why}"
        return run_mutation(inv, session, plan, lambda: None, dict)
    if project.IsRenderingInProgress():
        raise AxiError(
            "RENDER_BUSY",
            "a render is already running in Resolve",
            help=[
                "Wait for it to finish, or stop it first",
                hint("render stop", "stop the running render"),
            ],
        )
    single = len(ids) == 1
    after = "Complete" if wait else "started"
    plan.changes = [
        Change("status" if single else f"{j} status", statuses[j]["status"], after) for j in ids
    ]
    plan.extra["jobs"] = [statuses[j] for j in ids]

    def apply() -> None:
        if not project.StartRendering(list(ids), bool(inv.args.get("interactive"))):
            raise api_failed("StartRendering", "Resolve did not start the render")
        if not wait:
            return
        for job_id in ids:
            kind = RENDER_JOB if single else replace(RENDER_JOB, label=f"render {job_id}")
            read = _reader(project, job_id)
            poll_status(read, kind, timeout=timeout, interval=interval, err=sys.stderr)

    def verify() -> dict[str, object]:
        live: dict[str, object] = {}
        rows: list[dict[str, object]] = []
        for job_id in ids:
            row = _status_of(project, job_id)
            rows.append(row)
            status = row["status"]
            if not wait and status not in RENDER_JOB.idle and status not in RENDER_JOB.failed:
                status = "started"
            live["status" if single else f"{job_id} status"] = status
        plan.extra["jobs"] = rows
        return live

    return run_mutation(inv, session, plan, apply, verify)


def run_stop(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    session = inv.connect()
    project = render_project(session)
    rendering = bool(project.IsRenderingInProgress())
    plan = MutationPlan(
        command="render stop",
        target=f"project {project.GetName()}",
        changes=[Change("rendering", True, False)] if rendering else [],
        force_cmd=force_cmd(inv, "render", "stop"),
        snapshot="none",
        next=[hint("render status", "list the jobs and their final status")],
    )
    if not rendering:
        plan.extra["note"] = "no render is running"

    def apply() -> None:
        project.StopRendering()

    def verify() -> dict[str, object]:
        for _ in range(STOP_WAIT_TRIES):
            if not project.IsRenderingInProgress():
                break
            time.sleep(STOP_WAIT_SECONDS)
        return {"rendering": bool(project.IsRenderingInProgress())}

    return run_mutation(inv, session, plan, apply, verify)


def run_delete(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    refs = _job_refs(inv)
    session = inv.connect()
    project = render_project(session)
    ids, queue = _selected(project, refs)
    remaining = [job_id for job_id in queue if job_id not in ids]
    cmd = ["render", "delete", *([inv.positionals["jobs"]] if refs is not None else ["--all"])]
    plan = MutationPlan(
        command="render delete",
        target=f"project {project.GetName()}",
        changes=[Change("queue", queue, remaining)] if ids else [],
        force_cmd=force_words(inv, cmd),
        irreversible=True,
        warning="removes the jobs from the render queue; files already rendered stay on disk",
        next=[hint("render status", "list the remaining jobs")],
    )
    if not ids:
        plan.extra["jobs"] = empty_state("jobs", f"in project {project.GetName()}", {})

    def apply() -> None:
        if refs is None:
            if not project.DeleteAllRenderJobs():
                raise api_failed("DeleteAllRenderJobs", "Resolve could not clear the queue")
            return
        for job_id in ids:
            if not project.DeleteRenderJob(job_id):
                raise api_failed("DeleteRenderJob", f"could not delete job {job_id}")

    return run_mutation(inv, session, plan, apply, lambda: {"queue": _job_ids(project)})


JOB_ID = Positional("jobs", required=False, help="comma-separated job ids (or pass --all)")
ALL_FLAG = Flag("all", "bool", False, help="act on every job in the queue")

ADD_SPEC = CommandSpec(
    name="add",
    summary="Queue a render job for a timeline (applies --set settings first)",
    run=run_add,
    flags=(
        Flag("target-dir", "str", None, required=True, help="output directory (never a dialog)"),
        Flag("preset", "str", None, help="render preset to load first"),
        Flag("name", "str", None, help="output file name (CustomName)"),
        Flag("set", "str", None, help="comma-separated Key=value render settings"),
        TIMELINE_FLAG,
        *DIR_FLAGS,
        *MUTATION_FLAGS,
    ),
    examples=(
        "davinci-axi render add --target-dir ~/renders/out --mkdir",
        "davinci-axi render add --target-dir ~/renders --preset 'H.264 Master' --force",
        "davinci-axi render add --target-dir ~/r --set FormatWidth=1280,FormatHeight=720 --force",
    ),
    reads_only=False,
    methods=methods_of(
        Project="GetRenderPresetList LoadRenderPreset SetRenderSettings AddRenderJob "
        "GetRenderJobList SetCurrentTimeline",
        Timeline="GetName GetUniqueId GetTrackCount GetItemListInTrack",
        TimelineItem="GetMediaPoolItem",
        MediaPoolItem="GetClipProperty GetName",
    ),
)
START_SPEC = CommandSpec(
    name="start",
    summary="Start rendering queued jobs and wait for them (progress on stderr)",
    run=run_start,
    positionals=(JOB_ID,),
    flags=(
        ALL_FLAG,
        Flag("interactive", "bool", False, help="start in interactive mode"),
        Flag("rerender", "bool", False, help="render jobs that are already Complete again"),
        *POLL_FLAGS,
        *MUTATION_FLAGS,
    ),
    examples=(
        "davinci-axi render start job-2",
        "davinci-axi render start job-2,job-3 --force",
        "davinci-axi render start --all --force --no-wait",
    ),
    reads_only=False,
    methods=methods_of(
        Project="GetRenderJobList GetRenderJobStatus IsRenderingInProgress StartRendering"
    ),
)
STOP_SPEC = CommandSpec(
    name="stop",
    summary="Stop the render that is running (no snapshot; UI state only)",
    run=run_stop,
    flags=MUTATION_FLAGS,
    examples=("davinci-axi render stop", "davinci-axi render stop --force"),
    reads_only=False,
    methods=methods_of(Project="IsRenderingInProgress StopRendering"),
)
DELETE_SPEC = CommandSpec(
    name="delete",
    summary="Delete render jobs from the queue (irreversible)",
    run=run_delete,
    positionals=(JOB_ID,),
    flags=(ALL_FLAG, *MUTATION_FLAGS),
    examples=(
        "davinci-axi render delete job-1",
        "davinci-axi render delete job-1,job-2 --force",
        "davinci-axi render delete --all --force",
    ),
    reads_only=False,
    methods=methods_of(Project="GetRenderJobList DeleteRenderJob DeleteAllRenderJobs"),
)

JOB_SPECS: dict[str, CommandSpec] = {
    "add": ADD_SPEC,
    "start": START_SPEC,
    "stop": STOP_SPEC,
    "delete": DELETE_SPEC,
}
