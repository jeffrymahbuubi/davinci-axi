"""`snapshot diff` and `timeline diff`: changes since a snapshot, or between two timelines (U2).

`snapshot diff <id>` compares a snapshot's `state.json` with the live project (one read-only dump);
`snapshot diff <id> <id2>` compares two files and never connects. `timeline diff <a> <b>` compares
two live timelines of the current project, matching items by `(track, position)`; it makes each
timeline current while reading it (track enabled/locked are unreliable otherwise) and puts the
previous one back. The dump carries fades, volume, zoom, pan and tilt (F46).
"""

from __future__ import annotations

import json
import shlex
from typing import Any

from davinci_axi.commands.state_diff import delta_result, diff_project, diff_timeline
from davinci_axi.dump import dump_project_state, dump_timeline
from davinci_axi.errors import AxiError
from davinci_axi.mutate import with_current_timeline
from davinci_axi.output import apply_limit, collapse_home
from davinci_axi.snapshot import snapshot_root
from davinci_axi.snapshot_index import SnapshotMeta, find_snapshot
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

PROJECT_METHODS = (
    "Project.GetName",
    "Project.GetUniqueId",
    "Project.GetSettings",
    "Project.GetCurrentTimeline",
    "Project.GetTimelineCount",
    "Project.GetTimelineByIndex",
)
TIMELINE_METHODS = (
    "Timeline.GetName",
    "Timeline.GetUniqueId",
    "Timeline.GetStartFrame",
    "Timeline.GetEndFrame",
    "Timeline.GetStartTimecode",
    "Timeline.GetSettings",
    "Timeline.GetMarkers",
    "Timeline.GetTrackCount",
    "Timeline.GetTrackName",
    "Timeline.GetIsTrackEnabled",
    "Timeline.GetIsTrackLocked",
    "Timeline.GetTrackSubType",
    "Timeline.GetItemListInTrack",
    "TimelineItem.GetUniqueId",
    "TimelineItem.GetName",
    "TimelineItem.GetType",
    "TimelineItem.GetStart",
    "TimelineItem.GetEnd",
    "TimelineItem.GetDuration",
    "TimelineItem.GetSourceStartFrame",
    "TimelineItem.GetSourceEndFrame",
    "TimelineItem.GetClipEnabled",
    "TimelineItem.GetClipColor",
    "TimelineItem.GetFlagList",
    "TimelineItem.GetMarkers",
    "TimelineItem.GetMediaPoolItem",
    "TimelineItem.GetFades",
    "TimelineItem.GetProperties",
    "MediaPoolItem.GetName",
    "MediaPoolItem.GetUniqueId",
    "MediaPoolItem.GetClipProperty",
)
LIMIT_FLAG = Flag("limit", "int", 100, help="maximum change rows to show")
FULL_FLAG = Flag("full", "bool", False, help="show every change row (no --limit cap)")


def _limit(inv: Invocation, total: int) -> int:
    """The row cap: `--full` lifts it; a bad `--limit` is refused before any read."""
    value = inv.args.get("limit")
    limit = value if isinstance(value, int) else 100
    apply_limit([], limit)
    return max(total, 1) if inv.args.get("full") else limit


