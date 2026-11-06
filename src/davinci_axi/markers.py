"""Marker add and delete plans shared by timeline, timeline-item and media-pool markers.

The same five calls exist on `Timeline`, `TimelineItem` and `MediaPoolItem`, so one pair of plan
builders serves `timeline marker`, `item marker` and `media marker` (U11, U12, U13). Frames are
offsets from the start of the host, which is how Resolve keys `GetMarkers()`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import Change, MutationPlan, force_cmd
from davinci_axi.resolve_types import Info
from davinci_axi.spec import Flag, Invocation

# .pyi MarkerColor == FlagColor
MARKER_COLORS: tuple[str, ...] = (
    "Blue",
    "Cyan",
    "Green",
    "Yellow",
    "Red",
    "Pink",
    "Purple",
    "Fuchsia",
    "Rose",
    "Lavender",
    "Sky",
    "Mint",
    "Lemon",
    "Sand",
    "Cocoa",
    "Cream",
)
# .pyi ClipColor
CLIP_COLORS: tuple[str, ...] = (
    "Orange",
    "Apricot",
    "Yellow",
    "Lime",
    "Olive",
    "Green",
    "Teal",
    "Navy",
    "Blue",
    "Purple",
    "Violet",
    "Pink",
    "Tan",
    "Beige",
    "Brown",
    "Chocolate",
)

MARKER_ADD_FLAGS: tuple[Flag, ...] = (
    Flag("frame", "int", None, required=True, help="frame offset from the start of the host"),
    Flag("color", "str", "Blue", help="marker colour", choices=MARKER_COLORS),
    Flag("name", "str", "", help="marker name"),
    Flag("note", "str", "", help="marker note"),
    Flag("duration", "int", 1, help="marker length in frames"),
    Flag("custom-data", "str", "", help="custom data string stored with the marker"),
)
MARKER_DELETE_FLAGS: tuple[Flag, ...] = (
    Flag("frame", "int", None, help="delete the marker at this frame offset"),
    Flag(
        "color",
        "str",
        None,
        help="delete every marker of this colour (or All)",
        choices=(*MARKER_COLORS, "All"),
    ),
    Flag("custom-data", "str", None, help="delete the marker with this custom data"),
)

Plan = tuple[MutationPlan, Callable[[], None], Callable[[], dict[str, object]]]


class MarkerHost(Protocol):
    def GetMarkers(self) -> dict[int, Info]: ...
    def AddMarker(
        self,
        frameId: int,
        color: str,
        name: str,
        note: str,
        duration: int,
        customData: str | None = None,
    ) -> bool: ...
    def DeleteMarkerAtFrame(self, frameNum: int) -> bool: ...
    def DeleteMarkersByColor(self, color: str) -> bool: ...
    def DeleteMarkerByCustomData(self, customData: str) -> bool: ...


def _int_arg(inv: Invocation, key: str) -> int | None:
    value = inv.args.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text_arg(inv: Invocation, key: str) -> str | None:
    value = inv.args.get(key)
    return value if isinstance(value, str) and value != "" else None


def check_add_flags(inv: Invocation) -> None:
    """Validate `marker add` flags. Call before `inv.connect()` (usage errors exit 2 first)."""
    frame, duration = _int_arg(inv, "frame"), _int_arg(inv, "duration")
    if frame is None or frame < 0:
        raise AxiError(
            "VALIDATION_ERROR",
            "--frame must be 0 or more (an offset from the start)",
            help=["Valid example: --frame 24"],
        )
    if duration is None or duration < 1:
        raise AxiError(
            "VALIDATION_ERROR",
            "--duration must be 1 or more frames",
            help=["Valid example: --duration 1"],
        )


def check_delete_flags(inv: Invocation) -> None:
    """Exactly one of `--frame`, `--color`, `--custom-data`. Call before `inv.connect()`."""
    given = [
        name
        for name, present in (
            ("--frame", inv.args.get("frame") is not None),
            ("--color", _text_arg(inv, "color") is not None),
            ("--custom-data", _text_arg(inv, "custom_data") is not None),
        )
        if present
    ]
    if len(given) != 1:
        found = ", ".join(given) if given else "none"
        raise AxiError(
            "VALIDATION_ERROR",
            f"pass exactly one of --frame, --color, --custom-data (got {found})",
            help=["Valid examples: --frame 24, --color Green, --custom-data take-2"],
        )
    frame = _int_arg(inv, "frame")
    if frame is not None and frame < 0:
        raise AxiError(
            "VALIDATION_ERROR",
            "--frame must be 0 or more (an offset from the start)",
            help=["Valid example: --frame 24"],
        )


def _command_name(cmd_words: list[str]) -> str:
    """The command path: the leading words up to the first flag."""
    words: list[str] = []
    for word in cmd_words:
        if word.startswith("-"):
            break
        words.append(word)
    return " ".join(words)


def _label(color: object, name: object) -> str:
    return f"{color} {name}".rstrip()


def add_plan(inv: Invocation, host: MarkerHost, target: str, cmd_words: list[str]) -> Plan:
    """Plan `AddMarker` on `host`. `cmd_words` is the command line before the marker flags."""
    check_add_flags(inv)
    frame = _int_arg(inv, "frame") or 0
    color = str(inv.args.get("color") or "Blue")
    name = str(inv.args.get("name") or "")
    note = str(inv.args.get("note") or "")
    duration = _int_arg(inv, "duration") or 1
    custom = str(inv.args.get("custom_data") or "")
    field_name = f"marker@{frame}"
    existing = host.GetMarkers().get(frame)
    if existing is not None:
        raise AxiError(
            "MARKER_EXISTS",
            f"{target} already has a marker at frame {frame}",
            help=[
                f"It is {_label(existing.get('color', ''), existing.get('name', ''))}; "
                "delete it first or choose another frame"
            ],
        )
    words = [*cmd_words, "--frame", str(frame), "--color", color]
    for flag, value in (("--name", name), ("--note", note), ("--custom-data", custom)):
        if value:
            words.extend([flag, value])
    if duration != 1:
        words.extend(["--duration", str(duration)])
    plan = MutationPlan(
        command=_command_name(cmd_words),
        target=target,
        changes=[Change(field_name, None, _label(color, name))],
        force_cmd=force_cmd(inv, *words),
    )

    def apply() -> None:
        if not host.AddMarker(frame, color, name, note, duration, custom):
            raise api_failed("AddMarker", f"no marker was added at frame {frame}")

    def verify() -> dict[str, object]:
        found = host.GetMarkers().get(frame)
        return {
            field_name: _label(found.get("color", ""), found.get("name", "")) if found else None
        }

    return plan, apply, verify


def delete_plan(inv: Invocation, host: MarkerHost, target: str, cmd_words: list[str]) -> Plan:
    """Plan one marker delete by frame, colour or custom data (exactly one selector)."""
    check_delete_flags(inv)
    frame = _int_arg(inv, "frame")
    color = _text_arg(inv, "color")
    custom = _text_arg(inv, "custom_data")
    if frame is not None:
        field_name, flag_words, method = (
            f"marker@{frame}",
            ["--frame", str(frame)],
            "DeleteMarkerAtFrame",
        )
    elif color is not None:
        field_name, flag_words, method = (
            f"markers:{color}",
            ["--color", color],
            "DeleteMarkersByColor",
        )
    else:
        assert custom is not None
        field_name, flag_words, method = (
            f"markers:{custom}",
            ["--custom-data", custom],
            "DeleteMarkerByCustomData",
        )

    def matches(marker: Info) -> bool:
        if color is not None:
            return color == "All" or marker.get("color") == color
        return marker.get("customData") == custom

    def remove() -> bool:
        if frame is not None:
            return host.DeleteMarkerAtFrame(frame)
        if color is not None:
            return host.DeleteMarkersByColor(color)
        assert custom is not None
        return host.DeleteMarkerByCustomData(custom)

    def count() -> int:
        markers = host.GetMarkers()
        if frame is not None:
            return 1 if frame in markers else 0
        return sum(1 for m in markers.values() if matches(m))

    before = count()
    plan = MutationPlan(
        command=_command_name(cmd_words),
        target=target,
        changes=[Change(field_name, before, 0)] if before else [],
        force_cmd=force_cmd(inv, *cmd_words, *flag_words),
    )

    def apply() -> None:
        if not remove():
            raise api_failed(method, f"no marker was deleted ({field_name})")

    def verify() -> dict[str, object]:
        return {field_name: count()}

    return plan, apply, verify
