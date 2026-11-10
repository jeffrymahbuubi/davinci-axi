"""Strict flag parser. Every flag is validated here, before any Resolve call is made."""

from __future__ import annotations

from typing import cast

from davinci_axi.errors import AxiError
from davinci_axi.spec import GLOBAL_FLAGS, CommandSpec, Flag

BIN = "davinci-axi"


def key_of(name: str) -> str:
    """Invocation.args key for a flag name: no dashes, underscores for hyphens."""
    return name.lstrip("-").replace("-", "_")


def _flag_name(flag: Flag) -> str:
    return flag.name.lstrip("-")


def _usage_error(spec: CommandSpec, path: str, message: str) -> AxiError:
    return AxiError(
        "VALIDATION_ERROR",
        message,
        help=[
            f"Valid flags for `{path}`: {valid_flags(spec)}",
            f"Run `{BIN} {path} --help` for usage",
        ],
    )


def valid_flags(spec: CommandSpec) -> str:
    """The flags of `spec` plus the global ones, for error messages."""
    names = [f"--{_flag_name(f)}" for f in spec.flags]
    names.extend(f"--{_flag_name(f)}" for f in GLOBAL_FLAGS)
    return ", ".join(names)


def _convert(flag: Flag, raw: str, spec: CommandSpec, path: str) -> object:
    name = _flag_name(flag)
    value: object = raw
    try:
        if flag.kind == "int":
            value = int(raw)
        elif flag.kind == "float":
            value = float(raw)
        elif flag.kind == "list":
            value = [part.strip() for part in raw.split(",") if part.strip()]
    except ValueError:
        raise _usage_error(spec, path, f"flag --{name} expects {flag.kind}, got {raw}") from None
    if flag.choices:
        picks = value if isinstance(value, list) else [value]
        bad = [str(p) for p in picks if str(p) not in flag.choices]
        if bad:
            raise AxiError(
                "VALIDATION_ERROR",
                f"invalid value {', '.join(bad)} for --{name}",
                help=[f"Valid values for --{name}: {', '.join(flag.choices)}"],
            )
    return value


def parse(
    spec: CommandSpec, argv: list[str], path: str | None = None
) -> tuple[dict[str, object], dict[str, str]]:
    """Parse `argv` (global flags already removed) against `spec`.

    Returns (args, positionals). `args` is keyed by flag name with underscores for hyphens;
    unset flags hold their default (bool flags default to False). A flag given by its old name
    (`renamed_from`) is accepted and stored under the new key; `args["_renamed"]` then lists one
    note per old name for the caller to surface. Raises VALIDATION_ERROR for an
    unknown flag, a bad value, a missing required flag or positional, or a surplus positional.
    """
    path = path or spec.name
    by_name = {_flag_name(f): f for f in spec.flags}
    renamed = {f.renamed_from.lstrip("-"): f for f in spec.flags if f.renamed_from}
    args: dict[str, object] = {}
    positional_values: list[str] = []
    tokens = list(argv)
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token == "--":
            positional_values.extend(tokens[i:])
            break
        if not token.startswith("--"):
            positional_values.append(token)
            continue
        body = token[2:]
        name, eq, inline = body.partition("=")
        negated = False
        old = None
        if name in renamed:
            old, name = name, _flag_name(renamed[name])
        elif name.startswith("no-") and name[3:] in renamed and renamed[name[3:]].kind == "bool":
            old, name, negated = name[3:], _flag_name(renamed[name[3:]]), True
        if old is not None:
            note = f"--{old} is now --{name}; the old name still works"
            notes = cast(list[str], args.setdefault("_renamed", []))
            if note not in notes:
                notes.append(note)
        if (
            name not in by_name
            and name.startswith("no-")
            and name[3:] in by_name
            and by_name[name[3:]].kind == "bool"
        ):
            name, negated = name[3:], True
        flag = by_name.get(name)
        if flag is None:
            raise _usage_error(spec, path, f"unknown flag --{name} for `{path}`")
        if flag.kind == "bool":
            if eq:
                lowered = inline.lower()
                if lowered not in ("true", "false", "1", "0"):
                    raise _usage_error(spec, path, f"flag --{name} takes no value, got {inline}")
                args[key_of(name)] = lowered in ("true", "1")
            else:
                args[key_of(name)] = not negated
            continue
        if eq:
            raw = inline
        elif i < len(tokens) and not tokens[i].startswith("--"):
            raw = tokens[i]
            i += 1
        else:
            raise _usage_error(spec, path, f"flag --{name} needs a value")
        args[key_of(name)] = _convert(flag, raw, spec, path)
    for flag in spec.flags:
        key = key_of(flag.name)
        if key in args:
            continue
        if flag.required:
            raise _usage_error(
                spec, path, f"missing required flag --{_flag_name(flag)} for `{path}`"
            )
        args[key] = False if flag.kind == "bool" and flag.default is None else flag.default
    return args, _bind_positionals(spec, path, positional_values)


def _bind_positionals(spec: CommandSpec, path: str, values: list[str]) -> dict[str, str]:
    if len(values) > len(spec.positionals):
        extra = values[len(spec.positionals)]
        if not spec.positionals:
            raise _usage_error(spec, path, f"`{path}` takes no positional arguments, got {extra}")
        names = ", ".join(f"<{p.name}>" for p in spec.positionals)
        raise AxiError(
            "VALIDATION_ERROR",
            f"unexpected argument {extra} for `{path}`",
            help=[
                f"Valid positionals for `{path}`: {names}",
                f"Run `{BIN} {path} --help` for usage",
            ],
        )
    bound: dict[str, str] = {}
    for index, positional in enumerate(spec.positionals):
        if index < len(values):
            bound[positional.name] = values[index]
        elif positional.required:
            raise AxiError(
                "VALIDATION_ERROR",
                f"missing <{positional.name}> for `{path}`",
                help=[f"Run `{BIN} {path} --help` for usage and examples"],
            )
    return bound
