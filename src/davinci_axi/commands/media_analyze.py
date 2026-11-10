"""`media analyze`: ffprobe/ffmpeg measurements of the source files behind pool clips (U1).

Read-only: no `--force`, no snapshot; `--out` is the only file written. The Resolve reads (name,
id, path, `FPS`, `Frames`, `Type`) happen on the main thread first; only the subprocesses run in
the worker pool, because the scripting bridge is not known to be thread-safe. `ffprobe` and
`ffmpeg` are optional `PATH` tools: a missing one is `NOT_AVAILABLE` before any Resolve call.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from davinci_axi import registry
from davinci_axi.commands import ffmpeg_common, ffmpeg_vfr
from davinci_axi.commands.audio_common import FOLDER_FLAG, SCOPE_ARGS, pick_scope
from davinci_axi.commands.media_folders import Found, props
from davinci_axi.commands.render_common import methods_of
from davinci_axi.errors import AxiError
from davinci_axi.mutate import refuse_existing
from davinci_axi.output import apply_limit, collapse_home, empty_state, hint, select_fields
from davinci_axi.spec import DEFAULT_LIMIT, CommandSpec, Flag, Invocation, Result

DEFAULT_FIELDS = (
    "name",
    "duration_s",
    "fps",
    "vfr",
    "resolution",
    "lufs",
    "silences",
    "yavg",
)
ALL_FIELDS = (
    "id",
    "frames",
    "fps_avg",
    "width",
    "height",
    "video_codec",
    "audio_ch",
    "true_peak",
    "silences_list",
    "resolve_fps",
    "resolve_frames",
    "note",
    "error",
    "path",
    "ravg",
    "gavg",
    "bavg",
    "vfr_scan",
    "frame_spread_pct",
    "rgb_matrix",
)
RGB_FIELDS = ("ravg", "gavg", "bavg")
RGB_SHOWN = (*RGB_FIELDS, "rgb_matrix")
VFR_FIELDS = ("vfr_scan", "frame_spread_pct")
VFR_TOLERANCE = 0.005
MAX_JOBS = 16
SILENCE_DB, SILENCE_S, JOBS = -35.0, 0.7, 4


@dataclass(frozen=True)
class AnalyzeOpts:
    quick: bool = False
    audio: bool = True
    video: bool = True
    silence_db: float = SILENCE_DB
    silence_s: float = SILENCE_S
    rgb: bool = False  # --rgb: show all three channel means
    rgb_pass: bool = False  # run the RGB pass: --rgb, or a channel field named in --fields
    sample_fps: float = 0.0  # 0 = every frame; above 0 = that many frames per second
    vfr_scan: bool = False  # --vfr-scan: also read the first seconds of video packets


@dataclass(frozen=True)
class ClipInfo:
    """What Resolve says about one clip, read on the main thread."""

    name: str
    id: str
    path: str
    kind: str
    resolve_fps: float | None
    resolve_frames: int | None


def _number(value: object) -> float | None:
    try:
        return float(str(value))
    except ValueError:
        return None


def read_info(found: Found) -> ClipInfo:
    live = props(found.clip)
    frames = _number(live.get("Frames"))
    return ClipInfo(
        name=found.clip.GetName(),
        id=found.clip.GetUniqueId(),
        path=str(live.get("File Path") or ""),
        kind=str(live.get("Type") or ""),
        resolve_fps=_number(live.get("FPS")),
        resolve_frames=int(frames) if frames is not None else None,
    )


def _empty_row(info: ClipInfo) -> dict[str, object]:
    row: dict[str, object] = dict.fromkeys(("name", *DEFAULT_FIELDS, *ALL_FIELDS))
    row.update(
        name=info.name,
        id=info.id,
        path=info.path or None,
        resolve_fps=info.resolve_fps,
        resolve_frames=info.resolve_frames,
    )
    return row


def _is_vfr(fps: float | None, fps_avg: float | None) -> bool | None:
    if not fps or not fps_avg:
        return None
    return abs(fps - fps_avg) / fps > VFR_TOLERANCE


def measure(info: ClipInfo, opts: AnalyzeOpts) -> dict[str, object]:
    """One result row (R-A2). A failure becomes `error` on the row, never an exception."""
    row = _empty_row(info)
    if not info.path:
        row["error"] = "no file path"
        return row
    path = Path(info.path)
    if not path.is_file():
        row["error"] = "file not found"
        return row
    try:
        _fill(row, info, path, opts)
    except (AxiError, OSError) as err:
        row["error"] = err.message if isinstance(err, AxiError) else str(err)
    return row


def _fill(row: dict[str, object], info: ClipInfo, path: Path, opts: AnalyzeOpts) -> None:
    probed = ffmpeg_common.probe(path)
    row["duration_s"] = round(probed.duration_s, 3) if probed.duration_s is not None else None
    video, audio = probed.video, probed.audio
    height: object = None
    if video is not None:
        fps, fps_avg = video.get("fps"), video.get("fps_avg")
        fps = fps if isinstance(fps, float) else None
        fps_avg = fps_avg if isinstance(fps_avg, float) else None
        frames = video.get("frames")
        if not isinstance(frames, int) and probed.duration_s is not None and fps:
            frames = round(probed.duration_s * fps)
        width, height = video.get("width"), video.get("height")
        row.update(
            fps=fps,
            fps_avg=fps_avg,
            vfr=_is_vfr(fps, fps_avg),
            frames=frames,
            width=width,
            height=height,
            resolution=f"{width}x{height}" if width and height else None,
            video_codec=video.get("codec"),
        )
        theirs = info.resolve_frames
        if isinstance(frames, int) and theirs is not None and abs(frames - theirs) > 1:
            row["note"] = f"container {frames} frames, Resolve {theirs}"
    if audio is not None:
        row["audio_ch"] = audio.get("channels")
    wants_audio = audio is not None and opts.audio and not opts.quick
    wants_video = (
        video is not None
        and opts.video
        and not opts.quick
        and (not info.kind or "Video" in info.kind)
    )
    if video is not None and opts.vfr_scan and opts.video:
        _scan_vfr(row, path)
    if wants_audio:
        heard = ffmpeg_common.loudness(path, silence_db=opts.silence_db, silence_s=opts.silence_s)
        row.update(
            lufs=heard.lufs,
            true_peak=heard.true_peak,
            silences=len(heard.silences),
            silences_list=[[start, end] for start, end in heard.silences],
        )
    if wants_video:
        row["yavg"] = ffmpeg_common.luma_mean(path, sample_fps=opts.sample_fps)
        if opts.rgb_pass:
            colour = ffmpeg_common.colour_of(
                video.get("color_space") if video else None,
                video.get("color_range") if video else None,
                height,
            )
            row["ravg"], row["gavg"], row["bavg"] = ffmpeg_common.rgb_means(
                path, sample_fps=opts.sample_fps, colour=colour
            )
            row["rgb_matrix"] = colour.label()


def _scan_vfr(row: dict[str, object], path: Path) -> None:
    """Fold the packet-timing scan into the row; `vfr` is true when either test says so."""
    scan = ffmpeg_vfr.vfr_scan(path)
    row["vfr_scan"] = scan.variable
    row["frame_spread_pct"] = scan.spread_pct
    if row["vfr"] is not None or scan.variable is not None:
        row["vfr"] = bool(row["vfr"]) or bool(scan.variable)


def analyze_clip(found: Found, opts: AnalyzeOpts) -> dict[str, object]:
    """Read one clip from Resolve and measure it (single-threaded convenience)."""
    return measure(read_info(found), opts)


def _bad(message: str, help_line: str) -> AxiError:
    return AxiError("VALIDATION_ERROR", message, help=[help_line])


def _check_flags(
    inv: Invocation,
) -> tuple[AnalyzeOpts, int, list[str] | None, Path | None, int]:
    args = inv.args
    silence_db = _number(args.get("silence_db", SILENCE_DB))
    silence_s = _number(args.get("silence_s", SILENCE_S))
    jobs = _number(args.get("jobs", JOBS))
    if silence_db is None or silence_db > 0:
        raise _bad("--silence-db must be 0 or below", "Valid: --silence-db -35")
    if silence_s is None or silence_s <= 0:
        raise _bad("--silence-s must be above 0", "Valid: --silence-s 0.7")
    if jobs is None or not 1 <= jobs <= MAX_JOBS or jobs != int(jobs):
        raise _bad(f"--jobs must be 1-{MAX_JOBS}", "Valid: --jobs 4")
    limit = _number(args.get("limit", DEFAULT_LIMIT))
    if limit is None or limit < 1 or limit != int(limit):
        raise _bad("--limit must be 1 or more", "Valid: --limit 20")
    raw = args.get("fields")
    fields = [str(f) for f in raw] if isinstance(raw, list) else None
    valid = (*DEFAULT_FIELDS, *ALL_FIELDS)
    unknown = [f for f in fields or [] if f not in valid]
    if unknown:
        raise _bad(f"unknown field {', '.join(unknown)}", f"Valid fields: {', '.join(valid)}")
    out_text = args.get("out")
    out = Path(str(out_text)).expanduser().resolve() if out_text else None
    if out is not None:
        if not out.parent.is_dir():
            raise _bad(f"{collapse_home(out.parent)} is not a folder", "Create it or pick another")
        refuse_existing(out, bool(args.get("overwrite")))
    sample = _number(args.get("sample", 0.0))
    if sample is None or sample < 0:
        raise _bad("--sample must be 0 (every frame) or a frame rate above 0", "Valid: --sample 2")
    rgb = bool(args.get("rgb"))
    opts = AnalyzeOpts(
        quick=bool(args.get("quick")),
        audio=not args.get("no_audio"),
        video=not args.get("no_video"),
        silence_db=silence_db,
        silence_s=silence_s,
        rgb=rgb,
        rgb_pass=rgb or any(f in RGB_SHOWN for f in fields or []),
        sample_fps=sample,
        vfr_scan=bool(args.get("vfr_scan")),
    )
    return opts, int(jobs), fields, out, int(limit)


def run_analyze(inv: Invocation) -> Result:
    opts, jobs, fields, out, limit = _check_flags(inv)
    passes = not opts.quick and (opts.audio or opts.video)
    ffmpeg_common.require_tools("ffprobe", *(("ffmpeg",) if passes else ()))
    scope = pick_scope(inv, ["media", "analyze"])
    infos = [read_info(found) for found in scope.clips()]
    tools = {
        "ffprobe": ffmpeg_common.tool_path("ffprobe"),
        "ffmpeg": ffmpeg_common.tool_path("ffmpeg") if passes else None,
    }
    if not infos:
        data: dict[str, object] = {
            "clips": 0,
            "analyzed": 0,
            "errors": 0,
            "tools": tools,
            "rows": empty_state("clips", f"in {scope.target}", {}),
        }
        return Result(data)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        rows = list(pool.map(lambda info: measure(info, opts), infos))
    failed = [row for row in rows if row["error"]]
    if len(failed) == len(rows):
        raise AxiError(
            "API_FAILED",
            f"every clip failed ({len(rows)})",
            help=[f"{row['name']}: {row['error']}" for row in failed[:5]],
        )
    if out is not None:
        out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    full = bool(inv.args.get("full")) or inv.opts.json
    shown_default = (
        *DEFAULT_FIELDS,
        *(RGB_SHOWN if opts.rgb else ()),
        *(VFR_FIELDS if opts.vfr_scan else ()),
        *(("silences_list",) if full else ()),
        *(("error",) if failed else ()),
    )
    shown, total = apply_limit(rows, limit)
    data = {
        "clips": len(rows),
        "analyzed": len(rows) - len(failed),
        "errors": len(failed),
        "tools": tools,
    }
    if opts.sample_fps > 0 and opts.video and not opts.quick:
        data["sampled"] = f"{opts.sample_fps:g} fps (estimate: yavg, ravg, gavg, bavg)"
    data["rows"] = select_fields(shown, shown_default, fields)
    if len(shown) < total:
        data["shown"] = f"{len(shown)} of {total}"
    if out is not None:
        data["out"] = collapse_home(out)
    help_lines: list[str] = []
    if len(shown) < total:
        more = "raise --limit" if out is not None else "raise --limit or add --out"
        help_lines.append(f"Showing {len(shown)} of {total} rows; {more} to see the rest")
    if out is None:
        help_lines.append(
            hint(f"{' '.join(scope.words)} --out ~/analysis.json", "write every row to a JSON file")
        )
    if opts.rgb and "color" in registry.COMMANDS:
        help_lines.append(
            hint("color cdl set --edl grades.csv", "balance each clip from its channel means")
        )
    help_lines.append(hint("media stills --help", "export a small still of each clip to look at"))
    help_lines.append(hint("timeline append --help", "place clips by an EDL after choosing them"))
    return Result(data, help_lines)


ANALYZE_SPEC = CommandSpec(
    name="analyze",
    summary="Measure the source files of pool clips with ffprobe/ffmpeg: fps, VFR, loudness, luma",
    run=run_analyze,
    positionals=SCOPE_ARGS,
    flags=(
        Flag("quick", "bool", False, help="ffprobe only: skip the loudness and luma passes"),
        Flag("no-audio", "bool", False, help="skip the loudness and silence pass"),
        Flag("no-video", "bool", False, help="skip the luma pass"),
        Flag(
            "rgb",
            "bool",
            False,
            help="also measure ravg/gavg/bavg (the RGB of the clip's mean colour, unclipped) "
            "and rgb_matrix (one more ffmpeg pass per clip: about 6 s for a 26 s 1080p clip)",
        ),
        Flag(
            "sample",
            "float",
            0.0,
            help="measure this many frames per second instead of every frame (an estimate, "
            "marked `sampled` in the output; 0 = every frame; --sample 2 takes about 4 s where "
            "every frame takes 6 s)",
        ),
        Flag(
            "vfr-scan",
            "bool",
            False,
            help="also read the first 10 s of video packets and flag uneven frame lengths "
            "(one ffprobe call per clip)",
        ),
        Flag("silence-db", "float", SILENCE_DB, help="silence threshold in dB (0 or below)"),
        Flag("silence-s", "float", SILENCE_S, help="shortest silence to report, in seconds"),
        Flag("jobs", "int", JOBS, help=f"clips analysed at once (1-{MAX_JOBS})"),
        Flag("out", "str", None, help="write every row, with the silence lists, to this JSON file"),
        Flag("overwrite", "bool", False, help="replace the --out file if it exists"),
        Flag("full", "bool", False, help="also show the [start_s, end_s] silence lists"),
        Flag("fields", "list", None, help="comma-separated fields to show"),
        Flag("limit", "int", DEFAULT_LIMIT, help="rows shown (--out still writes every row)"),
        FOLDER_FLAG,
    ),
    examples=(
        "davinci-axi media analyze A001_beach,music_bed",
        "davinci-axi media analyze --folder Master/Footage --quick",
        "davinci-axi media analyze A001_beach --rgb --sample 2",
        "davinci-axi media analyze --folder Master --vfr-scan --quick",
        "davinci-axi media analyze --folder Master --jobs 4 --out ~/edit/analysis.json",
    ),
    reads_only=True,
    methods=methods_of(
        Project="GetMediaPool",
        MediaPool="GetRootFolder",
        Folder="GetClipList GetSubFolderList GetName",
        MediaPoolItem="GetName GetUniqueId GetClipProperty",
    ),
    notes=(
        "ffprobe and ffmpeg are optional programs on PATH (FFPROBE/FFMPEG name a binary); "
        "without them this verb fails with NOT_AVAILABLE before it touches Resolve",
        "The rows read each clip's File Path; a missing file gives that row an error and the "
        "rest continue. Pass clips or --folder (it includes sub folders), not both",
        "yavg is the mean luma 0-255 over every frame (limited range is 16-235); lufs and "
        "true_peak come from ebur128; vfr means fps and fps_avg differ by more than 0.5%, or "
        "with --vfr-scan that the frame lengths in the first 10 s spread more than 10% "
        "(vfr_scan, frame_spread_pct)",
        "ravg/gavg/bavg are full-range RGB means 0-255 over every frame, converted to RGB with "
        "the stream's own matrix and range before any averaging (rgb_matrix shows them: ffprobe "
        "color_space and color_range; when a tag is missing it is bt709 for 720p and up, bt601 "
        "below, tv range, marked (assumed)); the input of a white-balance CDL: the channel with "
        "the lowest mean needs the highest slope; --sample N measures N frames per second "
        "instead, an estimate; the means are **unclipped**: they are the RGB of the mean colour, "
        "which can differ from the average of the displayed (clipped) pixels on very saturated "
        "footage",
        "Resolve conforms variable-frame-rate clips on the timeline, so check vfr before "
        "trusting frame counts",
    ),
)
