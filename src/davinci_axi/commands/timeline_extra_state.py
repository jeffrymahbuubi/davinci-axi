"""`timeline mark|blanking|stereo|dolby-vision|selection` and `timeline marker custom` (U25).

Mark in/out lives in the Resolve session, not in the project, so `mark` takes no snapshot
(policy `none`). Blanking and custom marker data are project state and take the usual snapshot.
`stereo convert` and `dolby-vision analyze` have no getter: they are verified by the return
value of the call (KTD23).
"""

from __future__ import annotations

from typing import Any, Protocol, cast

from davinci_axi.commands.item_detail import TRACK_TYPES, resolve_item, walk_items
from davinci_axi.commands.timeline_extra_common import (
    ASSIGN_BLANKING,
    BLANKING_KEYS,
    DATA,
    FIND_FLAG,
    FRAME_FLAG,
    as_host,
    blanking_plan,
    blanking_values,
    check_custom_get_flags,
    frame_arg,
    items_hint,
    parse_blanking,
    resolve_enum,
    run_called,
    run_custom_get,
    run_custom_set,
    split_refs,
)
from davinci_axi.commands.timeline_tracks import (
    TIMELINE_FLAG,
    context_words,
    target_timeline,
    text_arg,
)
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.output import hint
from davinci_axi.resolve_types import Info, TimelineApi
from davinci_axi.resolve_types_edit import TimelineEdit
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

MARK_TYPES: tuple[str, ...] = ("video", "audio", "all")
STEREO_NOTE = (
    "converts the timeline to stereo 3D in place; not read back; restore from the snapshot"
)


class TimelineState(TimelineEdit, Protocol):
    def SetMarkInOut(self, markIn: int, markOut: int, markType: str | None = None) -> bool: ...
    def ClearMarkInOut(self, markType: str | None = None) -> bool: ...
    def SetOutputBlanking(self, outputBlanking: Info) -> bool: ...
    def ConvertTimelineToStereo(self) -> bool: ...
    def AnalyzeDolbyVision(self, timelineItems: list[Any], analysisType: object) -> bool: ...
    def GetMarkerByCustomData(self, customData: str) -> Info: ...
    def UpdateMarkerCustomData(self, frameId: int, customData: str) -> bool: ...


def _range(info: Info | None) -> str:
    if not info or "in" not in info or "out" not in info:
        return "none"
    return f"{info['in']}-{info['out']}"


def _marks(timeline: TimelineApi) -> dict[str, str]:
    live = timeline.GetMarkInOut() or {}
    return {"video": _range(live.get("video")), "audio": _range(live.get("audio"))}


def _kinds(inv: Invocation) -> tuple[str | None, tuple[str, ...]]:
    chosen = text_arg(inv, "type")
    if chosen is None or chosen == "all":
        return chosen, ("video", "audio")
    return chosen, (chosen,)


def _tl_open(inv: Invocation) -> tuple[Session, TimelineState]:
    check_snapshot_flags(inv)
    session = inv.connect()
    return session, cast(TimelineState, target_timeline(session, inv))


def run_mark_set(inv: Invocation) -> Result:
    mark_in, mark_out = inv.args.get("in"), inv.args.get("out")
    if (
        not isinstance(mark_in, int)
        or not isinstance(mark_out, int)
        or mark_in < 0
        or mark_out < mark_in
    ):
        raise AxiError(
            "VALIDATION_ERROR",
            "--in and --out must be frames with 0 <= in <= out",
            help=["Valid example: --in 10 --out 100 (frames from the timeline start)"],
        )
    chosen, kinds = _kinds(inv)
    session, timeline = _tl_open(inv)
    before = _marks(timeline)
    want = f"{mark_in}-{mark_out}"
    words = ["timeline", "mark", "set", "--in", str(mark_in), "--out", str(mark_out)]
    if chosen is not None:
        words += ["--type", chosen]
    plan = MutationPlan(
        command="timeline mark set",
        target=f"timeline {timeline.GetName()}",
        changes=[Change(f"{k} mark", before[k], want) for k in kinds if before[k] != want],
        force_cmd=force_cmd(inv, *words, *context_words(inv)),
        snapshot="none",
        extra={"note": "mark in/out is session state, not project data"},
    )

    def apply() -> None:
        ok = (
            timeline.SetMarkInOut(mark_in, mark_out, chosen)
            if chosen
            else timeline.SetMarkInOut(mark_in, mark_out)
        )
        if not ok:
            raise api_failed("Timeline.SetMarkInOut", f"could not set {want}")

    def verify() -> dict[str, object]:
        now = _marks(timeline)
        return {f"{k} mark": now[k] for k in kinds}

    return run_mutation(inv, session, plan, apply, verify)


