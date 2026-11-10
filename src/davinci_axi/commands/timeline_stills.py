"""`timeline stills`: one small JPEG per item (or per `--at` percentage) plus a manifest (U2).

No snapshot and no `--force`: it only moves the playhead and exports frames (UI state, like
`render frame`). The current timeline and the page are restored afterwards.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

from davinci_axi.commands.item_detail import Located, resolve_item, walk_items
from davinci_axi.commands.render_common import render_project
from davinci_axi.commands.stills_common import (
    STILL_FLAGS,
    StillRow,
    check_still_flags,
    compose_sheet,
    export_still,
    produced_name,
    still_path,
    stills_result,
    write_manifest,
)
from davinci_axi.commands.timeline_tracks import TIMELINE_FLAG, text_arg
from davinci_axi.errors import AxiError
from davinci_axi.mutate import refuse_existing, with_current_timeline, with_page
from davinci_axi.output import collapse_home
from davinci_axi.resolve_types_edit import TimelineEdit
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

_RATE = re.compile(r"\d+(?:\.\d+)?")


def _refuse_busy_dir(path: Path, overwrite: bool) -> None:
    """FILE_EXISTS when the folder already holds files and `--overwrite` was not given."""
    if path.is_file():
        raise AxiError(
            "VALIDATION_ERROR",
            f"{collapse_home(path)} is a file",
            help=["Pass a folder path such as ~/stills"],
        )
    if path.is_dir() and any(path.iterdir()) and not overwrite:
        raise AxiError(
            "FILE_EXISTS",
            f"{collapse_home(path)} already holds files",
            help=["Pass --overwrite to write into it, or choose another folder"],
        )


def _refs(inv: Invocation) -> list[str]:
    text = text_arg(inv, "items")
    if text is None:
        return []
    refs = [part.strip() for part in text.split(",")]
    if not all(refs) or len(set(refs)) != len(refs):
        raise AxiError(
            "VALIDATION_ERROR",
            "--items needs a comma list without empty parts or repeats",
            help=["Valid example: --items V1:2,V1:5"],
        )
    return refs


def _track(inv: Invocation, refs: list[str]) -> int | None:
    track = inv.args.get("track")
    if track is None:
        return None
    if refs:
        raise AxiError(
            "VALIDATION_ERROR",
            "--items and --track are mutually exclusive",
            help=["Valid example: --items V1:2,V1:5, or --track 1"],
        )
    if int(str(track)) < 1:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--track must be 1 or more, got {track}",
            help=["Valid example: --track 1"],
        )
    return int(str(track))


def _fps(session: Session, timeline: TimelineEdit) -> float:
    """The timeline's frame rate: its own settings, else the project's."""
    for settings in (timeline.GetSettings(), session.require_project().GetSettings()):
        found = _RATE.match(str((settings or {}).get("timelineFrameRate") or ""))
        if found:
            return float(found.group())
    raise AxiError(
        "API_FAILED",
        "Resolve reports no frame rate for this timeline",
        help=["Check the timeline with `davinci-axi timeline show`"],
    )


def _items(timeline: TimelineEdit, refs: list[str], track: int | None) -> list[Located]:
    if refs:
        found = [resolve_item(timeline, ref) for ref in refs]
        audio = [one.track for one in found if not one.track.startswith("V")]
        if audio:
            raise AxiError(
                "VALIDATION_ERROR",
                f"stills need video items, not {audio[0]}",
                help=["Name video items such as V1:2"],
            )
        return found
    return list(walk_items(timeline, ("video",), track or 1))


def run_timeline_stills(inv: Invocation) -> Result:
    ats, max_size, quality, columns = check_still_flags(inv.args)
    refs = _refs(inv)
    track = _track(inv, refs)
    target = Path(inv.positionals["dir"]).expanduser().resolve()
    overwrite = bool(inv.args.get("overwrite"))
    _refuse_busy_dir(target, overwrite)
    sheet_text = text_arg(inv, "sheet")
    sheet = Path(sheet_text).expanduser().resolve() if sheet_text else None
    if sheet is not None:
        refuse_existing(sheet, overwrite)
    session = inv.connect()
    project = render_project(session)
    timeline = cast(TimelineEdit, session.require_timeline(text_arg(inv, "timeline")))
    if ";" in timeline.GetStartTimecode():
        raise AxiError(
            "VALIDATION_ERROR",
            "stills cannot convert frames on a drop-frame timeline",
            help=["Use `render frame --tc HH:MM:SS;FF` for single frames"],
        )
    fps = _fps(session, timeline)
    items = [one for one in _items(timeline, refs, track) if one.item.GetDuration() > 0]
    rows: list[StillRow] = []
    extra: dict[str, object] = {"timeline": timeline.GetName(), "at": ",".join(map(str, ats))}
    with with_current_timeline(session, timeline) as restored:
        if restored:
            extra["timeline_restored"] = True
        with with_page(session, "color") as switched:
            if switched:
                extra["page_restored"] = True
            for index, located in enumerate(items, start=1):
                start = int(located.item.GetStart())
                length = int(located.item.GetDuration())
                for pct in ats:
                    frame = start + min(length - 1, round(length * pct / 100))
                    name = located.item.GetName()
                    dest = still_path(target, index, located.track, name, pct)
                    code = export_still(
                        session, project, timeline, frame, fps, dest, max_size=max_size,
                        quality=quality,
                    )  # fmt: skip
                    rows.append(
                        StillRow(
                            file=produced_name(dest),
                            item=f"{located.track}:{located.position}",
                            id=located.item.GetUniqueId(),
                            name=name,
                            frame=frame,
                            timecode=code,
                            pct=pct,
                        )
                    )
    if rows:
        write_manifest(target, rows)
        if sheet is not None:
            compose_sheet(rows, target, sheet, columns)
    return stills_result(target, rows, sheet, extra)


TIMELINE_STILLS_SPEC = CommandSpec(
    name="stills",
    summary="Export one small JPEG per timeline item (or per percentage) plus a manifest",
    run=run_timeline_stills,
    positionals=(Positional("dir", help="output folder (created if missing)"),),
    flags=(
        *STILL_FLAGS,
        TIMELINE_FLAG,
        Flag("items", "str", None, help="only these video items: V1:2,V1:5 (default: all of V1)"),
        Flag("track", "int", None, help="video track number instead of V1"),
    ),
    examples=(
        "davinci-axi timeline stills ~/sb",
        "davinci-axi timeline stills ~/sb --at 10,50,90 --items V1:2,V1:5",
        "davinci-axi timeline stills ~/sb --sheet ~/sb/sheet.jpg --columns 8",
    ),
    reads_only=False,
    methods=(
        "Resolve.GetCurrentPage",
        "Resolve.OpenPage",
        "Project.ExportCurrentFrameAsStill",
        "Project.GetCurrentTimeline",
        "Project.SetCurrentTimeline",
        "Project.GetSettings",
        "Timeline.GetName",
        "Timeline.GetSettings",
        "Timeline.GetStartTimecode",
        "Timeline.GetCurrentTimecode",
        "Timeline.SetCurrentTimecode",
        "Timeline.GetTrackCount",
        "Timeline.GetItemListInTrack",
        "TimelineItem.GetUniqueId",
        "TimelineItem.GetName",
        "TimelineItem.GetStart",
        "TimelineItem.GetDuration",
    ),
    notes=(
        "--at takes percentages of each item; the frame is the item's start plus that share",
        "Playhead is left at the last still; no snapshot (UI state)",
        "manifest.json lists the item id, frame and timecode of every still",
        "--sheet needs Pillow (pip install 'davinci-axi[sheet]'); without sips the stills stay PNG",
    ),
)
