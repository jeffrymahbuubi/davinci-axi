"""`items` and `item <id>`: flattened timeline item rows and a one-item detail view (R20, R21).

`SPEC` is the `items` command and `ITEM_SPEC` is `item`; the detail logic lives in `item_detail`.
Both are read-only (R24).
"""

from __future__ import annotations

import shlex

from davinci_axi import registry
from davinci_axi.commands import item_detail
from davinci_axi.commands.item_detail import TRACK_TYPES, Located, str_arg, walk_items
from davinci_axi.errors import AxiError
from davinci_axi.output import apply_limit, empty_state, hint, page_meta, select_fields
from davinci_axi.resolve_types import MediaPoolItemApi, TimelineItemApi
from davinci_axi.spec import (
    DEFAULT_LIMIT,
    LIST_FLAGS,
    CommandSpec,
    Flag,
    Invocation,
    Positional,
    Result,
)

DEFAULT_FIELDS = ("id", "name", "track", "start", "duration")
EXTRA_FIELDS = (
    "end",
    "type",
    "enabled",
    "color",
    "source_in",
    "source_out",
    "media",
    "fade_in",
    "fade_out",
    "volume",
    "zoom",
    "pan",
    "tilt",
    "fps",
    "resolution",
    "super_scale",
)
FADE_FIELDS = {"fade_in", "fade_out"}
PROPERTY_FIELDS = {"volume": "AudioVolume", "zoom": "ZoomX", "pan": "Pan", "tilt": "Tilt"}
CLIP_FIELDS = {"fps": "FPS", "resolution": "Resolution", "super_scale": "Super Scale"}

TIMELINE_FLAG = Flag("timeline", "str", None, help="timeline name (default: the current timeline)")


def _int_arg(inv: Invocation, key: str, default: int | None) -> int | None:
    value = inv.args.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _fades(item: TimelineItemApi) -> dict[str, object]:
    """`GetFades()`, or `{}` where the API is missing (before Resolve 21.1) or returns nothing."""
    try:
        return dict(item.GetFades() or {})
    except AttributeError:
        return {}


def _fade(fades: dict[str, object], *keys: str) -> object:
    for key in keys:
        if key in fades:
            return fades[key]
    return None


def _clip_value(media: MediaPoolItemApi | None, name: str) -> object:
    """One clip property, `None` with no media or an empty value; `fps` as a float when numeric."""
    value = media.GetClipProperty(name) if media is not None else None
    if value is None or value == "":
        return None
    if name == "FPS":
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


def _audit_fields(item: TimelineItemApi, extras: set[str]) -> dict[str, object]:
    """The audit fields in `extras`; each Resolve read happens once, and only when requested."""
    row: dict[str, object] = {}
    if extras & FADE_FIELDS:
        fades = _fades(item)
        row["fade_in"] = _fade(fades, "FadeIn", "fadeInLength")
        row["fade_out"] = _fade(fades, "FadeOut", "fadeOutLength")
    if extras & PROPERTY_FIELDS.keys():
        props = item.GetProperties() or {}
        for field, key in PROPERTY_FIELDS.items():
            row[field] = props.get(key)
    if extras & CLIP_FIELDS.keys():
        media = item.GetMediaPoolItem()
        for field, key in CLIP_FIELDS.items():
            row[field] = _clip_value(media, key)
    return row


def _row(located: Located, extras: set[str]) -> dict[str, object]:
    item = located.item
    start = int(item.GetStart())
    row: dict[str, object] = {
        "id": item.GetUniqueId(),
        "name": item.GetName(),
        "track": located.track,
        "start": start,
        "duration": int(item.GetDuration()),
    }
    if "end" in extras:
        row["end"] = int(item.GetEnd())
    if "type" in extras:
        row["type"] = item.GetType()
    if "enabled" in extras:
        row["enabled"] = bool(item.GetClipEnabled())
    if "color" in extras:
        row["color"] = item.GetClipColor() or None
    if "source_in" in extras:
        row["source_in"] = item.GetSourceStartFrame()
    if "source_out" in extras:
        row["source_out"] = item.GetSourceEndFrame()
    if "media" in extras:
        media = item.GetMediaPoolItem()
        row["media"] = media.GetName() if media is not None else None
    row.update(_audit_fields(item, extras))
    return row


def _scope(track_type: str | None, track: int | None, timeline_name: str) -> str:
    if track_type and track is not None:
        where = f"on {track_type} track {track}"
    elif track_type:
        where = f"on {track_type} tracks"
    elif track is not None:
        where = f"on track {track}"
    else:
        where = ""
    return f"{where} in timeline {timeline_name}".strip()


def _command(inv: Invocation, limit: int | None = None) -> str:
    """`items` with the filter flags of this call, optionally with a new `--limit`."""
    parts = ["items"]
    for key, flag in (("timeline", "--timeline"), ("track_type", "--track-type")):
        value = str_arg(inv, key)
        if value:
            parts += [flag, shlex.quote(value)]
    track = _int_arg(inv, "track", None)
    if track is not None:
        parts += ["--track", str(track)]
    if limit is not None:
        parts += ["--limit", str(limit)]
    return " ".join(parts)


