"""`audio gaps`: where the picture has no sound (round 2, B).

Read-only: no `--force`, no snapshot. Two kinds of rows, both in timeline frames: `no-audio`
(video spans that no enabled audio item covers, the structural rule `timeline check` already has)
and `silent` (stretches inside an audio item where ffmpeg's `silencedetect` hears nothing on the
used source range). `--no-measure` skips ffmpeg, so only the structural rows remain and ffmpeg
is not required. ffmpeg is checked before the first Resolve call.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from davinci_axi import registry
from davinci_axi.commands import audio_measure as am
from davinci_axi.commands.audio_common import TIMELINE_READS
from davinci_axi.commands.ffmpeg_common import require_tools
from davinci_axi.commands.item_detail import Located, str_arg, walk_items
from davinci_axi.commands.items import ITEM_LIST_METHODS, TIMELINE_FLAG
from davinci_axi.commands.render_common import methods_of
from davinci_axi.commands.render_frame import frame_to_timecode
from davinci_axi.commands.timeline_check import effective_settings
from davinci_axi.commands.timeline_check_rules import merged, span_of
from davinci_axi.errors import AxiError, not_found
from davinci_axi.mutate import refuse_existing
from davinci_axi.output import apply_limit, collapse_home, empty_state, hint
from davinci_axi.spec import DEFAULT_LIMIT, CommandSpec, Flag, Invocation, Result

MIN_S = 1.0
SHOWN_FAILURES = 3
OUT_SUFFIXES = (".csv", ".json")
ROW_FIELDS = ("frame", "color", "name", "note", "duration")
KIND_COLORS = {"no-audio": "Red", "silent": "Yellow"}


def _bad(message: str, help_line: str) -> AxiError:
    return AxiError("VALIDATION_ERROR", message, help=[help_line])


def _num(value: object, default: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return float(value)


@dataclass(frozen=True)
class Opts:
    """The validated flags of one call."""

    min_s: float
    silence_db: float
    silence_s: float
    jobs: int
    track: int | None
    limit: int
    measure: bool
    out: Path | None = None
    overwrite: bool = False


def _out_path(args: dict[str, object]) -> Path | None:
    text = args.get("out")
    if text in (None, ""):
        return None
    path = Path(str(text)).expanduser().resolve()
    if path.suffix.lower() not in OUT_SUFFIXES:
        raise _bad(f"--out must end in .csv or .json, got {path.name}", "Valid: --out gaps.csv")
    if not path.parent.is_dir():
        raise _bad(f"{collapse_home(path.parent)} is not a folder", "Create it or pick another")
    refuse_existing(path, bool(args.get("overwrite")))
    return path


def parse_opts(inv: Invocation) -> Opts:
    args = inv.args
    min_s = _num(args.get("min_s"), MIN_S)
    silence_db = _num(args.get("silence_db"), am.SILENCE_DB)
    silence_s = _num(args.get("silence_s"), am.SILENCE_S)
    jobs = _num(args.get("jobs"), float(am.JOBS))
    track = _num(args.get("track"))
    limit = _num(args.get("limit"), float(DEFAULT_LIMIT))
    if min_s is None or min_s < 0:
        raise _bad("--min-s must be 0 or more", "Valid: --min-s 1")
    if silence_db is None or silence_db > 0:
        raise _bad("--silence-db must be 0 or below (dB)", "Valid: --silence-db -35")
    if silence_s is None or silence_s <= 0:
        raise _bad("--silence-s must be above 0", "Valid: --silence-s 0.7")
    if jobs is None or not 1 <= jobs <= am.MAX_JOBS or jobs != int(jobs):
        raise _bad(f"--jobs must be 1-{am.MAX_JOBS}", "Valid: --jobs 4")
    if track is not None and (track < 1 or track != int(track)):
        raise _bad("--track must be 1 or more", "Audio tracks are numbered from 1")
    apply_limit([], int(limit or 0))
    out = _out_path(args)
    return Opts(
        min_s,
        silence_db,
        silence_s,
        int(jobs),
        int(track) if track is not None else None,
        int(limit or 1),
        not bool(args.get("no_measure")),
        out,
        bool(args.get("overwrite")),
    )


def marker_rows(rows: list[dict[str, object]], start_frame: int) -> list[dict[str, object]]:
    """Gap rows as marker rows; `frame` is the offset from the timeline start."""
    marks: list[dict[str, object]] = []
    for row in rows:
        kind, first, last = str(row["kind"]), int(str(row["start"])), int(str(row["end"]))
        seconds = f"{float(str(row['duration_s'])):.1f} s"
        name = f"{kind} {row['where']} {seconds}" if kind == "silent" else f"{kind} {seconds}"
        marks.append(
            {
                "frame": first - start_frame,
                "color": KIND_COLORS[kind],
                "name": name,
                "note": f"{row['tc']} frames {first}-{last}",
                "duration": last - first,
            }
        )
    return marks


def write_rows(path: Path, rows: list[dict[str, object]], overwrite: bool) -> None:
    """Write marker rows to a `.csv` (header first) or `.json` array, chosen by the suffix."""
    refuse_existing(path, overwrite)
    try:
        if path.suffix.lower() == ".json":
            path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
            return
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=ROW_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    except OSError as err:
        raise _bad(
            f"cannot write {collapse_home(path)}: {err}", "Pick another --out path"
        ) from None


def no_audio_spans(video: list[Located], audio: list[Located]) -> list[tuple[int, int]]:
    """Video spans (timeline frames) that no enabled audio item covers."""
    picture = merged([span_of(located) for located in video])
    sound = merged([span_of(a) for a in audio if a.item.GetClipEnabled()])
    found: list[tuple[int, int]] = []
    for first, last in picture:
        cursor = first
        for begin, finish in sound:
            if finish <= cursor or begin >= last:
                continue
            if begin > cursor:
                found.append((cursor, begin))
            cursor = max(cursor, finish)
        if cursor < last:
            found.append((cursor, last))
    return found


def _row(kind: str, where: str, span: tuple[int, int], fps: float) -> dict[str, object]:
    first, last = span
    return {
        "kind": kind,
        "where": where,
        "start": first,
        "end": last,
        "tc": frame_to_timecode(first, fps),
        "duration_s": round((last - first) / fps, 3),
    }


def _covered_pct(picture: list[tuple[int, int]], rows: list[dict[str, object]]) -> float:
    """Share of the picture frames not inside any gap row."""
    total = sum(last - first for first, last in picture)
    clipped: list[tuple[int, int]] = []
    for row in rows:
        begin, finish = int(str(row["start"])), int(str(row["end"]))
        for first, last in picture:
            lo, hi = max(first, begin), min(last, finish)
            if hi > lo:
                clipped.append((lo, hi))
    gap = sum(last - first for first, last in merged(clipped))
    return round(100.0 * (total - gap) / total, 1) if total else 0.0


def _seconds(rows: list[dict[str, object]], kind: str) -> float:
    return round(sum(float(str(r["duration_s"])) for r in rows if r["kind"] == kind), 3)


def _music_known() -> bool:
    """True when `timeline music` is registered (round 3, B)."""
    spec = registry.lookup("timeline")
    return spec is not None and "music" in spec.subcommands


def _help(
    timeline_audio_tracks: int,
    track2_name: str,
    rows: list[dict[str, object]],
    measured: list[am.Measured],
    shown: int,
    out: Path | None = None,
) -> list[str]:
    lines: list[str] = []
    if out is not None and rows:
        lines.append(
            hint(
                f"timeline marker add --edl {collapse_home(out)} --force",
                "mark the gaps on the timeline",
            )
        )
    if rows:
        if timeline_audio_tracks >= 2:
            named = f" ({track2_name})" if track2_name else ""
            if _music_known():
                lines.append(
                    hint(
                        f"timeline music add <music clip> --track 2 --frame {rows[0]['start']}",
                        f"lay a music bed on A2{named}",
                    )
                )
            else:
                lines.append(
                    hint(
                        f"timeline append <music clip> --track 2 --record-frame {rows[0]['start']}",
                        f"lay a music bed under the gap on A2{named}",
                    )
                )
        else:
            lines.append(hint("timeline track add audio", "add a music track first"))
        lines.append(
            "Cutting audio alone desyncs the picture; trim both with "
            "`item delete <V ref>,<A ref> --ripple`"
        )
    failed = [m for m in measured if m.error]
    lines += [f"not measured, {m.info.ref}: {m.error}" for m in failed[:SHOWN_FAILURES]]
    if len(failed) > SHOWN_FAILURES:
        lines.append(f"... and {len(failed) - SHOWN_FAILURES} more not measured; --json lists them")
    if shown < len(rows):
        lines.append(f"{len(rows) - shown} more rows; raise --limit or use --json")
    lines.append(hint("audio loudness", "see item levels"))
    return lines


def run_gaps(inv: Invocation) -> Result:
    opts = parse_opts(inv)
    if opts.measure:
        require_tools("ffmpeg")
    session = inv.connect()
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    name = timeline.GetName()
    count = timeline.GetTrackCount("audio")
    if opts.track is not None and opts.track > count:
        raise not_found("audio track", f"A{opts.track}", f"This timeline has {count} audio tracks")
    track2_name = str(timeline.GetTrackName("audio", 2) or "") if count >= 2 else ""
    fps = effective_settings(session, timeline)[0]
    video = list(walk_items(timeline, ("video",)))
    audio = am.audio_items(timeline, opts.track)
    scope = f"in timeline {name}"
    base: dict[str, object] = {"timeline": name, "track": f"A{opts.track}" if opts.track else "all"}
    if not video:
        return Result(
            {**base, "gaps": 0, "rows": empty_state("video items", scope, {})},
            [hint("items --track-type video", "list the video items")],
        )
    found = [_row("no-audio", "-", span, fps) for span in no_audio_spans(video, audio)]
    measured: list[am.Measured] = []
    if opts.measure:
        infos = [info for info in map(am.read_item, audio) if info.enabled]
        measured = am.measure_items(
            infos, silence_db=opts.silence_db, silence_s=opts.silence_s, jobs=opts.jobs
        )
        for m in measured:
            found += [_row("silent", m.info.ref, span, fps) for span in am.on_timeline(m, fps)]
    rows = sorted(
        (r for r in found if float(str(r["duration_s"])) >= opts.min_s),
        key=lambda r: (int(str(r["start"])), int(str(r["end"]))),
    )
    picture = merged([span_of(located) for located in video])
    data: dict[str, object] = {
        **base,
        "gaps": len(rows),
        "no_audio_s": _seconds(rows, "no-audio"),
        "silent_s": _seconds(rows, "silent"),
        "covered_pct": _covered_pct(picture, rows),
        "measured": sum(1 for m in measured if m.info.has_audio and m.error is None),
        "errors": sum(1 for m in measured if m.error),
    }
    if opts.out is not None and rows:
        write_rows(opts.out, marker_rows(rows, int(timeline.GetStartFrame())), opts.overwrite)
        data["out"] = collapse_home(opts.out)
    if opts.out is not None:
        data["out_rows"] = len(rows)
    if not rows:
        data["rows"] = empty_state("gaps", scope, {"min_s": opts.min_s, "track": opts.track})
        return Result(data, _help(count, track2_name, rows, measured, 0, opts.out))
    shown, _ = apply_limit(rows, opts.limit)
    data["rows"] = shown
    return Result(data, _help(count, track2_name, rows, measured, len(shown), opts.out))


GAPS_SPEC = CommandSpec(
    name="gaps",
    summary="Find stretches of the picture with no sound (no audio item, or ffmpeg hears silence)",
    run=run_gaps,
    flags=(
        TIMELINE_FLAG,
        Flag("track", "int", None, help="only this audio track number (1-based)"),
        Flag("min-s", "float", MIN_S, help="drop gaps shorter than this many seconds"),
        Flag("silence-db", "float", am.SILENCE_DB, help="silence threshold in dB (0 or below)"),
        Flag("silence-s", "float", am.SILENCE_S, help="shortest silence ffmpeg reports, seconds"),
        Flag("no-measure", "bool", False, help="skip ffmpeg: only the audio-less picture spans"),
        Flag("jobs", "int", am.JOBS, help=f"items measured at once (1-{am.MAX_JOBS})"),
        Flag("limit", "int", DEFAULT_LIMIT, help="maximum rows to show"),
        Flag(
            "out",
            "str",
            None,
            help="write the gaps as marker rows (frame, color, name, note, duration) to this "
            ".csv or .json file",
        ),
        Flag("overwrite", "bool", False, help="replace the --out file if it exists"),
    ),
    examples=(
        "davinci-axi audio gaps",
        "davinci-axi audio gaps --min-s 2 --track 1",
        "davinci-axi audio gaps --no-measure",
        "davinci-axi audio gaps --out gaps.csv",
    ),
    reads_only=True,
    methods=(
        *ITEM_LIST_METHODS,
        "TimelineItem.GetSourceStartFrame",
        "TimelineItem.GetSourceEndFrame",
        "TimelineItem.GetProperties",
        "TimelineItem.GetClipEnabled",
        "TimelineItem.GetMediaPoolItem",
        "MediaPoolItem.GetClipProperty",
        *TIMELINE_READS,
        *methods_of(
            Timeline="GetSettings GetStartFrame GetTrackCount GetTrackName", Project="GetSettings"
        ),
    ),
    notes=(
        "`no-audio` rows are video spans no enabled audio item covers; `silent` rows are "
        "stretches inside an audio item where ffmpeg hears silence (-35 dB for 0.7 s by default) "
        "on the item's used source range, mapped to timeline frames",
        "Rows shorter than --min-s (default 1) are dropped; covered_pct is the share of the "
        "picture outside every reported gap",
        "ffmpeg is an optional program on PATH (FFMPEG names a binary); it fails with "
        "NOT_AVAILABLE before touching Resolve, unless --no-measure skips it",
        "Cutting audio alone desyncs the picture: trim both with `item delete <V ref>,<A ref> "
        "--ripple`",
        "The music-bed hint targets A2 and names it (Timeline.GetTrackName)",
        "--out writes every gap (not only --limit) as marker rows: frame is the offset from the "
        "timeline start, color Red for no-audio and Yellow for silent; load them with `timeline "
        "marker add --edl`. No gaps writes no file; an existing file needs --overwrite",
    ),
)
