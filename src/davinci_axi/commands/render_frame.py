"""`render frame`: export the current (or a chosen) frame as a still (R33, F15b)."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import cast

from davinci_axi.commands.project_io import tree_bytes
from davinci_axi.commands.render_common import RenderProject, render_project
from davinci_axi.commands.timeline_tracks import TIMECODE
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    refuse_existing,
    with_page,
)
from davinci_axi.output import collapse_home
from davinci_axi.resolve_types_edit import ResolveEdit, TimelineEdit
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

FORMATS = {"png": ".png", "jpg": ".jpg", "tif": ".tif"}
_SUFFIX_FORMAT = {".png": "png", ".jpg": "jpg", ".jpeg": "jpg", ".tif": "tif", ".tiff": "tif"}
_SIPS_FORMAT = {"jpg": "jpeg", "tif": "tiff", "png": "png"}
_DEFAULT_QUALITY = 80
STALE_PAGES = ("deliver",)  # pages whose viewer does not follow SetCurrentTimecode (F30)


def frame_to_timecode(frame: int, fps: float) -> str:
    """Non-drop timecode of an absolute timeline frame, counting `round(fps)` frames a second."""
    rate = round(fps)
    if rate < 1:
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot convert frame {frame}: bad frame rate {fps}",
            help=["Check the timeline frame rate with `davinci-axi timeline show`"],
        )
    if frame < 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--frame must be 0 or more, got {frame}",
            help=["Valid example: --frame 0"],
        )
    seconds, ff = divmod(frame, rate)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}:{ff:02d}"


def _check_position(inv: Invocation) -> tuple[str | None, int | None]:
    """The `--tc` and `--frame` flags, validated before any Resolve call."""
    code = cast("str | None", inv.args.get("tc"))
    frame = cast("int | None", inv.args.get("frame"))
    if code is not None and frame is not None:
        raise AxiError(
            "VALIDATION_ERROR",
            "--tc and --frame are mutually exclusive",
            help=["Pass one of them: --tc 01:00:02:00 or --frame 86448"],
        )
    if code is not None and not TIMECODE.match(code):
        raise AxiError(
            "VALIDATION_ERROR",
            f"invalid timecode {code}",
            help=["Valid timecode: HH:MM:SS:FF or HH:MM:SS;FF (drop frame), e.g. 01:00:02:00"],
        )
    if frame is not None and frame < 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--frame must be 0 or more, got {frame}",
            help=["Valid example: --frame 0"],
        )
    return code, frame


def _check_format(inv: Invocation, path: Path) -> tuple[str, int | None]:
    """The image format and JPEG quality, validated before any Resolve call."""
    named = cast("str | None", inv.args.get("format"))
    quality = cast("int | None", inv.args.get("quality"))
    by_suffix = _SUFFIX_FORMAT.get(path.suffix.lower())
    if named is not None and named not in FORMATS:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown --format {named}",
            help=[f"Valid formats: {', '.join(FORMATS)}"],
        )
    if named is None and by_suffix is None:
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot tell the image format from {path.name}",
            help=[f"Name the file .png, .jpg or .tif, or pass --format ({', '.join(FORMATS)})"],
        )
    if named is not None and by_suffix is not None and named != by_suffix:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--format {named} contradicts the {path.suffix} extension of {path.name}",
            help=["Rename the file to match, or drop --format"],
        )
    fmt = named or cast(str, by_suffix)
    if quality is not None:
        if not 1 <= quality <= 100:
            raise AxiError(
                "VALIDATION_ERROR",
                f"--quality must be 1-100, got {quality}",
                help=["Valid example: --quality 80"],
            )
        if fmt != "jpg":
            raise AxiError(
                "VALIDATION_ERROR",
                f"--quality applies to jpg only, not {fmt}",
                help=["Use a .jpg file or --format jpg"],
            )
    return fmt, quality


def convert_with_sips(src: Path, dest: Path, fmt: str, quality: int | None) -> None:
    """Convert `src` to `dest` with macOS `sips`; API_FAILED when it is missing or fails."""
    if shutil.which("sips") is None:
        raise AxiError(
            "API_FAILED",
            "sips is not available (macOS only); export PNG or drop --quality",
            help=["Name the file .png to skip the conversion"],
        )
    command = [
        "sips",
        "-s",
        "format",
        _SIPS_FORMAT.get(fmt, fmt),
        "-s",
        "formatOptions",
        str(quality or _DEFAULT_QUALITY),
        str(src),
        "--out",
        str(dest),
    ]
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip() or f"exit {done.returncode}"
        raise AxiError(
            "API_FAILED",
            f"sips could not convert the frame: {detail}",
            help=["Run the command by hand to see the full error: " + shlex.join(command)],
        )


def _position_playhead(timeline: TimelineEdit, code: str | None, frame: int | None) -> str | None:
    """The timecode to move the playhead to: `--tc` as given, `--frame` converted, else None."""
    if frame is not None:
        if ";" in timeline.GetStartTimecode():
            raise AxiError(
                "VALIDATION_ERROR",
                "--frame cannot convert on a drop-frame timeline",
                help=["Pass the position as --tc HH:MM:SS;FF instead"],
            )
        fps = float((timeline.GetSettings() or {}).get("timelineFrameRate") or 0)
        code = frame_to_timecode(frame, fps)
    return code


def _export(project: RenderProject, target: Path) -> bool:
    return bool(project.ExportCurrentFrameAsStill(str(target)))


def _export_or_fail(project: RenderProject, target: Path) -> None:
    target.unlink(missing_ok=True)
    if not _export(project, target):
        raise api_failed("ExportCurrentFrameAsStill", "could not export the current frame")


def run_frame(inv: Invocation) -> Result:
    path = Path(inv.positionals["file"]).expanduser().resolve()
    if path.is_dir():
        raise AxiError(
            "VALIDATION_ERROR",
            f"{collapse_home(path)} is a directory",
            help=["Pass a file path such as ~/frame.png"],
        )
    wanted_tc, wanted_frame = _check_position(inv)
    fmt, quality = _check_format(inv, path)
    refuse_existing(path, bool(inv.args.get("overwrite")))
    session = inv.connect()
    project = render_project(session)
    timeline = cast(TimelineEdit, session.require_timeline(None))
    page = cast(ResolveEdit, session.resolve).GetCurrentPage()
    left = page in STALE_PAGES
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f".{path.stem}.axi-tmp.png")
    converted = ""
    note = ""
    moved = False
    try:
        # F30: the Deliver viewer ignores SetCurrentTimecode, so leave it before positioning.
        with with_page(session, "edit") if left else nullcontext(False) as switched_out:
            code = _position_playhead(timeline, wanted_tc, wanted_frame)
            before = timeline.GetCurrentTimecode()
            if code is None:
                code = before or None
            # On a stale page the playhead is re-issued even when it already reads `code`.
            if code is not None and (left or before != code):
                if not timeline.SetCurrentTimecode(code):
                    raise api_failed("SetCurrentTimecode", f"could not move the playhead to {code}")
                moved = before != code
            with with_page(session, "color") as switched:
                if fmt == "jpg" and quality is not None:
                    _export_or_fail(project, scratch)
                    converted = "sips"
                elif not _export(project, path):
                    if fmt != "jpg":
                        raise api_failed(
                            "ExportCurrentFrameAsStill", "could not export the current frame"
                        )
                    _export_or_fail(project, scratch)
                    converted = "sips"
                    note = "Resolve refused .jpg; exported PNG and converted"
        if converted:
            convert_with_sips(scratch, path, fmt, quality)
    finally:
        scratch.unlink(missing_ok=True)
    if not path.exists():
        raise api_failed(
            "ExportCurrentFrameAsStill", f"Resolve reported success but wrote no {path.name}"
        )
    data: dict[str, object] = {
        "timeline": timeline.GetName(),
        "path": collapse_home(path),
        "bytes": tree_bytes(path),
        "format": fmt,
    }
    if quality is not None:
        data["quality"] = quality
    if converted:
        data["converted_with"] = converted
    if note:
        data["note"] = note
    if code is not None:
        data["timecode"] = code
    if moved:
        data["playhead_moved"] = True
    if left:
        data["left_page"] = page
    if switched or switched_out:
        data["page_restored"] = True
    return Result(data)


FRAME_SPEC = CommandSpec(
    name="frame",
    summary="Export the current frame of the current timeline as a still file",
    run=run_frame,
    positionals=(Positional("file", help="output image file"),),
    flags=(
        Flag("overwrite", "bool", False, help="replace the file if it exists"),
        Flag(
            "format",
            "str",
            None,
            choices=tuple(FORMATS),
            help="image format (default: from the extension); jpg is much smaller than "
            "Resolve's uncompressed PNG",
        ),
        Flag("quality", "int", None, help="JPEG quality 1-100 (uses macOS sips)"),
        Flag("tc", "str", None, help="move the playhead to this timecode first (HH:MM:SS:FF)"),
        Flag(
            "frame",
            "int",
            None,
            help="move the playhead to this absolute timeline frame first (as `items` prints)",
        ),
    ),
    examples=(
        "davinci-axi render frame ~/frame.png",
        "davinci-axi render frame ~/f.png --overwrite",
        "davinci-axi render frame ~/f.png --tc 01:00:41:24",
        "davinci-axi render frame ~/f.jpg --quality 85",
    ),
    reads_only=False,
    methods=(
        "Resolve.GetCurrentPage",
        "Resolve.OpenPage",
        "Project.ExportCurrentFrameAsStill",
        "Timeline.GetCurrentTimecode",
        "Timeline.SetCurrentTimecode",
        "Timeline.GetSettings",
        "Timeline.GetStartTimecode",
    ),
    notes=(
        "--tc/--frame move the playhead and leave it there; no snapshot (UI state)",
        "The result shows the timecode the still was taken at",
        "On the Deliver page the viewer does not follow the playhead (F30): the command opens "
        "the Edit page first, re-positions, exports from the Color page and returns to Deliver",
        "Resolve writes uncompressed PNGs (about 6 MB per 1080x1920 frame); use .jpg for checks",
        "--quality and a Resolve refusal of .jpg go through macOS sips; elsewhere export PNG",
    ),
)
