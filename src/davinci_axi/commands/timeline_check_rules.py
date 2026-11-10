"""The rules behind `timeline check` (A): each takes a `CheckContext` and returns findings.

Every rule is read-only. Item spans are `[GetStart, GetEnd)` in absolute timeline frames.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise

from davinci_axi.commands.item_detail import Located
from davinci_axi.commands.render_frame import frame_to_timecode
from davinci_axi.resolve_types import MediaPoolItemApi

FPS_TOLERANCE = 0.01
UPSCALE_TOLERANCE = 1.01
RENDER_COST_FACTOR = 40
_SIZE = re.compile(r"(\d+)\s*[xX]\s*(\d+)")
Span = tuple[int, int]


@dataclass(frozen=True)
class Finding:
    level: str  # error | warn | info
    check: str
    where: str
    detail: str
    group: str = ""  # sentence shared by rows that collapse into one TOON row; "" = never grouped
    clip: str = ""  # media pool clip name a fix hint can target; "" = no clip-level fix


@dataclass(frozen=True)
class CheckContext:
    fps: float
    width: int
    height: int
    start: int
    end: int
    track: int | None
    video: list[Located]
    audio: list[Located]


def label(located: Located) -> str:
    return f"{located.track}:{located.position}"


def span_of(located: Located) -> Span:
    return round(located.item.GetStart()), round(located.item.GetEnd())


def span_text(first: int, last: int, fps: float) -> str:
    """`86400-86440 (01:00:00:00-01:00:01:16)`."""
    return f"{first}-{last} ({frame_to_timecode(first, fps)}-{frame_to_timecode(last, fps)})"


def merged(spans: list[Span]) -> list[Span]:
    """Union of `spans`, sorted, touching spans joined."""
    out: list[Span] = []
    for first, last in sorted(spans):
        if out and first <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], last))
        else:
            out.append((first, last))
    return out


def _by_start(items: list[Located]) -> list[Located]:
    return sorted(items, key=lambda located: span_of(located)[0])


def check_empty(ctx: CheckContext) -> list[Finding]:
    if ctx.track is not None and not ctx.video:
        return [
            Finding("error", "empty", f"V{ctx.track}", f"video track V{ctx.track} has no clips")
        ]
    if not ctx.video and not ctx.audio:
        return [Finding("error", "empty", "timeline", "the timeline has no clips")]
    return []


def check_gaps(ctx: CheckContext) -> list[Finding]:
    """Gaps on the base video track only: overlays on V2 and up are expected to have holes."""
    if not ctx.video:
        return []
    base = ctx.video[0].track
    items = _by_start([located for located in ctx.video if located.track == base])
    found: list[Finding] = []
    first, last = span_of(items[0])
    if first > ctx.start:
        text = span_text(ctx.start, first, ctx.fps)
        found.append(
            Finding(
                "warn",
                "gaps",
                label(items[0]),
                f"leading gap of {first - ctx.start} frames, {text}",
            )
        )
    prev, reach = items[0], last
    for located in items[1:]:
        begin, finish = span_of(located)
        if begin > reach:
            text = span_text(reach, begin, ctx.fps)
            where = f"{label(prev)}→{label(located)}"
            found.append(Finding("warn", "gaps", where, f"gap of {begin - reach} frames, {text}"))
        if finish > reach:
            prev, reach = located, finish
    return found


def check_overlaps(ctx: CheckContext) -> list[Finding]:
    """Overlapping neighbours: a side with no media pool clip is a transition, else an error."""
    found: list[Finding] = []
    seen: set[str] = set()
    for track in dict.fromkeys(located.track for located in ctx.video):
        items = _by_start([located for located in ctx.video if located.track == track])
        for left, right in pairwise(items):
            left_end, (right_start, _) = span_of(left)[1], span_of(right)
            if right_start >= left_end:
                continue
            frames = left_end - right_start
            text = span_text(right_start, left_end, ctx.fps)
            bare = [x for x in (left, right) if x.item.GetMediaPoolItem() is None]
            if not bare:
                where = f"{label(left)}→{label(right)}"
                names = f"{left.item.GetName()} overlaps {right.item.GetName()}"
                detail = f"{names} by {frames} frames, {text}"
                found.append(Finding("error", "overlap", where, detail))
            for side in bare:
                if label(side) in seen:
                    continue
                seen.add(label(side))
                detail = (
                    f"transition or generator overlapping its neighbour by {frames} frames, {text}"
                )
                found.append(Finding("info", "transition", label(side), detail))
    return found


def check_offline(ctx: CheckContext) -> list[Finding]:
    """One error per offline media pool clip, listing every place it is used."""
    used: dict[str, list[str]] = {}
    for located in [*ctx.video, *ctx.audio]:
        clip = located.item.GetMediaPoolItem()
        if clip is None:
            continue
        if str(clip.GetClipProperty("Online Status") or "").strip().lower() == "offline":
            used.setdefault(clip.GetName(), []).append(label(located))
    return [
        Finding("error", "offline", places[0], f"{name} is offline (used at {', '.join(places)})")
        for name, places in used.items()
    ]


def _number(value: object) -> float | None:
    try:
        return float(str(value))
    except ValueError:
        return None


def check_fps(ctx: CheckContext) -> list[Finding]:
    found: list[Finding] = []
    for located in ctx.video:
        clip = located.item.GetMediaPoolItem()
        rate = _number(clip.GetClipProperty("FPS")) if clip is not None else None
        if clip is None or rate is None or abs(rate - ctx.fps) <= FPS_TOLERANCE:
            continue
        group = (
            f"{rate:g} fps on a {ctx.fps:g} fps timeline (Resolve conforms it to the timeline rate)"
        )
        detail = f"{clip.GetName()} is {group}"
        found.append(Finding("warn", "fps", label(located), detail, group))
    return found


def super_scale_of(clip: MediaPoolItemApi) -> int:
    """GetClipProperty('Super Scale') as 1..4; 1 when missing, unreadable or out of range."""
    number = _number(clip.GetClipProperty("Super Scale"))
    if number is None or not 1 <= number <= 4:
        return 1
    return int(number)


def _shape(width: int, height: int) -> str:
    return "landscape" if width > height else "portrait" if height > width else "square"


def check_resolution(ctx: CheckContext) -> list[Finding]:
    """Orientation mismatch, or else upscaling (`max(width / w, height / h)`).

    One row per item; the presenter groups them. A clip's Super Scale covers an upscale up to
    that factor, which turns the row into info.
    """
    found: list[Finding] = []
    target = f"{ctx.width}x{ctx.height}"
    for located in ctx.video:
        clip = located.item.GetMediaPoolItem()
        match = _SIZE.search(str(clip.GetClipProperty("Resolution") or "")) if clip else None
        if clip is None or match is None:
            continue
        width, height = int(match.group(1)), int(match.group(2))
        if width < 1 or height < 1:
            continue
        shape, timeline_shape = _shape(width, height), _shape(ctx.width, ctx.height)
        name = clip.GetName()
        if "square" not in (shape, timeline_shape) and shape != timeline_shape:
            group = f"{width}x{height} ({shape}) on a {target} {timeline_shape} timeline"
            detail = f"{name} is {group}"
            found.append(Finding("warn", "resolution", label(located), detail, group))
            continue
        scale = max(ctx.width / width, ctx.height / height)
        if scale > UPSCALE_TOLERANCE:
            ss = super_scale_of(clip)
            covered = scale <= ss * UPSCALE_TOLERANCE
            group = f"{width}x{height} upscaled {scale:.1f}x on a {target} timeline"
            if covered:
                group += f", covered by Super Scale {ss}x"
            elif ss > 1:
                group += f" (Super Scale {ss}x does not cover it)"
            level = "info" if covered else "warn"
            found.append(
                Finding(level, "resolution", label(located), f"{name} is {group}", group, name)
            )
    return found


def check_render_cost(ctx: CheckContext) -> list[Finding]:
    """One info row when video items sit on clips with Super Scale 2x or more (slow renders)."""
    heavy = 0
    for located in ctx.video:
        clip = located.item.GetMediaPoolItem()
        if clip is not None and super_scale_of(clip) >= 2:
            heavy += 1
    if not heavy:
        return []
    detail = (
        f"{heavy} of {len(ctx.video)} video items use Super Scale (2x-4x): renders take about "
        f"{RENDER_COST_FACTOR}x longer (live: 40 s became 28 min for 209 s); "
        "turn it off for drafts"
    )
    return [Finding("info", "render_cost", f"{heavy} items", detail)]


def check_silence(ctx: CheckContext) -> list[Finding]:
    """Video spans that no enabled audio item covers."""
    video = merged([span_of(located) for located in ctx.video])
    audio = merged([span_of(a) for a in ctx.audio if a.item.GetClipEnabled()])
    found: list[Finding] = []
    for first, last in video:
        cursor = first
        for begin, finish in audio:
            if finish <= cursor or begin >= last:
                continue
            if begin > cursor:
                found.append(_silent(cursor, begin, ctx.fps))
            cursor = max(cursor, finish)
        if cursor < last:
            found.append(_silent(cursor, last, ctx.fps))
    return found


def _silent(first: int, last: int, fps: float) -> Finding:
    detail = f"no audio under the picture for {last - first} frames, {span_text(first, last, fps)}"
    return Finding("info", "silence", "audio", detail)


def check_disabled(ctx: CheckContext) -> list[Finding]:
    return [
        Finding(
            "info",
            "disabled",
            label(located),
            f"{located.item.GetName()} is disabled",
            "disabled",
        )
        for located in [*ctx.video, *ctx.audio]
        if not located.item.GetClipEnabled()
    ]


def fmt_seconds(seconds: float) -> str:
    """`5 s`, `2 m`, `3 m 30 s`."""
    if seconds < 60:
        return f"{seconds:g} s"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)} m" + (f" {rest:g} s" if rest else "")


def check_length(
    ctx: CheckContext, target: tuple[float | None, float | None], text: str
) -> list[Finding]:
    low, high = target
    seconds = (ctx.end - ctx.start) / ctx.fps
    shown = f"{seconds:.1f} s"
    if high is not None and seconds > high:
        return [
            Finding(
                "error", "length", "timeline", f"{shown} is over the {fmt_seconds(high)} maximum"
            )
        ]
    if low is not None and seconds < low:
        return [
            Finding(
                "error", "length", "timeline", f"{shown} is under the {fmt_seconds(low)} minimum"
            )
        ]
    return [Finding("info", "length", "timeline", f"{shown} is within the {text} target")]
