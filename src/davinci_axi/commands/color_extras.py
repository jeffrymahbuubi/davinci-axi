"""`color cdl|cache|node-colors|lut|thumbnail`: the small colour verbs (U19, R35).

`cdl set`, `cache set` and `node-colors reset` change one item; CDL has no getter, so `cdl set`
verifies with the call's return value only (KTD23). `lut export` and `thumbnail` write a file and
change no project state, so they take `--overwrite` instead of `--force`, never snapshot and never
overwrite silently. `thumbnail` needs the Color page (KTD27, OQ15): it opens it with
`mutate.with_page`, restores it and reports `page_restored` (the R24 exception, because the caller
asked for the file). `lut refresh` only asks Resolve to rescan its LUT folders; there is no getter,
so it is not read back (KTD23).
"""

from __future__ import annotations

import base64
import binascii
import filecmp
import os
import re
import shlex
import shutil
import time
from pathlib import Path
from typing import Any, Protocol, cast

from davinci_axi.commands.color_common import (
    ColorProject,
    called_write,
    color_item,
    enum,
    item_write,
    node_number,
    parent,
    project_write,
    verb,
)
from davinci_axi.commands.color_targets import (
    CALLED_NOTE,
    ITEMS,
    GraphTarget,
    Step,
    called_step,
    check_node_each,
    open_targets,
    targets_write,
)
from davinci_axi.commands.item_markers import ITEM_FLAGS, ITEM_ID, Target, open_target
from davinci_axi.commands.items import TIMELINE_FLAG
from davinci_axi.commands.project_io import tree_bytes
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    check_snapshot_flags,
    parse_assignments,
    refuse_existing,
    with_current_timeline,
    with_page,
)
from davinci_axi.output import collapse_home, hint
from davinci_axi.paths import lut_dirs, lut_install_dir
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

LUT_SIZES = {
    "17": "EXPORT_LUT_17PTCUBE",
    "33": "EXPORT_LUT_33PTCUBE",
    "65": "EXPORT_LUT_65PTCUBE",
    "panasonic": "EXPORT_LUT_PANASONICVLUT",
}
CDL_KEYS = ("NodeIndex", "Slope", "Offset", "Power", "Saturation")
# Resolve 21.1.1 refuses a SetCDL that lacks any of these, so omitted keys are sent as neutral
CDL_NEUTRAL: dict[str, object] = {
    "Slope": "1 1 1", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": 1.0,
}  # fmt: skip
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
_TRIPLE = re.compile(f"{_NUMBER}(?: +{_NUMBER}){{2}}")
ASSIGNMENTS = Positional("assignments", help="NodeIndex=1,Slope='1 1 1',Saturation=0.8")
SWITCH = "Project.GetCurrentTimeline Project.SetCurrentTimeline"
THUMBNAIL_SETTLE_READS = 12  # extra reads after a page switch, ~3 s at the interval below
THUMBNAIL_SETTLE_INTERVAL = 0.25
OVERWRITE = Flag("overwrite", "bool", False, help="replace the file if it exists")
SUBDIR = re.compile(r"[A-Za-z0-9 _.-]+")
INSTALL_HELP = (
    "Run `davinci-axi color lut install <file> --force` to copy it in and refresh, "
    "then pass the printed path"
)


class ThumbnailTimeline(TimelineApi, Protocol):
    def GetCurrentClipThumbnailImage(self) -> dict[str, Any] | None: ...


def _file_path(inv: Invocation) -> Path:
    path = Path(inv.positionals["file"]).expanduser().resolve()
    if path.is_dir():
        raise AxiError(
            "VALIDATION_ERROR", f"{collapse_home(path)} is a directory", help=["Pass a file path"]
        )
    refuse_existing(path, bool(inv.args.get("overwrite")))
    return path


def _read_thumbnail(
    timeline: ThumbnailTimeline, switched: bool
) -> tuple[dict[str, Any] | None, int]:
    """Read the thumbnail; right after a page switch Resolve needs a moment (B1), so retry."""
    info = timeline.GetCurrentClipThumbnailImage()
    extra = 0
    while switched and not (info and info.get("data")) and extra < THUMBNAIL_SETTLE_READS:
        if THUMBNAIL_SETTLE_INTERVAL > 0:
            time.sleep(THUMBNAIL_SETTLE_INTERVAL)
        info = timeline.GetCurrentClipThumbnailImage()
        extra += 1
    return info, extra


