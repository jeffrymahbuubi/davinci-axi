"""`audio loudness`: what each audio item contributes, measured with ffmpeg (round 2, A).

Read-only: no `--force`, no snapshot. Resolve has no loudness meter in its API, so each item's
used source range is measured with ffmpeg (`ebur128`) and the clip volume (`AudioVolume`, dB) is
added: the result is an estimate before track effects. ffmpeg is checked before the first
Resolve call; the Resolve reads run on the main thread and only ffmpeg runs in the worker pool.
"""

from __future__ import annotations

from davinci_axi.commands import audio_measure as am
from davinci_axi.commands.audio_common import TIMELINE_READS
from davinci_axi.commands.ffmpeg_common import require_tools
from davinci_axi.commands.item_detail import Located, resolve_item, str_arg
from davinci_axi.commands.items import ITEM_LIST_METHODS, TIMELINE_FLAG
from davinci_axi.commands.render_common import methods_of
from davinci_axi.errors import AxiError, not_found
from davinci_axi.output import apply_limit, empty_state, hint, select_fields
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.spec import (
    DEFAULT_LIMIT,
    CommandSpec,
    Flag,
    Invocation,
    Positional,
    Result,
)

DEFAULT_FIELDS = ("item", "name", "duration_s", "source_lufs", "volume_db", "lufs", "delta")
ALL_FIELDS = ("id", "start", "true_peak", "silent_pct", "disabled", "note", "error", "path")
SHOWN_HELP = 5


def _bad(message: str, help_line: str) -> AxiError:
    return AxiError("VALIDATION_ERROR", message, help=[help_line])


class Opts:
    """The validated flags of one call."""

    def __init__(self, inv: Invocation) -> None:
        args = inv.args
        self.target = _num(args.get("target"))
        self.tolerance = _num(args.get("tolerance"), 1.0)
        self.outlier = _num(args.get("outlier_lu"), am.OUTLIER_LU)
        self.jobs = _num(args.get("jobs"), float(am.JOBS))
        self.track = _num(args.get("track"))
        self.limit = _num(args.get("limit"), float(DEFAULT_LIMIT))
        raw = args.get("fields")
        self.fields = [str(f) for f in raw] if isinstance(raw, list) else None
        if self.target is not None and not -60.0 <= self.target <= 0.0:
            raise _bad("--target must be between -60 and 0 LUFS", "Valid: --target -14")
        if self.tolerance is None or self.tolerance < 0:
            raise _bad("--tolerance must be 0 or more", "Valid: --tolerance 1")
        if self.outlier is None or self.outlier <= 0:
            raise _bad("--outlier-lu must be above 0", "Valid: --outlier-lu 6")
        if self.jobs is None or not 1 <= self.jobs <= am.MAX_JOBS or self.jobs != int(self.jobs):
            raise _bad(f"--jobs must be 1-{am.MAX_JOBS}", "Valid: --jobs 4")
        if self.track is not None and (self.track < 1 or self.track != int(self.track)):
            raise _bad("--track must be 1 or more", "Audio tracks are numbered from 1")
        valid = (*DEFAULT_FIELDS, *ALL_FIELDS)
        unknown = [f for f in self.fields or [] if f not in valid]
        if unknown:
            raise _bad(f"unknown field {', '.join(unknown)}", f"Valid fields: {', '.join(valid)}")
        apply_limit([], int(self.limit or 0))
        if self.track is not None and inv.positionals.get("items"):
            raise _bad("pass items or --track, not both", "Valid: audio loudness A1:1,A1:2")


