"""`timeline music add`: a music bed on an audio track, trimmed or looped to the picture (R-B).

`MediaPool.AppendToTimeline` with `mediaType 2` places the song on an audio track, one call per
piece, each at the read-back end of the previous one; then `AudioVolume` and the fades are set on
the new items. All of it runs under one snapshot, where today it is four commands and four
snapshots.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from davinci_axi.commands.audio_gain import volume
from davinci_axi.commands.ffmpeg_common import fps_of
from davinci_axi.commands.insert_place import timecode_to_frame
from davinci_axi.commands.item_detail import Located, walk_items
from davinci_axi.commands.media_folders import open_pool, resolve_clips
from davinci_axi.commands.render_frame import frame_to_timecode
from davinci_axi.commands.timeline_batch import fit_fades
from davinci_axi.commands.timeline_check import effective_settings
from davinci_axi.commands.timeline_check_rules import span_of
from davinci_axi.commands.timeline_extra_clips import PoolClips
from davinci_axi.commands.timeline_music_length import clip_length
from davinci_axi.commands.timeline_music_pieces import (
    append_pieces,
    holes_in,
    pieces,
    stub_frames,
)
from davinci_axi.commands.timeline_music_pieces import next_piece as next_piece  # re-export
from davinci_axi.commands.timeline_tracks import (
    TIMECODE,
    TIMELINE_FLAG,
    context_words,
    text_arg,
)
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    force_cmd,
    run_mutation,
    with_current_timeline,
)
from davinci_axi.output import hint
from davinci_axi.resolve_types import TimelineApi, TimelineItemApi
from davinci_axi.resolve_types_edit import TimelineItemEdit
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

DEFAULT_TRACK, DEFAULT_DB, DEFAULT_FADE_IN, DEFAULT_FADE_OUT = 2, -18.0, 30, 90
DB_RANGE = (-100.0, 30.0)
MUSIC_METHODS = (
    "MediaPool.AppendToTimeline",
    "Folder.GetClipList",
    "Folder.GetSubFolderList",
    "Folder.GetName",
    "MediaPoolItem.GetClipProperty",
    "MediaPoolItem.GetName",
    "MediaPoolItem.GetUniqueId",
    "Project.GetCurrentTimeline",
    "Project.SetCurrentTimeline",
    "Project.GetSettings",
    "Timeline.GetItemListInTrack",
    "Timeline.GetTrackCount",
    "Timeline.GetStartFrame",
    "Timeline.GetSettings",
    "Timeline.GetName",
    "Timeline.GetUniqueId",
    "TimelineItem.SetProperties",
    "TimelineItem.GetProperties",
    "TimelineItem.SetFades",
    "TimelineItem.GetFades",
    "TimelineItem.GetDuration",
    "TimelineItem.GetStart",
    "TimelineItem.GetEnd",
    "TimelineItem.GetName",
    "TimelineItem.GetUniqueId",
)


def occupied(timeline: TimelineApi, track: int, first: int, last: int) -> list[Located]:
    """Items on `A<track>` that overlap the frames `[first, last)` (twin of the title's check)."""
    return [
        found
        for found in walk_items(timeline, ("audio",), only_track=track)
        if int(found.item.GetStart()) < last and first < int(found.item.GetEnd())
    ]


def _refuse_occupied(occupants: list[Located], track: int, first: int, last: int) -> None:
    rows = [
        f"{o.track}:{o.position} '{o.item.GetName()}' "
        f"{int(o.item.GetStart())}-{int(o.item.GetEnd())}"
        for o in occupants
    ]
    after = max(int(o.item.GetEnd()) for o in occupants)
    raise AxiError(
        "WRITE_REFUSED",
        f"A{track} is occupied between {first} and {last}: {'; '.join(rows)}",
        help=[
            f"Pick --track {track + 1} (add it with `timeline track add audio --force` if it "
            f"is missing) or --frame {after} (after the last occupant)",
            "Or clear the span first: `davinci-axi items --fields ref,name,start,end` lists "
            "what sits there",
        ],
        detail={"occupants": rows},
    )


def _int(inv: Invocation, key: str) -> int | None:
    value = inv.args.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _non_negative(inv: Invocation, key: str, default: int) -> int:
    value = _int(inv, key)
    if value is None:
        return default
    if value < 0:
        flag = key.replace("_", "-")
        raise AxiError(
            "VALIDATION_ERROR",
            f"--{flag} must be 0 or more frames, got {value}",
            help=[f"Valid example: --{flag} {default}"],
        )
    return value


def _check_flags(inv: Invocation) -> tuple[int, float, int, int, int | None, str | None]:
    """(track, db, fade_in, fade_out, frame, at) after the checks that need no Resolve call."""
    track = _int(inv, "track")
    track = DEFAULT_TRACK if track is None else track
    if track < 1:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--track must be 1 or more, got {track}",
            help=[f"Valid example: --track {DEFAULT_TRACK}"],
        )
    raw = inv.args.get("db")
    db = DEFAULT_DB if raw is None else raw
    if isinstance(db, bool) or not isinstance(db, (int, float)):
        raise AxiError(
            "VALIDATION_ERROR",
            "--db must be a number of decibels",
            help=[f"Valid example: --db {DEFAULT_DB:g}"],
        )
    if not DB_RANGE[0] <= db <= DB_RANGE[1]:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--db must be between {DB_RANGE[0]:g} and {DB_RANGE[1]:g}, got {db:g}",
            help=[f"A bed under dialogue sits near {DEFAULT_DB:g}: --db {DEFAULT_DB:g}"],
        )
    fade_in = _non_negative(inv, "fade_in", DEFAULT_FADE_IN)
    fade_out = _non_negative(inv, "fade_out", DEFAULT_FADE_OUT)
    at, frame = text_arg(inv, "at"), _int(inv, "frame")
    if at is not None and frame is not None:
        raise AxiError(
            "VALIDATION_ERROR",
            "--at and --frame are mutually exclusive",
            help=["Pass one of them: --at 01:00:02:00 or --frame 86448"],
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
            help=["Use the absolute frame `items` prints, e.g. --frame 86448"],
        )
    return track, float(db), fade_in, fade_out, frame, at


