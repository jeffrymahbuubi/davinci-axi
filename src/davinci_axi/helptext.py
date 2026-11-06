"""`--help` rendering: usage, flags with defaults, examples. Pure data, no side effects."""

from __future__ import annotations

from davinci_axi.spec import GLOBAL_FLAGS, CommandSpec, Flag

BIN = "davinci-axi"


def _default_value(flag: Flag) -> object:
    if flag.default is None:
        return "required" if flag.required else None
    return flag.default


def _flag_label(flag: Flag) -> str:
    name = flag.name.lstrip("-")
    if flag.kind == "bool":
        return f"--{name}"
    return f"--{name} <{'a,b' if flag.kind == 'list' else flag.kind}>"


def _flag_rows(flags: tuple[Flag, ...]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for flag in flags:
        text = flag.help
        if flag.choices:
            text = f"{text} (one of {'|'.join(flag.choices)})".strip()
        rows.append({"flag": _flag_label(flag), "default": _default_value(flag), "help": text})
    return rows


def render(spec: CommandSpec, path: tuple[str, ...] = ()) -> dict[str, object]:
    """The help document for one command or subcommand. `path` is its full command path."""
    words = path or (spec.name,)
    full = " ".join(words)
    parts = [BIN, full]
    if spec.subcommands:
        parts.append("<subcommand>")
    parts.extend(f"<{p.name}>" if p.required else f"[{p.name}]" for p in spec.positionals)
    if spec.flags:
        parts.append("[flags]")
    doc: dict[str, object] = {"usage": " ".join(parts), "summary": spec.summary}
    if spec.subcommands:
        doc["subcommands"] = [
            {"name": n, "summary": s.summary} for n, s in spec.subcommands.items()
        ]
    if spec.positionals:
        doc["positionals"] = [
            {"name": p.name, "required": p.required, "help": p.help} for p in spec.positionals
        ]
    if spec.flags:
        doc["flags"] = _flag_rows(spec.flags)
    doc["global_flags"] = _flag_rows(GLOBAL_FLAGS)
    doc["examples"] = list(spec.examples) or [f"{BIN} {full} --help"]
    return doc


def render_top(
    description: str, commands: list[tuple[str, str]], examples: list[str]
) -> dict[str, object]:
    """Top-level help: `commands[N]{command,summary}`, global flags and examples."""
    return {
        "usage": f"{BIN} [command] [flags]",
        "description": description,
        "commands": [{"command": name, "summary": summary} for name, summary in commands],
        "global_flags": _flag_rows(GLOBAL_FLAGS),
        "note": f"{BIN} --version prints the version",
        "examples": examples,
    }
