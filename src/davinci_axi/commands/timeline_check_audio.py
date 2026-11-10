"""`timeline check --audio` (round 2, C): loudness findings from the shared measuring engine.

Opt-in, so the default `timeline check` never needs ffmpeg. Levels are estimates (source LUFS plus
the clip gain, before track effects), the same numbers `audio loudness` prints.
"""

from __future__ import annotations

import re

from davinci_axi.commands import audio_measure as am
from davinci_axi.commands.timeline_check_rules import CheckContext, Finding
from davinci_axi.errors import AxiError
from davinci_axi.output import hint
from davinci_axi.spec import Flag, Invocation

__all__ = ["AUDIO_FLAGS", "SILENT_ITEM", "audio_hints", "check_audio", "check_audio_flags"]

SILENT_ITEM = 0.8  # share of the used range that is silent
DEFAULT_TOLERANCE = 1.0
AUDIO_FLAGS = (
    Flag(
        "audio",
        "bool",
        False,
        help="measure each audio item with ffmpeg: loudness outliers and mostly-silent items",
    ),
    Flag("lufs", "float", None, help="with --audio: warn when the median is off this LUFS"),
    Flag(
        "lufs-tolerance",
        "float",
        None,
        help=f"with --audio --lufs: allowed distance in LU (default {DEFAULT_TOLERANCE:g})",
    ),
    Flag(
        "outlier-lu",
        "float",
        None,
        help=f"with --audio: LU from the median that flags an item (default {am.OUTLIER_LU:g})",
    ),
)


def _bad(message: str, *help_lines: str) -> AxiError:
    return AxiError("VALIDATION_ERROR", message, help=list(help_lines))


def _number(inv: Invocation, name: str) -> float | None:
    value = inv.args.get(name.replace("-", "_"))
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _bad(f"--{name} must be a number", f"Valid example: --{name} 1")
    return float(value)


def check_audio_flags(inv: Invocation) -> tuple[bool, float | None, float, float]:
    """`(audio, target, tolerance, outlier_lu)`; the three value flags need `--audio` (exit 2)."""
    audio = bool(inv.args.get("audio"))
    target, tolerance = _number(inv, "lufs"), _number(inv, "lufs-tolerance")
    outlier = _number(inv, "outlier-lu")
    if not audio:
        named = (("lufs", target), ("lufs-tolerance", tolerance), ("outlier-lu", outlier))
        for name, value in named:
            if value is not None:
                raise _bad(f"--{name} needs --audio", "Valid: davinci-axi timeline check --audio")
    if tolerance is not None and tolerance < 0:
        raise _bad("--lufs-tolerance must be 0 or more", "Valid example: --lufs-tolerance 1")
    if outlier is not None and outlier <= 0:
        raise _bad("--outlier-lu must be above 0", "Valid example: --outlier-lu 6")
    return (
        audio,
        target,
        DEFAULT_TOLERANCE if tolerance is None else tolerance,
        am.OUTLIER_LU if outlier is None else outlier,
    )


_OFF = re.compile(r"(\d+(?:\.\d+)?) LU (under|over) the (median|target)")


def audio_hints(findings: list[Finding], target: float | None) -> list[str]:
    """Fix commands for the `loudness` warnings: one gain move, then the per-item table.

    An outlier row names its own item; a program row (median off `--lufs`) moves every item.
    """
    warns = [f for f in findings if f.check == "loudness" and f.level == "warn"]
    lines: list[str] = []
    for f in warns:
        match = _OFF.search(f.detail)
        if match:
            db = float(match.group(1)) * (1 if match.group(2) == "under" else -1)
            items = "<items>" if match.group(3) == "target" else f.where
            lines.append(hint(f"audio gain {items} --db {db:+.1f}", "move toward the level wanted"))
            break
    if warns:
        table = f"audio loudness --target {target:g}" if target is not None else "audio loudness"
        lines.append(hint(table, "see the per-item table"))
    return lines


def _side(delta: float) -> str:
    return "under" if delta < 0 else "over"


def _scope(measured: list[am.Measured]) -> str:
    tracks = {m.info.ref.split(":")[0] for m in measured}
    return tracks.pop() if len(tracks) == 1 else "all"


def _unmeasured(m: am.Measured) -> str | None:
    if m.error:
        return m.error
    if m.info.has_audio and m.lufs is not None and m.info.volume_db is None:
        return "volume not readable"
    return None


def check_audio(
    ctx: CheckContext,
    *,
    target: float | None,
    tolerance: float,
    outlier_lu: float = am.OUTLIER_LU,
    jobs: int = am.JOBS,
) -> list[Finding]:
    """Loudness outliers, the median against `target`, mostly-silent items, unmeasured items.

    Disabled items are skipped: they do not play, so they must not move the median.
    """
    infos = [am.read_item(located) for located in ctx.audio]
    measured = am.measure_items(
        [info for info in infos if info.enabled],
        silence_db=am.SILENCE_DB,
        silence_s=am.SILENCE_S,
        jobs=jobs,
    )
    found: list[Finding] = []
    estimates = [e for m in measured if (e := am.estimate(m)) is not None]
    centre = am.median(estimates)
    off = am.outliers(measured, outlier_lu)
    for m in measured:
        est, info = am.estimate(m), m.info
        if est is None or centre is None or m.lufs is None or info.ref not in off:
            continue
        delta = off[info.ref]
        found.append(
            Finding(
                "warn",
                "loudness",
                info.ref,
                f"{info.name} {est:.1f} LUFS est. (source {m.lufs:.1f}, gain "
                f"{info.volume_db or 0.0:+.1f}), {abs(delta):.1f} LU {_side(delta)} the "
                f"median {centre:.1f}",
            )
        )
    if centre is not None:
        found.append(
            Finding(
                "info",
                "loudness",
                _scope(measured),
                f"median {centre:.1f} LUFS est. across {len(estimates)} items "
                f"(before track effects; outliers: more than {outlier_lu:g} LU away)",
            )
        )
        if target is not None:
            gap = round(centre - target, 1)
            found.append(
                Finding(
                    "warn" if abs(centre - target) > tolerance else "info",
                    "loudness",
                    "program",
                    f"{abs(gap):.1f} LU {_side(gap)} the target {target:g} "
                    f"(median {centre:.1f} LUFS est.)",
                )
            )
    for m in measured:
        share = am.silent_share(m)
        if share is not None and share >= SILENT_ITEM:
            found.append(
                Finding(
                    "info",
                    "silent-item",
                    m.info.ref,
                    f"{m.info.name}: {share * 100:.0f}% of its range is silent",
                    group="mostly silent items",
                )
            )
        reason = _unmeasured(m)
        if reason:
            found.append(
                Finding(
                    "info", "loudness", m.info.ref, f"not measured: {reason}", group="not measured"
                )
            )
    return found
