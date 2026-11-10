"""`render deliver --max-mb`: queue a render job whose bit rate fits a size cap (U5, feature D).

The only size control Resolve exposes is `VideoQuality` in kbps (F28c), so this works the rate out
from the duration and hands the job to `render_jobs.run_add` with `VideoQuality=<kbps>` added to
`--set`. It queues only: the file size is predicted, not measured.
"""

from __future__ import annotations

import math
from dataclasses import replace

from davinci_axi.commands import render_jobs
from davinci_axi.commands.render_common import (
    DIR_BOOLS,
    methods_of,
    parse_settings,
    render_project,
)
from davinci_axi.commands.render_floor import (
    FLOOR_BASE_KBPS,
    FLOOR_KBPS_PER_MPIXEL,
    FLOOR_MARGIN_PCT,
    drift_pct,
    predicted_kbps,
    required_kbps,
)
from davinci_axi.commands.render_floor import encoder_floor_kbps as encoder_floor_kbps
from davinci_axi.commands.timeline_marks import video_marks
from davinci_axi.commands.timeline_tracks import context_words, text_arg
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import force_cmd, keep_page
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Result

MIN_VIDEO_KBPS = 500
MAX_HEADROOM_PCT = 50.0
DEFAULT_AUDIO_KBPS = 320
DEFAULT_HEADROOM_PCT = 5.0  # a buffer on top of the modelled encoder overshoot
KNOWN_CODECS = ("h264", "h265", "hevc")
MB_BYTES = 1_000_000  # --max-mb and every *_mb value are decimal megabytes


