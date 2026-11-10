"""The stills engine shared by `timeline stills` and `media stills` (U2, U3).

Export the current frame at a timeline position, shrink it to a small JPEG with macOS `sips`,
write `manifest.json`, and optionally lay the stills out as a contact sheet (Pillow, else ffmpeg).
Callers own the page switch (`with_page(session, "color")` around the loop) and the
current-timeline switch.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from davinci_axi.commands.project_io import tree_bytes
from davinci_axi.commands.render_common import RenderProject
from davinci_axi.commands.render_frame import frame_to_timecode
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.output import collapse_home, empty_state
from davinci_axi.resolve_types_edit import TimelineEdit
from davinci_axi.session import Session
from davinci_axi.spec import Flag, Result

DEFAULT_AT = "50"
MIN_MAX_SIZE = 64
MANIFEST = "manifest.json"
SIPS_NOTE = "sips not available: full-size PNGs"
SHOWN_FILES = 6
CELL_WIDTH = 320
LABEL_HEIGHT = 24
TMP_SUFFIX = ".axi-tmp.png"
FFMPEG_SHEET_NOTE = "contact sheet made with ffmpeg: no labels; manifest.json has item and timecode"

STILL_FLAGS: tuple[Flag, ...] = (
    Flag("at", "str", DEFAULT_AT, help="positions inside each item, in percent: 10,50,90"),
    Flag("max-size", "int", 480, help="longest edge of each JPEG in pixels (at least 64)"),
    Flag("quality", "int", 60, help="JPEG quality 1-100"),
    Flag(
        "sheet",
        "str",
        None,
        help="also write a contact sheet image (needs Pillow, or ffmpeg on PATH)",
    ),
    Flag("columns", "int", 6, help="contact sheet columns"),
    Flag("overwrite", "bool", False, help="write into a folder that already holds files"),
)


@dataclass(frozen=True)
class StillRow:
    """One exported still: where it came from and where on the timeline it was taken."""

    file: str
    item: str
    id: str
    name: str
    frame: int
    timecode: str
    pct: int


def parse_at(text: str) -> list[int]:
    """`10,50,90` as `[10, 50, 90]`: whole percentages 0-100, no repeats."""
    found: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part.isdigit() or not 0 <= int(part) <= 100:
            raise AxiError(
                "VALIDATION_ERROR",
                f"--at needs whole percentages from 0 to 100, got {part or 'an empty part'!r}",
                help=["Valid example: --at 10,50,90"],
            )
        value = int(part)
        if value in found:
            raise AxiError(
                "VALIDATION_ERROR",
                f"--at repeats {value}",
                help=["Name each percentage once: --at 10,50,90"],
            )
        found.append(value)
    return found


def _has_pillow() -> bool:
    return importlib.util.find_spec("PIL") is not None


def check_sheet_available(sheet: str | None) -> None:
    """NOT_AVAILABLE when `--sheet` is asked for and neither Pillow nor ffmpeg is available."""
    if sheet is None or _has_pillow() or shutil.which("ffmpeg") is not None:
        return
    raise AxiError(
        "NOT_AVAILABLE",
        "--sheet needs Pillow or ffmpeg",
        help=[
            "Install it: uv tool install --with pillow ... or pip install 'davinci-axi[sheet]'",
            "Or install ffmpeg (brew install ffmpeg)",
            "Or drop --sheet; the stills and manifest.json do not need it",
        ],
    )


def check_still_flags(args: dict[str, object]) -> tuple[list[int], int, int, int]:
    """Validate the shared flags; returns `(at, max_size, quality, columns)`."""
    ats = parse_at(str(args.get("at") or DEFAULT_AT))
    max_size, quality, columns = (
        int(str(args.get(key))) for key in ("max_size", "quality", "columns")
    )
    if max_size < MIN_MAX_SIZE:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--max-size must be {MIN_MAX_SIZE} or more",
            help=[f"Valid example: --max-size {MIN_MAX_SIZE * 8}"],
        )
    if not 1 <= quality <= 100:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--quality must be 1-100, got {quality}",
            help=["Valid example: --quality 80"],
        )
    if columns < 1:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--columns must be 1 or more, got {columns}",
            help=["Valid example: --columns 3"],
        )
    sheet = args.get("sheet")
    check_sheet_available(sheet if isinstance(sheet, str) else None)
    return ats, max_size, quality, columns


def still_path(dir: Path, index: int, track: str, name: str, pct: int) -> Path:
    """`<dir>/<NN>-<track>-<name>-<pct>.jpg` with the name cut down to `[A-Za-z0-9._-]`."""
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "clip"
    return dir / f"{index:02d}-{track}-{slug}-{pct}.jpg"


def shrink_with_sips(src: Path, dest: Path, *, max_size: int, quality: int) -> None:
    """Shrink `src` to a JPEG no longer than `max_size` pixels with macOS `sips`."""
    command = [
        "sips",
        "-s",
        "format",
        "jpeg",
        "-s",
        "formatOptions",
        str(quality),
        "-Z",
        str(max_size),
        str(src),
        "--out",
        str(dest),
    ]
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip() or f"exit {done.returncode}"
        raise AxiError(
            "API_FAILED",
            f"sips could not convert the still: {detail}",
            help=["Run the command by hand to see the full error: " + shlex.join(command)],
        )


def export_still(
    session: Session,
    project: RenderProject,
    timeline: TimelineEdit,
    frame: int,
    fps: float,
    dest: Path,
    *,
    max_size: int,
    quality: int,
) -> str:
    """Move the playhead to `frame`, export it and shrink it to `dest`; returns the timecode.

    Without `sips` the full-size PNG is kept as `dest` with a `.png` suffix instead.
    """
    del session  # the caller owns the page and timeline switches
    code = frame_to_timecode(frame, fps)
    if not timeline.SetCurrentTimecode(code):
        raise api_failed("SetCurrentTimecode", f"could not move the playhead to {code}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(TMP_SUFFIX)
    tmp.unlink(missing_ok=True)
    try:
        if not project.ExportCurrentFrameAsStill(str(tmp)):
            raise api_failed("ExportCurrentFrameAsStill", f"could not export the frame at {code}")
        if shutil.which("sips") is None:
            tmp.replace(dest.with_suffix(".png"))
        else:
            shrink_with_sips(tmp, dest, max_size=max_size, quality=quality)
    finally:
        tmp.unlink(missing_ok=True)
    return code


def produced_name(dest: Path) -> str:
    """The file name `export_still` left for `dest`: the JPEG, or the PNG when sips is missing."""
    return dest.name if dest.exists() else dest.with_suffix(".png").name


def write_manifest(dir: Path, rows: list[StillRow]) -> Path:
    path = dir / MANIFEST
    path.write_text(json.dumps([asdict(row) for row in rows], indent=2) + "\n", encoding="utf-8")
    return path


def _compose_with_pillow(rows: list[StillRow], dir: Path, sheet: Path, columns: int) -> None:
    """Lay the stills out `columns` wide, each under a `<item> <timecode>` label (needs Pillow)."""
    from PIL import Image, ImageDraw

    tiles = []
    for row in rows:
        with Image.open(dir / row.file) as picture:
            thumb = picture.convert("RGB")
        thumb.thumbnail((CELL_WIDTH, CELL_WIDTH))
        tiles.append((thumb, f"{row.item} {row.timecode}"))
    if not tiles:
        raise AxiError(
            "VALIDATION_ERROR",
            "there are no stills to put on a sheet",
            help=["Grab stills first with `davinci-axi timeline stills <dir> --at 10,50,90`"],
        )
    cell_w = max(tile.width for tile, _ in tiles)
    cell_h = max(tile.height for tile, _ in tiles) + LABEL_HEIGHT
    grid = -(-len(tiles) // columns)
    canvas = Image.new("RGB", (cell_w * min(columns, len(tiles)), cell_h * grid), "black")
    draw = ImageDraw.Draw(canvas)
    for n, (tile, label) in enumerate(tiles):
        x, y = (n % columns) * cell_w, (n // columns) * cell_h
        canvas.paste(tile, (x, y))
        draw.text((x + 4, y + tile.height + 6), label, fill="white")
    sheet.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(sheet, quality=85)


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_HEADER_BYTES = 64 * 1024


def _jpeg_size(head: bytes) -> tuple[int, int] | None:
    """Width and height from the first start-of-frame marker of a JPEG, or None."""
    pos = 2
    while pos + 4 <= len(head):
        if head[pos] != 0xFF:
            return None
        marker = head[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD9:  # markers without a length
            pos += 2
            continue
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if pos + 9 > len(head):
                return None
            height = int.from_bytes(head[pos + 5 : pos + 7], "big")
            width = int.from_bytes(head[pos + 7 : pos + 9], "big")
            return (width, height) if width and height else None
        pos += 2 + int.from_bytes(head[pos + 2 : pos + 4], "big")
    return None


def image_size(path: Path) -> tuple[int, int] | None:
    """`(width, height)` of a PNG or JPEG from its header, or None when unreadable."""
    try:
        with path.open("rb") as handle:
            head = handle.read(_HEADER_BYTES)
    except OSError:
        return None
    if head.startswith(_PNG_MAGIC) and len(head) >= 24:
        width = int.from_bytes(head[16:20], "big")
        height = int.from_bytes(head[20:24], "big")
        return (width, height) if width and height else None
    if head.startswith(b"\xff\xd8"):
        return _jpeg_size(head)
    return None


def sheet_cell(rows: list[StillRow], dir: Path) -> tuple[int, int]:
    """The ffmpeg cell size: `CELL_WIDTH` wide, tall as the first readable still's aspect."""
    for row in rows:
        size = image_size(dir / row.file)
        if size is not None:
            height = round(CELL_WIDTH * size[1] / size[0])
            return CELL_WIDTH, max(2, height + height % 2)
    return CELL_WIDTH, CELL_WIDTH


