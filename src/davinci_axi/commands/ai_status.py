"""Shared plumbing for the `ai` commands (U20): write Protocols, job status and `ai status`.

Five analysis jobs expose a status getter on the project (`GetAnalyzeForSlateStatus`,
`GetSmartReframeStatus`, `GetDetectSceneCutsStatus`, `GetCreateSubtitlesFromAudioStatus` and
`GetTranscribeAudioStatus`). `run_job` starts a job and follows it with `poll.ANALYSIS`: progress
on stderr, the result block on stdout, `--no-wait` returns after the start call (KTD21). The job
is verified by its status, not by an echo (KTD23). A False or None from an AI call means Studio
or AI Extras is missing, which is the coded error `NOT_AVAILABLE` (KTD29).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast

from davinci_axi import ai_state
from davinci_axi.commands.ai_preflight import ai_mutation
from davinci_axi.commands.audio_common import not_available
from davinci_axi.commands.item_detail import Located, resolve_item, str_arg
from davinci_axi.commands.render_common import force_words, methods_of
from davinci_axi.errors import AxiError
from davinci_axi.mutate import (
    Change,
    MutationPlan,
    check_snapshot_flags,
    with_current_timeline,
)
from davinci_axi.output import hint
from davinci_axi.poll import ANALYSIS, JobKind, poll_args, poll_status, settle_start
from davinci_axi.resolve_types import (
    FolderApi,
    Info,
    MediaPoolItemApi,
    TimelineApi,
    TimelineItemApi,
)
from davinci_axi.resolve_types_edit import MediaPoolEdit, ProjectEdit
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Result

BUSY = frozenset({"Initializing", "Analyzing", "Cancelling"})
UNREAD = "the call has no getter, so the result is not read back"


class AiProject(ProjectEdit, Protocol):
    def ResetIntellisearchAnalysis(self) -> bool: ...
    def GetTranscribeAudioStatus(self) -> Info: ...
    def GetAnalyzeForSlateStatus(self) -> Info: ...
    def GetSmartReframeStatus(self) -> Info: ...
    def GetDetectSceneCutsStatus(self) -> Info: ...
    def GetCreateSubtitlesFromAudioStatus(self) -> Info: ...


class AiClip(MediaPoolItemApi, Protocol):
    def AnalyzeForIntellisearch(self, identifyFaces: bool, isBetterMode: bool) -> bool: ...
    def AnalyzeForSlate(self, markerColor: Any) -> bool: ...
    def RemoveMotionBlur(self, deblurOption: Info) -> MediaPoolItemApi | None: ...


class AiFolder(FolderApi, Protocol):
    def AnalyzeForIntellisearch(self, identifyFaces: bool, isBetterMode: bool) -> bool: ...
    def AnalyzeForSlate(self, markerColor: Any) -> bool: ...
    def RemoveMotionBlur(self, deblurOption: Info | None = None) -> list[list[Any]] | None: ...


class AiPool(MediaPoolEdit, Protocol):
    def CreateMulticamClip(
        self, clips: list[Any], multicamOptions: Info
    ) -> list[MediaPoolItemApi] | None: ...


class AiTimeline(TimelineApi, Protocol):
    def DetectSceneCuts(self) -> bool: ...
    def CreateSubtitlesFromAudio(self, autoCaptionSettings: Info | None = None) -> bool: ...


class AiItem(TimelineItemApi, Protocol):
    def CreateMagicMask(self, mode: str) -> bool: ...
    def RegenerateMagicMask(self) -> bool: ...
    def Stabilize(self) -> bool: ...
    def SmartReframe(self) -> bool: ...
    def PerformMulticamSmartSwitch(self, smartSwitchSettings: Info) -> bool: ...
    def FlattenMulticam(self, gradeOption: Any) -> bool: ...


def require_ok(result: object, method: str) -> None:
    """A falsy answer from an AI call is NOT_AVAILABLE: Studio or AI Extras is missing."""
    if not result:
        raise not_available(method)


# ---- job status -----------------------------------------------------------------------------


@dataclass(frozen=True)
class JobSpec:
    key: str
    label: str
    getter: str

    def kind(self) -> JobKind:
        return replace(ANALYSIS, label=self.label)


SLATE = JobSpec("slate", "slate", "GetAnalyzeForSlateStatus")
REFRAME = JobSpec("reframe", "reframe", "GetSmartReframeStatus")
SCENE_CUTS = JobSpec("scene-cuts", "scene cuts", "GetDetectSceneCutsStatus")
SUBTITLES = JobSpec("subtitles", "subtitles", "GetCreateSubtitlesFromAudioStatus")
TRANSCRIBE = JobSpec("transcribe", "transcribe", "GetTranscribeAudioStatus")
ALL_JOBS = (SLATE, REFRAME, SCENE_CUTS, SUBTITLES, TRANSCRIBE)
JOB_COMMANDS = {"transcribe": "audio transcribe"}
"""The command name each job's error is remembered under; the others are `ai <key>`."""


