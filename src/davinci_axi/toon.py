"""A stdlib TOON v4.4 encoder (the subset the CLI needs).

Rules follow docs/context/axi-spec.md section 5: two-space indentation, no trailing newline,
tabular arrays only for uniform primitive rows, `key: []` for empty arrays, canonical numbers.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_NUMERIC_RE = re.compile(r"^[+-]?[0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?$", re.IGNORECASE)
_SPECIAL = set(':"\\[]{},')
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _is_primitive(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _escape(text: str) -> str:
    out: list[str] = []
    for ch in text:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        elif 0xD800 <= ord(ch) <= 0xDFFF:
            raise ValueError("unpaired surrogate cannot be encoded")
        else:
            out.append(ch)
    return "".join(out)


def _needs_quotes(text: str) -> bool:
    if text == "" or text != text.strip():
        return True
    if text in ("true", "false", "null") or _NUMERIC_RE.match(text):
        return True
    if text[0] in "-#":
        return True
    return any(ch in _SPECIAL or ord(ch) < 0x20 or 0xD800 <= ord(ch) <= 0xDFFF for ch in text)


def _number(value: float) -> str:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "null"
        if value == 0:
            return "0"
        text = format(Decimal(repr(value)), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text
    return str(value)


def _scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _number(value)
    text = value if isinstance(value, str) else str(value)
    return f'"{_escape(text)}"' if _needs_quotes(text) else text


def _key(name: str) -> str:
    return name if _KEY_RE.match(name) else f'"{_escape(name)}"'


def _tabular_fields(rows: list[object]) -> list[str] | None:
    """Field names when every row is a non-empty dict with the same primitive-only keys."""
    first = rows[0]
    if not isinstance(first, dict) or not first:
        return None
    fields = [str(f) for f in first]
    for row in rows:
        if not isinstance(row, dict) or {str(k) for k in row} != set(fields):
            return None
        if not all(_is_primitive(v) for v in row.values()):
            return None
    return fields


def _array_lines(
    name: str, items: list[object], depth: int, list_keys: frozenset[str]
) -> list[str]:
    pad = "  " * depth
    key = _key(name)
    if not items:
        return [f"{pad}{key}: []"]
    count = len(items)
    forced_list = name in list_keys
    if not forced_list:
        if all(_is_primitive(v) for v in items):
            return [f"{pad}{key}[{count}]: " + ",".join(_scalar(v) for v in items)]
        fields = _tabular_fields(items)
        if fields is not None:
            lines = [f"{pad}{key}[{count}]{{{','.join(_key(f) for f in fields)}}}:"]
            for row in items:
                assert isinstance(row, dict)
                lines.append(f"{pad}  " + ",".join(_scalar(row[f]) for f in fields))
            return lines
    lines = [f"{pad}{key}[{count}]:"]
    for item in items:
        lines.extend(_list_item_lines(item, depth + 1, list_keys))
    return lines


def _list_item_lines(item: object, depth: int, list_keys: frozenset[str]) -> list[str]:
    pad = "  " * depth
    if _is_primitive(item):
        return [f"{pad}- {_scalar(item)}"]
    if isinstance(item, (list, tuple)):
        values = list(item)
        if not values:
            return [f"{pad}- [0]:"]
        if all(_is_primitive(v) for v in values):
            return [f"{pad}- [{len(values)}]: " + ",".join(_scalar(v) for v in values)]
        lines = [f"{pad}- [{len(values)}]:"]
        for inner in values:
            lines.extend(_list_item_lines(inner, depth + 2, list_keys))
        return lines
    assert isinstance(item, dict)
    if not item:
        return [f"{pad}-"]
    body = _object_lines(item, depth + 1, list_keys)
    body[0] = f"{pad}- {body[0].lstrip()}"
    return body


def _object_lines(obj: dict[object, object], depth: int, list_keys: frozenset[str]) -> list[str]:
    pad = "  " * depth
    lines: list[str] = []
    for raw_key, value in obj.items():
        name = str(raw_key)
        if isinstance(value, dict):
            lines.append(f"{pad}{_key(name)}:")
            lines.extend(_object_lines(value, depth + 1, list_keys))
        elif isinstance(value, (list, tuple)):
            lines.extend(_array_lines(name, list(value), depth, list_keys))
        else:
            lines.append(f"{pad}{_key(name)}: {_scalar(value)}")
    return lines


def encode(value: object, *, list_keys: frozenset[str] = frozenset({"help"})) -> str:
    """Encode `value` as TOON. Keys in `list_keys` always use list form. No trailing newline."""
    if isinstance(value, dict):
        lines = _object_lines(value, 0, list_keys)
    elif isinstance(value, (list, tuple)):
        items = list(value)
        if not items:
            lines = ["[0]:"]
        elif all(_is_primitive(v) for v in items):
            lines = [f"[{len(items)}]: " + ",".join(_scalar(v) for v in items)]
        else:
            lines = [f"[{len(items)}]:"]
            for item in items:
                lines.extend(_list_item_lines(item, 1, list_keys))
    else:
        lines = [_scalar(value)]
    return "\n".join(lines)
