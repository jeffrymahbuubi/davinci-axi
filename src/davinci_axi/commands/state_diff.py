"""Compare two project-state dumps (`dump.py`) or two timeline dumps into change rows (U2).

Pure data in, pure data out: no Resolve call and no file read. `snapshot diff` feeds it a saved
`state.json` and a live dump, `timeline diff` two live timeline dumps. Items are matched by unique
id (`match="id"`, a snapshot against the same project) or by `(track, position)`
(`match="position"`, two timelines whose items got fresh ids when one was duplicated).
A key missing or `None` on either side is unknown and never a change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from davinci_axi.commands.item_detail import PREFIX
from davinci_axi.errors import AxiError
from davinci_axi.output import hint
from davinci_axi.spec import Result

ITEM_FIELDS = (
    "name",
    "track",
    "start",
    "end",
    "duration",
    "source_start",
    "source_end",
    "enabled",
    "color",
    "media",
    "fade_in",
    "fade_out",
    "volume",
    "zoom",
    "pan",
    "tilt",
)
TRACK_FIELDS = ("name", "enabled", "locked")
UNKNOWABLE = frozenset(
    ("fade_in", "fade_out", "volume", "zoom", "pan", "tilt", "enabled", "locked")
)
SUMMARY_KEYS = (
    "timelines_added",
    "timelines_removed",
    "settings_changed",
    "items_added",
    "items_removed",
    "items_moved",
    "items_changed",
    "fields_changed",
    "markers_changed",
    "tracks_changed",
)
_ITEM_COUNTER = {
    "added": "items_added",
    "removed": "items_removed",
    "moved": "items_moved",
}


@dataclass(frozen=True)
class Row:
    kind: str
    where: str
    field: str
    before: object
    after: object


@dataclass
class Delta:
    summary: dict[str, int | str]
    rows: list[Row]


def _empty_summary() -> dict[str, int | str]:
    return {key: 0 for key in SUMMARY_KEYS}


def _bump(summary: dict[str, int | str], key: str, by: int = 1) -> None:
    current = summary.get(key)
    summary[key] = (current if isinstance(current, int) else 0) + by


def _flat_items(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    """Every item of the dump with its track label and 1-based position on that track."""
    flat: list[dict[str, Any]] = []
    for track in timeline.get("tracks") or []:
        label = f"{PREFIX.get(str(track.get('type')), '?')}{track.get('index')}"
        for position, item in enumerate(track.get("items") or [], start=1):
            media = item.get("media")
            flat.append(
                {
                    **item,
                    "track": label,
                    "position": position,
                    "media": media.get("name") if isinstance(media, dict) else None,
                }
            )
    return flat


def _where(item: dict[str, Any]) -> str:
    return f"{item['track']}:{item['position']} {item.get('name')}"


def comparable(prior: dict[str, Any], item: dict[str, Any], field: str) -> bool:
    """True when `field` is present and not `None` on both sides (else it is unknown)."""
    return prior.get(field) is not None and item.get(field) is not None


def differs(prior: dict[str, Any], item: dict[str, Any], field: str) -> bool:
    """A real change of `field`; numbers within 1e-6 count as equal.

    The fields that may be unknown (`UNKNOWABLE`) are compared only when `comparable`; the rest
    compare plainly, so a clip losing its media is still a change.
    """
    if field in UNKNOWABLE and not comparable(prior, item, field):
        return False
    a, b = prior[field], item[field]
    numbers = (int, float)
    if isinstance(a, numbers) and isinstance(b, numbers) and not isinstance(a, bool):
        return abs(a - b) > 1e-6
    return bool(a != b)


def _item_key(item: dict[str, Any], match: str) -> object:
    return item.get("id") if match == "id" else (item["track"], item["position"])


def _item_rows(before: dict[str, Any], after: dict[str, Any], match: str) -> list[Row]:
    old = {_item_key(i, match): i for i in _flat_items(before)}
    new = {_item_key(i, match): i for i in _flat_items(after)}
    rows = [
        Row("removed", _where(i), "item", i.get("name"), None)
        for key, i in old.items()
        if key not in new
    ]
    for key, item in new.items():
        prior = old.get(key)
        if prior is None:
            rows.append(Row("added", _where(item), "item", None, item.get("name")))
            continue
        where = _where(item)
        differing = [f for f in ITEM_FIELDS if differs(prior, item, f)]
        if (
            "start" in differing
            and "end" in differing
            and prior.get("duration") == item.get("duration")
        ):
            rows.append(Row("moved", where, "start", prior.get("start"), item.get("start")))
            differing = [f for f in differing if f not in ("start", "end")]
        rows += [Row("changed", where, f, prior.get(f), item.get(f)) for f in differing]
    return rows


def _marker_where(marker: dict[str, Any], frame: object) -> str:
    return f"marker {marker.get('name') or ''} @{frame}".replace("  ", " ")


def _marker_rows(before: dict[str, Any], after: dict[str, Any]) -> list[Row]:
    old = {m.get("frame"): m for m in before.get("markers") or []}
    new = {m.get("frame"): m for m in after.get("markers") or []}
    rows = [
        Row("marker", _marker_where(m, frame), "frame", frame, None)
        for frame, m in old.items()
        if frame not in new
    ]
    for frame, marker in new.items():
        prior = old.get(frame)
        if prior is None:
            rows.append(Row("marker", _marker_where(marker, frame), "frame", None, frame))
            continue
        keys = sorted((set(prior) | set(marker)) - {"frame"})
        rows += [
            Row("marker", _marker_where(marker, frame), k, prior.get(k), marker.get(k))
            for k in keys
            if prior.get(k) != marker.get(k)
        ]
    return rows


def _by_type(timeline: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for track in timeline.get("tracks") or []:
        grouped.setdefault(str(track.get("type")), []).append(track)
    return grouped


def _track_rows(before: dict[str, Any], after: dict[str, Any]) -> list[Row]:
    old, new = _by_type(before), _by_type(after)
    rows: list[Row] = []
    for kind in sorted(set(old) | set(new)):
        a, b = old.get(kind, []), new.get(kind, [])
        if len(a) != len(b):
            rows.append(Row("track", f"{kind} tracks", "count", len(a), len(b)))
        for prior, track in zip(a, b, strict=False):
            where = f"{PREFIX.get(kind, '?')}{track.get('index')}"
            rows += [
                Row("track", where, f, prior.get(f), track.get(f))
                for f in TRACK_FIELDS
                if differs(prior, track, f)
            ]
    return rows


def _setting_rows(
    before: dict[str, Any] | None, after: dict[str, Any] | None, where: str
) -> list[Row]:
    old, new = before or {}, after or {}
    return [
        Row("setting", where, str(key), old.get(key), new.get(key))
        for key in sorted(set(old) | set(new), key=str)
        if old.get(key) != new.get(key)
    ]


def _count_rows(summary: dict[str, int | str], rows: list[Row]) -> None:
    for row in rows:
        if row.kind in _ITEM_COUNTER:
            _bump(summary, _ITEM_COUNTER[row.kind])
        elif row.kind == "changed":
            _bump(summary, "fields_changed")
        elif row.kind == "marker":
            _bump(summary, "markers_changed")
        elif row.kind == "track":
            _bump(summary, "tracks_changed")
        elif row.kind == "setting":
            _bump(summary, "settings_changed")
    _bump(summary, "items_changed", len({r.where for r in rows if r.kind == "changed"}))


def _length(timeline: dict[str, Any]) -> int:
    return int(timeline.get("end_frame") or 0) - int(timeline.get("start_frame") or 0)


def diff_timeline(
    before: dict[str, Any], after: dict[str, Any], *, match: Literal["id", "position"]
) -> Delta:
    """Item, marker, track and settings differences between two timeline dumps."""
    rows = [
        *_item_rows(before, after, match),
        *_marker_rows(before, after),
        *_track_rows(before, after),
        *_setting_rows(before.get("settings"), after.get("settings"), "timeline settings"),
    ]
    summary = _empty_summary()
    _count_rows(summary, rows)
    if _length(before) != _length(after):
        summary["frames"] = f"{_length(before)} → {_length(after)}"
    return Delta(summary, rows)


def _timelines(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    named: dict[str, dict[str, Any]] = {}
    for timeline in state.get("timelines") or []:
        named.setdefault(str(timeline.get("name")), timeline)
    return named


def diff_project(before: dict[str, Any], after: dict[str, Any], *, timeline: str | None) -> Delta:
    """Project settings, added/removed timelines and the item diff of every shared timeline."""
    old, new = _timelines(before), _timelines(after)
    if timeline is not None and timeline not in old and timeline not in new:
        names = ", ".join(sorted(set(old) | set(new))) or "(none)"
        raise AxiError(
            "NOT_FOUND", f"timeline {timeline} is in neither state", help=[f"Timelines: {names}"]
        )
    rows = _setting_rows(before.get("settings"), after.get("settings"), "project")
    if before.get("current_timeline") != after.get("current_timeline"):
        was, now = before.get("current_timeline"), after.get("current_timeline")
        rows.append(Row("setting", "project", "current_timeline", was, now))
    summary = _empty_summary()
    _count_rows(summary, rows)
    for name in old:
        if name not in new:
            rows.append(Row("removed", f"timeline {name}", "timeline", name, None))
            _bump(summary, "timelines_removed")
    for name in new:
        if name not in old:
            rows.append(Row("added", f"timeline {name}", "timeline", None, name))
            _bump(summary, "timelines_added")
    for name, tl_after in new.items():
        tl_before = old.get(name)
        if tl_before is None or (timeline is not None and name != timeline):
            continue
        delta = diff_timeline(tl_before, tl_after, match="id")
        for key in SUMMARY_KEYS:
            value = delta.summary.get(key)
            _bump(summary, key, value if isinstance(value, int) else 0)
        if "frames" in delta.summary:
            summary[f"frames[{name}]"] = delta.summary["frames"]
        rows += [Row(r.kind, f"{name}: {r.where}", r.field, r.before, r.after) for r in delta.rows]
    return Delta(summary, rows)


def _as_dict(row: Row) -> dict[str, object]:
    return {
        "kind": row.kind,
        "where": row.where,
        "field": row.field,
        "before": row.before,
        "after": row.after,
    }


def delta_result(delta: Delta, *, limit: int, full_cmd: str, header: dict[str, Any]) -> Result:
    """The summary plus `changes[]`, cut at `limit` with the exact `--full` command when cut."""
    shown = delta.rows[:limit]
    data: dict[str, object] = {**header, **delta.summary}
    help_lines: list[str] = []
    if shown:
        data["changes"] = [_as_dict(r) for r in shown]
        if len(delta.rows) > len(shown):
            data["note"] = f"showing {len(shown)} of {len(delta.rows)} changes"
            help_lines.append(hint(full_cmd, f"see all {len(delta.rows)} changes"))
    else:
        data["changes"] = f"no differences between {header.get('before')} and {header.get('after')}"
    return Result(data, help_lines)
