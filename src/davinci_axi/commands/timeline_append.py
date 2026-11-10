"""`timeline append`: append clips to a timeline, from names or an edit list (U25).

Dry run until `--force`. `--edl <file.json|.csv>` appends many clips in one call: one plan, one
snapshot, one `MediaPool.AppendToTimeline`, and the result lists the id of every new item (F14a).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict, cast

from davinci_axi.commands.item_detail import TRACK_TYPES, walk_items
from davinci_axi.commands.media_folders import (
    Ctx,
    Found,
    clips_target,
    open_pool,
    resolve_clip,
    resolve_clips,
)
from davinci_axi.commands.timeline_extra_clips import (
    PoolClips,
    int_flag,
    track_kind,
)
from davinci_axi.commands.timeline_extra_common import (
    check_range,
    items_hint,
    source_range,
)
from davinci_axi.commands.timeline_tracks import (
    TIMELINE_FLAG,
    context_words,
    plural,
    text_arg,
)
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    force_cmd,
    run_mutation,
    with_current_timeline,
)
from davinci_axi.output import collapse_home
from davinci_axi.resolve_types import Info, TimelineApi, TimelineItemApi
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

EDL_COLUMNS = ("clip", "in", "out", "duration", "track", "record_frame", "media_type")
COLUMNS_HELP = "Columns are: " + ", ".join(EDL_COLUMNS)
JSON_EXAMPLE = 'Valid example: [{"clip": "A001_beach", "in": 0, "out": 30}]'
MEDIA_TYPES = {"video": 1, "1": 1, "audio": 2, "2": 2}
CLIP_ONLY_FLAGS = ("track", "record_frame", "in", "out", "duration")


class EdlRow(TypedDict, total=False):
    clip: str
    in_: int
    out: int
    duration: int
    track: int
    record_frame: int
    media_type: int


def _bad(n: int, text: str, *help: str) -> AxiError:
    return AxiError("VALIDATION_ERROR", f"row {n}: {text}", help=list(help))


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _number(n: int, raw: dict[str, Any], name: str, minimum: int) -> int | None:
    value = raw.get(name)
    if _blank(value):
        return None
    number: int | None = None
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif isinstance(value, str):
        try:
            number = int(value.strip())
        except ValueError:
            number = None
    if number is None:
        raise _bad(n, f"{name} must be an integer", f"Valid example: {name} 30")
    if number < minimum:
        raise _bad(n, f"{name} must be {minimum} or more")
    return number


def _media_type(n: int, raw: dict[str, Any]) -> int | None:
    value = raw.get("media_type")
    if _blank(value):
        return None
    kind = MEDIA_TYPES.get(str(value).strip().lower())
    if kind is None:
        raise _bad(n, "media_type must be video, audio, 1 or 2")
    return kind


STILL_WARNING = (
    "stills: Resolve may ignore endFrame and use its standard still duration; "
    "the result lists each item's frames"
)


def _row(n: int, raw: object) -> EdlRow:
    if not isinstance(raw, dict):
        raise _bad(
            n, "must be an object with a clip field", 'Valid example: {"clip": "A001_beach"}'
        )
    keys = [str(key) if key is not None else "(extra value)" for key in raw]
    unknown = [key for key in keys if key not in EDL_COLUMNS]
    if unknown:
        raise _bad(
            n,
            f"unknown column {', '.join(unknown)}",
            f"Columns are: {', '.join(EDL_COLUMNS)}",
        )
    clip = raw.get("clip")
    if not isinstance(clip, str) or not clip.strip():
        raise _bad(n, "clip is required (a clip id or exact name)")
    row = EdlRow(clip=clip.strip())
    start, end = _number(n, raw, "in", 0), _number(n, raw, "out", 0)
    if start is not None or end is not None:
        try:
            source_range(start, end)
        except AxiError:
            raise _bad(n, "in and out go together, and out must be greater than in") from None
        row["in_"], row["out"] = cast(int, start), cast(int, end)
    length = _number(n, raw, "duration", 1)
    if length is not None:
        if "in_" in row:
            raise _bad(n, "use duration or in/out, not both")
        row["duration"] = length
    track = _number(n, raw, "track", 1)
    if track is not None:
        row["track"] = track
    record = _number(n, raw, "record_frame", 0)
    if record is not None:
        row["record_frame"] = record
    kind = _media_type(n, raw)
    if kind is not None:
        row["media_type"] = kind
    return row


def read_edl(path: Path) -> list[EdlRow]:
    """Rows of a `.json` (array of objects) or `.csv` (header row) edit list."""
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path of the edit list")
    suffix = path.suffix.lower()
    if suffix not in (".json", ".csv"):
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot read {path.name}: use .json or .csv",
            help=[COLUMNS_HELP],
        )
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        message = f"cannot read {path.name} as UTF-8 text: {exc}"
        raise AxiError("VALIDATION_ERROR", message, help=["Save the file as UTF-8"]) from None
    raws: list[object]
    if suffix == ".json":
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as exc:
            message = f"{path.name} is not valid JSON: {exc}"
            raise AxiError("VALIDATION_ERROR", message, help=[JSON_EXAMPLE]) from None
        if not isinstance(loaded, list):
            raise AxiError(
                "VALIDATION_ERROR",
                f"{path.name} must be a JSON array of rows",
                help=[JSON_EXAMPLE],
            )
        raws = list(loaded)
    else:
        reader = csv.DictReader(text.splitlines(), restkey=None)
        raws = [
            {(key.strip() if isinstance(key, str) else key): val for key, val in one.items()}
            for one in reader
        ]
    if not raws:
        raise AxiError("VALIDATION_ERROR", f"{path.name} has no rows", help=[COLUMNS_HELP])
    return [_row(n, raw) for n, raw in enumerate(raws, 1)]


@dataclass
class Placement:
    """One clip to append: its `AppendClipInfo`, the track kind it lands on, its source length."""

    info: Info
    kind: str
    frames: int
    row: int = 0
    expect: int | None = None  # frames a still must fill (checked after the write)


def is_still(clip: Any) -> bool:
    """A one-frame clip or one Resolve types as a Still (its length comes from the span)."""
    return (
        str(clip.GetClipProperty("Frames") or "") == "1"
        or str(clip.GetClipProperty("Type") or "") == "Still"
    )


def _check_span(clips: list[Any], span: tuple[int, int], duration: int | None) -> None:
    """`--out` past the end is refused, except for stills given a `--duration`."""
    check_range([c for c in clips if duration is None or not is_still(c)], span)


def _frames(live: list[int], wanted: list[int]) -> object:
    """One number when every still should have the same length (the first wrong one when not)."""
    shown = next((a for a, w in zip(live, wanted, strict=True) if a != w), live[0])
    return shown if len(set(wanted)) == 1 else ",".join(str(n) for n in live)


def _clip_frames(clip: Any) -> int:
    frames = str(clip.GetClipProperty("Frames") or "")
    return int(frames) if frames.isdigit() else 0


def _check_track(timeline: TimelineApi, kind: str, track: int | None, prefix: str = "") -> None:
    if track is not None and track > timeline.GetTrackCount(kind):
        have = plural(timeline.GetTrackCount(kind), f"{kind} track")
        raise AxiError(
            "VALIDATION_ERROR",
            f"{prefix}timeline {timeline.GetName()} has {have}, not {track}",
            help=["Add a track first with `davinci-axi timeline track add`"],
        )


def _edl_placements(ctx: Ctx, timeline: TimelineApi, rows: list[EdlRow]) -> list[Placement]:
    cache: dict[str, Found] = {}
    placements: list[Placement] = []
    for n, row in enumerate(rows, 1):
        ref = row["clip"]
        found = cache[ref] if ref in cache else cache.setdefault(ref, resolve_clip(ctx, ref))
        wanted = row.get("media_type")
        kind = {1: "video", 2: "audio"}[wanted] if wanted else track_kind([found])
        info: Info = {"mediaPoolItem": found.clip}
        frames = _clip_frames(found.clip)
        length = row.get("duration")
        if "in_" in row or length is not None:
            span = (row["in_"], row["out"]) if length is None else (0, length)
            try:
                _check_span([found.clip], span, length)
            except AxiError as err:
                err.help.insert(0, f"Fix row {n} of the edit list")
                raise
            info["startFrame"], info["endFrame"] = span
            frames = span[1] - span[0]
        for key, name in (("track", "trackIndex"), ("record_frame", "recordFrame")):
            if key in row:
                info[name] = row[key]  # type: ignore[literal-required]
        if wanted:
            info["mediaType"] = wanted
        _check_track(timeline, kind, row.get("track"), f"row {n}: ")
        still = length is not None and is_still(found.clip)
        placements.append(Placement(info, kind, frames, n, length if still else None))
    return placements


def _item_rows(placements: list[Placement], made: list[TimelineItemApi]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, item in enumerate(made):
        where = item.GetTrackTypeAndIndex()
        track = f"{str(where[0])[0].upper()}{where[1]}" if len(where) >= 2 and where[0] else ""
        rows.append(
            {
                "row": placements[index].row if index < len(placements) else index + 1,
                "clip": item.GetName(),
                "id": item.GetUniqueId(),
                "track": track,
                "record_frame": int(item.GetStart()),
                "frames": int(item.GetDuration()),
            }
        )
    return rows


def _refuse_mixed(inv: Invocation, edl: str | None, clips: str | None) -> None:
    if (edl is None) == (clips is None):
        raise AxiError(
            "VALIDATION_ERROR",
            "pass clips or --edl, not both" if edl else "pass clips or --edl, not neither",
            help=[
                "Valid example: davinci-axi timeline append A001_beach,A002_city",
                "Valid example: davinci-axi timeline append --edl cuts.json",
            ],
        )
    if edl is not None:
        used = [
            f"--{key.replace('_', '-')}" for key in CLIP_ONLY_FLAGS if inv.args.get(key) is not None
        ]
        if used:
            raise AxiError(
                "VALIDATION_ERROR",
                f"{', '.join(used)} cannot be combined with --edl",
                help=["Put track, record_frame, in and out in the rows of the edit list"],
            )


def run_append(inv: Invocation) -> Result:
    edl, clips_text = text_arg(inv, "edl"), inv.positionals.get("clips")
    _refuse_mixed(inv, edl, clips_text)
    rows: list[EdlRow] = []
    path: Path | None = None
    if edl is not None:
        path = Path(edl).expanduser().resolve()
        rows = read_edl(path)
    track = int_flag(inv, "track", 1)
    record = int_flag(inv, "record_frame", 0)
    span = source_range(int_flag(inv, "in", 0), int_flag(inv, "out", 1))
    duration = int_flag(inv, "duration", 1)
    if duration is not None:
        if span is not None:
            raise AxiError(
                "VALIDATION_ERROR",
                "use --duration or --in/--out, not both",
                help=["Valid example: --duration 90 (frames), or --in 30 --out 120"],
            )
        span = (0, duration)
    ctx = open_pool(inv)
    session = ctx.session
    timeline = session.require_timeline(text_arg(inv, "timeline"))
    extra: dict[str, object]
    if path is not None:
        placements = _edl_placements(ctx, timeline, rows)
        words = ["timeline", "append", "--edl", cast(str, edl)]
        target = f"timeline {timeline.GetName()} ({len(rows)} rows from {collapse_home(path)})"
        extra = {"rows": len(rows), "source_frames": sum(p.frames for p in placements)}
        noun = "rows"
    else:
        found = resolve_clips(ctx, cast(str, clips_text))
        if span is not None:
            _check_span([one.clip for one in found], span, duration)
        kind = track_kind(found)
        _check_track(timeline, kind, track)
        placements = []
        for one in found:
            info: Info = {"mediaPoolItem": one.clip}
            if track is not None:
                info["trackIndex"] = track
            if record is not None:
                info["recordFrame"] = record
            if span is not None:
                info["startFrame"], info["endFrame"] = span
            expect = duration if duration is not None and is_still(one.clip) else None
            placements.append(Placement(info, kind, 0, len(placements) + 1, expect))
        words = ["timeline", "append", cast(str, clips_text)]
        start, end = (None, None) if duration is not None else span or (None, None)
        for flag, value in (
            ("--track", track),
            ("--record-frame", record),
            ("--duration", duration),
            ("--in", start),
            ("--out", end),
        ):
            if value is not None:
                words += [flag, str(value)]
        target = f"timeline {timeline.GetName()} ({clips_target(found)})"
        extra = {"track": f"{kind[0].upper()}{track}"} if track is not None else {}
        if duration is not None:
            extra["duration"] = duration
        noun = "clips"
    infos = [p.info for p in placements]
    changes = [Change("appended", 0, len(placements))]
    stills = [(i, p.expect) for i, p in enumerate(placements) if p.expect is not None]
    wanted = [w for _, w in stills]
    if stills:
        changes.append(Change("frames", None, _frames(wanted, wanted)))
    plan = MutationPlan(
        command="timeline append",
        target=target,
        changes=changes,
        force_cmd=force_cmd(inv, *words, *context_words(inv)),
        warning=STILL_WARNING if stills else "",
        extra=extra,
        next=items_hint(),
    )
    pool = cast(PoolClips, ctx.pool)
    appended: list[TimelineItemApi] = []

    def apply() -> None:
        with with_current_timeline(session, timeline) as switched:
            if switched:
                plan.extra["timeline_restored"] = True
            result = pool.AppendToTimeline(infos)
        if not result:
            raise api_failed("MediaPool.AppendToTimeline", "Resolve appended nothing")
        if len(result) < len(infos):
            raise api_failed(
                "MediaPool.AppendToTimeline",
                f"Resolve appended {len(result)} of {len(infos)} {noun}",
            )
        appended.extend(result)
        plan.extra["items"] = _item_rows(placements, appended)

    def verify() -> dict[str, object]:
        present = {located.item.GetUniqueId() for located in walk_items(timeline, TRACK_TYPES)}
        data: dict[str, object] = {
            "appended": sum(1 for item in appended if item.GetUniqueId() in present)
        }
        if stills:
            data["frames"] = _frames([int(appended[i].GetDuration()) for i, _ in stills], wanted)
        return data

    return run_mutation(inv, session, plan, apply, verify)


APPEND_SPEC = CommandSpec(
    name="append",
    summary="Append media pool clips to the end of a timeline",
    run=run_append,
    positionals=(
        Positional(
            "clips",
            required=False,
            help="clip ids or exact names, comma-separated (or use --edl)",
        ),
    ),
    flags=(
        Flag(
            "edl",
            "str",
            None,
            help="JSON or CSV edit list: rows of clip, in, out, duration, track, record_frame, "
            "media_type",
        ),
        Flag("track", "int", None, help="destination track number (default: Resolve picks)"),
        Flag(
            "record-frame",
            "int",
            None,
            help="absolute timeline frame to place the clips at (the timeline's start frame, "
            "e.g. 108000 at 01:00:00:00 and 30 fps, is its first frame; default: the end of "
            "the track)",
        ),
        Flag(
            "duration",
            "int",
            None,
            help="length in frames for stills and generators; for footage the same as "
            "--in 0 --out N",
        ),
        Flag(
            "in",
            "int",
            None,
            help="first source frame to use, in the clip's own frame rate (needs --out)",
        ),
        Flag(
            "out",
            "int",
            None,
            help="source frame to stop at, exclusive, in the clip's frame rate (needs --in)",
        ),
        TIMELINE_FLAG,
        *MUTATION_FLAGS,
    ),
    examples=(
        "davinci-axi timeline append A003_dusk --track 2",
        "davinci-axi timeline append A001_beach,A002_city --force",
        "davinci-axi timeline append A001_beach --in 30 --out 120 --force",
        "davinci-axi timeline append --edl cuts.json --force",
        "davinci-axi timeline append title_card --track 2 --record-frame 108090 --duration 90 "
        "--force",
    ),
    reads_only=False,
    methods=(
        "MediaPool.AppendToTimeline",
        "Folder.GetClipList",
        "Folder.GetSubFolderList",
        "MediaPoolItem.GetClipProperty",
        "Project.GetCurrentTimeline",
        "Project.SetCurrentTimeline",
        "Timeline.GetItemListInTrack",
    ),
    notes=(
        "--in/--out count the clip's own frames (out exclusive); Resolve conforms the span to the "
        "timeline rate, so 90 frames of a 24 fps clip fill 112 frames at 30 fps",
        "--edl rows take the same in/out units; one snapshot covers the whole list and the "
        "result lists each new item id",
        "--record-frame is absolute (timeline frames, not an offset from the timeline start)",
        "Stills and generators: --duration N appends N frames (startFrame 0, endFrame N); "
        "footage: the same as --in 0 --out N",
        "No API trims, slips or re-times an existing item (TimelineItem has no start/end/"
        "duration setter in 21.1.1): delete it and append again with --in/--out or --duration, "
        "or use `item speed`",
    ),
)
