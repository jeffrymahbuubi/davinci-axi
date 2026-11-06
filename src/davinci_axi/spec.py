"""Command specification dataclasses and shared constants (pure data, no imports of Resolve)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from davinci_axi.session import Session

TRUNCATE_CHARS = 1500
DEFAULT_LIMIT = 100
DEFAULT_TIMEOUT = 3.0


@dataclass(frozen=True)
class Flag:
    name: str
    kind: Literal["str", "int", "float", "bool", "list"] = "str"
    default: object = None
    required: bool = False
    help: str = ""
    choices: tuple[str, ...] = ()
    renamed_from: str | None = None


@dataclass(frozen=True)
class Positional:
    name: str
    required: bool = True
    help: str = ""


@dataclass(frozen=True)
class GlobalOpts:
    json: bool = False
    debug: bool = False
    timeout: float = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class Result:
    data: dict[str, object]
    help: list[str] = field(default_factory=list)
    exit_code: int = 0


@dataclass
class Invocation:
    opts: GlobalOpts
    args: dict[str, object]
    positionals: dict[str, str]
    connect: Callable[[], Session]
    bin_name: str = "davinci-axi"


@dataclass(frozen=True)
class CommandSpec:
    name: str
    summary: str
    run: Callable[[Invocation], Result] | None = None
    positionals: tuple[Positional, ...] = ()
    flags: tuple[Flag, ...] = ()
    examples: tuple[str, ...] = ()
    subcommands: dict[str, CommandSpec] = field(default_factory=dict)
    reads_only: bool = True
    methods: tuple[str, ...] = ()


GLOBAL_FLAGS: tuple[Flag, ...] = (
    Flag("json", "bool", False, help="emit JSON instead of TOON"),
    Flag("debug", "bool", False, help="print tracebacks to stderr"),
    Flag(
        "timeout",
        "float",
        DEFAULT_TIMEOUT,
        help="seconds to wait for Resolve (env DAVINCI_AXI_TIMEOUT)",
    ),
)

LIST_FLAGS: tuple[Flag, ...] = (
    Flag("fields", "list", None, help="comma-separated fields to show"),
    Flag("limit", "int", DEFAULT_LIMIT, help="maximum rows to show"),
)
