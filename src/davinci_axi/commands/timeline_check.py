"""`timeline check` (A): lint a timeline before a render. Read-only.

Rules live in `timeline_check_rules.py`. The exit code is 0 unless `--strict` is given and a rule
found an error. Transitions are recognised structurally (an overlap where one side has no media
pool clip); the live `GetType()` of a transition is an open question, so it is not relied on.
In TOON, repeated findings (same level, check and group sentence) collapse into one `N items`
row; `--verbose` and `--json` list every item, and the counts always count items.
"""

from __future__ import annotations

import re
import shlex
from collections import Counter

from davinci_axi import registry
from davinci_axi.commands import ffmpeg_common
from davinci_axi.commands.item_detail import str_arg, walk_items
from davinci_axi.commands.items import ITEM_LIST_METHODS, TIMELINE_FLAG
from davinci_axi.commands.timeline_check_audio import (
    AUDIO_FLAGS,
    audio_hints,
    check_audio,
    check_audio_flags,
)
from davinci_axi.commands.timeline_check_rules import (
    CheckContext,
    Finding,
    check_disabled,
    check_empty,
    check_fps,
    check_gaps,
    check_length,
    check_offline,
    check_overlaps,
    check_render_cost,
    check_resolution,
    check_silence,
)
from davinci_axi.errors import AxiError
from davinci_axi.output import empty_state, hint
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Result

__all__ = ["CHECK_SPEC", "Finding", "effective_settings", "parse_target", "run_check"]

_ORDER = {"error": 0, "warn": 1, "info": 2}
_CLOCK = re.compile(r"(\d+):(\d{1,2})(?::(\d{1,2}))?")
_PART = re.compile(r"(\d+(?:\.\d+)?)([hms])")
_PLAIN = re.compile(r"\d+(?:\.\d+)?")
_UNITS = {"h": 3600.0, "m": 60.0, "s": 1.0}
_TARGET_HELP = [
    "Examples: --target 90s, --target 3:30, --target 2m-5m (a lone value is the maximum)"
]


def group_findings(found: list[Finding]) -> list[Finding]:
    """Collapse rows sharing (level, check, group) when group is set and the group has 2+ rows:
    one Finding(level, check, f"{n} items", group, group, "") in the first member's place."""
    keys = [(f.level, f.check, f.group) for f in found]
    counts = Counter(key for key in keys if key[2])
    out: list[Finding] = []
    done: set[tuple[str, str, str]] = set()
    for finding, key in zip(found, keys, strict=True):
        if counts[key] < 2 or not key[2]:
            out.append(finding)
        elif key not in done:
            done.add(key)
            out.append(Finding(key[0], key[1], f"{counts[key]} items", key[2], key[2]))
    return out


def _bad_target(text: str, why: str) -> AxiError:
    return AxiError("VALIDATION_ERROR", f"invalid --target {text!r}: {why}", help=_TARGET_HELP)


def _seconds(text: str, whole: str) -> float:
    word = text.strip().lower()
    clock = _CLOCK.fullmatch(word)
    if clock:
        first, second, third = clock.groups()
        if int(second) > 59 or (third is not None and int(third) > 59):
            raise _bad_target(whole, "minutes and seconds go up to 59")
        if third is None:
            return int(first) * 60.0 + int(second)
        return int(first) * 3600.0 + int(second) * 60.0 + int(third)
    if _PLAIN.fullmatch(word):
        return float(word)
    parts = _PART.findall(word)
    if parts and "".join(f"{n}{u}" for n, u in parts) == word:
        return sum(float(n) * _UNITS[u] for n, u in parts)
    raise _bad_target(whole, f"cannot read {text!r} as a duration")


def parse_target(text: str) -> tuple[float | None, float | None]:
    """`2m-5m` -> (120, 300); `90s` or `3:30` -> (None, seconds): a lone value is the maximum."""
    word = text.strip()
    if not word:
        raise _bad_target(text, "it is empty")
    if "-" in word:
        low_text, _, high_text = word.partition("-")
        low, high = _seconds(low_text, text), _seconds(high_text, text)
        if low > high:
            raise _bad_target(text, "the minimum is longer than the maximum")
        return low, high
    high = _seconds(word, text)
    if high <= 0:
        raise _bad_target(text, "the maximum must be above zero")
    return None, high