def _state(meta: SnapshotMeta) -> dict[str, Any]:
    if meta.state is None:
        raise AxiError(
            "NOT_FOUND",
            f"snapshot {meta.id} has no state.json to compare",
            help=[
                f"Expected {collapse_home(meta.dir)}/state.json",
                "Pick another snapshot with `davinci-axi snapshot list`",
            ],
        )
    try:
        state = json.loads(meta.state.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AxiError(
            "NOT_FOUND",
            f"state.json of snapshot {meta.id} is unreadable ({exc})",
            help=["Pick another snapshot with `davinci-axi snapshot list`"],
        ) from exc
    if not isinstance(state, dict):
        raise AxiError(
            "NOT_FOUND",
            f"state.json of snapshot {meta.id} is not a JSON object",
            help=["Pick another snapshot with `davinci-axi snapshot list`"],
        )
    return state


def _full_cmd(*words: str) -> str:
    return shlex.join([*words, "--full"])


def run_snapshot_diff(inv: Invocation) -> Result:
    _limit(inv, 1)
    root = snapshot_root()
    first = find_snapshot(root, inv.positionals["id"])
    other = inv.positionals.get("id2")
    before = _state(first)
    if other:
        second = find_snapshot(root, other)
        after, after_label = _state(second), second.id
    else:
        after = dump_project_state(inv.connect().require_project())
        after_label = "live"
    if before.get("project") != after.get("project") and not inv.args.get("allow_other_project"):
        raise AxiError(
            "VALIDATION_ERROR",
            f"snapshot {first.id} is of project {before.get('project')}, "
            f"the other side is {after.get('project')}",
            help=["Pass --allow-other-project to compare them anyway"],
        )
    timeline = inv.args.get("timeline")
    scope = timeline if isinstance(timeline, str) and timeline else None
    delta = diff_project(before, after, timeline=scope)
    words = ["snapshot", "diff", first.id, *([other] if other else [])]
    if scope:
        words += ["--timeline", scope]
    header = {"before": first.id, "after": after_label, "project": after.get("project")}
    return delta_result(
        delta,
        limit=_limit(inv, len(delta.rows)),
        full_cmd=_full_cmd(*words),
        header=header,
    )


def run_timeline_diff(inv: Invocation) -> Result:
    _limit(inv, 1)
    first, second = inv.positionals["a"], inv.positionals["b"]
    session = inv.connect()
    switched: list[str] = []
    dumps: list[dict[str, Any]] = []
    for name in (first, second):
        timeline = session.require_timeline(name)
        with with_current_timeline(session, timeline) as did:
            dumps.append(dump_timeline(timeline))
        if did:
            switched.append(name)
    before, after = dumps
    delta = diff_timeline(before, after, match="position")
    return delta_result(
        delta,
        limit=_limit(inv, len(delta.rows)),
        full_cmd=_full_cmd("timeline", "diff", first, second),
        header={"before": first, "after": second, "switched": switched or None},
    )


SNAPSHOT_DIFF_SPEC = CommandSpec(
    name="diff",
    summary="Show what changed in a project since a snapshot (or between two snapshots)",
    run=run_snapshot_diff,
    positionals=(
        Positional("id", help="snapshot id from `snapshot list`"),
        Positional("id2", required=False, help="second snapshot id (default: the live project)"),
    ),
    flags=(
        Flag("timeline", "str", None, help="restrict the item diff to this timeline"),
        LIMIT_FLAG,
        FULL_FLAG,
        Flag(
            "allow-other-project",
            "bool",
            False,
            help="compare snapshots of two different projects",
        ),
    ),
    examples=(
        "davinci-axi snapshot diff 20261108-230256-edit-20261108",
        "davinci-axi snapshot diff 20261108-230256-edit-20261108 --timeline 'Edit v1' --full",
        "davinci-axi snapshot diff 20261108-230256-edit-20261108 20261109-101500-edit-20261108",
    ),
    reads_only=True,
    methods=(*PROJECT_METHODS, *TIMELINE_METHODS),
    notes=(
        "One id compares that snapshot's state.json with the live project; two ids compare two "
        "files and never connect to Resolve.",
        "Items are matched by unique id. A start/end change with the same duration is `moved`.",
        "Fades, volume, zoom, pan and tilt are in the dump. Track enabled/locked of timelines that "
        "were not current when the snapshot was taken are unknown and never compared.",
        "`items_changed` counts items with at least one changed field; `fields_changed` counts "
        "the fields (an item whose fade_in and fade_out both changed is 1 item, 2 fields).",
        "Markers and tracks are compared too (`markers_changed`, `tracks_changed`); items, fades, "
        "volume and zoom are the item diff.",
    ),
)
TIMELINE_DIFF_SPEC = CommandSpec(
    name="diff",
    summary="Show what differs between two timelines of the current project",
    run=run_timeline_diff,
    positionals=(
        Positional("a", help="the timeline to compare from (name)"),
        Positional("b", help="the timeline to compare to (name)"),
    ),
    flags=(LIMIT_FLAG, FULL_FLAG),
    examples=(
        "davinci-axi timeline diff 'edit v1' probe-x",
        "davinci-axi timeline diff 'Edit v1' 'Edit v2' --full",
    ),
    reads_only=True,
    methods=(*TIMELINE_METHODS, "Project.SetCurrentTimeline"),
    notes=(
        "Items are matched by track and position because a duplicated timeline gets new ids.",
        "Each timeline is made current while it is read (track enabled/locked read as false on a "
        "non-current timeline, F46b) and the previous timeline is put back.",
        "Fades, volume, zoom, pan and tilt are compared; a value missing on a side is unknown.",
        "`items_changed` counts items with at least one changed field; `fields_changed` counts "
        "the fields (an item whose fade_in and fade_out both changed is 1 item, 2 fields).",
    ),
)
