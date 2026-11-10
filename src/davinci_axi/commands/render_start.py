"""`render start`: start queued render jobs, wait for them, and optionally measure the files.

Split out of `render_jobs` (E, round 2). `--verify` (implied by any cap flag) runs
`render verify --job` on each job once it is Complete and reports the rows under `verify[]`; the
render itself stays done, and a failed cap only turns the exit code into 1.
"""

from __future__ import annotations

import sys
from dataclasses import replace

from davinci_axi.commands import ffmpeg_common
from davinci_axi.commands.render_common import (
    force_words,
    methods_of,
    render_project,
)
from davinci_axi.commands.render_jobs import (
    ALL_FLAG,
    JOB_ID,
    job_refs,
    overwrite_warning,
    reader,
    selected_jobs,
    status_of,
)
from davinci_axi.commands.render_verify import caps_from, measure_job
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    PAGE_LEFT_NOTE,
    Change,
    MutationPlan,
    check_snapshot_flags,
    keep_page,
    run_mutation,
)
from davinci_axi.output import hint
from davinci_axi.poll import POLL_FLAGS, RENDER_JOB, poll_args, poll_status
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Result

VERIFY_FLAGS = (
    Flag(
        "verify",
        "bool",
        False,
        help="measure each rendered file once its job is Complete (implied by any cap flag)",
    ),
    Flag("max-mb", "float", None, help="verify: size cap in decimal MB (1,000,000 bytes)"),
    Flag("lufs", "float", None, help="verify: target integrated loudness in LUFS (EBU R128)"),
    Flag("lufs-tolerance", "float", 1.0, help="verify: allowed LUFS distance (0 or more)"),
    Flag("true-peak", "float", None, help="verify: true-peak ceiling in dBTP (0 or below)"),
)
CAP_ARGS = ("max_mb", "lufs", "true_peak")


def _verify_words(inv: Invocation) -> list[str]:
    """The verify flags to repeat in the force command."""
    words = ["--verify"] if inv.args.get("verify") else []
    for flag in ("max-mb", "lufs", "lufs-tolerance", "true-peak"):
        value = inv.args.get(flag.replace("-", "_"))
        if value is None or (flag == "lufs-tolerance" and value == 1.0):
            continue
        words.extend([f"--{flag}", f"{value:g}"])
    return words


def _verify_jobs(
    session: Session, ids: list[str], caps: dict[str, float | None], plan: MutationPlan
) -> None:
    """Measure each job's file into `plan.extra["verify"]`; a missing file becomes an error row."""
    rows: list[dict[str, object]] = []
    lines: list[str] = []
    for job_id in ids:
        try:
            data, help_lines = measure_job(session, job_id, caps, loud=True)
        except AxiError as err:
            rows.append({"job": job_id, "error": err.message, "ok": False})
            help_lines = err.help
        else:
            rows.append({"job": job_id, **data})
        lines.extend(line for line in help_lines if line not in lines)
    plan.extra["verify"] = rows
    plan.next.extend(line for line in lines if line not in plan.next)


def run_start(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    wait, timeout, interval = poll_args(inv)
    refs = job_refs(inv)
    verifying = bool(inv.args.get("verify")) or any(
        inv.args.get(arg) is not None for arg in CAP_ARGS
    )
    caps = caps_from(inv)
    if verifying and not wait:
        raise AxiError(
            "VALIDATION_ERROR",
            "--verify needs --wait (the file is measured when the job completes)",
            help=["Drop --no-wait, or run `render verify --job <id>` after the render"],
        )
    if verifying:
        ffmpeg_common.require_tools("ffprobe", "ffmpeg")
    session = inv.connect()
    project = render_project(session)
    ids, queue = selected_jobs(project, refs)
    statuses = {job_id: status_of(project, job_id) for job_id in ids}
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
    cmd.extend(_verify_words(inv))
    plan = MutationPlan(
        command="render start",
        warning=overwrite_warning(project, done) if done and inv.args.get("rerender") else "",
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
        with keep_page(session) as page_state:
            if not project.StartRendering(list(ids), bool(inv.args.get("interactive"))):
                raise api_failed("StartRendering", "Resolve did not start the render")
            if wait:
                for job_id in ids:
                    kind = RENDER_JOB if single else replace(RENDER_JOB, label=f"render {job_id}")
                    read = reader(project, job_id)
                    poll_status(read, kind, timeout=timeout, interval=interval, err=sys.stderr)
        if verifying:
            _verify_jobs(session, ids, caps, plan)
        plan.extra.update(page_state)
        if not wait and page_state.get("page_before"):
            plan.next.append(PAGE_LEFT_NOTE.replace("<page>", str(page_state["page_before"])))

    def verify() -> dict[str, object]:
        live: dict[str, object] = {}
        rows: list[dict[str, object]] = []
        for job_id in ids:
            row = status_of(project, job_id)
            rows.append(row)
            status = row["status"]
            if not wait and status not in RENDER_JOB.idle and status not in RENDER_JOB.failed:
                status = "started"
            live["status" if single else f"{job_id} status"] = status
        plan.extra["jobs"] = rows
        return live

    result = run_mutation(inv, session, plan, apply, verify)
    rows = plan.extra.get("verify")
    if isinstance(rows, list) and any(isinstance(r, dict) and not r.get("ok") for r in rows):
        result.help.append("the render completed; the verify caps failed")
        return replace(result, exit_code=1)
    return result


START_SPEC = CommandSpec(
    name="start",
    summary="Start rendering queued jobs and wait for them (progress on stderr)",
    run=run_start,
    positionals=(JOB_ID,),
    flags=(
        ALL_FLAG,
        Flag("interactive", "bool", False, help="start in interactive mode"),
        Flag(
            "rerender",
            "bool",
            False,
            help="render Complete jobs again; Resolve overwrites the existing output file in "
            "place and the status stays Complete",
        ),
        *VERIFY_FLAGS,
        *POLL_FLAGS,
        *MUTATION_FLAGS,
    ),
    notes=(
        "Complete jobs are refused without --rerender; with it the file is overwritten in place "
        "without a dialog",
        "StartRendering opens the Deliver page; the page that was open is put back once the render "
        "is done (--wait, page_restored). With --no-wait Resolve reports no page while rendering "
        "and refuses the switch, so page_restored is false and page_before names the page to put "
        "back with `page set <page> --force`",
        "--verify runs `render verify --job` on each job once it is Complete; caps as there; "
        "exit 1 when a cap fails (the render stays done). It needs --wait, ffprobe and ffmpeg",
    ),
    examples=(
        "davinci-axi render start job-2",
        "davinci-axi render start job-2,job-3 --force",
        "davinci-axi render start --all --force --no-wait",
        "davinci-axi render start job-2 --force --verify --max-mb 6 --lufs -14",
    ),
    reads_only=False,
    methods=methods_of(
        Resolve="GetCurrentPage OpenPage",
        Project="GetRenderJobList GetRenderJobStatus IsRenderingInProgress StartRendering "
        "GetTimelineCount GetTimelineByIndex GetSettings",
        Timeline="GetName GetSettings",
    ),
)
