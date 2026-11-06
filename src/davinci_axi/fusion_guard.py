"""The one Fusion safety helper (KTD22): preflight, timeout, comp snapshot and undo grouping.

Every Fusion command goes through this module. Fusion calls from an external script can hang with
no error (fusion-scripting quirk 1), so each call runs under a timeout in a daemon thread
(`fusion_call`, as `connect._run_with_timeout` does); a mutation exports the comp to
`snapshot_root()/fusion/` first (`export_comp`) and wraps its calls in one undo event
(`undo_group`). The interface is frozen: later units import it and never edit it.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from davinci_axi.commands.item_detail import Located, resolve_item
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.output import collapse_home, hint
from davinci_axi.probe import is_fake
from davinci_axi.resolve_types import ResolveApp, TimelineApi, TimelineItemApi
from davinci_axi.session import Session
from davinci_axi.snapshot import snapshot_root
from davinci_axi.spec import Flag, Invocation

T = TypeVar("T")

FUSION_TIMEOUT_ENV = "DAVINCI_AXI_FUSION_TIMEOUT"
DEFAULT_FUSION_TIMEOUT = 10.0
UNDO_PREFIX = "davinci-axi: "

FUSION_FLAGS: tuple[Flag, ...] = (
    Flag("item", "str", None, help="timeline item: <V|A|S><track>:<index> or its unique id"),
    Flag("comp", "str", "1", help="comp name or 1-based index on the item"),
    Flag("timeline", "str", None, help="timeline name; it must be the current timeline"),
    Flag(
        "active", "bool", False, help="use the comp open on the Fusion page (read-only verbs only)"
    ),
)


class ResolveFusion(ResolveApp, Protocol):
    def Fusion(self) -> Any: ...


class TimelineItemFusion(TimelineItemApi, Protocol):
    def GetFusionCompByIndex(self, compIndex: int) -> Any | None: ...
    def GetFusionCompByName(self, compName: str) -> Any | None: ...
    def ExportFusionComp(self, path: str, compIndex: int) -> bool: ...
    def GetIsFusionOutputCacheEnabled(self) -> str: ...


@dataclass
class CompRef:
    item: Located | None
    item_ref: str
    comp: Any
    comp_name: str
    comp_index: int
    timeline: TimelineApi | None


@dataclass(frozen=True)
class Preflight:
    timeline: str
    resolvepython_processes: int
    warnings: list[str]


def _text(inv: Invocation, key: str) -> str | None:
    value = inv.args.get(key)
    return value if isinstance(value, str) and value != "" else None


def _timeout_seconds(timeout: float | None) -> float:
    if timeout is not None:
        return timeout
    raw = os.environ.get(FUSION_TIMEOUT_ENV)
    if not raw:
        return DEFAULT_FUSION_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if value <= 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"{FUSION_TIMEOUT_ENV} needs a positive number of seconds, got {raw}",
            help=[f"Valid example: {FUSION_TIMEOUT_ENV}=30"],
        )
    return value


def fusion_call(what: str, fn: Callable[..., T], *args: object, timeout: float | None = None) -> T:
    """Run `fn(*args)` in a daemon thread; FUSION_TIMEOUT if it does not return in time.

    A hung call cannot be cancelled, so the thread is abandoned and the caller must make no
    further Fusion call. An exception from `fn` is re-raised (AxiError as is, anything else as
    API_FAILED).
    """
    limit = _timeout_seconds(timeout)
    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            outcome["value"] = fn(*args)
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=worker, name="davinci-axi-fusion", daemon=True)
    thread.start()
    thread.join(limit)
    if thread.is_alive():
        raise AxiError(
            "FUSION_TIMEOUT",
            f"Fusion call {what} did not return within {limit:g} seconds",
            help=[
                "Resolve may be hung: two ResolvePython processes usually mean a stuck Fusion call",
                "Quit Resolve (kill -9 if it does not exit) and relaunch it, then retry",
                f"Open the target timeline first, or raise the wait: {FUSION_TIMEOUT_ENV}=30",
            ],
            detail={"call": what, "seconds": limit},
        )
    error = outcome.get("error")
    if isinstance(error, AxiError):
        raise error
    if isinstance(error, BaseException):
        raise api_failed(what, f"{type(error).__name__}: {error}") from error
    return cast(T, outcome.get("value"))


def count_resolve_python() -> int:
    """How many `ResolvePython` processes run (macOS only; 0 elsewhere and in fake mode).

    Uses `pgrep -f` and counts the lines: the BSD `pgrep` has no `-c` flag.
    """
    if is_fake() or sys.platform != "darwin":
        return 0
    try:
        done = subprocess.run(
            ["pgrep", "-f", "ResolvePython"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    return len([line for line in done.stdout.splitlines() if line.strip()])


def preflight(session: Session, inv: Invocation) -> Preflight:
    """Refuse a non-current `--timeline` and count `ResolvePython` processes (quirk 1)."""
    timeline = session.require_timeline(_text(inv, "timeline"))
    current = session.require_timeline()
    if timeline.GetUniqueId() != current.GetUniqueId():
        name = timeline.GetName()
        raise AxiError(
            "FUSION_TIMELINE_NOT_CURRENT",
            f"timeline {name} is not the current timeline",
            help=[
                "Fusion calls can hang on a timeline that is not open, so none is made",
                hint(f"timeline open {shlex.quote(name)} --force", "open it, then retry"),
            ],
            detail={"timeline": name, "current": current.GetName()},
        )
    processes = count_resolve_python()
    warnings: list[str] = []
    if processes >= 2:
        warnings.append(
            f"{processes} ResolvePython processes are running; two or more usually mean a hung "
            "Fusion call (quit Resolve and relaunch it if calls time out)"
        )
    return Preflight(current.GetName(), processes, warnings)


def validate_target(inv: Invocation, *, mutating: bool) -> None:
    """Check the `--item` and `--active` flags before any Resolve call (exit 2)."""
    active = bool(inv.args.get("active"))
    item = _text(inv, "item")
    if active and mutating:
        raise AxiError(
            "VALIDATION_ERROR",
            "--active is read-only: a Fusion change needs --item so the comp can be exported first",
            help=["Valid example: --item V1:2 (use `davinci-axi items` to find the item)"],
        )
    if active and item is not None:
        raise AxiError(
            "VALIDATION_ERROR",
            "--active and --item contradict each other",
            help=["Pass one of them, not both"],
        )
    if not active and item is None:
        raise AxiError(
            "VALIDATION_ERROR",
            "no target comp: pass --item <id> or --active",
            help=["Valid examples: --item V1:2, --item <unique id>, --active (reads only)"],
        )


def lua_values(table: object) -> list[Any]:
    """A Lua table arriving as a 1-based dict (or a list) as a list in key order."""
    if table is None:
        return []
    if isinstance(table, dict):
        try:
            return [table[key] for key in sorted(table)]
        except TypeError:
            return list(table.values())
    if isinstance(table, (list, tuple)):
        return list(table)
    return []


def attrs_of(obj: Any) -> dict[str, Any]:
    found = fusion_call("GetAttrs", obj.GetAttrs)
    return found if isinstance(found, dict) else {}


def comp_name_of(comp: Any) -> str:
    name = attrs_of(comp).get("COMPS_Name")
    return str(name) if name else "(active comp)"


def fusion_app(session: Session) -> Any:
    """`resolve.Fusion()` under the timeout; API_FAILED when Resolve returns nothing."""
    app = fusion_call("Resolve.Fusion", cast(ResolveFusion, session.resolve).Fusion)
    if app is None:
        raise api_failed("Resolve.Fusion", "Resolve returned no Fusion object")
    return app


def resolve_comp(session: Session, inv: Invocation, *, mutating: bool) -> CompRef:
    """The comp the flags name: `--active`, or `--item` with `--comp` (name or 1-based index)."""
    validate_target(inv, mutating=mutating)
    if inv.args.get("active"):
        comp = fusion_call("GetCurrentComp", fusion_app(session).GetCurrentComp)
        if comp is None:
            raise AxiError(
                "NOT_FOUND",
                "no comp is open on the Fusion page",
                help=[
                    "Open the Fusion page on a clip in Resolve, or use --item to name the clip",
                    "GetCurrentComp returns nothing until a comp has been loaded on that page",
                ],
            )
        return CompRef(None, "", comp, comp_name_of(comp), 0, None)
    timeline = session.require_timeline(_text(inv, "timeline"))
    item_ref = _text(inv, "item") or ""
    located = resolve_item(timeline, item_ref)
    item = cast(TimelineItemFusion, located.item)
    names = [str(n) for n in fusion_call("GetFusionCompNameList", item.GetFusionCompNameList) or []]
    if not names:
        raise AxiError(
            "NOT_FOUND",
            f"item {item_ref} has no Fusion comp",
            help=["Create a comp for the item in Resolve first (Fusion page or Inspector)"],
        )
    wanted = _text(inv, "comp") or "1"
    if wanted in names:
        index = names.index(wanted) + 1
        comp = fusion_call("GetFusionCompByName", item.GetFusionCompByName, wanted)
    elif wanted.isdigit() and 1 <= int(wanted) <= len(names):
        index = int(wanted)
        comp = fusion_call("GetFusionCompByIndex", item.GetFusionCompByIndex, index)
    else:
        raise AxiError(
            "NOT_FOUND",
            f"comp {wanted} not found on item {item_ref}",
            help=[f"Comps on this item: {', '.join(names)} (use the name or a 1-based index)"],
        )
    if comp is None:
        raise api_failed("GetFusionComp", f"comp {names[index - 1]} did not load")
    return CompRef(located, item_ref, comp, names[index - 1], index, timeline)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "comp"


def export_comp(ref: CompRef, reason: str) -> Path:
    """Write the comp to `snapshot_root()/fusion/<ts>-<item uid>-<comp slug>.comp`: the rollback."""
    if ref.item is None:
        raise AxiError(
            "FUSION_SNAPSHOT_FAILED",
            f"cannot snapshot the active comp before {reason}: it belongs to no named item",
            help=["Name the clip with --item so its comp can be exported"],
        )
    item = cast(TimelineItemFusion, ref.item.item)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    stem = f"{stamp}-{_slug(item.GetUniqueId())}-{_slug(ref.comp_name)}"
    folder = snapshot_root() / "fusion"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AxiError(
            "FUSION_SNAPSHOT_FAILED",
            f"could not create the comp snapshot folder: {exc}",
            help=["Check that the snapshot location is writable; no change was made"],
        ) from exc
    path, number = folder / f"{stem}.comp", 1
    while path.exists():
        number += 1
        path = folder / f"{stem}-{number}.comp"
    done = fusion_call("ExportFusionComp", item.ExportFusionComp, str(path), ref.comp_index)
    if not done or not path.is_file():
        raise AxiError(
            "FUSION_SNAPSHOT_FAILED",
            f"Resolve could not export comp {ref.comp_name} before {reason}",
            help=[
                "No change was made",
                "Check disk space and that the item is on the current timeline, then retry",
            ],
            detail={"comp": ref.comp_name, "path": collapse_home(path)},
        )
    return path


@contextmanager
def undo_group(comp: Any, name: str, *, lock: bool = True) -> Iterator[None]:
    """Lock, `StartUndo(name)`, the block, `EndUndo(True)`, Unlock; `EndUndo(False)` on error.

    `lock=False` skips Lock and Unlock (setting keyframes while locked loses them, quirk 3). After
    a FUSION_TIMEOUT nothing more is sent: the comp is hung and another call would hang too.
    """
    if lock:
        fusion_call("Lock", comp.Lock)
    hung = False
    try:
        fusion_call("StartUndo", comp.StartUndo, name)
        try:
            yield
        except AxiError as err:
            hung = err.code == "FUSION_TIMEOUT"
            if not hung:
                fusion_call("EndUndo", comp.EndUndo, False)
            raise
        except BaseException:
            fusion_call("EndUndo", comp.EndUndo, False)
            raise
        else:
            fusion_call("EndUndo", comp.EndUndo, True)
    except AxiError as err:
        hung = hung or err.code == "FUSION_TIMEOUT"
        raise
    finally:
        if lock and not hung:
            fusion_call("Unlock", comp.Unlock)


def tools_of(comp: Any) -> list[Any]:
    """Every tool of the comp (`GetToolList()` is a 1-based Lua dict)."""
    return lua_values(fusion_call("GetToolList", comp.GetToolList))


def tool_name(tool: Any) -> str:
    return str(attrs_of(tool).get("TOOLS_Name") or "")


def find_tool(comp: Any, name: str) -> Any:
    """The tool called `name`; NOT_FOUND lists the tool names of the comp."""
    tool = fusion_call("FindTool", comp.FindTool, name)
    if tool is None:
        names = [tool_name(t) for t in tools_of(comp)]
        raise AxiError(
            "NOT_FOUND",
            f"tool {name} not found",
            help=[f"Tools in this comp: {', '.join(names) if names else '(none)'}"],
        )
    return tool


def _position(comp: Any, tool: Any) -> tuple[float, float] | None:
    def read() -> tuple[float, float] | None:
        frame = getattr(comp, "CurrentFrame", None)
        flow = getattr(frame, "FlowView", None) if frame is not None else None
        if flow is None:
            return None
        table = flow.GetPosTable(tool)
        if not isinstance(table, dict):
            return None
        values = lua_values(table)
        if len(values) < 2:
            return None
        return float(values[0]), float(values[1])

    try:
        return fusion_call("GetPosTable", read)
    except AxiError as err:
        if err.code == "FUSION_TIMEOUT":
            raise
        return None


def tool_row(comp: Any, tool: Any) -> dict[str, object]:
    """`name`, `id` (TOOLS_RegID), `passthrough` (TOOLB_PassThrough) and `pos` ((x, y) or None)."""
    attrs = attrs_of(tool)
    return {
        "name": attrs.get("TOOLS_Name"),
        "id": attrs.get("TOOLS_RegID"),
        "passthrough": bool(attrs.get("TOOLB_PassThrough")),
        "pos": _position(comp, tool),
    }


def comp_snapshot_hint(path: Path) -> str:
    return (
        f"Re-import with `fusion comp import {shlex.quote(collapse_home(path))} "
        "--item <id> --force`"
    )
