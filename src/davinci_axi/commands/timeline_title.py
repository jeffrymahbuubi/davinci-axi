"""`timeline title add`: a Text+ card on a track and frame of your choice, as one mutation (R-D).

`InsertFusionTitleIntoTimeline` takes no track, so a title always lands on V1 (F24). The
live-verified workaround is a compound clip: build the title on a scratch timeline, set its
text, compound it, append the compound on the wanted track at the wanted record frame, and delete
the scratch. This command runs those six steps under one snapshot and records each in `steps`.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from davinci_axi.commands.insert_place import locate, timecode_to_frame
from davinci_axi.commands.item_detail import Located, walk_items
from davinci_axi.commands.media_folders import walk_clips
from davinci_axi.commands.render_frame import frame_to_timecode
from davinci_axi.commands.timeline_check import effective_settings
from davinci_axi.commands.timeline_title_style import (
    STYLE_FLAGS,
    apply_style,
    check_style_flags,
    read_inputs,
    resolve_style,
    style_inputs,
    style_summary,
    style_words,
)
from davinci_axi.commands.timeline_tracks import TIMECODE, TIMELINE_FLAG, text_arg
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.fusion_guard import fusion_call
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.output import hint
from davinci_axi.resolve_types import MediaPoolItemApi, TimelineApi
from davinci_axi.resolve_types_edit import ProjectEdit
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

MAX_TEXT = 200
DEFAULT_FRAMES = 90
DEFAULT_TRACK = 2
DEFAULT_TITLE = "Text+"
RISK = (
    "adds a compound clip {name} to the current media pool folder and a temporary timeline "
    "that is deleted afterwards (unless --keep-scratch); the title is a compound clip, so its "
    "text is edited by opening the compound"
)
TITLE_METHODS = (
    "Project.GetCurrentTimeline",
    "Project.SetCurrentTimeline",
    "Project.GetTimelineCount",
    "Project.GetTimelineByIndex",
    "Project.GetSettings",
    "MediaPool.CreateEmptyTimeline",
    "MediaPool.AppendToTimeline",
    "MediaPool.DeleteTimelines",
    "MediaPool.GetCurrentFolder",
    "MediaPool.GetRootFolder",
    "Folder.GetClipList",
    "Folder.GetSubFolderList",
    "Folder.GetName",
    "MediaPoolItem.GetName",
    "Timeline.SetMarkInOut",
    "Timeline.InsertFusionTitleIntoTimeline",
    "Timeline.CreateCompoundClip",
    "Timeline.GetTrackCount",
    "Timeline.GetItemListInTrack",
    "Timeline.GetStartFrame",
    "Timeline.GetSettings",
    "TimelineItem.GetFusionCompByIndex",
    "TimelineItem.GetDuration",
    "TimelineItem.GetStart",
    "TimelineItem.GetUniqueId",
    "TimelineItem.GetName",
)


def free_span(timeline: TimelineApi, track: int, frame: int, frames: int) -> list[Located]:
    """Items on `V<track>` that overlap the frames `[frame, frame + frames)`."""
    end = frame + frames
    return [
        found
        for found in walk_items(timeline, ("video",), only_track=track)
        if int(found.item.GetStart()) < end
        and frame < int(found.item.GetStart()) + int(found.item.GetDuration())
    ]


def _int(inv: Invocation, key: str) -> int | None:
    value = inv.args.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _check_flags(inv: Invocation) -> tuple[str, int, int, str]:
    """(text, duration, track, title) after the checks that need no Resolve call."""
    text = str(inv.positionals["text"])
    if not text.strip():
        raise AxiError(
            "VALIDATION_ERROR",
            "the title text is empty",
            help=[
                "Valid example: davinci-axi timeline title add 'The Indoor Garden' --at 01:00:32:00"
            ],
        )
    if len(text) > MAX_TEXT:
        raise AxiError(
            "VALIDATION_ERROR",
            f"the title text is {len(text)} characters; the limit is {MAX_TEXT}",
            help=["Shorten the text, or put the long part in a second title"],
        )
    at, frame = text_arg(inv, "at"), _int(inv, "frame")
    if at is not None and frame is not None:
        raise AxiError(
            "VALIDATION_ERROR",
            "--at and --frame are mutually exclusive",
            help=["Pass one of them: --at 01:00:02:00 or --frame 86448"],
        )
    if at is None and frame is None:
        raise AxiError(
            "VALIDATION_ERROR",
            "say where the title goes: --at TIMECODE or --frame N",
            help=[
                "Valid example: --at 01:00:32:00 (HH:MM:SS:FF), or --frame 86448 (as `items` shows)"
            ],
        )
    if at is not None and not TIMECODE.match(at):
        raise AxiError(
            "VALIDATION_ERROR",
            f"invalid timecode {at}",
            help=["Valid timecode: HH:MM:SS:FF, e.g. 01:00:02:00"],
        )
    if frame is not None and frame < 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--frame must be 0 or more, got {frame}",
            help=["Valid example: --frame 0"],
        )
    duration = DEFAULT_FRAMES if _int(inv, "duration") is None else cast(int, _int(inv, "duration"))
    if duration < 1:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--duration must be 1 or more, got {duration}",
            help=["Valid example: --duration 100"],
        )
    track = DEFAULT_TRACK if _int(inv, "track") is None else cast(int, _int(inv, "track"))
    if track < 1:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--track must be 1 or more, got {track}",
            help=["Valid example: --track 3"],
        )
    title = inv.args.get("title")
    if title is not None and not str(title).strip():
        raise AxiError(
            "VALIDATION_ERROR", "--title is empty", help=["Valid example: --title Text+"]
        )
    return text, duration, track, str(title) if title else DEFAULT_TITLE


def _refuse_occupied(occupants: list[Located], track: int, frame: int, frames: int) -> None:
    rows = [
        f"{o.track}:{o.position} '{o.item.GetName()}' "
        f"{int(o.item.GetStart())}-{int(o.item.GetStart()) + int(o.item.GetDuration())}"
        for o in occupants
    ]
    last = max(int(o.item.GetStart()) + int(o.item.GetDuration()) for o in occupants)
    raise AxiError(
        "WRITE_REFUSED",
        f"V{track} is occupied between {frame} and {frame + frames}: {'; '.join(rows)}",
        help=[
            f"Pick --frame {last} (after the last occupant) or --track {track + 1}",
            "Add a track first with `timeline track add video --force` if it is missing",
        ],
        detail={"occupants": rows},
    )


def _unique_scratch(session: Session) -> str:
    base = f"davinci-axi-title-{time.strftime('%H%M%S')}"
    name, number = base, 1
    while session.find_timeline(name) is not None:
        number += 1
        name = f"{base}-{number}"
    return name


def _clip_named(pool: Any, name: str) -> tuple[MediaPoolItemApi, str] | None:
    """The pool clip `name`: the current folder first, then every folder (live check 4)."""
    for clip in pool.GetCurrentFolder().GetClipList() or []:
        if clip.GetName() == name:
            return clip, "current folder"
    for found in walk_clips(pool.GetRootFolder()):
        if found.clip.GetName() == name:
            return found.clip, found.folder_path
    return None


class _Steps:
    """What the apply closure has done so far; every failure carries it."""

    def __init__(self) -> None:
        self.done: list[str] = []

    def fail(self, method: str, detail: str) -> AxiError:
        err = api_failed(method, f"{detail} (done so far: {', '.join(self.done) or 'nothing'})")
        err.detail["steps"] = list(self.done)
        return err


@contextmanager
def _restore_current(
    project: ProjectEdit, target: TimelineApi, plan: MutationPlan
) -> Iterator[None]:
    """Whatever happens, leave the timeline that was current when the command started current."""
    previous = project.GetCurrentTimeline()
    try:
        yield
    finally:
        now = project.GetCurrentTimeline()
        if previous is not None and (now is None or now.GetUniqueId() != previous.GetUniqueId()):
            restored = bool(project.SetCurrentTimeline(previous))
            if previous.GetUniqueId() != target.GetUniqueId():
                plan.extra["timeline_restored"] = restored


def run_title_add(inv: Invocation) -> Result:
    text, duration, track, title = _check_flags(inv)
    checked = check_style_flags(inv)
    check_snapshot_flags(inv)
    session = inv.connect()
    target = session.require_timeline(text_arg(inv, "timeline"))
    fps, width, height = effective_settings(session, target)
    style = resolve_style(checked, int(width), int(height))
    at = text_arg(inv, "at")
    frame = timecode_to_frame(at, fps) if at is not None else cast(int, _int(inv, "frame"))
    start = int(target.GetStartFrame())
    if frame < start:
        raise AxiError(
            "VALIDATION_ERROR",
            f"frame {frame} is before the timeline start "
            f"({start}, {frame_to_timecode(start, fps)})",
            help=[f"Use --frame {start} or later, or --at {frame_to_timecode(start, fps)}"],
        )
    tracks = target.GetTrackCount("video")
    if track > tracks:
        raise AxiError(
            "NOT_FOUND",
            f"this timeline has {tracks} video track{'' if tracks == 1 else 's'}; "
            f"V{track} does not exist",
            help=["Add one first: `davinci-axi timeline track add video --force`"],
        )
    occupants = free_span(target, track, frame, duration)
    if occupants:
        _refuse_occupied(occupants, track, frame, duration)
    project = cast(ProjectEdit, session.require_project())
    pool: Any = project.GetMediaPool()
    name = text_arg(inv, "name") or f"title: {text[:24]}"
    if _clip_named(pool, name) is not None:
        raise AxiError(
            "VALIDATION_ERROR",
            f"a media pool clip named {name!r} already exists",
            help=["Pick another compound clip name with --name"],
        )
    scratch_name = _unique_scratch(session)
    keep = bool(inv.args.get("keep_scratch"))
    where = f"V{track}@{frame} ({duration} frames)"
    words = ["timeline", "title", "add", text, "--at" if at else "--frame", at or str(frame)]
    words += ["--duration", str(duration), "--track", str(track)]
    for flag, value in (("--title", inv.args.get("title")), ("--name", text_arg(inv, "name"))):
        if value:
            words += [flag, str(value)]
    words += style_words(inv)
    if keep:
        words.append("--keep-scratch")
    words += [
        w
        for flag in ("timeline", "snapshot_out")
        if (v := text_arg(inv, flag))
        for w in (f"--{flag.replace('_', '-')}", v)
    ]
    plan = MutationPlan(
        command="timeline title add",
        target=f"timeline {target.GetName()}",
        changes=[Change("title", None, where)],
        force_cmd=force_cmd(inv, *words),
        warning=RISK.format(name=name),
        extra={
            "text": text,
            "title": title,
            "scratch": scratch_name,
            "compound": name,
            "track": f"V{track}",
        },
        next=[
            hint("items", f"list the timeline items; the title is on V{track}"),
            hint(f"render frame ~/title.jpg --tc {frame_to_timecode(frame, fps)}", "look at it"),
            hint(
                f"media delete {shlex.quote(name)} --force",
                "remove the compound from the pool once the title is gone "
                "(it is the title's source until then)",
            ),
        ],
    )
    if summary := style_summary(style, int(width), int(height)):
        plan.extra["style"] = summary
    made: list[Any] = []

    def apply() -> None:
        steps = _Steps()
        plan.extra["steps"] = steps.done
        pool_api: Any = pool
        with _restore_current(project, target, plan):
            scratch = pool_api.CreateEmptyTimeline(scratch_name)
            if scratch is None:
                raise steps.fail(
                    "MediaPool.CreateEmptyTimeline", f"could not create {scratch_name}"
                )
            steps.done.append("scratch created")
            if not project.SetCurrentTimeline(scratch):
                raise steps.fail(
                    "Project.SetCurrentTimeline", "could not open the scratch timeline"
                )
            if not scratch.SetMarkInOut(0, duration - 1, "video"):
                raise steps.fail("Timeline.SetMarkInOut", "could not set the video marks")
            item = scratch.InsertFusionTitleIntoTimeline(title)
            if item is None:
                raise steps.fail(
                    "Timeline.InsertFusionTitleIntoTimeline",
                    f"Resolve inserted nothing; is `{title}` in the Effects Library?",
                )
            frames = int(item.GetDuration())
            steps.done.append(f"title inserted {frames} frames")
            if frames != duration:
                plan.extra["duration_note"] = f"the title is {frames} frames, not {duration}"
            comp = fusion_call("GetFusionCompByIndex", item.GetFusionCompByIndex, 1)
            tool = fusion_call("FindTool", comp.FindTool, "Template") if comp is not None else None
            if tool is None:
                raise steps.fail("FindTool", "no Template tool in the title comp")
            fusion_call("SetInput", tool.SetInput, "StyledText", text)
            read = fusion_call("GetInput", tool.GetInput, "StyledText")
            steps.done.append("text set")
            if read != text:
                plan.extra["text_read_back"] = str(read)
            if inputs := style_inputs(style):
                mismatch = apply_style(tool, style)
                steps.done.append(f"style set ({len(inputs)} inputs)")
                if mismatch:
                    plan.extra["style_read_back"] = mismatch
            plan.extra["text_inputs"] = read_inputs(tool)
            steps.done.append("inputs read")
            if scratch.CreateCompoundClip([item], {"name": name}) is None:
                raise steps.fail(
                    "Timeline.CreateCompoundClip", "Resolve did not create a compound clip"
                )
            found = _clip_named(pool_api, name)
            if found is None:
                raise steps.fail("Folder.GetClipList", "compound clip not found in the media pool")
            clip, folder = found
            steps.done.append(f"compound {name}")
            plan.extra["compound_in"] = folder
            if not project.SetCurrentTimeline(target):
                raise steps.fail("Project.SetCurrentTimeline", "could not return to the timeline")
            placed = pool_api.AppendToTimeline(
                [{"mediaPoolItem": clip, "trackIndex": track, "recordFrame": frame}]
            )
            if not placed:
                raise steps.fail("MediaPool.AppendToTimeline", "Resolve appended nothing")
            made.extend(placed)
            steps.done.append(f"appended V{track}@{frame}")
            if not keep:
                if pool_api.DeleteTimelines([scratch]):
                    steps.done.append("scratch deleted")
                else:
                    plan.extra["scratch_note"] = (
                        f"could not delete {scratch_name}; delete it by hand"
                    )
            uid = made[0].GetUniqueId()
            located = locate(target, uid)
            plan.extra.update(item_id=uid, timeline_count=project.GetTimelineCount())
            if located is not None:
                plan.extra["item"] = f"{located.track}:{located.position}"

    def verify() -> dict[str, object]:
        located = locate(target, made[0].GetUniqueId()) if made else None
        if located is None:
            return {"title": None}
        return {
            "title": f"V{track}@{int(located.item.GetStart())} "
            f"({int(located.item.GetDuration())} frames)"
        }

    return run_mutation(inv, session, plan, apply, verify)


TITLE_ADD_SPEC = CommandSpec(
    name="add",
    summary="Put a Text+ title card on a track and frame of your choice, in one step",
    run=run_title_add,
    positionals=(Positional("text", help="the title text (up to 200 characters)"),),
    flags=(
        TIMELINE_FLAG,
        Flag("at", "str", help="timecode of the first frame, HH:MM:SS:FF"),
        Flag("frame", "int", help="absolute timeline frame instead of --at, as `items` prints"),
        Flag("duration", "int", DEFAULT_FRAMES, help="length in timeline frames"),
        Flag(
            "track", "int", DEFAULT_TRACK, help="video track to put it on (must exist and be free)"
        ),
        Flag("title", "str", DEFAULT_TITLE, help="the Effects Library title to use"),
        Flag(
            "name",
            "str",
            help="name of the compound clip (default: 'title: <first 24 characters>')",
        ),
        *STYLE_FLAGS,
        Flag("keep-scratch", "bool", False, help="keep the temporary timeline to inspect it"),
        *MUTATION_FLAGS,
    ),
    examples=(
        "davinci-axi timeline title add 'The Indoor Garden' --at 01:00:32:00 --track 2",
        "davinci-axi timeline title add 'The Indoor Garden' --at 01:00:32:00 --track 2 --force",
        "davinci-axi timeline title add 'Dusk' --at 01:00:32:00 --size 0.14 --color '#ffd400' "
        "--y 0.2 --force",
    ),
    reads_only=False,
    methods=TITLE_METHODS,
    notes=(
        "`timeline insert title` always lands on V1 (F24); this builds the title on a scratch "
        "timeline, compounds it and appends the compound on the track you pick",
        "The compound clip stays in the media pool as the title's source; the scratch timeline "
        "is deleted unless --keep-scratch",
        "Text+ titles render (F24); comps built through the API do not (F22)",
        "--duration is in timeline frames; the span on the track must be free",
        "Style: --size is the Text+ glyph scale relative to the frame, not the text's width "
        "(default 0.08, or 0.08*height/width on a portrait timeline, 0.142 on 9:16); --y is the "
        "centre's height, 0 bottom to 1 top; --color is #rrggbb or "
        "white/black/red/green/blue/yellow; the inputs are read back and a real mismatch is "
        "reported as style_read_back",
        "text_inputs lists the Text+ input values read back before compounding (StyledText, "
        "Size, Font, Style, Red1/Green1/Blue1, Center); use them to confirm the input ids on a "
        "live run",
        "The compound clip is the title's source: delete it with `media delete '<compound>' "
        "--force` only after the title item is gone",
    ),
)
TITLE_SPEC = CommandSpec(
    name="title",
    summary="Title cards placed on a track of your choice",
    subcommands={"add": TITLE_ADD_SPEC},
    examples=TITLE_ADD_SPEC.examples,
    reads_only=False,
    methods=TITLE_ADD_SPEC.methods,
)
