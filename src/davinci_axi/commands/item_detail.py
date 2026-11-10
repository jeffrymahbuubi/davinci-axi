"""`item <id>`: one timeline item in detail, plus the shared item addressing (R13, R21, KTD10)."""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from davinci_axi.errors import not_found
from davinci_axi.output import truncate
from davinci_axi.resolve_types import TimelineApi, TimelineItemApi
from davinci_axi.session import Session
from davinci_axi.spec import TRUNCATE_CHARS, Invocation, Result
from davinci_axi.toon import encode

TRACK_TYPES: tuple[str, ...] = ("video", "audio", "subtitle")
PREFIX = {"video": "V", "audio": "A", "subtitle": "S"}
BY_PREFIX = {prefix: track_type for track_type, prefix in PREFIX.items()}
_SHORTHAND = re.compile(r"([VASvas])([0-9]+):([0-9]+)")
_PRIMITIVE = (str, int, float, bool)
FADES_MINIMUM = (21, 1, 0)


@dataclass(frozen=True)
class Located:
    """One timeline item with where it sits: `V1`, its 1-based position in that track."""

    track: str
    position: int
    item: TimelineItemApi


def track_label(track_type: str, index: int) -> str:
    """`V1`, `A2`, `S1`."""
    return f"{PREFIX.get(track_type, '?')}{index}"


def walk_items(
    timeline: TimelineApi, track_types: Sequence[str], only_track: int | None = None
) -> Iterator[Located]:
    """Every item of the given track types, track by track, in track order."""
    for track_type in track_types:
        if only_track is not None:
            indexes: Sequence[int] = (only_track,)
            if only_track > timeline.GetTrackCount(track_type):
                continue
        else:
            indexes = range(1, timeline.GetTrackCount(track_type) + 1)
        for index in indexes:
            label = track_label(track_type, index)
            for position, item in enumerate(timeline.GetItemListInTrack(track_type, index) or []):
                yield Located(label, position + 1, item)


def str_arg(inv: Invocation, key: str) -> str | None:
    value = inv.args.get(key)
    return value if isinstance(value, str) and value else None


def _by_shorthand(timeline: TimelineApi, ref: str, match: re.Match[str]) -> Located:
    track_type = BY_PREFIX[match.group(1).upper()]
    track, position = int(match.group(2)), int(match.group(3))
    count = timeline.GetTrackCount(track_type)
    label = track_label(track_type, track)
    if not 1 <= track <= count:
        raise not_found(
            "item",
            ref,
            f"This timeline has {count} {track_type} tracks (tracks are numbered from 1)",
        )
    items = timeline.GetItemListInTrack(track_type, track) or []
    if not 1 <= position <= len(items):
        raise not_found(
            "item", ref, f"Track {label} has {len(items)} items (positions are numbered from 1)"
        )
    return Located(label, position, items[position - 1])


def resolve_item(timeline: TimelineApi, ref: str) -> Located:
    """KTD10: `<V|A|S><track>:<index>` (1-based) or the full unique id. No prefix matching."""
    match = _SHORTHAND.fullmatch(ref)
    if match:
        return _by_shorthand(timeline, ref, match)
    for located in walk_items(timeline, TRACK_TYPES):
        if located.item.GetUniqueId() == ref:
            return located
    raise not_found(
        "item",
        ref,
        "Item ids come from `davinci-axi items`; or use <V|A|S><track>:<index> such as V1:2",
    )


def _primitive(value: Any) -> object:
    return value if value is None or isinstance(value, _PRIMITIVE) else str(value)


def _properties(
    props: dict[str, Any], full: bool, full_cmd: str
) -> tuple[dict[str, object], str | None]:
    """The properties block, cut to whole `key: value` lines within the character limit (R13)."""
    block = {str(k): _primitive(v) for k, v in props.items()}
    if full or not block:
        return block, None
    lines = encode(block).splitlines()
    text, note = truncate("\n".join(lines), full_cmd, TRUNCATE_CHARS)
    if note is None:
        return block, None
    keep, used = 0, 0
    for line in lines:
        used += len(line) + (1 if keep else 0)
        if used > len(text):
            break
        keep += 1
    return dict(list(block.items())[:keep]), note


def _linked(item: TimelineItemApi) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for other in item.GetLinkedItems() or []:
        kind, index = other.GetTrackTypeAndIndex()[:2]
        rows.append(
            {
                "id": other.GetUniqueId(),
                "name": other.GetName(),
                "track": track_label(str(kind), int(index)),
            }
        )
    return rows


def _markers(item: TimelineItemApi) -> list[dict[str, object]]:
    return [
        {
            "frame": frame,
            "color": info.get("color"),
            "name": info.get("name"),
            "duration": info.get("duration"),
            "note": info.get("note"),
        }
        for frame, info in sorted((item.GetMarkers() or {}).items())
    ]