def run_mark_clear(inv: Invocation) -> Result:
    chosen, kinds = _kinds(inv)
    session, timeline = _tl_open(inv)
    before = _marks(timeline)
    words = ["timeline", "mark", "clear"] + (["--type", chosen] if chosen else [])
    plan = MutationPlan(
        command="timeline mark clear",
        target=f"timeline {timeline.GetName()}",
        changes=[Change(f"{k} mark", before[k], "none") for k in kinds if before[k] != "none"],
        force_cmd=force_cmd(inv, *words, *context_words(inv)),
        snapshot="none",
    )

    def apply() -> None:
        ok = timeline.ClearMarkInOut(chosen) if chosen else timeline.ClearMarkInOut()
        if not ok:
            raise api_failed("Timeline.ClearMarkInOut", "could not clear the marks")

    def verify() -> dict[str, object]:
        now = _marks(timeline)
        return {f"{k} mark": now[k] for k in kinds}

    return run_mutation(inv, session, plan, apply, verify)


def run_blanking_show(inv: Invocation) -> Result:
    session = inv.connect()
    timeline = target_timeline(session, inv)
    data: dict[str, object] = {"timeline": timeline.GetName()}
    data.update(blanking_values(timeline.GetOutputBlanking()))
    return Result(data)


def run_blanking_set(inv: Invocation) -> Result:
    wanted = parse_blanking(inv.positionals["assignments"])
    session, timeline = _tl_open(inv)
    before = blanking_values(timeline.GetOutputBlanking())
    words = ["timeline", "blanking", "set", inv.positionals["assignments"], *context_words(inv)]
    plan = blanking_plan(
        "timeline blanking set",
        f"timeline {timeline.GetName()}",
        before,
        wanted,
        words,
        inv,
        [hint("timeline blanking show", "read the blanking back")],
    )
    full = {**before, **wanted}

    def apply() -> None:
        if not timeline.SetOutputBlanking(dict(full)):
            raise api_failed("Timeline.SetOutputBlanking", "the blanking was not set")

    def verify() -> dict[str, object]:
        return dict(blanking_values(timeline.GetOutputBlanking()))

    return run_mutation(inv, session, plan, apply, verify)


def run_stereo_convert(inv: Invocation) -> Result:
    session, timeline = _tl_open(inv)
    plan = MutationPlan(
        command="timeline stereo convert",
        target=f"timeline {timeline.GetName()}",
        changes=[],
        force_cmd=force_cmd(inv, "timeline", "stereo", "convert", *context_words(inv)),
        irreversible=True,
        warning=STEREO_NOTE,
    )
    return run_called(
        inv,
        session,
        plan,
        "Timeline.ConvertTimelineToStereo",
        timeline.ConvertTimelineToStereo,
        "Resolve refused the conversion",
    )


def run_dolby_analyze(inv: Invocation) -> Result:
    refs = split_refs(
        inv.positionals["items"], "item", "davinci-axi timeline dolby-vision analyze V1:1,V1:2"
    )
    session, timeline = _tl_open(inv)
    items = [resolve_item(timeline, ref).item for ref in refs]
    mode = resolve_enum(session, "DLB_BLEND_SHOTS")
    words = ["timeline", "dolby-vision", "analyze", ",".join(refs), *context_words(inv)]
    plan = MutationPlan(
        command="timeline dolby-vision analyze",
        target=f"timeline {timeline.GetName()} ({', '.join(refs)})",
        changes=[],
        force_cmd=force_cmd(inv, *words),
        warning="analysis writes Dolby Vision metadata to the items; not read back",
        next=items_hint(),
    )
    return run_called(
        inv,
        session,
        plan,
        "Timeline.AnalyzeDolbyVision",
        lambda: timeline.AnalyzeDolbyVision(items, mode),
        "Dolby Vision analysis needs Resolve Studio and a Dolby Vision licence",
    )


