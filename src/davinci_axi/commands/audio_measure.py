"""The shared loudness engine of the `audio` verbs: what each audio item contributes (round 2, A).

Resolve reads happen on the main thread (`read_item`); only the ffmpeg subprocesses run in the
worker pool, because the scripting bridge is not known to be thread-safe. Everything after the
measurement (estimates, medians, outliers, timeline mapping) is a pure function so `audio
loudness`, `audio gaps` and `timeline check --audio` compute the same numbers.

The loudness of an item is an estimate: the integrated LUFS of the used source range plus the
clip's `AudioVolume` in dB. Track effects and Fairlight processing are outside it.
"""

from __future__ import annotations

import statistics
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from davinci_axi.commands import ffmpeg_common
from davinci_axi.commands.item_detail import Located, walk_items
from davinci_axi.errors import AxiError
from davinci_axi.output import hint
from davinci_axi.resolve_types import TimelineApi

SILENCE_DB, SILENCE_S, JOBS, MAX_JOBS = -35.0, 0.7, 4, 16  # mirror media_analyze
OUTLIER_LU = 6.0


@dataclass(frozen=True)
class ItemInfo:
    """What Resolve says about one audio timeline item (read on the main thread)."""

    ref: str
    id: str
    name: str
    start: int
    end: int
    enabled: bool
    source_start: int
    source_end: int
    clip_fps: float | None
    path: str
    volume_db: float | None
    has_audio: bool


@dataclass(frozen=True)
class Measured:
    info: ItemInfo
    lufs: float | None
    true_peak: float | None
    silences: list[tuple[float, float]]  # seconds from the window start
    error: str | None


def _float(value: object) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def read_item(located: Located) -> ItemInfo:
    """Read one item from Resolve; tolerant of missing properties and missing media."""
    item = located.item
    props = item.GetProperties() or {}
    media = item.GetMediaPoolItem()
    kind = str(media.GetClipProperty("Type") or "") if media is not None else ""
    path = str(media.GetClipProperty("File Path") or "") if media is not None else ""
    fps = _float(media.GetClipProperty("FPS")) if media is not None else None
    return ItemInfo(
        ref=f"{located.track}:{located.position}",
        id=item.GetUniqueId(),
        name=item.GetName(),
        start=int(item.GetStart()),
        end=int(item.GetEnd()),
        enabled=bool(item.GetClipEnabled()),
        source_start=int(item.GetSourceStartFrame()),
        source_end=int(item.GetSourceEndFrame()),
        clip_fps=fps if fps and fps > 0 else None,
        path=path,
        volume_db=_float(props.get("AudioVolume")),
        has_audio=media is not None and (not kind or "Audio" in kind),
    )


def audio_items(timeline: TimelineApi, track: int | None) -> list[Located]:
    """Every audio item, track by track (or only audio track `track`)."""
    return list(walk_items(timeline, ("audio",), track))


def window(info: ItemInfo) -> tuple[float, float] | None:
    """`(start_s, duration_s)` of the used source range; source frames count at the clip's rate."""
    span = info.source_end - info.source_start
    if info.clip_fps is None or span <= 0:
        return None
    return info.source_start / info.clip_fps, span / info.clip_fps


def _measure_one(info: ItemInfo, silence_db: float, silence_s: float) -> Measured:
    if not info.has_audio:
        return Measured(info, None, None, [], None)
    if not info.path:
        return Measured(info, None, None, [], "no file path")
    path = Path(info.path)
    if not path.is_file():
        return Measured(info, None, None, [], "file not found")
    span = window(info)
    try:
        heard = ffmpeg_common.loudness(
            path,
            silence_db=silence_db,
            silence_s=silence_s,
            start_s=span[0] if span else None,
            duration_s=span[1] if span else None,
        )
    except (AxiError, OSError) as err:
        return Measured(
            info, None, None, [], err.message if isinstance(err, AxiError) else str(err)
        )
    return Measured(info, heard.lufs, heard.true_peak, heard.silences, None)


def measure_items(
    infos: list[ItemInfo], *, silence_db: float, silence_s: float, jobs: int
) -> list[Measured]:
    """One ffmpeg pass per item, `jobs` at a time; a failure becomes that item's `error`."""
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        return list(pool.map(lambda info: _measure_one(info, silence_db, silence_s), infos))


def estimate(m: Measured) -> float | None:
    """Source LUFS plus the clip volume in dB (before track effects); None when either is."""
    if m.lufs is None or m.info.volume_db is None:
        return None
    return round(m.lufs + m.info.volume_db, 2)


def estimate_peak(m: Measured) -> float | None:
    if m.true_peak is None or m.info.volume_db is None:
        return None
    return round(m.true_peak + m.info.volume_db, 2)


def silent_share(m: Measured) -> float | None:
    """The 0..1 share of the measured window that `silencedetect` called silent."""
    span = window(m.info)
    if span is None or m.error is not None or not m.info.has_audio:
        return None
    total = span[1]
    silent = sum(max(0.0, min(b, total) - max(a, 0.0)) for a, b in m.silences)
    return round(min(1.0, silent / total), 4)


def on_timeline(m: Measured, fps: float) -> list[tuple[int, int]]:
    """Silences as timeline frames `(start, end)`, clamped to the item's span."""
    info = m.info
    spans: list[tuple[int, int]] = []
    for a, b in m.silences:
        first = min(info.end, max(info.start, info.start + round(a * fps)))
        last = min(info.end, max(info.start, info.start + round(b * fps)))
        if last > first:
            spans.append((first, last))
    return spans


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def outliers(measured: list[Measured], lu: float = OUTLIER_LU) -> dict[str, float]:
    """`ref -> signed LU from the median estimate` for items more than `lu` away from it."""
    estimates = {m.info.ref: e for m in measured if (e := estimate(m)) is not None}
    centre = median(list(estimates.values()))
    if centre is None:
        return {}
    deltas = {ref: round(value - centre, 1) for ref, value in estimates.items()}
    return {ref: delta for ref, delta in deltas.items() if abs(delta) > lu}


def gain_hint(refs: list[str], db: float) -> str:
    return hint(
        f"audio gain {','.join(refs)} --db {db:+.1f}", "move the estimate by that many decibels"
    )
