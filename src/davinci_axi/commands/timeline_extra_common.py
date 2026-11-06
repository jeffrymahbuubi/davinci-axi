"""Shared plumbing for the timeline and item edit extras (U25).

Custom marker data, output blanking and fire-and-forget calls look the same on a timeline and on
an item, so both command modules build their plans here. Fire-and-forget calls have no getter to
read back (KTD23): the `called` change is verified by the return value of the call.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Protocol, cast

from davinci_axi import registry
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    Change,
    MutationPlan,
    force_cmd,
    parse_assignments,
    run_mutation,
)
from davinci_axi.output import hint
from davinci_axi.resolve_types import Info
from davinci_axi.session import Session
from davinci_axi.spec import Flag, Invocation, Positional, Result

# `OutputBlanking` keys from the `.pyi`, in pixels.
BLANKING_KEYS: tuple[str, ...] = ("Top", "Bottom", "Left", "Right")
TIMECODE = re.compile(r"^\d{2}:\d{2}:\d{2}[:;]\d{2}$")
FRAME_FLAG = Flag("frame", "int", None, help="marker frame offset from the start of the host")
FIND_FLAG = Flag("find", "str", None, help="find the marker that carries this custom data")
DATA = Positional("data", help="the custom data string to store with the marker")
ASSIGN_BLANKING = Positional(
    "assignments", help="comma-separated Top|Bottom|Left|Right=pixels pairs, e.g. Top=10,Left=4"
)


class CustomHost(Protocol):
    """A timeline or a timeline item: both carry the same custom marker data calls."""

    def GetMarkers(self) -> dict[int, Info]: ...
    def GetMarkerCustomData(self, frameId: int) -> str: ...
    def GetMarkerByCustomData(self, customData: str) -> Info: ...
    def UpdateMarkerCustomData(self, frameId: int, customData: str) -> bool: ...


def split_refs(text: str, noun: str, example: str) -> list[str]:
    """`V1:1, V1:2` -> two refs, without repeats; an empty list or an empty part is an error."""
    parts = [p.strip() for p in text.split(",")]
    if not parts or any(not p for p in parts):
        raise AxiError(
            "VALIDATION_ERROR",
            f"an empty {noun} in the comma-separated list",
            help=[f"Valid example: {example}"],
        )
    return list(dict.fromkeys(parts))


def items_hint() -> list[str]:
    if "items" not in registry.COMMANDS:
        return []
    return [hint("items", "list the items of the timeline")]


def resolve_enum(session: Session, name: str) -> object:
    """KTD24: `resolve.<NAME>`, or API_FAILED when this Resolve has no such constant."""
    value = getattr(session.resolve, name, None)
    if value is None:
        raise api_failed("Resolve." + name, f"this Resolve has no {name}")
    return value


def parse_blanking(text: str) -> dict[str, int]:
    """`Top=10,Left=4` -> `{"Top": 10, "Left": 4}`; unknown keys and bad pixels are errors."""
    pairs = parse_assignments(text)
    unknown = [key for key in pairs if key not in BLANKING_KEYS]
    if unknown:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown blanking key {', '.join(unknown)}",
            help=[f"Valid keys: {', '.join(BLANKING_KEYS)}"],
        )
    values: dict[str, int] = {}
    for key, raw in pairs.items():
        try:
            pixels = int(raw)
        except ValueError:
            pixels = -1
        if pixels < 0:
            raise AxiError(
                "VALIDATION_ERROR",
                f"{key} must be a whole number of pixels, 0 or more (got {raw})",
                help=["Valid example: Top=10,Bottom=10"],
            )
        values[key] = pixels
    return values


def blanking_values(live: Info | None) -> dict[str, int]:
    """The four blanking values of a live dict; a missing key reads as 0 pixels."""
    live = live or {}
    return {key: int(live.get(key) or 0) for key in BLANKING_KEYS}


def blanking_plan(
    command: str,
    target: str,
    before: dict[str, int],
    wanted: dict[str, int],
    words: list[str],
    inv: Invocation,
    next_lines: list[str],
) -> MutationPlan:
    full = {**before, **wanted}
    return MutationPlan(
        command=command,
        target=target,
        changes=[
            Change(key, before[key], full[key]) for key in BLANKING_KEYS if before[key] != full[key]
        ],
        force_cmd=force_cmd(inv, *words),
        next=next_lines,
    )


def parse_on_off(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in ("on", "true", "1"):
        return True
    if lowered in ("off", "false", "0"):
        return False
    raise AxiError(
        "VALIDATION_ERROR",
        f"cannot read {text} as on or off",
        help=["Valid values: on, off"],
    )


def run_called(
    inv: Invocation,
    session: Session | None,
    plan: MutationPlan,
    method: str,
    call: Callable[[], bool],
    detail: str = "",
) -> Result:
    """Run a call with no getter: the plan's one change is `called`, checked by the return value."""
    plan.changes = [Change("called", False, True)]
    done = {"ok": False}

    def apply() -> None:
        if not call():
            raise api_failed(method, detail)
        done["ok"] = True

    return run_mutation(inv, session, plan, apply, lambda: {"called": done["ok"]})