def run_selection(inv: Invocation) -> Result:
    session = inv.connect()
    timeline = target_timeline(session, inv)
    selected = {item.GetUniqueId() for item in timeline.GetSelectedClips() or []}
    rows: list[dict[str, object]] = [
        {
            "id": located.item.GetUniqueId(),
            "name": located.item.GetName(),
            "track": located.track,
            "start": int(located.item.GetStart()),
        }
        for located in walk_items(timeline, TRACK_TYPES)
        if located.item.GetUniqueId() in selected
    ]
    data: dict[str, object] = {"timeline": timeline.GetName(), "selected": len(rows)}
    if rows:
        data["items"] = rows
    help_lines = [] if rows else ["No item is selected; select clips in the Resolve timeline first"]
    return Result(data, help_lines)


def run_custom_get_tl(inv: Invocation) -> Result:
    check_custom_get_flags(inv)
    session = inv.connect()
    timeline = target_timeline(session, inv)
    return run_custom_get(inv, as_host(timeline), f"timeline {timeline.GetName()}")


def run_custom_set_tl(inv: Invocation) -> Result:
    frame = frame_arg(inv)
    session, timeline = _tl_open(inv)
    words = ["timeline", "marker", "custom", "set", inv.positionals["data"], "--frame", str(frame)]
    words += context_words(inv)
    return run_custom_set(
        inv,
        session,
        as_host(timeline),
        f"timeline {timeline.GetName()}",
        "timeline marker custom set",
        "Timeline",
        words,
        [hint(f"timeline marker custom get --frame {frame}", "read the custom data back")],
    )


_MARK_TYPE = Flag("type", "str", None, choices=MARK_TYPES, help="video, audio or all (default)")
_ITEMS = Positional("items", help="comma-separated items such as V1:1,V1:2")