def _num(value: object, default: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return float(value)


def _pick(inv: Invocation, timeline: TimelineApi, track: int | None) -> list[Located]:
    text = inv.positionals.get("items")
    if not text:
        if track is not None and track > timeline.GetTrackCount("audio"):
            raise not_found(
                "audio track",
                f"A{track}",
                f"This timeline has {timeline.GetTrackCount('audio')} audio tracks",
            )
        return am.audio_items(timeline, track)
    picked: list[Located] = []
    seen: set[str] = set()
    for part in (p.strip() for p in str(text).split(",")):
        if not part:
            raise _bad("an empty item in the comma-separated list", "Valid example: A1:1,A1:2")
        one = resolve_item(timeline, part)
        if not one.track.startswith("A"):
            raise _bad(
                f"{part} is a video item; audio loudness takes audio items such as A1:1",
                "Audio items are addressed A<track>:<index>; `davinci-axi items` lists them",
            )
        uid = one.item.GetUniqueId()
        if uid not in seen:
            seen.add(uid)
            picked.append(one)
    return picked


def _note(m: am.Measured, off: dict[str, float]) -> str | None:
    parts: list[str] = []
    if not m.info.enabled:
        parts.append("disabled")
    if not m.info.has_audio:
        parts.append("no audio stream")
    elif m.error is None and m.info.volume_db is None:
        parts.append("volume not readable")
    if m.error is None and m.info.has_audio and am.window(m.info) is None:
        parts.append("whole file measured (clip rate unknown)")
    if m.info.ref in off:
        delta = off[m.info.ref]
        parts.append(f"{abs(delta):g} LU {'over' if delta > 0 else 'under'} median")
    return "; ".join(parts) or None


def _row(m: am.Measured, target: float | None, off: dict[str, float]) -> dict[str, object]:
    info, span = m.info, am.window(m.info)
    est, share = am.estimate(m), am.silent_share(m)
    return {
        "item": info.ref,
        "name": info.name,
        "duration_s": round(span[1], 3) if span else None,
        "source_lufs": m.lufs,
        "volume_db": round(info.volume_db, 2) if info.volume_db is not None else None,
        "lufs": est,
        "delta": round(target - est, 1) if target is not None and est is not None else None,
        "id": info.id,
        "start": info.start,
        "true_peak": am.estimate_peak(m),
        "silent_pct": round(share * 100, 1) if share is not None else None,
        "disabled": not info.enabled,
        "note": _note(m, off),
        "error": m.error,
        "path": info.path or None,
    }


def _program_hint(centre: float, target: float, tolerance: float) -> str:
    diff = round(target - centre, 1)
    if abs(diff) <= tolerance:
        return f"median {centre:g} LUFS is within {tolerance:g} LU of {target:g}"
    way = "under" if diff > 0 else "over"
    return (
        f"median {centre:g} LUFS is {abs(diff):g} LU {way} {target:g}; "
        f"`audio gain <items> --db {diff:g}` or `audio normalize <items> --loudness {target:g}`"
    )


def _help(
    measured: list[am.Measured], off: dict[str, float], target: float | None, shown: int, total: int
) -> list[str]:
    lines = [am.gain_hint([ref], -delta) for ref, delta in list(off.items())[:SHOWN_HELP]]
    if len(off) > SHOWN_HELP:
        lines.append(f"... and {len(off) - SHOWN_HELP} more; --json lists them")
    if target is not None:
        refs = ",".join(m.info.ref for m in measured if am.estimate(m) is not None)
        if refs:
            lines.append(
                hint(f"audio normalize {refs} --loudness {target:g}", "let Resolve level them")
            )
    if shown < total:
        lines.append(f"{total - shown} more rows; raise --limit or use --json")
    lines.append(hint("audio gaps", "find silent stretches"))
    return lines


def run_loudness(inv: Invocation) -> Result:
    opts = Opts(inv)
    require_tools("ffmpeg")
    session = inv.connect()
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    name = timeline.GetName()
    track = int(opts.track) if opts.track is not None else None
    picked = _pick(inv, timeline, track)
    where = f"on audio track A{track}" if track is not None else "on audio tracks"
    scope = f"{where} in timeline {name}"
    base: dict[str, object] = {"timeline": name, "track": f"A{track}" if track else "all"}
    if not picked:
        return Result(
            {**base, "items": 0, "rows": empty_state("audio items", scope, {})},
            [hint("items --track-type audio", "list the audio items")],
        )
    measured = am.measure_items(
        [am.read_item(one) for one in picked],
        silence_db=am.SILENCE_DB,
        silence_s=am.SILENCE_S,
        jobs=int(opts.jobs or am.JOBS),
    )
    failed = [m for m in measured if m.error]
    if len(failed) == len(measured):
        raise AxiError(
            "API_FAILED",
            f"every audio item failed ({len(measured)})",
            help=[f"{m.info.ref}: {m.error}" for m in failed[:5]],
        )
    off = am.outliers(measured, opts.outlier or am.OUTLIER_LU)
    estimates = [e for m in measured if (e := am.estimate(m)) is not None]
    raw_centre = am.median(estimates)
    centre = round(raw_centre, 2) if raw_centre is not None else None
    rows = [_row(m, opts.target, off) for m in measured]
    shown_default = tuple(
        f
        for f in (*DEFAULT_FIELDS, *(("note",) if any(r["note"] for r in rows) else ()))
        if f != "delta" or opts.target is not None
    ) + (("error",) if failed else ())
    shown, total = apply_limit(rows, int(opts.limit or 1))
    data: dict[str, object] = {
        **base,
        "items": len(measured),
        "measured": sum(1 for m in measured if m.info.has_audio) - len(failed),
        "errors": len(failed),
        "median_lufs": centre,
        "range": f"{min(estimates):g}..{max(estimates):g}" if estimates else None,
    }
    if opts.target is not None:
        data["target"] = opts.target
        if centre is not None:
            data["program_hint"] = _program_hint(centre, opts.target, opts.tolerance or 0.0)
    data["rows"] = select_fields(shown, shown_default, opts.fields)
    return Result(data, _help(measured, off, opts.target, len(shown), total))


LOUDNESS_SPEC = CommandSpec(
    name="loudness",
    summary="Estimate what each audio item contributes in LUFS (ffmpeg on the used source range)",
    run=run_loudness,
    positionals=(
        Positional(
            "items",
            required=False,
            help="audio items, comma-separated: A1:3 or unique ids (default: every audio item)",
        ),
    ),
    flags=(
        TIMELINE_FLAG,
        Flag("track", "int", None, help="only this audio track number (1-based)"),
        Flag("target", "float", None, help="program loudness to compare with, -60..0 LUFS"),
        Flag("tolerance", "float", 1.0, help="LU within the target that counts as on target"),
        Flag(
            "outlier-lu",
            "float",
            am.OUTLIER_LU,
            help="LU from the median that flags an item as an outlier",
        ),
        Flag("jobs", "int", am.JOBS, help=f"items measured at once (1-{am.MAX_JOBS})"),
        Flag("fields", "list", None, help="comma-separated fields to show"),
        Flag("limit", "int", DEFAULT_LIMIT, help="maximum rows to show"),
    ),
    examples=(
        "davinci-axi audio loudness",
        "davinci-axi audio loudness --track 1 --target -14",
        "davinci-axi audio loudness A1:1,A1:2 --fields item,lufs,silent_pct",
        "davinci-axi audio loudness --outlier-lu 3",
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
        *methods_of(Timeline="GetTrackCount"),
    ),
    notes=(
        "lufs is an estimate: the integrated loudness of the used source range plus the clip "
        "volume (AudioVolume, dB); track effects and Fairlight processing are not included",
        "ffmpeg is an optional program on PATH (FFMPEG names a binary); without it this verb "
        "fails with NOT_AVAILABLE before it touches Resolve",
        "The measured window is the item's used source range at the clip's frame rate",
        "An item is an outlier when its estimate is more than --outlier-lu LU (default 6) from "
        "the median of the measured items; outliers get a note and an `audio gain` hint",
        "silent_pct is the share of the used range below -35 dB for 0.7 s or longer (the same "
        "rule `timeline check --audio` uses to call an item mostly silent at 80% or more)",
    ),
)
