"""Command line entry point.

Nothing heavy is imported at module level: `--version` must answer before any other import
(R4), and `--help` and usage errors must never touch Resolve (R11, R15).
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from davinci_axi.session import Session
    from davinci_axi.spec import CommandSpec, GlobalOpts

BIN = "davinci-axi"
DESCRIPTION = "Agent-ergonomic CLI for DaVinci Resolve Studio"
VERSION_FLAGS = ("-v", "-V", "--version")
HELP_FLAGS = ("--help", "-h")
TOP_EXAMPLES = [f"{BIN}", f"{BIN} --json", f"{BIN} --help"]


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    _configure_streams()
    if any(a in VERSION_FLAGS for a in _before_separator(args)):
        from davinci_axi import __version__

        sys.stdout.write(__version__ + "\n")
        return 0
    return _run(args)


def _before_separator(args: list[str]) -> list[str]:
    return args[: args.index("--")] if "--" in args else args


def _configure_streams() -> None:
    """UTF-8 everywhere and LF-only stdout, so output is identical on every platform."""
    for stream, newline in ((sys.stdout, "\n"), (sys.stderr, None)):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if newline is None:
                reconfigure(encoding="utf-8")
            else:
                reconfigure(encoding="utf-8", newline=newline)
        except (ValueError, OSError):
            continue


def _write(text: str) -> None:
    sys.stdout.write(text + "\n")


def _run(args: list[str]) -> int:
    from davinci_axi.errors import AxiError
    from davinci_axi.output import render_error

    scanned = _before_separator(args)
    as_json = "--json" in scanned
    try:
        return _dispatch(args)
    except AxiError as err:
        _write(render_error(err, as_json))
        return err.exit_code
    except Exception as exc:
        if "--debug" in scanned:
            import traceback

            traceback.print_exc(file=sys.stderr)
        internal = AxiError(
            "INTERNAL",
            f"unexpected {type(exc).__name__}: {exc}",
            help=[f"Run `{BIN} <command> --debug` to print the traceback on stderr"],
        )
        _write(render_error(internal, as_json))
        return internal.exit_code


def _parse_timeout(raw: str, source: str) -> float:
    from davinci_axi.errors import AxiError

    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if value <= 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"{source} needs a positive number of seconds, got {raw}",
            help=["Valid example: --timeout 10"],
        )
    return value


def _split_global(args: list[str]) -> tuple[GlobalOpts, list[str], bool]:
    """Pull the global flags out of `args` wherever they appear (before or after the command)."""
    from davinci_axi.errors import AxiError
    from davinci_axi.spec import DEFAULT_TIMEOUT, GlobalOpts

    env_timeout = os.environ.get("DAVINCI_AXI_TIMEOUT")
    timeout = _parse_timeout(env_timeout, "DAVINCI_AXI_TIMEOUT") if env_timeout else DEFAULT_TIMEOUT
    as_json = debug = want_help = False
    rest: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        i += 1
        if token == "--":
            rest.extend(args[i - 1 :])
            break
        if token == "--json":
            as_json = True
        elif token == "--debug":
            debug = True
        elif token in HELP_FLAGS:
            want_help = True
        elif token == "--timeout" or token.startswith("--timeout="):
            if token == "--timeout":
                if i >= len(args):
                    raise AxiError(
                        "VALIDATION_ERROR",
                        "flag --timeout needs a value",
                        help=["Valid example: --timeout 10"],
                    )
                raw = args[i]
                i += 1
            else:
                raw = token.partition("=")[2]
            timeout = _parse_timeout(raw, "--timeout")
        else:
            rest.append(token)
    return GlobalOpts(json=as_json, debug=debug, timeout=timeout), rest, want_help


def _dispatch(args: list[str]) -> int:
    from davinci_axi import flags, registry
    from davinci_axi.errors import AxiError
    from davinci_axi.spec import GLOBAL_FLAGS, Invocation

    opts, rest, want_help = _split_global(args)
    if want_help and (not rest or rest[0].startswith("-")):
        return _emit_help_top(opts)
    if not rest:
        rest = ["home"]
    name, tail = rest[0], rest[1:]
    if name.startswith("-"):
        valid = ", ".join(["--help", "--version", *(f"--{f.name}" for f in GLOBAL_FLAGS)])
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown flag {name}",
            help=[f"Valid flags: {valid}", f"Run `{BIN} --help` to list commands"],
        )
    spec = registry.lookup(name)
    if spec is None:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown command {name}",
            help=[
                f"Valid commands: {', '.join(registry.command_names())}",
                f"Run `{BIN} --help` for usage",
            ],
        )
    path = [name]
    spec, path, tail = _descend(spec, path, tail)
    from davinci_axi.output import render

    if want_help:
        from davinci_axi import helptext

        _write(render(helptext.render(spec, tuple(path)), [], opts.json))
        return 0
    command = " ".join(path)
    if spec.run is None:
        raise AxiError(
            "VALIDATION_ERROR",
            f"`{command}` needs a subcommand",
            help=[
                f"Valid subcommands for `{command}`: {', '.join(spec.subcommands)}",
                f"Run `{BIN} {command} --help` for usage",
            ],
        )
    parsed, positionals = flags.parse(spec, tail, command)
    inv = Invocation(
        opts=opts,
        args=parsed,
        positionals=positionals,
        connect=_connector(opts),
        bin_name=BIN,
    )
    result = spec.run(inv)
    _write(render(result.data, result.help, opts.json))
    return result.exit_code


def _descend(
    spec: CommandSpec, path: list[str], tail: list[str]
) -> tuple[CommandSpec, list[str], list[str]]:
    """Follow subcommand words while the next token names one."""
    from davinci_axi.errors import AxiError

    while spec.subcommands and tail and not tail[0].startswith("-"):
        word = tail[0]
        if word in spec.subcommands:
            spec = spec.subcommands[word]
            path = [*path, word]
            tail = tail[1:]
            continue
        if spec.run is not None:
            break
        command = " ".join(path)
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown subcommand {word} for `{command}`",
            help=[
                f"Valid subcommands for `{command}`: {', '.join(spec.subcommands)}",
                f"Run `{BIN} {command} --help` for usage",
            ],
        )
    return spec, path, tail


def _connector(opts: GlobalOpts) -> Callable[[], Session]:
    def connect() -> Session:
        from davinci_axi import connect as connect_module

        return connect_module.connect(opts)

    return connect


def _emit_help_top(opts: GlobalOpts) -> int:
    from davinci_axi import helptext, registry
    from davinci_axi.output import render

    rows: list[tuple[str, str]] = []
    for name in registry.command_names():
        spec = registry.lookup(name)
        if spec is not None:
            rows.append((name, spec.summary))
    _write(render(helptext.render_top(DESCRIPTION, rows, TOP_EXAMPLES), [], opts.json))
    return 0
