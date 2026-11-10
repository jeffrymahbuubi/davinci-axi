"""Row files for per-item batch verbs (`item set --edl`, `color cdl set --edl`).

A row file is a `.json` array of objects or a `.csv` with a header row. Every row names its
target in the `key` column (default `item`); the other columns are the assignments. All values
come back as stripped strings and empty cells are dropped, so both formats behave alike.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from davinci_axi.errors import AxiError, not_found
from davinci_axi.output import collapse_home

SUFFIXES = (".json", ".csv")
SCALARS = (str, int, float, bool)


def _bad(message: str, *help_lines: str) -> AxiError:
    return AxiError("VALIDATION_ERROR", message, help=list(help_lines))


def _load_text(path: Path) -> str:
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path of the row file")
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise _bad(f"cannot read {path.name} as UTF-8 text: {exc}") from None


def _json_rows(path: Path, text: str, key: str) -> list[object]:
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _bad(f"{path.name} is not valid JSON: {exc}") from None
    if not isinstance(loaded, list):
        raise _bad(
            f"{path.name} must be a JSON array of rows",
            f'Valid example: [{{"{key}": "V1:1", "ZoomX": 1.2}}]',
        )
    return list(loaded)


def _csv_rows(text: str) -> list[object]:
    reader = csv.DictReader(text.splitlines(), restkey=None)
    return [
        {(name.strip() if isinstance(name, str) else name): cell for name, cell in one.items()}
        for one in reader
    ]


def _row(n: int, raw: object, key: str, allowed: tuple[str, ...] | None) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise _bad(f"row {n}: must be an object with an {key} field")
    row: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str):
            raise _bad(f"row {n}: more values than header columns")
        if value is None:
            continue
        if not isinstance(value, SCALARS):
            raise _bad(f"row {n}: {name} must be a number, a string or a boolean")
        text = str(value).strip()
        if isinstance(value, bool):
            text = text.lower()
        if text:
            row[name] = text
    if key not in row:
        raise _bad(
            f"row {n}: {key} is required", f"Every row needs an {key} column (V1:3 or an id)"
        )
    if allowed is not None:
        unknown = [name for name in row if name not in allowed]
        if unknown:
            raise _bad(
                f"row {n}: unknown column {', '.join(unknown)}",
                f"Columns are: {', '.join(allowed)}",
            )
    return row


def read_rows(
    path: Path, *, key: str = "item", allowed: tuple[str, ...] | None = None
) -> list[dict[str, str]]:
    """Rows of a `.json` array or `.csv` file; each has a non-empty `key` and string values."""
    text = _load_text(path)
    suffix = path.suffix.lower()
    if suffix not in SUFFIXES:
        raise _bad(
            f"cannot read {path.name}: use .json or .csv",
            f"Valid example: {key},ZoomX,Tilt header row in a .csv file",
        )
    raws = _json_rows(path, text, key) if suffix == ".json" else _csv_rows(text)
    if not raws:
        raise _bad(f"{path.name} has no rows")
    rows = [_row(n, raw, key, allowed) for n, raw in enumerate(raws, 1)]
    seen: dict[str, int] = {}
    for n, row in enumerate(rows, 1):
        if row[key] in seen:
            raise _bad(
                f"row {n}: {row[key]} already appears in row {seen[row[key]]}",
                f"One row per {key}: merge the columns into a single row",
            )
        seen[row[key]] = n
    return rows
