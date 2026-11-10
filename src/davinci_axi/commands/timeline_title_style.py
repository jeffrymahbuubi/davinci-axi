"""Style flags for `timeline title add` (F47b): size, font, colour and height of the Text+ card.

Text+ `Size` is the glyph scale relative to the frame (not the text's width), so the default
shrinks on a portrait timeline (0.08 * height / width). The input ids (`Size`, `Font`,
`Red1`/`Green1`/`Blue1`, `Center`) are read from Text+ `.setting` files and are checked live; every
input is read back and a real mismatch is reported, never raised.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from davinci_axi.errors import AxiError
from davinci_axi.fusion_guard import fusion_call
from davinci_axi.spec import Flag, Invocation

DEFAULT_SIZE = 0.08
MAX_FONT = 100
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
COLORS: dict[str, str] = {
    "white": "#ffffff",
    "black": "#000000",
    "red": "#ff0000",
    "green": "#00ff00",
    "blue": "#0000ff",
    "yellow": "#ffff00",
}
STYLE_FLAGS: tuple[Flag, ...] = (
    Flag(
        "size",
        "float",
        help="Text+ Size input: the glyph scale relative to the frame (not the text's width); "
        "default 0.08, or 0.08*height/width on a portrait timeline (0.142 on 9:16)",
    ),
    Flag("font", "str", help="font family name, e.g. 'Open Sans'"),
    Flag("color", "str", help="#rrggbb or white, black, red, green, blue or yellow"),
    Flag("y", "float", help="height of the text centre, 0 (bottom) to 1 (top)"),
)
_TOLERANCE = 1e-6
Color = tuple[float, float, float]


@dataclass(frozen=True)
class Style:
    size: float | None  # None = leave Fusion's default
    size_auto: bool  # True when size came from the aspect rule
    font: str | None
    color: Color | None  # 0..1 each
    y: float | None


def _number(inv: Invocation, key: str) -> float | None:
    value = inv.args.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_color(text: str) -> Color:
    """`#rrggbb` (any case) or a `COLORS` name as three 0..1 floats; ValueError when neither."""
    hexed = COLORS.get(text.strip().lower(), text.strip())
    if not HEX.match(hexed):
        raise ValueError(text)
    red, green, blue = (round(int(hexed[i : i + 2], 16) / 255, 3) for i in (1, 3, 5))
    return red, green, blue


def check_style_flags(inv: Invocation) -> dict[str, object]:
    """Validate --size, --font, --color and --y before any Resolve call; only given flags appear."""
    out: dict[str, object] = {}
    size = _number(inv, "size")
    if size is not None:
        if not math.isfinite(size) or not 0 < size <= 1:
            raise AxiError(
                "VALIDATION_ERROR",
                f"--size must be above 0 and at most 1, got {size}",
                help=["Valid: --size 0.12; Text+ Size is the glyph scale relative to the frame"],
            )
        out["size"] = size
    y = _number(inv, "y")
    if y is not None:
        if not math.isfinite(y) or not 0 <= y <= 1:
            raise AxiError(
                "VALIDATION_ERROR",
                f"--y must be between 0 and 1, got {y}",
                help=["Valid: --y 0.15 puts the centre 15% up from the bottom"],
            )
        out["y"] = y
    color = inv.args.get("color")
    if color is not None:
        try:
            out["color"] = parse_color(str(color))
        except ValueError:
            raise AxiError(
                "VALIDATION_ERROR",
                f"invalid --color {color}",
                help=["Valid: --color '#ffd400' or --color yellow (" + ", ".join(COLORS) + ")"],
            ) from None
    font = inv.args.get("font")
    if font is not None:
        if not str(font).strip() or len(str(font)) > MAX_FONT:
            raise AxiError(
                "VALIDATION_ERROR",
                f"--font must be 1 to {MAX_FONT} characters",
                help=["Valid: --font 'Open Sans'"],
            )
        out["font"] = str(font)
    return out


def resolve_style(checked: dict[str, object], width: int, height: int) -> Style:
    """Apply the aspect rule: no --size on a portrait timeline gets 0.08 * height / width."""
    size = checked.get("size")
    auto = False
    if size is None and width > 0 and height > width:
        size, auto = round(DEFAULT_SIZE * height / width, 3), True
    color = checked.get("color")
    font, y = checked.get("font"), checked.get("y")
    return Style(
        size=float(size) if isinstance(size, (int, float)) else None,
        size_auto=auto,
        font=str(font) if font is not None else None,
        color=color if isinstance(color, tuple) else None,
        y=float(y) if isinstance(y, (int, float)) else None,
    )


def style_inputs(style: Style) -> list[tuple[str, object]]:
    """Ordered (input_id, value) to set on the Template tool."""
    rows: list[tuple[str, object]] = []
    if style.size is not None:
        rows.append(("Size", style.size))
    if style.font is not None:
        rows.append(("Font", style.font))
    if style.color is not None:
        rows += list(zip(("Red1", "Green1", "Blue1"), style.color, strict=True))
    if style.y is not None:
        rows.append(("Center", {1: 0.5, 2: style.y}))
    return rows


def point_of(value: object) -> tuple[float, float] | None:
    """(x, y) of a point: Resolve's dict `{1, 2[, 3]}` (int or str keys), a list/tuple, or "x y"."""

    def num(item: object) -> float | None:
        if isinstance(item, bool):
            return None
        if isinstance(item, (int, float)):
            return float(item)
        if isinstance(item, str):
            try:
                return float(item)
            except ValueError:
                return None
        return None

    parts: list[object]
    if isinstance(value, dict):
        keyed = {str(k).strip(): v for k, v in value.items()}
        if "1" not in keyed or "2" not in keyed:
            return None
        parts = [keyed["1"], keyed["2"]]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    elif isinstance(value, str):
        parts = list(value.replace(",", " ").split())
    else:
        return None
    if len(parts) < 2:
        return None
    x, y = num(parts[0]), num(parts[1])
    return (x, y) if x is not None and y is not None else None