def compose_sheet(rows: list[StillRow], dir: Path, sheet: Path, columns: int) -> None:
    """Make the contact sheet with Pillow, or with ffmpeg (no labels) when Pillow is missing."""
    if _has_pillow():
        _compose_with_pillow(rows, dir, sheet, columns)
    else:
        compose_sheet_ffmpeg(rows, dir, sheet, columns, ffmpeg=shutil.which("ffmpeg") or "ffmpeg")


def compose_sheet_ffmpeg(
    rows: list[StillRow], dir: Path, sheet: Path, columns: int, *, ffmpeg: str = "ffmpeg"
) -> None:
    """Tile the stills `columns` wide with ffmpeg.

    Each is scaled and padded to a cell with the first still's aspect.
    """
    if not rows:
        raise AxiError(
            "VALIDATION_ERROR",
            "there are no stills to put on a sheet",
            help=["Grab stills first with `davinci-axi timeline stills <dir> --at 10,50,90`"],
        )
    count = len(rows)
    cols = min(columns, count)
    grid = -(-count // cols)
    cell_w, cell_h = sheet_cell(rows, dir)
    cell = f"{cell_w}:{cell_h}"
    graph = "".join(
        f"[{n}:v]scale={cell}:force_original_aspect_ratio=decrease,"
        f"pad={cell}:-1:-1:color=black,setsar=1[v{n}];"
        for n in range(count)
    )
    graph += "".join(f"[v{n}]" for n in range(count))
    graph += f"concat=n={count}:v=1:a=0,tile={cols}x{grid}[out]"
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for row in rows:
        command += ["-i", str(dir / row.file)]
    command += ["-filter_complex", graph, "-map", "[out]", "-frames:v", "1", "-update", "1"]
    command += ["-q:v", "3", str(sheet)]
    sheet.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip() or f"exit {done.returncode}"
        raise AxiError(
            "API_FAILED",
            f"ffmpeg could not build the contact sheet: {detail}",
            help=["Run the command by hand to see the full error: " + shlex.join(command)],
        )


def stills_result(
    dir: Path,
    rows: list[StillRow],
    sheet: Path | None,
    extra: dict[str, object],
) -> Result:
    """The command result: folder, count, size, a short file list, manifest and sheet paths."""
    names = [row.file for row in rows]
    shown: object = names if len(names) <= SHOWN_FILES else [names[0], "...", names[-1]]
    data: dict[str, object] = {
        "dir": collapse_home(dir),
        "stills": len(rows),
        "bytes": tree_bytes(dir),
        "files": shown or empty_state("stills", "exported", {}),
    }
    manifest = dir / MANIFEST
    if rows and manifest.exists():
        data["manifest"] = collapse_home(manifest)
    if rows and sheet is not None and sheet.exists():
        data["sheet"] = collapse_home(sheet)
        if not _has_pillow():
            data["sheet_note"] = FFMPEG_SHEET_NOTE
    if any(name.endswith(".png") for name in names):
        data["note"] = SIPS_NOTE
    data.update(extra)
    help_lines = (
        [f"Open {collapse_home(manifest)} for the item id, frame and timecode of each still"]
        if rows
        else []
    )
    return Result(data, help_lines)