def run_thumbnail(inv: Invocation) -> Result:
    path = _file_path(inv)
    session = inv.connect()
    timeline = cast(ThumbnailTimeline, session.require_timeline(None))
    with with_page(session, "color") as switched:
        info, settle_reads = _read_thumbnail(timeline, switched)
    if not info or not info.get("data"):
        err = api_failed("Timeline.GetCurrentClipThumbnailImage", "Resolve returned no image")
        err.help.append(
            "The Color page needs a moment after switching; retry, or run `page set color` first"
        )
        raise err
    try:
        raw = base64.b64decode(str(info["data"]), validate=True)
    except (binascii.Error, ValueError):
        raise api_failed("Timeline.GetCurrentClipThumbnailImage", "data is not base64") from None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    data: dict[str, object] = {"path": collapse_home(path), "bytes": len(raw)}
    for key in ("width", "height", "format"):
        data[key] = info.get(key)
    if switched:
        data["page_restored"] = True
    if settle_reads:
        data["settle_reads"] = settle_reads
    note = "The Color page was opened to read the thumbnail and then restored"
    return Result(data, [note] if switched else [])


def run_lut_export(inv: Invocation) -> Result:
    size = str(inv.args["size"])
    path = _file_path(inv)
    target = open_target(inv)
    export_type = enum(target.session, LUT_SIZES[size])
    path.parent.mkdir(parents=True, exist_ok=True)
    with with_current_timeline(target.session, target.timeline) as switched:
        ok = color_item(target).ExportLUT(export_type, str(path))
    if not ok:
        raise api_failed("TimelineItem.ExportLUT", f"could not export a LUT for {target.ref}")
    if not path.exists():
        raise api_failed(
            "TimelineItem.ExportLUT", f"Resolve reported success, wrote no {path.name}"
        )
    data: dict[str, object] = {"item": target.label, "size": size, "path": collapse_home(path)}
    data["bytes"] = tree_bytes(path)
    if switched:
        data["timeline_restored"] = True
    return Result(data)


