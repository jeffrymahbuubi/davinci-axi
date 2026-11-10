"""`timeline marker add --edl`: many timeline markers from a row file, under one snapshot (R-D1).

Each row is a `frame` (offset from the timeline start, as the single add) plus optional `color`,
`name`, `note`, `duration` and `custom` (or `custom_data`) columns. A frame that already holds a
marker is not written; it is listed under `skipped` (the single add errors, a batch reports). A
refusal from `AddMarker` on some rows drops them from the plan and warns; on every row it is
API_FAILED. `MARKER_ADD_EDL_SPEC` is the single add with `--frame` optional and the `--edl` flag;
the single path stays `timeline_tracks.run_marker_add`. The lead splices the spec into
`timeline_extra`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from davinci_axi import markers
from davinci_axi.commands import timeline_tracks
from davinci_axi.commands.rowfile import read_rows
from davinci_axi.commands.timeline_tracks import (
    TIMELINE_FLAG,
    context_words,
    target_timeline,
    text_arg,
)
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.markers import MARKER_COLORS
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.output import collapse_home, hint
from davinci_axi.spec import Flag, Invocation, Result

EDL_FLAG = Flag(
    "edl",
    "str",
    None,
    help="JSON or CSV rows: frame plus color, name, note, duration, custom (or custom_data)",
)
ROW_COLUMNS = ("color", "name", "note", "duration", "custom", "custom_data")
DEFAULT_COLOR = "Blue"
SHOWN_REFUSED = 5

SINGLE_FLAGS = ("frame", "name", "note", "custom_data")


@dataclass(frozen=True)
class MarkerRow:
    """One validated row: the frame is an offset from the timeline start."""

    frame: int
    color: str
    name: str
    note: str
    duration: int
    custom: str

    @property
    def label(self) -> str:
        return f"{self.color} {self.name}".rstrip()


def _bad(message: str, *help_lines: str) -> AxiError:
    return AxiError("VALIDATION_ERROR", message, help=list(help_lines))


def _whole(n: int, name: str, text: str, minimum: int) -> int:
    try:
        value = int(text)
    except ValueError:
        value = minimum - 1
    if value < minimum:
        raise _bad(
            f"row {n}: {name} must be a whole number of {minimum} or more, got {text}",
            f"Valid example: {name}={minimum}",
        )
    return value


def parse_rows(raw: list[dict[str, str]]) -> list[MarkerRow]:
    """Validate the cells of every row; the frame offsets must differ."""
    rows: list[MarkerRow] = []
    first: dict[int, int] = {}
    for n, row in enumerate(raw, 1):
        frame = _whole(n, "frame", row["frame"], 0)
        color = row.get("color", DEFAULT_COLOR)
        if color not in MARKER_COLORS:
            raise _bad(
                f"row {n}: {color} is not a marker colour",
                f"Valid colours: {', '.join(MARKER_COLORS)}",
            )
        if frame in first:
            raise _bad(
                f"row {n}: {frame} already appears in row {first[frame]}",
                "One row per frame: a frame holds one marker",
            )
        first[frame] = n
        duration = _whole(n, "duration", row["duration"], 1) if "duration" in row else 1
        if "custom" in row and "custom_data" in row:
            raise _bad(
                f"row {n}: give custom or custom_data, not both",
                "Keep one column; both are the marker's custom data",
            )
        custom = row.get("custom", row.get("custom_data", ""))
        rows.append(
            MarkerRow(
                frame,
                color,
                row.get("name", ""),
                row.get("note", ""),
                duration,
                custom,
            )
        )
    return rows


def _check_exclusive(inv: Invocation) -> None:
    given = [f"--{key.replace('_', '-')}" for key in SINGLE_FLAGS if _given(inv, key)]
    if inv.args.get("color") not in (None, "", DEFAULT_COLOR):
        given.append("--color")
    if inv.args.get("duration") not in (None, 1):
        given.append("--duration")
    if given:
        raise _bad(
            f"use --edl or the marker flags, not both (got --edl with {', '.join(given)})",
            "One marker: davinci-axi timeline marker add --frame 10 --color Green --name Pick",
            "Many markers: davinci-axi timeline marker add --edl rows.csv (put color, name, "
            "note in the file)",
        )


def _given(inv: Invocation, key: str) -> bool:
    return inv.args.get(key) not in (None, "")


def run_add_edl(inv: Invocation) -> Result:
    _check_exclusive(inv)
    check_snapshot_flags(inv)
    edl = text_arg(inv, "edl") or ""
    path = Path(edl).expanduser().resolve()
    rows = parse_rows(read_rows(path, key="frame", allowed=("frame", *ROW_COLUMNS)))
    session = inv.connect()
    timeline = target_timeline(session, inv)
    length = timeline.GetEndFrame() - timeline.GetStartFrame()
    for n, row in enumerate(rows, 1):
        if row.frame > length:
            raise _bad(
                f"row {n}: frame {row.frame} is past the end ({length} frames)",
                "Marker frames are offsets from the start of the timeline",
            )
    present = timeline.GetMarkers()
    skipped: list[dict[str, object]] = []
    kept: list[MarkerRow] = []
    for row in rows:
        old = present.get(row.frame)
        if old is None:
            kept.append(row)
        else:
            label = f"{old.get('color', '')} {old.get('name', '')}".rstrip()
            skipped.append({"frame": row.frame, "existing": label})
    colors = list(dict.fromkeys(row.color for row in kept))
    plan = MutationPlan(
        command="timeline marker add",
        target=f"{len(kept)} markers in timeline {timeline.GetName()}",
        changes=[Change(f"marker@{row.frame}", None, row.label) for row in kept],
        force_cmd=force_cmd(inv, "timeline", "marker", "add", "--edl", edl, *context_words(inv)),
        extra={
            "rows": len(rows),
            "written": 0,
            "skipped": skipped,
            "file": collapse_home(path),
        },
        next=[
            *(
                hint(
                    f"timeline marker delete --color {color} --force",
                    f"remove the {color} markers again (every {color} marker, not only this batch)",
                )
                for color in colors[:3]
            ),
            hint("items", "list the items"),
        ],
    )
    if not kept:
        plan.extra["note"] = "every frame already has a marker"
    done: list[MarkerRow] = []

    def apply() -> None:
        refused: list[MarkerRow] = []
        for row in kept:
            if timeline.AddMarker(
                row.frame, row.color, row.name, row.note, row.duration, row.custom
            ):
                done.append(row)
            else:
                refused.append(row)
        plan.extra["written"] = len(done)
        if not refused:
            return
        if not done:
            raise api_failed(
                "AddMarker", f"no marker was added (first refused: {refused[0].frame})"
            )
        gone = {f"marker@{row.frame}" for row in refused}
        plan.changes[:] = [c for c in plan.changes if c.field not in gone]
        frames = ", ".join(str(row.frame) for row in refused[:SHOWN_REFUSED])
        more = ", ..." if len(refused) > SHOWN_REFUSED else ""
        plan.warning = f"{len(refused)} row(s) refused by AddMarker: {frames}{more}"

    def verify() -> dict[str, object]:
        live = timeline.GetMarkers()
        out: dict[str, object] = {}
        for row in done:
            found = live.get(row.frame)
            out[f"marker@{row.frame}"] = (
                f"{found.get('color', '')} {found.get('name', '')}".rstrip() if found else None
            )
        return out

    return run_mutation(inv, session, plan, apply, verify)


def run_add_or_edl(inv: Invocation) -> Result:
    if text_arg(inv, "edl") is not None:
        return run_add_edl(inv)
    if inv.args.get("frame") is None:
        raise _bad(
            "pass --frame for one marker, or --edl for a row file",
            "One marker: davinci-axi timeline marker add --frame 10 --color Green --name Pick",
            "Many markers: davinci-axi timeline marker add --edl rows.csv",
        )
    return timeline_tracks.run_marker_add(inv)


_SINGLE = timeline_tracks.MARKER_SPEC.subcommands["add"]
MARKER_ADD_EDL_SPEC = replace(
    _SINGLE,
    summary="Add a marker at a frame offset from the timeline start, or many from a row file",
    run=run_add_or_edl,
    flags=(
        EDL_FLAG,
        *(replace(f, required=False) if f.name == "frame" else f for f in markers.MARKER_ADD_FLAGS),
        TIMELINE_FLAG,
        *MUTATION_FLAGS,
    ),
    examples=(*_SINGLE.examples, "davinci-axi timeline marker add --edl gaps.csv --force"),
    notes=(
        *_SINGLE.notes,
        "--edl: one snapshot for many markers; rows whose frame already holds a marker are "
        "listed under skipped and left alone; columns frame (offset from the timeline start), "
        "color, name, note, duration, custom (or custom_data)",
    ),
)
