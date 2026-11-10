"""The home view: live Resolve state with no arguments. Always exits 0 (R17)."""

from __future__ import annotations

import shutil

from davinci_axi import registry, version
from davinci_axi.errors import AxiError
from davinci_axi.output import collapse_home, empty_state, hint
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Invocation, Result

DESCRIPTION = "Agent-ergonomic CLI for DaVinci Resolve Studio"
MAX_TIMELINES = 10
PENDING = ("Ready", "Ready for background render", "Ready to remotely render")
MAX_STATE_HINTS = 4
HELP_LINE = "Run `davinci-axi --help` to list commands and flags"


def _live(session: Session) -> dict[str, object]:
    data: dict[str, object] = {
        "resolve": "reachable",
        "edition": "Studio" if session.is_studio else "Free",
        "resolve_version": ".".join(str(n) for n in session.version),
        "page": session.resolve.GetCurrentPage(),
    }
    project = session.current_project()
    data["project"] = project.GetName() if project is not None else None
    if project is None:
        return data
    current = project.GetCurrentTimeline()
    data["timeline"] = current.GetName() if current is not None else None
    current_name = current.GetName() if current is not None else None
    rows: list[dict[str, object]] = []
    count = project.GetTimelineCount()
    for index in range(1, count + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is None:
            continue
        rows.append(
            {
                "name": timeline.GetName(),
                "frames": timeline.GetEndFrame() - timeline.GetStartFrame(),
                "current": timeline.GetName() == current_name,
            }
        )
    data["timeline_count"] = len(rows)
    if rows:
        data["timelines"] = rows[:MAX_TIMELINES]
    else:
        data["timelines"] = empty_state("timelines", f"in project {project.GetName()}", {})
    jobs = project.GetRenderJobList()
    statuses = [
        project.GetRenderJobStatus(str(job.get("JobId", ""))).get("JobStatus") for job in jobs
    ]
    data["render_jobs"] = len(jobs)
    data["render_pending"] = sum(1 for s in statuses if s in PENDING)
    data["render_complete"] = sum(1 for s in statuses if s == "Complete")
    return data


def _count(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    return value if isinstance(value, int) else 0


def _known(command: str) -> bool:
    return command.split()[0] in registry.COMMANDS


def next_steps(data: dict[str, object]) -> list[str]:
    """Up to four hints computed from the live home data, each naming a registered command."""
    steps: list[tuple[str, str]] = []
    if not data.get("project"):
        steps += [
            ("projects", "list the projects in the current folder"),
            ("project open <name>", "open one"),
        ]
    else:
        if _count(data, "timeline_count") == 0:
            steps += [
                ("timelines", "list the timelines of the current project"),
                ("timeline create <name> --force", "make the first timeline"),
                ("media import <files> --force", "bring footage in"),
            ]
        current = data.get("timeline")
        if isinstance(current, str) and current:
            steps += [
                ("items", f"list the items of {current}"),
                ("timeline check --target <length>", "lint it before rendering"),
            ]
        pending = _count(data, "render_pending")
        if pending > 0:
            steps.append(("render start <job> --force", f"start the {pending} pending job(s)"))
        if _count(data, "render_complete") > 0:
            steps.append(("render verify --job <id> --max-mb <mb>", "measure a finished render"))
    return [hint(cmd, purpose) for cmd, purpose in steps if _known(cmd)][:MAX_STATE_HINTS]


def _help(
    reachable: bool, starting: bool = False, data: dict[str, object] | None = None
) -> list[str]:
    if starting:
        return [
            "Wait a few seconds and run `davinci-axi` again; Resolve is still loading",
            HELP_LINE,
        ]
    if not reachable:
        return [
            "Fix the connection problem named in fix, then run `davinci-axi` again",
            HELP_LINE,
        ]
    return [*next_steps(data or {}), HELP_LINE]


def run(inv: Invocation) -> Result:
    data: dict[str, object] = {
        "bin": collapse_home(shutil.which(inv.bin_name) or inv.bin_name),
        "version": version.describe(),
        "description": DESCRIPTION,
    }
    try:
        data.update(_live(inv.connect()))
    except AxiError as err:
        starting = err.code == "RESOLVE_STARTING"
        data["resolve"] = "starting" if starting else "unreachable"
        data["code"] = err.code
        data["fix"] = err.help[0] if err.help else err.message
        return Result(data, _help(False, starting))
    return Result(data, _help(True, data=data))


SPEC = CommandSpec(
    name="home",
    summary="Show live Resolve state (also the default with no arguments)",
    run=run,
    examples=("davinci-axi", "davinci-axi --json", "davinci-axi home --timeout 10"),
    methods=(
        # connect.py and session.py reads every command makes, then the home panel itself
        "Resolve.GetVersion",
        "Resolve.GetProductName",
        "Resolve.IsStudio",
        "Resolve.GetProjectManager",
        "Resolve.GetCurrentPage",
        "ProjectManager.GetCurrentProject",
        "Project.GetName",
        "Project.GetCurrentTimeline",
        "Project.GetTimelineCount",
        "Project.GetTimelineByIndex",
        "Project.GetRenderJobList",
        "Project.GetRenderJobStatus",
        "Timeline.GetName",
        "Timeline.GetStartFrame",
        "Timeline.GetEndFrame",
    ),
)
