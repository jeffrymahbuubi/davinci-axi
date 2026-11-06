"""The home view: live Resolve state with no arguments. Always exits 0 (R17)."""

from __future__ import annotations

import shutil

from davinci_axi import __version__, registry
from davinci_axi.errors import AxiError
from davinci_axi.output import collapse_home, empty_state, hint
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Invocation, Result

DESCRIPTION = "Agent-ergonomic CLI for DaVinci Resolve Studio"
MAX_TIMELINES = 10
PENDING = ("Ready", "Ready for background render", "Ready to remotely render")
NEXT_STEPS = (
    ("projects", "list the projects in the current folder"),
    ("timelines", "list the timelines of the current project"),
    ("items", "list the items of the current timeline"),
)


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


def _help(reachable: bool) -> list[str]:
    if not reachable:
        return [
            "Fix the connection problem named in fix, then run `davinci-axi` again",
            "Run `davinci-axi --help` to list commands and flags",
        ]
    steps = [hint(cmd, purpose) for cmd, purpose in NEXT_STEPS if cmd in registry.COMMANDS]
    return [*steps[:2], "Run `davinci-axi --help` to list commands and flags"]


def run(inv: Invocation) -> Result:
    data: dict[str, object] = {
        "bin": collapse_home(shutil.which(inv.bin_name) or inv.bin_name),
        "version": __version__,
        "description": DESCRIPTION,
    }
    try:
        data.update(_live(inv.connect()))
    except AxiError as err:
        data["resolve"] = "unreachable"
        data["code"] = err.code
        data["fix"] = err.help[0] if err.help else err.message
        return Result(data, _help(False))
    return Result(data, _help(True))


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