def _fits(item: TimelineItemEdit, wanted: dict[str, int], plan: MutationPlan) -> dict[str, int]:
    """The fades to send for `item`, after the clamp; the plan's changes follow what was sent."""
    full, rows = fit_fades(wanted, item.GetFades() or {}, int(item.GetDuration()))
    if rows:
        clamped = cast(list[dict[str, object]], plan.extra.setdefault("clamped", []))
        clamped += [{"item": item.GetName(), **row} for row in rows]
    plan.changes[:] = [
        replace(c, after=full[c.field]) if c.field in wanted else c for c in plan.changes
    ]
    return full


def _tail_warning(tail: int, stub: int, last_piece: int) -> str:
    return (
        f"the bed stops {tail} frame(s) before the picture end (a piece under {stub} frames "
        f"would be a blip of the song's start); the fade-out is on piece {last_piece}"
    )


def run_music_add(inv: Invocation) -> Result:
    track, db, fade_in, fade_out, frame, at = _check_flags(inv)
    loop = bool(inv.args.get("loop"))
    ctx = open_pool(inv)
    session = ctx.session
    timeline = session.require_timeline(text_arg(inv, "timeline"))
    ref = str(inv.positionals["clip"])
    found = resolve_clips(ctx, ref)
    if len(found) != 1:
        raise AxiError(
            "VALIDATION_ERROR",
            f"name one clip, got {len(found)}",
            help=["Valid example: davinci-axi timeline music add song.wav"],
        )
    clip = found[0].clip
    name, kind = clip.GetName(), str(clip.GetClipProperty("Type") or "")
    if "Audio" not in kind:
        raise AxiError(
            "VALIDATION_ERROR",
            f"{name} has no audio (Type {kind or 'unknown'})",
            help=["Pick an audio clip; `davinci-axi media --fields name,type` lists the types"],
        )
    clip_frames, frames_from = clip_length(clip)
    clip_fps = fps_of(str(clip.GetClipProperty("FPS") or ""))
    fps = effective_settings(session, timeline)[0]
    start = int(timeline.GetStartFrame())
    ends = [span_of(v)[1] for v in walk_items(timeline, ("video",))]
    if not ends:
        raise AxiError(
            "VALIDATION_ERROR",
            "nothing to lay music under: the timeline has no video items",
            help=["Add the picture first: `davinci-axi timeline append <clip> --force`"],
        )
    picture_end = max(ends)
    record = timecode_to_frame(at, fps) if at is not None else (start if frame is None else frame)
    if record < start or record >= picture_end:
        raise AxiError(
            "VALIDATION_ERROR",
            f"frame {record} is outside the picture, {start}-{picture_end} "
            f"({frame_to_timecode(start, fps)}-{frame_to_timecode(picture_end, fps)})",
            help=[
                f"Use --frame between {start} and {picture_end - 1}, or leave it out for {start}"
            ],
        )
    remaining_tl = picture_end - record
    scaled = clip_fps is not None and abs(clip_fps - fps) > 0.02
    ratio = fps / clip_fps if scaled and clip_fps else 1.0
    remaining_clip = max(1, round(remaining_tl / ratio))
    stub = stub_frames(fade_out)
    min_tail = max(1, round(stub / ratio))
    plan_pieces = pieces(clip_frames, remaining_clip, loop, min_tail)
    clip_total = sum(b - a for a, b in plan_pieces)
    covered_tl = min(remaining_tl, round(clip_total * ratio))
    tail_planned = remaining_tl - covered_tl if loop else 0
    audio_tracks = timeline.GetTrackCount("audio")
    if track > audio_tracks:
        raise AxiError(
            "NOT_FOUND",
            f"this timeline has {audio_tracks} audio track{'' if audio_tracks == 1 else 's'}; "
            f"A{track} does not exist",
            help=["Add one first: `davinci-axi timeline track add audio --force`"],
        )
    clash = occupied(timeline, track, record, record + covered_tl)
    if clash:
        _refuse_occupied(clash, track, record, record + covered_tl)
    picture_frames = picture_end - start
    covered_pct = round(100 * covered_tl / picture_frames, 1)
    words = ["timeline", "music", "add", ref, "--track", str(track), "--db", format(db, "g")]
    words += ["--fade-in", str(fade_in), "--fade-out", str(fade_out)]
    words += ["--loop"] if loop else []
    if at is not None:
        words += ["--at", at]
    elif frame is not None:
        words += ["--frame", str(frame)]
    words += context_words(inv)
    next_steps = [
        hint(f"audio loudness --track {track}", "see the bed's level against the dialogue"),
        hint("audio gaps", "see where the bed is alone"),
        hint("timeline check --audio --lufs -14", "lint the mix"),
    ]
    warning = ""
    if covered_pct < 100 and not loop:
        warning = f"the song covers {covered_pct:g}% of the picture; --loop repeats it to the end"
        next_steps.insert(
            0, hint(f"timeline music add {ref} --loop", "repeat the song to the end of the picture")
        )
    if tail_planned > 0:
        warning = _tail_warning(tail_planned, stub, len(plan_pieces))
    plan = MutationPlan(
        command="timeline music add",
        target=f"timeline {timeline.GetName()} (A{track})",
        changes=[
            Change("pieces", 0, len(plan_pieces)),
            Change(f"A{track} frames", 0, covered_tl),
            Change("AudioVolume", None, db),
            Change("FadeIn", None, fade_in),
            Change("FadeOut", None, fade_out),
        ],
        force_cmd=force_cmd(inv, *words),
        warning=warning,
        extra={
            "clip": name,
            "clip_frames": clip_frames,
            "clip_frames_from": frames_from,
            "picture_frames": picture_frames,
            "covered_pct": covered_pct,
            "tail_frames": tail_planned,
            "loop": loop,
            "track": f"A{track}",
        },
        next=next_steps,
    )
    if scaled:
        plan.extra["rate_scaled"] = (
            f"{name} is {clip_fps:g} fps and the timeline {fps:g} fps; piece lengths are in the "
            "clip's frames and the record frames are scaled to the timeline"
        )
    pool = cast(PoolClips, ctx.pool)
    made: list[TimelineItemApi] = []
    cap = len(plan_pieces) + 2

    def apply() -> None:
        with with_current_timeline(session, timeline) as switched:
            if switched:
                plan.extra["timeline_restored"] = True
            placed, left = append_pieces(
                pool, clip, track, (record, picture_end), (clip_frames, ratio, loop), cap, stub
            )
            made.extend(placed)
            tail = left if loop else 0
            plan.extra["tail_frames"] = tail
            if loop:
                plan.warning = _tail_warning(tail, stub, len(made)) if tail else ""
            last_end = int(made[-1].GetEnd())
            covered = last_end - record
            plan.extra["planned_frames"] = covered_tl
            plan.extra["covered_pct"] = round(100 * covered / picture_frames, 1)
            holes = plan.extra["holes"] = holes_in(made)
            live = {"pieces": len(made), f"A{track} frames": covered}
            plan.changes[:] = [
                replace(c, after=live[c.field]) if c.field in live else c for c in plan.changes
            ]
            notes = [plan.warning]
            if holes:
                notes.append(f"{holes} uncovered frame(s) between pieces; check with `audio gaps`")
            if abs(covered - covered_tl) > len(made):
                notes.append(
                    f"{covered} frames landed on the track, not the {covered_tl} planned; "
                    "check the bed's end with `davinci-axi items`"
                )
            plan.warning = "; ".join(filter(None, notes))
            for index, one in enumerate(made):
                item = cast(TimelineItemEdit, one)
                if not item.SetProperties({"AudioVolume": db}):
                    err = api_failed(
                        "TimelineItem.SetProperties", f"AudioVolume on piece {index + 1}"
                    )
                    err.help.insert(0, "Set it by hand: `davinci-axi audio gain <ref> --db …`")
                    raise err
                wanted: dict[str, int] = {}
                if index == 0 and fade_in:
                    wanted["FadeIn"] = fade_in
                if index == len(made) - 1 and fade_out:
                    wanted["FadeOut"] = fade_out
                if wanted and not item.SetFades(_fits(item, wanted, plan)):
                    raise api_failed("TimelineItem.SetFades", f"fades on piece {index + 1}")
        ids = {one.GetUniqueId() for one in made}
        plan.extra["items"] = [
            {
                "item": f"{loc.track}:{loc.position}",
                "id": loc.item.GetUniqueId(),
                "start": int(loc.item.GetStart()),
                "end": int(loc.item.GetEnd()),
            }
            for loc in walk_items(timeline, ("audio",), only_track=track)
            if loc.item.GetUniqueId() in ids
        ]

    def verify() -> dict[str, object]:
        ids = {one.GetUniqueId() for one in made}
        live = [
            loc.item
            for loc in walk_items(timeline, ("audio",), only_track=track)
            if loc.item.GetUniqueId() in ids
        ]
        data: dict[str, object] = {
            "pieces": len(live),
            f"A{track} frames": sum(int(i.GetDuration()) for i in live),
            "holes": holes_in(live),
        }
        if live:
            data["AudioVolume"] = volume(live[0])
            data["FadeIn"] = int((live[0].GetFades() or {}).get("FadeIn") or 0)
            data["FadeOut"] = int((live[-1].GetFades() or {}).get("FadeOut") or 0)
        return data

    return run_mutation(inv, session, plan, apply, verify)


