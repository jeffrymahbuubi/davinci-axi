"""Output helpers shared by every command: rendering, field selection, limits, hints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from davinci_axi.errors import AxiError
from davinci_axi.spec import TRUNCATE_CHARS
from davinci_axi.toon import encode

T = TypeVar("T")

BIN_NAME = "davinci-axi"
LIST_KEYS = frozenset({"help", "examples"})


def render(data: dict[str, object], help_lines: list[str], as_json: bool) -> str:
    """Render a result body (TOON by default, JSON with `--json`). No trailing newline."""
    body: dict[str, object] = dict(data)
    if help_lines:
        body["help"] = list(help_lines)
    if as_json:
        return json.dumps(body, ensure_ascii=False, default=str)
    return encode(body, list_keys=LIST_KEYS)


def render_error(err: AxiError, as_json: bool) -> str:
    """Render an error as `error`, `code`, `help[]` (plus optional `detail`)."""
    body: dict[str, object] = {"error": err.message, "code": err.code}
    if err.detail:
        body["detail"] = err.detail
    if err.help:
        body["help"] = list(err.help)
    if as_json:
        return json.dumps(body, ensure_ascii=False, default=str)
    return encode(body, list_keys=LIST_KEYS)


def truncate(text: str, full_cmd: str, limit: int = TRUNCATE_CHARS) -> tuple[str, str | None]:
    """Cut `text` at `limit` characters. Returns (text, note); note is None when nothing was cut."""
    if len(text) <= limit:
        return text, None
    note = f"truncated at {limit} of {len(text)} chars; run `{full_cmd}` for the full text"
    return text[:limit], note


def select_fields(
    rows: list[dict[str, object]], default: tuple[str, ...], requested: list[str] | None
) -> list[dict[str, object]]:
    """Project rows to the default fields, or to `requested` (validated against the row keys)."""
    if requested is None:
        return [{f: row.get(f) for f in default} for row in rows]
    if not rows:
        return []
    valid: list[str] = list(default)
    for row in rows:
        for key in row:
            if key not in valid:
                valid.append(key)
    unknown = [f for f in requested if f not in valid]
    if unknown:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown field {', '.join(unknown)}",
            help=[f"Valid fields: {', '.join(valid)}"],
        )
    return [{f: row.get(f) for f in requested} for row in rows]


def apply_limit(rows: list[T], limit: int) -> tuple[list[T], int]:
    """Return (first `limit` rows, total row count)."""
    if limit < 1:
        raise AxiError("VALIDATION_ERROR", "--limit must be 1 or more", help=["Valid: --limit 100"])
    return rows[:limit], len(rows)


def page_meta(shown: int, total: int) -> dict[str, object]:
    """`total` for a paged list, plus `truncated: true` when a limit cut it short."""
    meta: dict[str, object] = {"total": total}
    if shown < total:
        meta["truncated"] = True
    return meta


def empty_state(noun: str, scope: str, filters: dict[str, object]) -> str:
    """`0 <noun> <scope>` plus any active filters, e.g. `0 items on audio tracks in timeline X`."""
    text = f"0 {noun} {scope}"
    active = {k: v for k, v in filters.items() if v not in (None, "", [])}
    if active:
        text += " (filters: " + ", ".join(f"{k}={v}" for k, v in active.items()) + ")"
    return text


def hint(cmd: str, purpose: str) -> str:
    """A next-step hint written as prose so validators find no false subcommand."""
    return f"Run `{BIN_NAME} {cmd}` to {purpose}"


def collapse_home(path: str | Path) -> str:
    """Replace the home directory prefix with `~`."""
    text = str(path)
    home = str(Path.home())
    if text == home:
        return "~"
    if text.startswith(home + "/"):
        return "~" + text[len(home) :]
    return text