def _positive(value: object) -> float | None:
    try:
        number = float(str(value))
    except ValueError:
        return None
    return number if number > 0 else None


def effective_settings(session: Session, timeline: TimelineApi) -> tuple[float, int, int]:
    """(fps, width, height): the timeline's own values when it uses custom settings, else the
    project's, each falling back to the other source when a value is missing or empty."""
    own = timeline.GetSettings() or {}
    base = session.require_project().GetSettings() or {}
    custom = str(own.get("useCustomSettings", "")).strip().lower() in {"1", "true", "yes"}
    order = (own, base) if custom else (base, own)

    def pick(key: str) -> float:
        for source in order:
            number = _positive(source.get(key))
            if number is not None:
                return number
        raise AxiError(
            "API_FAILED",
            f"cannot read {key} from the timeline or the project settings",
            help=["Run `davinci-axi timeline settings show` to see what Resolve reports"],
        )

    fps = pick("timelineFrameRate")
    return fps, round(pick("timelineResolutionWidth")), round(pick("timelineResolutionHeight"))


def _fix_hints(
    findings: list[Finding], first: dict[str, Finding], lufs: float | None = None
) -> list[str]:
    def known(command: str) -> bool:
        return command.split()[0] in registry.COMMANDS

    lines: list[str] = []
    fix = {
        "gaps": ("timeline append --edl <file>", "re-cut the timeline from an edit list"),
        "overlap": ("item delete {ref} --ripple", "remove an item and close up the track"),
        "offline": ("media relink <clips> --path <folder>", "relink the offline media"),
    }
    for check, (command, purpose) in fix.items():
        if check in first and known(command):
            ref = first[check].where.split("→")[0]
            lines.append(hint(command.replace("{ref}", ref), purpose))
    target = next(
        (f for f in findings if f.check == "resolution" and f.level == "warn" and f.clip), None
    )
    if target is not None and known("media property set"):
        command = f"media property set {shlex.quote(target.clip)} 'Super Scale=2x-enhanced'"
        purpose = (
            "add resolution before the upscale (about 40x the render time live; final render only)"
        )
        lines.append(hint(command, purpose))
    if "render_cost" in first and known("media property set"):
        command = (
            "media property set --used 'Super Scale=1' --save-restore ~/super-scale.json --force"
        )
        purpose = (
            "make draft renders take seconds; `media property set --restore ~/super-scale.json "
            "--force` puts it back before the final"
        )
        lines.append(hint(command, purpose))
    if known("audio gain"):
        lines.extend(audio_hints(findings, lufs))
    return lines


def run_check(inv: Invocation) -> Result:
    track = inv.args.get("track")
    if track is not None and (not isinstance(track, int) or track < 1):
        raise AxiError(
            "VALIDATION_ERROR", "--track must be 1 or more", help=["Tracks are numbered from 1"]
        )
    text = str_arg(inv, "target")
    target = parse_target(text) if text is not None else None
    strict = bool(inv.args.get("strict"))
    audio, lufs, tolerance, outlier_lu = check_audio_flags(inv)
    if audio:
        ffmpeg_common.require_tools("ffmpeg")

    session = inv.connect()
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    fps, width, height = effective_settings(session, timeline)
    start, end = timeline.GetStartFrame(), timeline.GetEndFrame()
    ctx = CheckContext(
        fps=fps,
        width=width,
        height=height,
        start=start,
        end=end,
        track=track,
        video=list(walk_items(timeline, ("video",), track)),
        audio=list(walk_items(timeline, ("audio",))),
    )
    found = [
        *check_empty(ctx),
        *check_gaps(ctx),
        *check_overlaps(ctx),
        *check_offline(ctx),
        *check_fps(ctx),
        *check_resolution(ctx),
        *check_render_cost(ctx),
        *check_silence(ctx),
        *check_disabled(ctx),
        *(check_length(ctx, target, text) if target is not None and text else []),
        *(
            check_audio(ctx, target=lufs, tolerance=tolerance, outlier_lu=outlier_lu)
            if audio
            else []
        ),
    ]
    found.sort(key=lambda finding: _ORDER[finding.level])
    errors = sum(1 for f in found if f.level == "error")
    plain = bool(inv.opts.json or inv.args.get("verbose"))
    shown = found if plain else group_findings(found)
    name = timeline.GetName()
    data: dict[str, object] = {
        "timeline": name,
        "track": f"V{track}" if track is not None else "all",
        "frames": end - start,
        "duration": f"{(end - start) / fps:.1f} s",
        "fps": fps,
        "resolution": f"{width}x{height}",
    }
    if text is not None:
        data["target"] = text
    data["ok"] = errors == 0
    data["errors"] = errors
    data["warnings"] = sum(1 for f in found if f.level == "warn")
    if found:
        data["findings"] = [
            {"level": f.level, "check": f.check, "where": f.where, "detail": f.detail}
            for f in shown
        ]
    else:
        data["findings"] = empty_state("findings", f"in timeline {shlex.quote(name)}", {})
    first = {f.check: f for f in reversed(found)}
    lines = _fix_hints(found, first, lufs)
    if len(shown) < len(found):
        rows = f"{len(shown)} row" + ("" if len(shown) == 1 else "s")
        lines.insert(0, f"{len(found)} findings shown as {rows}; pass --verbose to list every item")
    return Result(data, lines, exit_code=1 if strict and errors else 0)