def _help(inv: Invocation, timeline_name: str, shown: list[Located], total: int) -> list[str]:
    lines: list[str] = []
    if len(shown) < total:
        lines.append(hint(_command(inv, total), f"list all {total} items"))
    if shown and "item" in registry.COMMANDS:
        suffix = f" --timeline {shlex.quote(timeline_name)}" if str_arg(inv, "timeline") else ""
        first = shown[0]
        lines.append(
            hint(f"item {first.track}:{first.position}{suffix}", "show that item in detail")
        )
    if "timelines" in registry.COMMANDS:
        lines.append(hint("timelines", "list the other timelines of this project"))
    return lines


def run(inv: Invocation) -> Result:
    raw = inv.args.get("fields")
    requested = [str(f) for f in raw] if isinstance(raw, list) else None
    limit = _int_arg(inv, "limit", DEFAULT_LIMIT)
    limit = DEFAULT_LIMIT if limit is None else limit
    track_type = str_arg(inv, "track_type")
    track = _int_arg(inv, "track", None)
    # Validate every flag before the first Resolve call (R11).
    select_fields([dict.fromkeys((*DEFAULT_FIELDS, *EXTRA_FIELDS))], DEFAULT_FIELDS, requested)
    apply_limit([], limit)
    if track is not None and track < 1:
        raise AxiError(
            "VALIDATION_ERROR", "--track must be 1 or more", help=["Tracks are numbered from 1"]
        )
    extras = {f for f in requested or [] if f in EXTRA_FIELDS}

    session = inv.connect()
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    timeline_name = timeline.GetName()
    types = (track_type,) if track_type else TRACK_TYPES
    found = list(walk_items(timeline, types, track))
    shown, total = apply_limit(found, limit)
    data: dict[str, object] = {"timeline": timeline_name, **page_meta(len(shown), total)}
    if shown:
        rows = [_row(located, extras) for located in shown]
        data["items"] = select_fields(rows, DEFAULT_FIELDS, requested)
    else:
        data["items"] = empty_state("items", _scope(track_type, track, timeline_name), {})
    return Result(data, _help(inv, timeline_name, shown, total))


# Every read `items` (here) and `item <id>` (`item_detail.py`) make, by API class.
ITEM_LIST_METHODS = (
    "Resolve.GetProjectManager",
    "ProjectManager.GetCurrentProject",
    "Project.GetName",
    "Project.GetCurrentTimeline",
    "Project.GetTimelineCount",
    "Project.GetTimelineByIndex",
    "Timeline.GetName",
    "Timeline.GetTrackCount",
    "Timeline.GetItemListInTrack",
    "TimelineItem.GetUniqueId",
    "TimelineItem.GetName",
    "TimelineItem.GetStart",
    "TimelineItem.GetEnd",
    "TimelineItem.GetDuration",
    "TimelineItem.GetType",
    "TimelineItem.GetSourceStartFrame",
    "TimelineItem.GetSourceEndFrame",
    "TimelineItem.GetClipColor",
    "TimelineItem.GetClipEnabled",
    "TimelineItem.GetMediaPoolItem",
    "MediaPoolItem.GetName",
    "TimelineItem.GetFades",
    "TimelineItem.GetProperties",
    "MediaPoolItem.GetClipProperty",
)
ITEM_DETAIL_METHODS = (
    *ITEM_LIST_METHODS,
    "TimelineItem.GetFlagList",
    "TimelineItem.GetMarkers",
    "TimelineItem.GetFusionCompNameList",
    "TimelineItem.GetLinkedItems",
    "TimelineItem.GetTrackTypeAndIndex",
    "TimelineItem.GetTakesCount",
    "TimelineItem.GetTakeByIndex",
    "TimelineItem.GetSelectedTakeIndex",
    "TimelineItem.GetLeftOffset",
    "TimelineItem.GetRightOffset",
    "TimelineItem.GetSourceStartTime",
    "TimelineItem.GetSourceEndTime",
)

SPEC = CommandSpec(
    name="items",
    summary="List the items of a timeline across tracks",
    run=run,
    flags=(
        TIMELINE_FLAG,
        Flag(
            "track-type",
            "str",
            None,
            help="only this track type",
            choices=("video", "audio", "subtitle"),
        ),
        Flag("track", "int", None, help="only this track number (1-based)"),
        *LIST_FLAGS,
    ),
    examples=(
        "davinci-axi items",
        "davinci-axi items --timeline 'Edit v2' --track-type audio",
        "davinci-axi items --track-type video --track 1 --fields name,start,end,media",
        "davinci-axi items --track-type audio --fields name,volume,fade_in,fade_out",
    ),
    notes=(
        "audit fields (read only when requested): fade_in, fade_out, volume, zoom, pan, tilt, "
        "fps, resolution, super_scale",
        "fades need Resolve 21.1+; volume is null on video items; clip fields are null "
        "with no media",
    ),
    methods=ITEM_LIST_METHODS,
)

ITEM_SPEC = CommandSpec(
    name="item",
    summary="Show one timeline item in detail",
    run=item_detail.run,
    positionals=(
        Positional("id", help="item unique id, or <V|A|S><track>:<index> (1-based) such as V1:2"),
    ),
    flags=(
        TIMELINE_FLAG,
        Flag("full", "bool", False, help="do not truncate long properties"),
    ),
    examples=(
        "davinci-axi item V1:2",
        "davinci-axi item A1:1 --timeline 'Edit v2'",
        "davinci-axi item V1:2 --full --json",
    ),
    methods=ITEM_DETAIL_METHODS,
)