def job_command(job: JobSpec) -> str:
    return JOB_COMMANDS.get(job.key, f"ai {job.key}")


def read_status(project: AiProject, job: JobSpec) -> Info:
    return dict(getattr(project, job.getter)() or {})


def status_text(info: Info) -> str:
    return str(info.get("JobStatus") or "unknown")


def refuse_no_wait_switch(session: Session, timeline: TimelineApi, wait: bool) -> None:
    """`--no-wait` on another timeline would switch back while the job still runs."""
    current = session.current_timeline()
    if wait or current is None or current.GetUniqueId() == timeline.GetUniqueId():
        return
    raise AxiError(
        "VALIDATION_ERROR",
        f"--no-wait cannot run on timeline {timeline.GetName()}: it is not the current timeline",
        help=[
            "Drop --no-wait so the timeline stays open until the job ends",
            "Or open the timeline first: davinci-axi timeline open <name> --force",
        ],
    )


@contextmanager
def timeline_scope(session: Session, timeline: TimelineApi, plan: MutationPlan) -> Iterator[None]:
    """`with_current_timeline` for a write, noting a restore in the output."""
    with with_current_timeline(session, timeline) as switched:
        if switched:
            plan.extra["timeline_restored"] = True
        yield


def run_job(
    inv: Invocation,
    session: Session,
    plan: MutationPlan,
    job: JobSpec,
    start: Callable[[], None],
    *,
    scope: Callable[[], AbstractContextManager[object]] | None = None,
    after: Callable[[bool], None] | None = None,
    package: str | None = None,
    advisory: bool = False,
) -> Result:
    """Start `job` with `start()` and follow its status; `plan.changes` is set here.

    `scope` wraps the start call and the polling (to keep a timeline current while the job
    runs); `after(wait)` runs while verifying, to put read-back facts into `plan.extra`.
    """
    wait, timeout, interval = poll_args(inv)
    project = cast(AiProject, session.require_project())
    before = status_text(read_status(project, job))
    if before in BUSY:
        raise AxiError(
            "AI_BUSY",
            f"a {job.label} job is already {before} in Resolve",
            help=["Wait for it to finish (`davinci-axi ai status`), then retry"],
        )
    plan.changes = [Change("status", before, "Complete" if wait else "started")]
    kind = job.kind()

    def apply() -> None:
        with scope() if scope is not None else nullcontext():
            start()
            if wait:
                poll_status(
                    lambda: read_status(project, job), kind, timeout=timeout, interval=interval
                )

    def verify() -> dict[str, object]:
        if wait:
            status = status_text(read_status(project, job))
        else:
            status = status_text(settle_start(lambda: read_status(project, job), kind))
        if not wait and status not in kind.idle and status not in kind.failed:
            status = "started"
        if after is not None:
            after(wait)
        return {"status": status}

    return ai_mutation(inv, session, plan, apply, verify, package=package, advisory=advisory)


# ---- one timeline item ----------------------------------------------------------------------


@dataclass
class OpenItem:
    session: Session
    timeline: AiTimeline
    located: Located

    @property
    def item(self) -> AiItem:
        return cast(AiItem, self.located.item)

    @property
    def ref(self) -> str:
        return f"{self.located.track}:{self.located.position}"

    @property
    def target(self) -> str:
        return f"item {self.ref} ({self.item.GetName()}) in timeline {self.timeline.GetName()}"