def _whole(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def output_size(
    settings: dict[str, object], timeline: TimelineApi, session: Session
) -> tuple[int, int]:
    """The frame size the job renders: `--set` FormatWidth/Height, else the timeline's, else the
    project's; `(0, 0)` when none is known (the floor is then skipped)."""
    width, height = _whole(settings.get("FormatWidth")), _whole(settings.get("FormatHeight"))
    if width > 0 and height > 0:
        return width, height
    for source in (timeline.GetSettings(), session.require_project().GetSettings()):
        width = _whole((source or {}).get("timelineResolutionWidth"))
        height = _whole((source or {}).get("timelineResolutionHeight"))
        if width > 0 and height > 0:
            return width, height
    return 0, 0


def fitting_size(width: int, height: int, kbps: int) -> tuple[int, int] | None:
    """A size at which `kbps` passes `required_kbps`: same aspect, even sides, at least 2.

    `None` when the rate is under the floor's base at any size."""
    budget = kbps / (1 + FLOOR_MARGIN_PCT / 100)
    if budget <= FLOOR_BASE_KBPS or width <= 0 or height <= 0:
        return None
    mp = (budget - FLOOR_BASE_KBPS) / FLOOR_KBPS_PER_MPIXEL
    scale = math.sqrt(mp / (width * height / 1e6))

    def side(value: float) -> int:
        return max(2, round(value) // 2 * 2)

    fit_w, fit_h = side(width * scale), side(height * scale)
    while required_kbps(fit_w, fit_h) > kbps and fit_w > 2:
        fit_w -= 2
        fit_h = side(fit_w * height / width)
    return fit_w, fit_h


def needed_mb(width: int, height: int, seconds: float, audio: int, headroom: float) -> float:
    """The smallest cap (0.01 MB steps) whose video rate passes `required_kbps` at this size."""
    required, floor = required_kbps(width, height), encoder_floor_kbps(width, height)
    rate = predicted_kbps(required, floor) + audio
    value = math.ceil(rate * seconds / 8 / 1000 / (1 - headroom / 100) * 100) / 100
    while deliver_kbps(value, seconds, audio, headroom, floor) < required:
        value = round(value + 0.01, 2)
    return value


def _below_floor_error(
    size: tuple[int, int],
    max_mb: float,
    kbps: int,
    audio: int,
    seconds: float,
    headroom: float,
    given_size: bool,
) -> AxiError:
    width, height = size
    floor, required = encoder_floor_kbps(width, height), required_kbps(width, height)
    needed = needed_mb(width, height, seconds, audio, headroom)
    fit = fitting_size(width, height, kbps)
    if fit is None:
        smaller = f"No smaller size fits: the floor stays near {FLOOR_BASE_KBPS} kbps at any size"
    else:
        again = "Render smaller still" if given_size else "Render smaller"
        smaller = f"{again}: --set FormatWidth={fit[0]},FormatHeight={fit[1]}"
    return AxiError(
        "VALIDATION_ERROR",
        f"video gets {kbps} kbps but Resolve's H.264/H.265 encoder stays near {floor} kbps at "
        f"{width}x{height} (estimate from live renders; with {FLOOR_MARGIN_PCT:g}% safety the "
        f"job needs {required} kbps), so the {max_mb:g} MB cap is likely to be exceeded",
        help=[
            smaller,
            f"Or raise the cap: --max-mb {needed:g}",
            "Or shorten the range",
            "Or pass --below-floor to queue anyway",
        ],
    )


def deliver_kbps(
    max_mb: float, seconds: float, audio_kbps: int, headroom_pct: float, floor: int
) -> int:
    """The largest video rate (>= 500) whose modelled render fits `max_mb` less `headroom_pct`.

    `predicted_kbps(kbps, floor) + audio_kbps <= max_mb * 8000 / seconds * (1 - headroom / 100)`,
    solved by bisection (the left side only grows with `kbps`). `max_mb` is decimal megabytes
    (1 MB = 1,000,000 bytes); `floor` 0 (unknown) models the far-from-floor overshoot only."""
    spare = max_mb * 8 * 1000 / seconds * (1 - headroom_pct / 100)
    low, high = MIN_VIDEO_KBPS, max(MIN_VIDEO_KBPS, math.floor(spare) - audio_kbps)
    if predicted_kbps(low, floor) + audio_kbps > spare:
        left = math.floor((spare - audio_kbps) / (1 + drift_pct(low, floor) / 100))
        raise AxiError(
            "VALIDATION_ERROR",
            f"{max_mb:g} MB over {seconds:.1f} s leaves {left} kbps for video, "
            f"below {MIN_VIDEO_KBPS} kbps",
            help=[
                "Raise --max-mb, or shorten the range (in/out marks or --set MarkIn=..,MarkOut=..)",
                "Or lower --audio-kbps / --headroom",
            ],
        )
    while low < high:
        mid = (low + high + 1) // 2
        if predicted_kbps(mid, floor) + audio_kbps <= spare:
            low = mid
        else:
            high = mid - 1
    return low


def _frame_rate(session: Session, timeline: TimelineApi) -> float:
    """The timeline's `timelineFrameRate`, else the project's; Resolve may return a string."""
    for settings in (timeline.GetSettings(), session.require_project().GetSettings()):
        try:
            rate = float((settings or {}).get("timelineFrameRate") or 0)
        except (TypeError, ValueError):
            continue
        if rate > 0:
            return rate
    raise api_failed("GetSettings", "no timeline or project frame rate")


def _frames(timeline: TimelineApi, wanted: dict[str, object], all_frames: bool) -> int:
    """Frames the job renders: `--set` marks, else the timeline's video marks, else the whole."""
    full = int(timeline.GetEndFrame()) - int(timeline.GetStartFrame())
    if all_frames or wanted.get("SelectAllFrames") is True:
        return full
    low, high = wanted.get("MarkIn"), wanted.get("MarkOut")
    if isinstance(low, int) and isinstance(high, int):
        return high - low + 1
    marks = video_marks(timeline)
    if marks is not None:
        return marks.frames
    return full


def timeline_seconds(
    session: Session, timeline: TimelineApi, wanted: dict[str, object], all_frames: bool
) -> float:
    """Seconds the job renders. Mark ranges count inclusive (`out - in + 1`), live-verified."""
    frames = _frames(timeline, wanted, all_frames)
    if frames <= 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"the range to render holds {frames} frames",
            help=["Check the in/out marks or --set MarkIn=..,MarkOut=.."],
        )
    return frames / _frame_rate(session, timeline)


def _codec_warning(codec: str, preset: str | None) -> str:
    flat = codec.lower().replace(".", "").replace("-", "").replace(" ", "")
    if preset is None and any(known in flat for known in KNOWN_CODECS):
        return ""
    where = f"the preset {preset} sets the codec, and " if preset else f"codec {codec or '?'}: "
    return f"{where}VideoQuality's kbps unit is verified for H.264/H.265 only"


def _bounded(name: str, value: object, low: float, high: float | None, rule: str) -> float:
    """`value` as a float within `low`..`high`, or VALIDATION_ERROR saying `rule`."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number >= low and (high is None or number <= high):
            return number
    raise AxiError(
        "VALIDATION_ERROR",
        f"--{name} must be {rule}, got {value}",
        help=[f"Valid example: davinci-axi render deliver --target-dir ~/out --{name} 10"],
    )


def _force_line(inv: Invocation, max_mb: float, audio: int, headroom: float) -> str:
    """The `render deliver` force command that repeats every flag the user gave."""
    cmd = [
        "render",
        "deliver",
        "--target-dir",
        str(inv.args["target_dir"]),
        "--max-mb",
        f"{max_mb:g}",
        "--audio-kbps",
        str(audio),
        "--headroom",
        f"{headroom:g}",
    ]
    for flag, key in (("--preset", "preset"), ("--name", "name"), ("--set", "set")):
        value = text_arg(inv, key)
        if value is not None:
            cmd.extend([flag, value])
    given = [
        f"--{flag}"
        for flag in (*DIR_BOOLS, "all-frames", "below-floor")
        if inv.args.get(flag.replace("-", "_"))
    ]
    return force_cmd(inv, *cmd, *given, *context_words(inv))


def run_deliver(inv: Invocation) -> Result:
    max_mb = _bounded("max-mb", inv.args.get("max_mb"), 0, None, "greater than 0")
    if max_mb <= 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--max-mb must be greater than 0, got {max_mb:g}",
            help=["Valid example: davinci-axi render deliver --target-dir ~/out --max-mb 50"],
        )
    audio = int(_bounded("audio-kbps", inv.args.get("audio_kbps"), 0, None, "0 or more"))
    headroom = _bounded("headroom", inv.args.get("headroom"), 0, MAX_HEADROOM_PCT, "0 to 50")
    given = text_arg(inv, "set")
    settings = parse_settings(given)
    if "VideoQuality" in settings:
        raise AxiError(
            "VALIDATION_ERROR",
            "deliver computes VideoQuality; drop it from --set",
            help=["Use `render add --set VideoQuality=..` to choose the bit rate yourself"],
        )
    session = inv.connect()
    project = render_project(session)
    timeline = session.require_timeline(text_arg(inv, "timeline"))
    seconds = timeline_seconds(session, timeline, settings, bool(inv.args.get("all_frames")))
    width, height = output_size(settings, timeline, session)
    floor = encoder_floor_kbps(width, height)
    kbps = deliver_kbps(max_mb, seconds, audio, headroom, floor)
    preset = text_arg(inv, "preset")
    codec = str((project.GetCurrentRenderFormatAndCodec() or {}).get("codec") or "")
    required = required_kbps(width, height)
    known = _codec_warning(codec, preset) == ""
    below = bool(required) and kbps < required
    if below and known and not inv.args.get("below_floor"):
        given_size = "FormatWidth" in settings or "FormatHeight" in settings
        raise _below_floor_error(
            (width, height), max_mb, kbps, audio, seconds, headroom, given_size
        )
    set_text = ",".join(filter(None, [given, f"VideoQuality={kbps}"]))
    res = render_jobs.run_add(replace(inv, args={**inv.args, "set": set_text}))
    drift = drift_pct(kbps, floor)
    predicted = predicted_kbps(kbps, floor)
    worst = round((max(predicted, required) + audio) * seconds / 8 / 1000, 2)
    deliver: dict[str, object] = {
        "seconds": round(seconds, 2),
        "max_mb": max_mb,
        "video_kbps": kbps,
        "audio_kbps": audio,
        "headroom_pct": headroom,
        "predicted_mb": round((kbps + audio) * seconds / 8 / 1000, 2),
        "predicted_mb_worst": worst,
        "drift_pct": round(drift, 1),
        "predicted_kbps": predicted,
        "output_size": f"{width}x{height}" if floor else None,
        "floor_kbps": floor or None,
        "required_kbps": required or None,
        "floor_margin_pct": FLOOR_MARGIN_PCT,
    }
    data = {**res.data, "command": "render deliver", "deliver": deliver}
    warnings = [_codec_warning(codec, preset)]
    if below:
        warnings.append(
            f"video_kbps {kbps} is under the {required} kbps the encoder floor at "
            f"{width}x{height} needs; the cap is likely to be exceeded"
        )
    if worst > max_mb:
        warnings.append(
            f"predicted {worst:g} MB with {drift:.1f}% encoder drift exceeds the "
            f"{max_mb:g} MB cap; raise --headroom or --max-mb"
        )
    warning = "; ".join(filter(None, warnings))
    if warning:
        previous = data.get("warning")
        data["warning"] = f"{previous}; {warning}" if previous else warning
    help_lines = list(res.help)
    if help_lines and not res.data.get("applied"):
        help_lines[0] = f"Run `{_force_line(inv, max_mb, audio, headroom)}` to apply this change"
    if res.data.get("applied"):
        back: dict[str, object] = {"VideoQuality": 0}
        formatted = "FormatWidth" in settings or "FormatHeight" in settings
        tw, th = output_size({}, timeline, session) if formatted else (0, 0)
        if formatted and tw > 0 and th > 0:
            back.update({"FormatWidth": tw, "FormatHeight": th})
        with keep_page(session) as page_state:
            reset = bool(project.SetRenderSettings(back))
        data["video_quality_reset"] = reset
        if "FormatWidth" in back and reset:
            data["format_reset"] = f"{tw}x{th}"
        elif formatted:
            data["format_left"] = f"{width}x{height}"
            help_lines.append(
                "FormatWidth/FormatHeight stay set; run `render settings set "
                "FormatWidth=<w>,FormatHeight=<h> --force` to put them back"
            )
        if "page_restored" not in data:
            data.update(page_state)
        if not reset:
            data["video_quality_left"] = kbps
            help_lines.append(
                "VideoQuality stays set in the project; once the job has rendered, run "
                "`render settings set VideoQuality=0 --force` to reset it"
            )
    return Result(data, help_lines, res.exit_code)


DELIVER_SPEC = CommandSpec(
    name="deliver",
    summary="Queue a render job sized to a maximum file size (computes VideoQuality in kbps)",
    run=run_deliver,
    flags=(
        Flag(
            "max-mb",
            "float",
            None,
            required=True,
            help="largest file size wanted, in decimal MB (1 MB = 1,000,000 bytes)",
        ),
        Flag(
            "audio-kbps",
            "int",
            DEFAULT_AUDIO_KBPS,
            help="audio bit rate to reserve, in kbps; Resolve's AAC default is 320 and no "
            "render setting changes it",
        ),
        Flag(
            "headroom",
            "float",
            DEFAULT_HEADROOM_PCT,
            help="extra buffer on top of the modelled encoder overshoot, in percent of the "
            "budget (0-50; default 5%)",
        ),
        Flag(
            "below-floor",
            "bool",
            False,
            help="queue even when video_kbps is below the estimated encoder floor",
        ),
        *render_jobs.ADD_SPEC.flags,
    ),
    examples=(
        "davinci-axi render deliver --target-dir ~/out --max-mb 50 --mkdir",
        "davinci-axi render deliver --target-dir ~/out --max-mb 25 --headroom 10 --all-frames",
    ),
    notes=(
        "VideoQuality is sized so that the modelled H.264 overshoot (30% at the encoder floor, "
        "2% at 2.5x the floor, fitted to live renders) plus --headroom fits the cap: the "
        "largest video kbps with kbps * (1 + drift) + audio-kbps <= max-mb * 8 * 1000 / seconds "
        "* (1 - headroom / 100); refused under 500 kbps",
        "seconds come from --set MarkIn/MarkOut, else the timeline's in/out marks (unless "
        "--all-frames), else the whole timeline, at the timeline's frame rate",
        "VideoQuality's kbps unit is verified for H.264/H.265 only; the dry run warns for others",
        "Mark ranges count inclusive (out - in + 1); the timeline's marks are stored relative to "
        "its start, the frame count is the same either way",
        "audio is budgeted at 320 kbps (Resolve's AAC rate; the API has no audio bit-rate setting)",
        "drift_pct, predicted_kbps and predicted_mb_worst show the overshoot the model applied "
        "at the chosen rate",
        "A queued job keeps its own VideoQuality (live-verified), so the project value is reset "
        "to 0 (automatic) right after queueing (video_quality_reset); if Resolve refuses the "
        "reset, video_quality_left says so",
        "Resolve's H.264/H.265 encoder stayed near 900 + 900 kbps per megapixel live (2.77 Mbps "
        "at 1080x1920, 1.75 at 728x1294); a rate under that plus 10% safety is refused with a "
        "smaller --set FormatWidth/FormatHeight and a bigger --max-mb that both pass this check, "
        "unless --below-floor queues it anyway (then it only warns)",
        "FormatWidth/FormatHeight given to --set are put back to the timeline's resolution after "
        "queueing (format_reset), as VideoQuality is",
        "MB means decimal megabytes; 5 MB is 5,000,000 bytes (4.77 MiB)",
        "Queues only: run `render start <job> --force` to render; the size is predicted, "
        "not measured",
    ),
    reads_only=False,
    methods=(
        *render_jobs.ADD_SPEC.methods,
        *methods_of(
            Timeline="GetStartFrame GetEndFrame GetSettings GetMarkInOut",
            Project="GetCurrentRenderFormatAndCodec GetSettings",
        ),
    ),
)