CHECK_SPEC = CommandSpec(
    name="check",
    summary=(
        "Lint a timeline before a render: gaps, overlaps, offline media, fps and resolution "
        "mismatches, upscaling, render cost (Super Scale), silence and length"
    ),
    run=run_check,
    flags=(
        TIMELINE_FLAG,
        Flag("track", "int", None, help="check only this video track (1-based); default all"),
        Flag(
            "target",
            "str",
            None,
            help="wanted length: 90s, 3:30 (a maximum) or 2m-5m (a range)",
        ),
        Flag("strict", "bool", False, help="exit 1 when any check finds an error"),
        Flag(
            "verbose",
            "bool",
            False,
            help="list every finding; by default repeats collapse into one row",
        ),
        *AUDIO_FLAGS,
    ),
    examples=(
        "davinci-axi timeline check",
        "davinci-axi timeline check --target 2m-5m",
        "davinci-axi timeline check --verbose",
        "davinci-axi timeline check --audio --lufs -14",
        "davinci-axi timeline check --audio --lufs -14 --outlier-lu 3",
        "davinci-axi timeline check --timeline 'Edit v2' --strict",
    ),
    methods=(
        *ITEM_LIST_METHODS,
        "Timeline.GetStartFrame",
        "Timeline.GetEndFrame",
        "Timeline.GetSettings",
        "Project.GetSettings",
        "MediaPoolItem.GetClipProperty",
        "TimelineItem.GetSourceStartFrame",
        "TimelineItem.GetSourceEndFrame",
        "TimelineItem.GetProperties",
        "TimelineItem.GetMediaPoolItem",
    ),
    notes=(
        "Read-only. Exit 0 unless --strict is given and an error was found.",
        "Gaps are checked on the base video track only; V2 and up are overlays with holes.",
        "A transition is an overlap where a side has no media pool clip (structural, not GetType).",
        "Timeline settings fall back to the project's when the timeline does not use custom ones.",
        "Upscaling is max(width / w, height / h) against the timeline size; a clip's Super Scale "
        "(1-4) covers upscales up to that factor and turns the row into info.",
        "Render cost is an info row: video items on clips with Super Scale 2x or more render "
        "about 40x slower; it never changes ok, errors or warnings.",
        "--audio measures each audio item's source range with ffmpeg (an estimate before track "
        "effects); without it there are no loudness checks and ffmpeg is never needed.",
        "--audio outliers: an item whose estimate is more than --outlier-lu LU (default 6) from "
        "the median of the enabled items is a warn; mostly silent (info): at least 80% of the used "
        "range is below -35 dB for 0.7 s or longer.",
        "--audio --lufs: the program row is a warn when the median is more than --lufs-tolerance "
        "LU (default 1) from --lufs.",
        "Repeated findings collapse into one row per (level, check, detail) in TOON; --verbose "
        "and --json list every item. errors and warnings always count items.",
    ),
)
