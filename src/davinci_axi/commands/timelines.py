"""`timelines`: list the current project's timelines (R12, R14, R19, R24). Read-only."""

from __future__ import annotations

import shlex

from davinci_axi import registry
from davinci_axi.output import apply_limit, empty_state, hint, select_fields
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.spec import DEFAULT_LIMIT, LIST_FLAGS, CommandSpec, Invocation, Result

DEFAULT_FIELDS = ("index", "name", "frames", "video", "audio", "subtitle", "current")
EXTRA_FIELDS = ("id", "start_tc", "fps")


def _requested(inv: Invocation) -> list[str] | None:
    raw = inv.args.get("fields")
    return [str(f) for f in raw] if isinstance(raw, list) else None


def _limit(inv: Invocation) -> int:
    raw = inv.args.get("limit", DEFAULT_LIMIT)
    return raw if isinstance(raw, int) else DEFAULT_LIMIT


def _row(
    timeline: TimelineApi, index: int, current: str | None, extras: set[str]
) -> dict[str, object]:
    name = timeline.GetName()
    row: dict[str, object] = {
        "index": index,
        "name": name,
        "frames": timeline.GetEndFrame() - timeline.GetStartFrame(),
        "video": timeline.GetTrackCount("video"),
        "audio": timeline.GetTrackCount("audio"),
        "subtitle": timeline.GetTrackCount("subtitle"),
        "current": name == current,
    }
    if "id" in extras:
        row["id"] = timeline.GetUniqueId()
    if "start_tc" in extras:
        row["start_tc"] = timeline.GetStartTimecode()
    if "fps" in extras:
        row["fps"] = timeline.GetSettings().get("timelineFrameRate")
    return row


def _help(rows: list[dict[str, object]], current: str | None, shown: int, total: int) -> list[str]:
    scan = current or (str(rows[0]["name"]) if rows else None)
    steps: list[tuple[str, str, str]] = []
    if scan is not None:
        steps.append(
            ("items", f"items --timeline {shlex.quote(scan)}", "list the items of that timeline")
        )
    steps.append(("item", "item <id>", "show one timeline item in detail"))
    lines = [hint(cmd, purpose) for key, cmd, purpose in steps if key in registry.COMMANDS]
    if shown < total:
        lines.append(hint(f"timelines --limit {total}", f"list all {total} timelines"))
    return lines


def run(inv: Invocation) -> Result:
    requested = _requested(inv)
    limit = _limit(inv)
    # Validate every flag before the first Resolve call (R11).
    select_fields([dict.fromkeys((*DEFAULT_FIELDS, *EXTRA_FIELDS))], DEFAULT_FIELDS, requested)
    apply_limit([], limit)
    extras = {f for f in requested or [] if f in EXTRA_FIELDS}

    session = inv.connect()
    project = session.require_project()
    current = session.current_timeline()
    current_name = current.GetName() if current is not None else None
    rows: list[dict[str, object]] = []
    warnings: list[str] = []
    for index in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is None:
            warnings.append(f"timeline at index {index} could not be read and was skipped")
            continue
        rows.append(_row(timeline, index, current_name, extras))
    shown, total = apply_limit(rows, limit)
    data: dict[str, object] = {
        "project": project.GetName(),
        "total": total,
        "current": current_name,
    }
    if shown:
        data["timelines"] = select_fields(shown, DEFAULT_FIELDS, requested)
    else:
        data["timelines"] = empty_state("timelines", f"in project {project.GetName()}", {})
    if warnings:
        data["warnings"] = warnings
    return Result(data, _help(rows, current_name, len(shown), total))


SPEC = CommandSpec(
    name="timelines",
    summary="List the timelines of the current project",
    run=run,
    flags=LIST_FLAGS,
    examples=(
        "davinci-axi timelines",
        "davinci-axi timelines --fields name,id,fps",
        "davinci-axi timelines --limit 5 --json",
    ),
    methods=(
        "Resolve.GetProjectManager",
        "ProjectManager.GetCurrentProject",
        "Project.GetName",
        "Project.GetCurrentTimeline",
        "Project.GetTimelineCount",
        "Project.GetTimelineByIndex",
        "Timeline.GetName",
        "Timeline.GetUniqueId",
        "Timeline.GetStartFrame",
        "Timeline.GetEndFrame",
        "Timeline.GetStartTimecode",
        "Timeline.GetSettings",
        "Timeline.GetTrackCount",
    ),
)