MARK_SPEC = CommandSpec(
    name="mark",
    summary="Set or clear the timeline mark in and out (session state, no snapshot)",
    reads_only=False,
    subcommands={
        "set": CommandSpec(
            name="set",
            summary="Set mark in and out, in frames from the timeline start",
            run=run_mark_set,
            flags=(
                Flag("in", "int", None, required=True, help="mark-in frame"),
                Flag("out", "int", None, required=True, help="mark-out frame"),
                _MARK_TYPE,
                TIMELINE_FLAG,
                *MUTATION_FLAGS,
            ),
            examples=(
                "davinci-axi timeline mark set --in 10 --out 100",
                "davinci-axi timeline mark set --in 10 --out 100 --type video --force",
            ),
            reads_only=False,
            methods=("Timeline.GetMarkInOut", "Timeline.SetMarkInOut"),
        ),
        "clear": CommandSpec(
            name="clear",
            summary="Clear the mark in and out",
            run=run_mark_clear,
            flags=(_MARK_TYPE, TIMELINE_FLAG, *MUTATION_FLAGS),
            examples=(
                "davinci-axi timeline mark clear",
                "davinci-axi timeline mark clear --type audio --force",
            ),
            reads_only=False,
            methods=("Timeline.GetMarkInOut", "Timeline.ClearMarkInOut"),
        ),
    },
    examples=("davinci-axi timeline mark set --in 10 --out 100 --force",),
    methods=("Timeline.GetMarkInOut", "Timeline.SetMarkInOut", "Timeline.ClearMarkInOut"),
)
BLANKING_SPEC = CommandSpec(
    name="blanking",
    summary=f"Show or set the output blanking ({', '.join(BLANKING_KEYS)}) in pixels",
    reads_only=False,
    subcommands={
        "show": CommandSpec(
            name="show",
            summary="Show the timeline output blanking",
            run=run_blanking_show,
            flags=(TIMELINE_FLAG,),
            examples=("davinci-axi timeline blanking show",),
            methods=("Timeline.GetOutputBlanking",),
        ),
        "set": CommandSpec(
            name="set",
            summary="Set output blanking pixels",
            run=run_blanking_set,
            positionals=(ASSIGN_BLANKING,),
            flags=(TIMELINE_FLAG, *MUTATION_FLAGS),
            examples=(
                "davinci-axi timeline blanking set Top=10,Bottom=10",
                "davinci-axi timeline blanking set Top=10 --force",
            ),
            reads_only=False,
            methods=("Timeline.GetOutputBlanking", "Timeline.SetOutputBlanking"),
        ),
    },
    examples=("davinci-axi timeline blanking show",),
    methods=("Timeline.GetOutputBlanking", "Timeline.SetOutputBlanking"),
)
STEREO_SPEC = CommandSpec(
    name="stereo",
    summary="Convert the timeline to stereo 3D (irreversible)",
    reads_only=False,
    subcommands={
        "convert": CommandSpec(
            name="convert",
            summary="Convert the timeline to stereo (not read back)",
            run=run_stereo_convert,
            flags=(TIMELINE_FLAG, *MUTATION_FLAGS),
            examples=(
                "davinci-axi timeline stereo convert",
                "davinci-axi timeline stereo convert --force",
            ),
            reads_only=False,
            methods=("Timeline.ConvertTimelineToStereo",),
        )
    },
    examples=("davinci-axi timeline stereo convert --force",),
    methods=("Timeline.ConvertTimelineToStereo",),
)
DOLBY_SPEC = CommandSpec(
    name="dolby-vision",
    summary="Analyze Dolby Vision on timeline items",
    reads_only=False,
    subcommands={
        "analyze": CommandSpec(
            name="analyze",
            summary="Run Dolby Vision analysis (blend shots) on items (not read back)",
            run=run_dolby_analyze,
            positionals=(_ITEMS,),
            flags=(TIMELINE_FLAG, *MUTATION_FLAGS),
            examples=(
                "davinci-axi timeline dolby-vision analyze V1:1,V1:2",
                "davinci-axi timeline dolby-vision analyze V1:1 --force",
            ),
            reads_only=False,
            methods=("Timeline.AnalyzeDolbyVision",),
        )
    },
    examples=("davinci-axi timeline dolby-vision analyze V1:1 --force",),
    methods=("Timeline.AnalyzeDolbyVision",),
)
SELECTION_SPEC = CommandSpec(
    name="selection",
    summary="List the items selected in the timeline",
    run=run_selection,
    flags=(TIMELINE_FLAG,),
    examples=("davinci-axi timeline selection",),
    methods=("Timeline.GetSelectedClips",),
)
CUSTOM_SPEC = CommandSpec(
    name="custom",
    summary="Read or write the custom data of a timeline marker",
    reads_only=False,
    subcommands={
        "get": CommandSpec(
            name="get",
            summary="Read the custom data of the marker at a frame, or find a marker by its data",
            run=run_custom_get_tl,
            flags=(FRAME_FLAG, FIND_FLAG, TIMELINE_FLAG),
            examples=(
                "davinci-axi timeline marker custom get --frame 100",
                "davinci-axi timeline marker custom get --find review-1",
            ),
            methods=(
                "Timeline.GetMarkers",
                "Timeline.GetMarkerCustomData",
                "Timeline.GetMarkerByCustomData",
            ),
        ),
        "set": CommandSpec(
            name="set",
            summary="Store custom data on the marker at a frame",
            run=run_custom_set_tl,
            positionals=(DATA,),
            flags=(FRAME_FLAG, TIMELINE_FLAG, *MUTATION_FLAGS),
            examples=(
                "davinci-axi timeline marker custom set --frame 100 review-1",
                "davinci-axi timeline marker custom set --frame 100 review-1 --force",
            ),
            reads_only=False,
            methods=(
                "Timeline.GetMarkers",
                "Timeline.GetMarkerCustomData",
                "Timeline.UpdateMarkerCustomData",
            ),
        ),
    },
    examples=("davinci-axi timeline marker custom get --frame 100",),
    methods=(
        "Timeline.GetMarkerCustomData",
        "Timeline.GetMarkerByCustomData",
        "Timeline.UpdateMarkerCustomData",
    ),
)