def point_text(value: object) -> str | None:
    """`"x y"` for a point `point_of` understands, else None."""
    point = point_of(value)
    return None if point is None else f"{point[0]:g} {point[1]:g}"


def _same(want: object, got: object) -> bool:
    if isinstance(want, dict):
        pw, pg = point_of(want), point_of(got)
        return (
            pw is not None
            and pg is not None
            and abs(pw[0] - pg[0]) <= _TOLERANCE
            and abs(pw[1] - pg[1]) <= _TOLERANCE
        )
    if isinstance(want, (int, float)) and not isinstance(want, bool):
        return isinstance(got, (int, float)) and abs(float(got) - float(want)) <= _TOLERANCE
    return want == got


def apply_style(tool: Any, style: Style) -> dict[str, object]:
    """SetInput each style input and read it back; returns {id: value read} for mismatches."""
    mismatch: dict[str, object] = {}
    for key, value in style_inputs(style):
        fusion_call("SetInput", tool.SetInput, key, value)
        read = fusion_call("GetInput", tool.GetInput, key)
        if not _same(value, read):
            if isinstance(value, dict) and (text := point_text(read)) is not None:
                mismatch[key] = text
            else:
                mismatch[key] = read if isinstance(read, (int, float, str)) else str(read)
    return mismatch


TEXT_INPUT_IDS = ("StyledText", "Size", "Font", "Style", "Red1", "Green1", "Blue1", "Center")


def read_inputs(tool: Any) -> dict[str, object]:
    """GetInput each `TEXT_INPUT_IDS` id: number or string as is, a point as "x y", else None."""
    out: dict[str, object] = {}
    hung = False
    for key in TEXT_INPUT_IDS:
        out[key] = None
        if hung:  # after a Fusion timeout no further Fusion call is safe
            continue
        try:
            read = fusion_call("GetInput", tool.GetInput, key)
        except AxiError as err:
            hung = err.code == "FUSION_TIMEOUT"
            continue
        if read is None or isinstance(read, (int, float, str)):
            out[key] = read
        else:
            out[key] = point_text(read) or str(read)
    return out


def style_words(inv: Invocation) -> list[str]:
    """The --size/--font/--color/--y words the user passed, for the --force command."""
    words: list[str] = []
    for flag in ("size", "font", "color", "y"):
        value = inv.args.get(flag)
        if value is not None:
            words += [f"--{flag}", str(value)]
    return words


def style_summary(style: Style, width: int = 0, height: int = 0) -> dict[str, object]:
    """plan.extra["style"]: what will be set, with absent flags omitted."""
    out: dict[str, object] = {}
    if style.size is not None:
        out["size"] = style.size
        out["size_source"] = f"auto ({width}x{height})" if style.size_auto else "flag"
    if style.font is not None:
        out["font"] = style.font
    if style.color is not None:
        out["color"] = "#" + "".join(f"{round(c * 255):02x}" for c in style.color)
    if style.y is not None:
        out["y"] = style.y
    return out