def open_item(inv: Invocation, positional: str = "item") -> OpenItem:
    """Refuse a bad `--no-snapshot`, connect and resolve the one item (`--timeline` aware)."""
    check_snapshot_flags(inv)
    session = inv.connect()
    timeline = cast(AiTimeline, session.require_timeline(str_arg(inv, "timeline")))
    return OpenItem(session, timeline, resolve_item(timeline, inv.positionals[positional]))


def item_call(
    inv: Invocation,
    sel: OpenItem,
    command: str,
    words: list[str],
    call: Callable[[AiItem], object],
    method: str,
    warning: str,
    *,
    irreversible: bool = False,
    extra: dict[str, object] | None = None,
    package: str | None = None,
) -> Result:
    """One call on `sel.item` with no getter (KTD23): its return value is the check."""
    plan = MutationPlan(
        command=command,
        target=sel.target,
        changes=[Change("called", False, True)],
        force_cmd=force_words(inv, words),
        irreversible=irreversible,
        extra=dict(extra or {}),
        warning=f"{warning}; {UNREAD}",
        next=[hint(f"item {sel.ref}", "show the item")],
    )

    def apply() -> None:
        with timeline_scope(sel.session, sel.timeline, plan):
            require_ok(call(sel.item), method)

    return ai_mutation(inv, sel.session, plan, apply, lambda: {"called": True}, package=package)


ITEM_READS = methods_of(
    Timeline="GetItemListInTrack GetTrackCount GetName GetUniqueId",
    TimelineItem="GetName GetUniqueId GetTrackTypeAndIndex",
    Project="GetCurrentTimeline SetCurrentTimeline GetName",
)


# ---- ai status ------------------------------------------------------------------------------


def _row(job: JobSpec, info: Info, errors: dict[str, ai_state.LastError]) -> dict[str, object]:
    last = errors.get(job_command(job))
    index, total = info.get("CurrentClipIndex"), info.get("TotalClips")
    return {
        "job": job.key,
        "status": status_text(info),
        "percent": info.get("CompletionPercentage"),
        "clip": f"{index}/{total}" if index is not None and total is not None else None,
        "eta_s": info.get("EstimatedTimeRemaining"),
        "error": info.get("Error"),
        "last_error": ai_state.label(last) if last is not None else None,
    }


def run_status(inv: Invocation) -> Result:
    project = cast(AiProject, inv.connect().require_project())
    cleared = ai_state.clear() if inv.args.get("clear_errors") else None
    errors = ai_state.load()
    rows = [_row(job, read_status(project, job), errors) for job in ALL_JOBS]
    busy = [str(r["job"]) for r in rows if r["status"] in BUSY]
    data: dict[str, object] = {"project": project.GetName(), "busy": ", ".join(busy) or "none"}
    if cleared is not None:
        data["errors_cleared"] = cleared
    data["jobs"] = rows
    known = {job_command(job) for job in ALL_JOBS}
    other = [
        {"command": e.command, "code": e.code, "at": e.at}
        for key, e in errors.items()
        if key not in known
    ]
    if other:
        data["other_errors"] = other
    help_lines = [hint("ai scene-cuts --no-wait", "start a job and check back here")]
    if errors:
        help_lines.append(hint("ai status --clear-errors", "forget the remembered errors"))
    return Result(data, help_lines)


STATUS_SPEC = CommandSpec(
    name="status",
    summary="Show the status of the five analysis jobs (slate, reframe, scene cuts, subtitles, "
    "transcribe) and the last error of each AI verb",
    run=run_status,
    flags=(
        Flag(
            "clear-errors",
            "bool",
            False,
            help="forget the remembered last errors (local state only)",
        ),
    ),
    examples=("davinci-axi ai status", "davinci-axi ai status --json"),
    methods=methods_of(
        Project=(
            "GetName GetAnalyzeForSlateStatus GetSmartReframeStatus GetDetectSceneCutsStatus "
            "GetCreateSubtitlesFromAudioStatus GetTranscribeAudioStatus"
        )
    ),
)
