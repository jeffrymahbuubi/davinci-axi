"""`render sheet`: a QA contact sheet of a rendered file, one ffmpeg `tile` command (round 4, U1).

A pure file verb (no Resolve call unless `--job` finds the file): it probes the duration, then
tiles one frame every `--every` seconds into a single JPEG. The input is never rewritten.
`ffmpeg`/`ffprobe` are optional `PATH` programs: a missing one is `NOT_AVAILABLE` up front.
"""

from __future__ import annotations

import math
import shlex
from pathlib import Path
from typing import cast

from davinci_axi.commands import ffmpeg_common
from davinci_axi.commands.render_verify import VERIFY_SPEC, job_target
from davinci_axi.errors import AxiError, not_found
from davinci_axi.mutate import refuse_existing
from davinci_axi.output import collapse_home, hint
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

DEFAULT_EVERY, DEFAULT_COLUMNS, DEFAULT_WIDTH = 10.0, 6, 270
MAX_COLUMNS, MIN_WIDTH, MAX_WIDTH = 12, 64, 1920


def _bad(message: str, help_line: str) -> AxiError:
    return AxiError("VALIDATION_ERROR", message, help=[help_line])


def grid_for(duration_s: float, every: float, columns: int) -> tuple[int, int, int]:
    """`(frames, cols, rows)`: one cell per `every` seconds from t = 0, row-major."""
    frames = int(duration_s // every) + 1
    cols = min(columns, frames)
    return frames, cols, -(-frames // cols)


def default_out(path: Path) -> Path:
    """`<stem>-sheet.jpg` beside the input."""
    return path.with_name(f"{path.stem}-sheet.jpg")


def sheet_command(
    path: Path, out: Path, every: float, cols: int, rows: int, width: int
) -> list[str]:
    """The one ffmpeg command: sample, scale, tile, write a single JPEG."""
    chain = f"fps=1/{every:g},scale={width}:-2,tile={cols}x{rows}"
    return [
        ffmpeg_common.tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(path), "-vf", chain, "-frames:v", "1", "-update", "1", "-q:v", "3", str(out),
    ]  # fmt: skip


def _numbers(inv: Invocation) -> tuple[float, int, int]:
    """The validated `(every, columns, width)`; exit 2 before any subprocess."""
    args = inv.args
    every = cast(float, args.get("every", DEFAULT_EVERY))
    if not math.isfinite(every) or every <= 0:
        raise _bad(f"--every must be above 0, got {every:g}", "Valid: --every 10 (seconds)")
    columns = cast(int, args.get("columns", DEFAULT_COLUMNS))
    if not 1 <= columns <= MAX_COLUMNS:
        raise _bad(f"--columns must be 1 to {MAX_COLUMNS}, got {columns}", "Valid: --columns 6")
    width = cast(int, args.get("width", DEFAULT_WIDTH))
    if not MIN_WIDTH <= width <= MAX_WIDTH:
        raise _bad(
            f"--width must be {MIN_WIDTH} to {MAX_WIDTH}, got {width}",
            "Valid: --width 270 (pixels)",
        )
    return every, columns, width


def _failed(command: list[str], name: str, stderr: str) -> AxiError:
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    return AxiError(
        "API_FAILED",
        f"ffmpeg could not build the sheet for {name}: {lines[0] if lines else 'no output'}",
        help=[f"Run the command by hand: {shlex.join(command)}"],
    )


def _help(file: str, job: str | None) -> list[str]:
    lines = [
        hint(f"render verify {file} --max-mb <mb> --lufs -14", "check the numbers too"),
        hint(f"render sheet {file} --every 5", "see a finer grid"),
    ]
    if job:
        lines.append(hint(f"render status {job}", "see that job's state and settings"))
    return lines


def run_sheet(inv: Invocation) -> Result:
    every, columns, width = _numbers(inv)
    given = inv.positionals.get("file")
    job = str(inv.args["job"]) if inv.args.get("job") else None
    if given and job:  # before any tool or Resolve call
        raise _bad("give a file or --job, not both", "davinci-axi render sheet --job <id>")
    if not given and not job:
        raise _bad("name a file or --job", "davinci-axi render sheet ~/out/edit.mp4 --every 10")
    chosen = inv.args.get("out")
    out_arg = Path(str(chosen)).expanduser().resolve() if chosen else None
    if given and out_arg is not None and out_arg == Path(str(given)).expanduser().resolve():
        raise _bad("--out is the input file", "Pick another path; the input is never rewritten")
    ffmpeg_common.require_tools("ffmpeg", "ffprobe")
    note: str | None = None
    if job:
        path, _, note = job_target(inv.connect(), job)
    else:
        path = Path(str(given)).expanduser().resolve()
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path; for a job use --job <id>")
    out = out_arg or default_out(path)
    if out == path:
        raise _bad("--out is the input file", "Pick another path; the input is never rewritten")
    refuse_existing(out, bool(inv.args.get("overwrite")))
    probed = ffmpeg_common.probe(path)
    if probed.video is None:
        raise _bad(
            "no video stream to tile",
            "Pick a rendered video; `render verify <file>` lists the streams",
        )
    if probed.duration_s is None:
        raise AxiError(
            "API_FAILED",
            "ffprobe reported no duration",
            help=[f"Run `ffprobe -i {shlex.quote(str(path))}` by hand to see what it reports"],
        )
    frames, cols, rows = grid_for(probed.duration_s, every, columns)
    command = sheet_command(path, out, every, cols, rows, width)
    done = ffmpeg_common.run(command)
    if done.returncode != 0:
        out.unlink(missing_ok=True)
        raise _failed(command, path.name, done.stderr or done.stdout or "")
    if not out.is_file():
        raise AxiError(
            "API_FAILED",
            "ffmpeg wrote nothing",
            help=[f"Run the command by hand: {shlex.join(command)}"],
        )
    data: dict[str, object] = {
        "file": collapse_home(path),
        "out": collapse_home(out),
        "duration_s": round(probed.duration_s, 3),
        "every_s": every,
        "frames": frames,
        "columns": cols,
        "rows": rows,
        "cell_width": width,
        "bytes": out.stat().st_size,
        "grid": f"cell n (row-major from 0) shows t = n x {every:g} s",
    }
    if note:
        data["note"] = note
    return Result(data, _help(collapse_home(path), job))


SHEET_SPEC = CommandSpec(
    name="sheet",
    summary="Tile one frame every N seconds of a rendered file into a QA contact sheet (ffmpeg)",
    run=run_sheet,
    positionals=(Positional("file", required=False, help="a rendered video file"),),
    flags=(
        Flag("job", "str", None, help="tile the output of this render job instead of a file"),
        Flag("every", "float", DEFAULT_EVERY, help="seconds between frames, above 0"),
        Flag("columns", "int", DEFAULT_COLUMNS, help="cells per row, 1 to 12"),
        Flag("width", "int", DEFAULT_WIDTH, help="width of one cell in pixels, 64 to 1920"),
        Flag("out", "str", None, help="output JPEG (default: <name>-sheet.jpg beside the input)"),
        Flag("overwrite", "bool", False, help="replace the output file if it exists"),
    ),
    examples=(
        "davinci-axi render sheet ~/out/edit.mp4",
        "davinci-axi render sheet ~/out/edit.mp4 --every 5 --columns 8 --out ~/qa/edit-5s.jpg",
        "davinci-axi render sheet --job job-1 --overwrite",
    ),
    reads_only=False,
    methods=VERIFY_SPEC.methods,
    notes=(
        "Writes a new JPEG and never touches the input; there is no --force. --job is the only "
        "Resolve call: it finds the job's output file",
        "One frame every --every seconds from t = 0; cells run row-major from the top left, and "
        "black cells pad the last row. There are no labels: use the grid line to map a cell to "
        "a time (cell n shows t = n x every)",
        "ffmpeg and ffprobe are optional programs on PATH; without them this verb fails with "
        "NOT_AVAILABLE before it does anything else",
    ),
)