def takes_count(item: TimelineItemApi) -> int:
    """Resolve returns None for items without a take selector (audio items); treat it as 0."""
    value = item.GetTakesCount()
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _fades(item: TimelineItemApi, session: Session) -> dict[str, object] | None:
    """Fade lengths in frames; `None` before Resolve 21.1 (the API is not there)."""
    if session.version < FADES_MINIMUM:
        return None
    fades = item.GetFades() or {}
    return {"in": int(fades.get("FadeIn") or 0), "out": int(fades.get("FadeOut") or 0)}


def _takes(item: TimelineItemApi) -> list[dict[str, object]]:
    count = takes_count(item)
    if count == 0:
        return []
    selected = item.GetSelectedTakeIndex()
    rows: list[dict[str, object]] = []
    for index in range(1, count + 1):
        take = item.GetTakeByIndex(index) or {}
        rows.append(
            {
                "index": index,
                "selected": index == selected,
                "start": take.get("startFrame"),
                "end": take.get("endFrame"),
            }
        )
    return rows


def _media(item: TimelineItemApi) -> dict[str, object] | None:
    pool_item = item.GetMediaPoolItem()
    if pool_item is None:
        return None
    path = pool_item.GetClipProperty("File Path")
    return {"name": pool_item.GetName(), "path": str(path) if path else None}


def is_transition(item: TimelineItemApi) -> bool:
    """`GetType()` is `transition` (live, lower case) or `Transition` (fake)."""
    return str(item.GetType() or "").lower() == "transition"


def _num(value: object, kind: type) -> int | float | None:
    """`kind(value)`; a getter that returns None (transitions have no source) stays None."""
    return None if value is None else kind(value)


def _side(track: str, position: int, item: TimelineItemApi) -> dict[str, object]:
    return {
        "id": item.GetUniqueId(),
        "name": item.GetName(),
        "track_ref": f"{track}:{position}",
        "start": int(item.GetStart()),
        "end": int(item.GetEnd()),
    }


def _sides(timeline: TimelineApi, located: Located) -> dict[str, dict[str, object] | None]:
    """The clips a transition joins: the last clip starting before it and the first ending after."""
    track_type, index = BY_PREFIX[located.track[0]], int(located.track[1:])
    listed = timeline.GetItemListInTrack(track_type, index) or []
    clips = sorted(
        ((pos + 1, it) for pos, it in enumerate(listed) if not is_transition(it)),
        key=lambda pair: int(pair[1].GetStart()),
    )
    t_start, t_end = int(located.item.GetStart()), int(located.item.GetEnd())
    left = [pair for pair in clips if int(pair[1].GetStart()) < t_start]
    right = [pair for pair in clips if int(pair[1].GetEnd()) > t_end]
    return {
        "left": _side(located.track, *left[-1]) if left else None,
        "right": _side(located.track, *right[0]) if right else None,
    }


def run(inv: Invocation) -> Result:
    ref = inv.positionals["id"]
    timeline_name = str_arg(inv, "timeline")
    full = bool(inv.args.get("full"))
    session = inv.connect()
    timeline = session.require_timeline(timeline_name)
    located = resolve_item(timeline, ref)
    item = located.item
    start, duration = int(item.GetStart()), int(item.GetDuration())
    full_cmd = f"{inv.bin_name} item {shlex.quote(ref)}"
    if timeline_name:
        full_cmd += f" --timeline {shlex.quote(timeline_name)}"
    properties, note = _properties(item.GetProperties() or {}, full, f"{full_cmd} --full")
    data: dict[str, object] = {
        "timeline": timeline.GetName(),
        "id": item.GetUniqueId(),
        "name": item.GetName(),
        "type": item.GetType(),
        "track": located.track,
        "start": start,
        "end": int(item.GetEnd()),
        "duration": duration,
        "source_start_time": _num(item.GetSourceStartTime(), float),
        "source_end_time": _num(item.GetSourceEndTime(), float),
        "left_offset": _num(item.GetLeftOffset(), int),
        "right_offset": _num(item.GetRightOffset(), int),
        "enabled": bool(item.GetClipEnabled()),
        "color": item.GetClipColor() or None,
        "flags": list(item.GetFlagList() or []),
        "media": _media(item),
        "properties": properties,
    }
    if is_transition(item):
        data["sides"] = _sides(timeline, located)
    if note is not None:
        data["properties_note"] = note
    data["markers"] = _markers(item)
    fades = _fades(item, session)
    if fades is not None:
        data["fades"] = fades
    data["takes"] = _takes(item)
    data["fusion_comps"] = list(item.GetFusionCompNameList() or [])
    data["linked"] = _linked(item)
    return Result(data)