def frame_arg(inv: Invocation) -> int:
    frame = inv.args.get("frame")
    if not isinstance(frame, int) or isinstance(frame, bool) or frame < 0:
        raise AxiError(
            "VALIDATION_ERROR",
            "--frame must be 0 or more (an offset from the start of the host)",
            help=["Valid example: --frame 24"],
        )
    return frame


def check_custom_get_flags(inv: Invocation) -> None:
    """Exactly one of `--frame` and `--find`. Call before `inv.connect()`."""
    given = [
        name
        for name, present in (
            ("--frame", inv.args.get("frame") is not None),
            ("--find", bool(inv.args.get("find"))),
        )
        if present
    ]
    if len(given) != 1:
        raise AxiError(
            "VALIDATION_ERROR",
            f"pass exactly one of --frame and --find (got {', '.join(given) or 'none'})",
            help=["Valid examples: --frame 24, --find review-1"],
        )
    if inv.args.get("frame") is not None:
        frame_arg(inv)


def run_custom_get(inv: Invocation, host: CustomHost, label: str) -> Result:
    markers = host.GetMarkers() or {}
    find = inv.args.get("find")
    data: dict[str, object] = {"target": label}
    if isinstance(find, str) and find:
        info = host.GetMarkerByCustomData(find)
        if not info:
            raise not_found("marker", f"with custom data {find}", "No marker carries that data")
        frames = sorted(f for f, m in markers.items() if m.get("customData") == find)
        data.update({"frame": frames[0] if frames else None, "custom_data": find})
    else:
        frame = frame_arg(inv)
        info = markers.get(frame) or {}
        if frame not in markers:
            shown = ", ".join(str(f) for f in sorted(markers)) or "(none)"
            raise not_found("marker", f"at frame {frame}", f"Marker frames here: {shown}")
        data.update({"frame": frame, "custom_data": str(host.GetMarkerCustomData(frame) or "")})
    data.update({"name": info.get("name"), "color": info.get("color")})
    return Result(data)


def run_custom_set(
    inv: Invocation,
    session: Session,
    host: CustomHost,
    label: str,
    command: str,
    owner: str,
    words: list[str],
    next_lines: list[str],
) -> Result:
    frame = frame_arg(inv)
    value = inv.positionals["data"]
    markers = host.GetMarkers() or {}
    if frame not in markers:
        shown = ", ".join(str(f) for f in sorted(markers)) or "(none)"
        raise not_found("marker", f"at frame {frame}", f"Marker frames here: {shown}")
    before = str(host.GetMarkerCustomData(frame) or "")
    plan = MutationPlan(
        command=command,
        target=label,
        changes=[Change("custom_data", before, value)] if before != value else [],
        force_cmd=force_cmd(inv, *words),
        extra={"frame": frame},
        next=next_lines,
    )

    def apply() -> None:
        if not host.UpdateMarkerCustomData(frame, value):
            raise api_failed(f"{owner}.UpdateMarkerCustomData", f"no marker at frame {frame}")

    def verify() -> dict[str, object]:
        return {"custom_data": str(host.GetMarkerCustomData(frame) or "")}

    return run_mutation(inv, session, plan, apply, verify)


def as_host(obj: object) -> CustomHost:
    return cast(CustomHost, obj)


def source_range(start: int | None, end: int | None) -> tuple[int, int] | None:
    """`--in`/`--out` source frames for `timeline append`; both or neither, out after in."""
    if start is None and end is None:
        return None
    if start is None or end is None or end <= start:
        raise AxiError(
            "VALIDATION_ERROR",
            "--in and --out go together, and --out must be greater than --in",
            help=["Valid example: --in 30 --out 120 (source frames)"],
        )
    return start, end


def check_range(clips: list[Any], span: tuple[int, int]) -> None:
    """Refuse an `--out` past the end of any clip, using its `Frames` property."""
    for clip in clips:
        frames = str(clip.GetClipProperty("Frames") or "")
        if frames.isdigit() and span[1] > int(frames):
            raise AxiError(
                "VALIDATION_ERROR",
                f"--out {span[1]} is past the end of {clip.GetName()} ({frames} frames)",
                help=[f"Use --out {frames} or less"],
            )