def run_lut_refresh(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    session = inv.connect()
    project = cast(ColorProject, session.require_project())
    return project_write(
        inv,
        session,
        command="color lut refresh",
        words=["color", "lut", "refresh"],
        changes=[Change("called", False, True)],
        call=project.RefreshLUTList,
        read=lambda: {"called": True},
        fail="Project.RefreshLUTList",
        snapshot="none",
        extra={
            "read_back": "call result only: Resolve has no getter for this, so it is not read back"
        },
    )


def lut_path_warning(path: Path) -> str:
    """Empty when `path` is inside a LUT folder Resolve scans; else why SetLUT may refuse it."""
    dirs = lut_dirs()
    resolved = path.resolve()
    if any(resolved.is_relative_to(d.resolve()) for d in dirs):
        return ""
    shown = ", ".join(collapse_home(d) for d in dirs)
    return (
        f"{collapse_home(path)} is outside Resolve's LUT folders ({shown}); SetLUT accepts only "
        f"LUTs Resolve has discovered there. {INSTALL_HELP}"
    )


def _writable(folder: Path) -> bool:
    """Whether `folder`, or the nearest ancestor that exists, can be written to."""
    probe = folder
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return os.access(probe, os.W_OK)


def run_lut_install(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    src = Path(inv.positionals["file"]).expanduser().resolve()
    if not src.is_file():
        raise not_found("file", collapse_home(src), "Check the path of the LUT file")
    subdir = str(inv.args.get("subdir") or "")
    if subdir and (not SUBDIR.fullmatch(subdir) or subdir in (".", "..")):
        raise AxiError(
            "VALIDATION_ERROR",
            f"--subdir {subdir!r} must be a plain folder name",
            help=["Use letters, digits, spaces and . _ - only, like --subdir my-project"],
        )
    overwrite = bool(inv.args.get("overwrite"))
    dest = lut_install_dir() / subdir / src.name
    refuse_existing(dest, overwrite)
    session = inv.connect()
    project = cast(ColorProject, session.require_project())
    shown = collapse_home(dest)

    def call() -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        if project.RefreshLUTList():
            return True
        raise api_failed(
            "Project.RefreshLUTList",
            f"the file was copied to {shown} but Resolve did not refresh its LUT list",
        )

    def read() -> dict[str, object]:
        same = dest.is_file() and filecmp.cmp(src, dest, shallow=False)
        return {"installed": shown if same else None, "called": True}

    words = ["color", "lut", "install", inv.positionals["file"]]
    if subdir:
        words += ["--subdir", subdir]
    if overwrite:
        words.append("--overwrite")
    return project_write(
        inv,
        session,
        command="color lut install",
        words=words,
        changes=[Change("installed", None, shown), Change("called", False, True)],
        call=call,
        read=read,
        fail="Project.RefreshLUTList",
        snapshot="none",
        warning="" if _writable(dest.parent) else f"{collapse_home(dest.parent)} is not writable",
        extra={
            "lut_arg": f"{subdir}/{src.name}" if subdir else src.name,
            "bytes": src.stat().st_size,
            "read_back": "the copy is compared with the source; the refresh is the call result",
        },
        next_lines=[hint(f"color node lut set <target> 1 {shlex.quote(str(dest))}", "apply it")],
    )


def parse_cdl(text: str) -> dict[str, object]:
    """`NodeIndex=1,Slope=1 1 1` -> the CDL dict; every problem is VALIDATION_ERROR (exit 2)."""
    pairs = parse_assignments(text)
    unknown = [key for key in pairs if key not in CDL_KEYS]

    def bad(message: str) -> AxiError:
        help_line = "Valid example: NodeIndex=1,Slope='1.1 1 0.9',Saturation=0.8"
        return AxiError("VALIDATION_ERROR", message, help=[help_line])

    if unknown:
        raise bad(f"unknown CDL key {', '.join(unknown)} (valid: {', '.join(CDL_KEYS)})")
    if "NodeIndex" not in pairs or len(pairs) < 2:
        raise bad("a CDL needs NodeIndex and at least one of Slope, Offset, Power, Saturation")
    cdl: dict[str, object] = {"NodeIndex": node_number(pairs["NodeIndex"])}
    for key in ("Slope", "Offset", "Power"):
        if key in pairs:
            triple = " ".join(pairs[key].split())
            if not _TRIPLE.fullmatch(triple):
                raise bad(f"{key} needs three numbers separated by spaces, like '1 1 1'")
            cdl[key] = triple
    if "Saturation" in pairs:
        try:
            cdl["Saturation"] = float(pairs["Saturation"])
        except ValueError:
            raise bad(f"Saturation {pairs['Saturation']} is not a number") from None
    for key, neutral in CDL_NEUTRAL.items():
        cdl.setdefault(key, neutral)
    return cdl


def run_cdl_set(inv: Invocation) -> Result:
    given = parse_assignments(inv.positionals["assignments"])
    cdl = parse_cdl(inv.positionals["assignments"])
    neutral = [key for key in CDL_NEUTRAL if key not in given]
    targets = open_targets(inv, graphs=False)
    check_node_each(targets, int(cast(int, cdl["NodeIndex"])))

    def step(entry: GraphTarget) -> Step:
        item = color_item(cast(Target, entry.target))
        return called_step(targets, entry, lambda: item.SetCDL(cdl))

    return targets_write(
        inv,
        targets,
        command="color cdl set",
        words=["color", "cdl", "set", inv.positionals["target"], inv.positionals["assignments"]],
        step=step,
        fail="TimelineItem.SetCDL",
        extra={
            "read_back": CALLED_NOTE,
            "cdl": " ".join(f"{k}={v}" for k, v in cdl.items()),
            **({"neutral": f"{', '.join(neutral)} sent as neutral values"} if neutral else {}),
        },
    )


def run_cache_set(inv: Invocation) -> Result:
    state = inv.positionals["state"]
    if state not in ("on", "off"):
        raise AxiError("VALIDATION_ERROR", f"unknown state {state}", help=["Valid states: on, off"])
    enabled = state == "on"
    target = open_target(inv)
    item = color_item(target)
    before = bool(item.GetIsColorOutputCacheEnabled())
    return item_write(
        inv,
        target,
        command="color cache set",
        words=["color", "cache", "set", target.ref, state],
        changes=[Change("color_cache", before, enabled)] if before != enabled else [],
        call=lambda: item.SetColorOutputCache(enabled),
        read=lambda: {"color_cache": bool(item.GetIsColorOutputCacheEnabled())},
        fail="TimelineItem.SetColorOutputCache",
    )


def run_node_colors_reset(inv: Invocation) -> Result:
    target = open_target(inv)
    return called_write(
        inv,
        target,
        command="color node-colors reset",
        words=["color", "node-colors", "reset", target.ref],
        call=color_item(target).ResetAllNodeColors,
        fail="TimelineItem.ResetAllNodeColors",
    )


THUMBNAIL_SPEC = verb(
    "thumbnail",
    "Write the current clip's Color page thumbnail to a file (opens the Color page, restores it)",
    run_thumbnail,
    (Positional("file", help="output file (the image data exactly as Resolve returns it)"),),
    (OVERWRITE,),
    "davinci-axi color thumbnail ~/thumb.png",
    "Project.GetCurrentTimeline Timeline.GetCurrentClipThumbnailImage",
    disk=True,
)
LUT_INSTALL_SPEC = verb(
    "install",
    "Copy a LUT file into Resolve's LUT folder and refresh the list (no project state changes)",
    run_lut_install,
    (Positional("file", help="the .cube, .3dl or .vlut file to install"),),
    (
        *MUTATION_FLAGS,
        OVERWRITE,
        Flag("subdir", "str", None, help="folder inside the LUT folder, e.g. my-project"),
    ),
    "davinci-axi color lut install ~/luts/film.cube --subdir my-project",
    "Project.RefreshLUTList",
    notes=(
        "Resolve's master LUT folder may need the right permissions; the dry run warns if it "
        "is not writable.",
        "Set DAVINCI_AXI_LUT_DIRS (colon-separated) to name custom LUT folders: the first one "
        "is installed into.",
    ),
)
LUT_SPEC = parent(
    "lut",
    "Export an item's LUT, install a LUT file into Resolve's LUT folder, or refresh the LUT list",
    {
        "export": verb(
            "export",
            "Export a LUT of an item (files only, never overwrites)",
            run_lut_export,
            (ITEM_ID, Positional("file", help="output .cube or .vlut file")),
            (
                TIMELINE_FLAG,
                OVERWRITE,
                Flag("size", "str", None, required=True, choices=tuple(LUT_SIZES), help="LUT size"),
            ),
            "davinci-axi color lut export V1:1 ~/look.cube --size 33",
            "TimelineItem.ExportLUT Project.GetCurrentTimeline Project.SetCurrentTimeline",
            disk=True,
        ),
        "install": LUT_INSTALL_SPEC,
        "refresh": verb(
            "refresh",
            "Refresh Resolve's LUT list (changes no project state; not read back)",
            run_lut_refresh,
            (),
            MUTATION_FLAGS,
            "davinci-axi color lut refresh",
            "Project.RefreshLUTList",
        ),
    },
    "davinci-axi color lut export V1:1 ~/look.cube --size 33"
    "|davinci-axi color lut install ~/luts/film.cube --subdir my-project --force"
    "|davinci-axi color lut refresh --force",
)

CDL_SPEC = parent(
    "cdl",
    "Set the CDL values of a node",
    {
        "set": verb(
            "set",
            "Set Slope, Offset, Power and Saturation of one node of an item or item list",
            run_cdl_set,
            (ITEMS, ASSIGNMENTS),
            ITEM_FLAGS,
            "davinci-axi color cdl set V1:1 NodeIndex=1,Slope='1.1 1 0.9'"
            "|davinci-axi color cdl set V1:1,V1:3 NodeIndex=1,Saturation=0.8",
            f"{SWITCH} TimelineItem.GetNodeGraph TimelineItem.SetCDL Graph.GetNumNodes",
        )
    },
    "davinci-axi color cdl set V1:1 NodeIndex=1,Saturation=0.8",
    notes=(
        "SetCDL is item-only: shared graphs (timeline, group:<name>:pre|post) are not accepted.",
        "Resolve has no GetCDL; confirm a CDL with `color graph --item V1:1` (node tools) "
        "or export the node with `color lut export`.",
    ),
)
CACHE_SPEC = parent(
    "cache",
    "Turn the colour output cache of an item on or off",
    {
        "set": verb(
            "set",
            "Set the colour output cache of an item: on or off",
            run_cache_set,
            (ITEM_ID, Positional("state", help="on or off")),
            ITEM_FLAGS,
            "davinci-axi color cache set V1:1 on",
            f"{SWITCH} TimelineItem.GetIsColorOutputCacheEnabled TimelineItem.SetColorOutputCache",
        )
    },
    "davinci-axi color cache set V1:1 on --force",
)
NODE_COLORS_SPEC = parent(
    "node-colors",
    "Reset the node colours of an item",
    {
        "reset": verb(
            "reset",
            "Reset all node colours of an item's active version",
            run_node_colors_reset,
            (ITEM_ID,),
            ITEM_FLAGS,
            "davinci-axi color node-colors reset V1:1",
            f"{SWITCH} TimelineItem.ResetAllNodeColors",
        )
    },
    "davinci-axi color node-colors reset V1:1 --force",
)

SUBCOMMANDS: dict[str, CommandSpec] = {
    "cdl": CDL_SPEC,
    "cache": CACHE_SPEC,
    "node-colors": NODE_COLORS_SPEC,
    "lut": LUT_SPEC,
    "thumbnail": THUMBNAIL_SPEC,
}