MUSIC_ADD_SPEC = CommandSpec(
    name="add",
    summary="Lay a song on an audio track, trimmed or looped to the picture, ducked and faded",
    run=run_music_add,
    positionals=(Positional("clip", help="the audio clip: media pool id or exact name"),),
    flags=(
        TIMELINE_FLAG,
        Flag("track", "int", DEFAULT_TRACK, help="audio track to put the bed on (must exist)"),
        Flag("at", "str", help="timecode to start at, HH:MM:SS:FF (default: the timeline start)"),
        Flag("frame", "int", help="absolute timeline frame instead of --at, as `items` prints"),
        Flag("db", "float", DEFAULT_DB, help="clip volume in dB, -100 to 30"),
        Flag(
            "fade-in", "int", DEFAULT_FADE_IN, help="fade-in length in frames, on the first piece"
        ),
        Flag("fade-out", "int", DEFAULT_FADE_OUT, help="fade-out length in frames, on the last"),
        Flag("loop", "bool", False, help="repeat the song until the picture is covered"),
        *MUTATION_FLAGS,
    ),
    examples=(
        "davinci-axi timeline music add song.wav",
        "davinci-axi timeline music add song.wav --db -18 --loop --force",
        "davinci-axi timeline music add song.wav --track 3 --at 01:00:10:00 --fade-out 120 --force",
    ),
    reads_only=False,
    methods=MUSIC_METHODS,
    notes=(
        "Audio only (mediaType 2): a video clip's picture never lands on the timeline",
        "The song is cut at the end of the picture (the last video item's end); `endFrame` is in "
        "the clip's own frames, so a clip at another frame rate is scaled to the timeline's",
        "--loop repeats the song back to back; it needs a song that loops cleanly",
        "The last piece is never shorter than --fade-out or 30 frames: a shorter remainder is "
        "left uncovered and reported as tail_frames, so the bed ends on a faded piece instead of "
        "a blip",
        "Pieces are appended one at a time, each at the read-back end of the previous one, so a "
        "rate-scaled loop has no 1-frame holes; `holes` reports the count",
        "An audio-only clip has no Frames in Resolve; its length is Duration x FPS (the clip FPS "
        "Resolve reports, 30 for a WAV), scaled to the timeline rate",
        "A span of the track that already holds items is refused (WRITE_REFUSED); pick another "
        "track or frame",
        "The volume is the clip volume (AudioVolume, dB), not the track fader; fades are clamped "
        "to the piece length and listed under clamped",
    ),
)
MUSIC_SPEC = CommandSpec(
    name="music",
    summary="Lay a music bed on an audio track",
    subcommands={"add": MUSIC_ADD_SPEC},
    examples=MUSIC_ADD_SPEC.examples,
    reads_only=False,
    methods=MUSIC_ADD_SPEC.methods,
)
