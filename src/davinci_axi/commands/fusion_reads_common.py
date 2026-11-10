"""Shared helpers of the read-only `fusion` verbs: flags, fields, target lookup, list blocks."""

from __future__ import annotations

import shlex

from davinci_axi.fusion_guard import (
    FUSION_FLAGS,
    CompRef,
    Preflight,
    preflight,
    resolve_comp,
    validate_target,
)
from davinci_axi.output import apply_limit, select_fields
from davinci_axi.session import Session
from davinci_axi.spec import Invocation

ITEM_FLAGS = tuple(f for f in FUSION_FLAGS if f.name in ("item", "timeline"))
TIMELINE_FLAGS = tuple(f for f in FUSION_FLAGS if f.name == "timeline")
FIELDS_TOOLS = ("name", "id", "passthrough", "x", "y")
FIELDS_INPUTS = ("id", "name", "type", "value", "source")
FIELDS_EDGES = ("from", "output", "to", "input")
FIELDS_REGISTRY = ("id", "name")
FIELDS_UNDO = ("position", "name", "ours")
UNREADABLE_TYPES = frozenset({"Image", "Mask"})


def scalar(value: object) -> object:
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def requested(inv: Invocation) -> list[str] | None:
    raw = inv.args.get("fields")
    return [str(f) for f in raw] if isinstance(raw, list) else None


def limit_of(inv: Invocation) -> int:
    raw = inv.args.get("limit")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else 100


def check_list_flags(inv: Invocation, default: tuple[str, ...]) -> None:
    """Validate `--fields` and `--limit` before the first Resolve call (R11)."""
    select_fields([dict.fromkeys(default)], default, requested(inv))
    apply_limit([], limit_of(inv))


def target(inv: Invocation) -> tuple[Session, Preflight, CompRef]:
    validate_target(inv, mutating=False)
    session = inv.connect()
    pre = preflight(session, inv)
    return session, pre, resolve_comp(session, inv, mutating=False)


def head(pre: Preflight, ref: CompRef) -> dict[str, object]:
    data: dict[str, object] = {"timeline": pre.timeline}
    if ref.item_ref:
        data["item"] = ref.item_ref
    data["comp"] = ref.comp_name
    return data


def tail(data: dict[str, object], pre: Preflight) -> dict[str, object]:
    data["resolvepython_processes"] = pre.resolvepython_processes
    if pre.warnings:
        data["warnings"] = list(pre.warnings)
    return data


def target_flags(ref: CompRef) -> str:
    """The flags that address `ref` again, for next-step hints."""
    if not ref.item_ref:
        return "--active"
    flags = f"--item {shlex.quote(ref.item_ref)}"
    return flags if ref.comp_index <= 1 else f"{flags} --comp {shlex.quote(ref.comp_name)}"


def block(
    inv: Invocation,
    data: dict[str, object],
    key: str,
    rows: list[dict[str, object]],
    default: tuple[str, ...],
    empty: str,
    noun: str,
    total_key: str = "total",
) -> tuple[list[dict[str, object]], list[str]]:
    """Put the limited rows (or the empty-state text) under `key`; also return the --limit hint."""
    shown, total = apply_limit(rows, limit_of(inv))
    data[total_key] = total
    if total > len(shown):
        data["truncated"] = True
    data[key] = select_fields(shown, default, requested(inv)) if shown else empty
    more = total > len(shown)
    return shown, [f"Showing {len(shown)} of {total} {noun}; raise --limit to see more"] * more
