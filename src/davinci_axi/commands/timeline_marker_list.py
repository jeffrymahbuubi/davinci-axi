"""`timeline marker list`: the markers of a timeline as rows (F50f1). Read-only.

`frame` is the offset from the timeline start, the number `marker add --frame` and `marker delete
--frame` take; `abs_frame` is the absolute frame `items` prints. The lead splices
`MARKER_LIST_SPEC` into `timeline_extra.WRAPPED_MARKER`.
"""

from __future__ import annotations

from davinci_axi import registry
from davinci_axi.commands.timeline_tracks import TIMELINE_FLAG, target_timeline, text_arg
from davinci_axi.markers import MARKER_COLORS
from davinci_axi.output import apply_limit, empty_state, hint, select_fields
from davinci_axi.resolve_types import Info
from davinci_axi.spec import LIST_FLAGS, CommandSpec, Flag, Invocation, Result

DEFAULT_FIELDS = ("frame", "color", "name", "note", "duration")
EXTRA_FIELDS = ("abs_frame", "custom")


def marker_rows(markers: dict[int, Info], start: int) -> list[dict[str, object]]:
    """One row per marker, sorted by frame offset; `start` turns offsets into absolute frames."""
    return [
        {
            "frame": frame,
            "abs_frame": start + frame,
            "color": marker.get("color", ""),
            "name": marker.get("name", ""),
            "note": marker.get("note", ""),
            "duration": marker.get("duration", 1),
            "custom": marker.get("customData", ""),
        }
        for frame, marker in sorted(markers.items())
    ]


def default_fields(rows: list[dict[str, object]]) -> tuple[str, ...]:
    """The default columns, plus `custom` when any listed marker carries custom data."""
    if any(row.get("custom") for row in rows):
        return (*DEFAULT_FIELDS, "custom")
    return DEFAULT_FIELDS


def _help(rows: list[dict[str, object]], total: int, color: str | None) -> list[str]:
    if "timeline" not in registry.COMMANDS:
        return []
    lines: list[str] = []
    if len(rows) < total:
        flag = f" --color {color}" if color else ""
        lines.append(
            hint(f"timeline marker list{flag} --limit {total}", f"list all {total} markers")
        )
    if rows:
        lines.append(
            hint(f"timeline marker delete --frame {rows[0]['frame']}", "delete the first one")
        )
    lines.append(hint("timeline marker add --frame <n> --color Green --name Pick", "add one"))
    return lines


def run_marker_list(inv: Invocation) -> Result:
    raw = inv.args.get("fields")
    requested = [str(f) for f in raw] if isinstance(raw, list) else None
    limit = inv.args.get("limit")
    limit = limit if isinstance(limit, int) and not isinstance(limit, bool) else 100
    color = text_arg(inv, "color")
    # Validate every flag before the first Resolve call.
    select_fields([dict.fromkeys((*DEFAULT_FIELDS, *EXTRA_FIELDS))], DEFAULT_FIELDS, requested)
    apply_limit([], limit)

    session = inv.connect()
    timeline = target_timeline(session, inv)
    name = timeline.GetName()
    rows = marker_rows(timeline.GetMarkers() or {}, timeline.GetStartFrame())
    if color:
        rows = [row for row in rows if row["color"] == color]
    shown, total = apply_limit(rows, limit)
    data: dict[str, object] = {"timeline": name, "total": total}
    if shown:
        data["markers"] = select_fields(shown, default_fields(shown), requested)
        if len(shown) < total:
            data["truncated"] = True
    else:
        data["markers"] = empty_state("markers", f"in timeline {name}", {"color": color})
    return Result(data, _help(shown, total, color))


MARKER_LIST_SPEC = CommandSpec(
    name="list",
    summary=(
        "List the markers of a timeline: frame offset, colour, name, note, duration "
        "(and custom data when any marker has it)"
    ),
    run=run_marker_list,
    flags=(
        TIMELINE_FLAG,
        Flag("color", "str", None, help="only markers of this colour", choices=MARKER_COLORS),
        *LIST_FLAGS,
    ),
    examples=(
        "davinci-axi timeline marker list",
        "davinci-axi timeline marker list --color Red --fields frame,name,note",
        "davinci-axi timeline marker list --fields frame,abs_frame,custom --json",
    ),
    notes=(
        "frame is the offset from the timeline start (as `marker add --frame`); abs_frame is "
        "the absolute frame `items` prints",
        "extra fields (read only when requested): abs_frame; custom is shown by default when "
        "any marker has it",
    ),
    methods=(
        "Resolve.GetProjectManager",
        "ProjectManager.GetCurrentProject",
        "Project.GetName",
        "Project.GetCurrentTimeline",
        "Project.GetTimelineCount",
        "Project.GetTimelineByIndex",
        "Timeline.GetName",
        "Timeline.GetStartFrame",
        "Timeline.GetMarkers",
    ),
)
