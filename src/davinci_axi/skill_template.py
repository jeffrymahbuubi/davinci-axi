"""The text of `skills/davinci-axi/SKILL.md`, rendered from the command registry.

Pure functions: the same specs always give the same text (LF endings, no live state, no dates).
"""

from __future__ import annotations

from davinci_axi.spec import CommandSpec, Flag

BIN = "davinci-axi"
REPO_URL = "https://github.com/jeffrymahbuubi/davinci-axi"

DESCRIPTION = (
    "Inspect DaVinci Resolve Studio from the command line, or change it with a dry run, "
    "snapshot and `--force`. Use when the user asks about Resolve projects, timelines, "
    "timeline items, render presets or the render queue, wants a project snapshot, "
    "needs live Resolve state for an editing task, or wants a project edited safely."
)

PREREQUISITES = (
    "DaVinci Resolve **Studio** 21.1 or newer must be running, with "
    "**Preferences > System > General > External scripting using** set to **Local**. "
    "The free edition does not allow external scripting."
)

OUTPUT_NOTES = (
    "- Output is TOON by default (compact `key: value` lines and `name[N]{fields}:` tables). "
    "Add `--json` for JSON.",
    "- Every result ends with a `help[]` list of suggested next commands.",
    "- Errors print `error`, `code` and `help[]` on stdout. Exit codes: `0` ok, `1` runtime "
    "error, `2` usage error.",
    "- Connection failures carry one of `RESOLVE_NOT_INSTALLED`, `RESOLVE_NOT_RUNNING`, "
    "`SCRIPTING_DISABLED`, `RESOLVE_STARTING` or `FREE_EDITION`; the `help[]` line says how "
    "to fix it.",
    "- Run `davinci-axi <command> --help` for the flags and examples of one command.",
)

SAFETY = (
    "- Commands that change Resolve (projects, timelines, tracks, markers, items, media) are "
    "dry runs: without `--force` they print the planned `changes[]` with `applied: false` and "
    "write nothing.",
    "- With `--force` a command saves the project, writes a snapshot to "
    "`~/.davinci-axi/snapshots/<id>`, makes the change, then verifies it by reading it back. "
    "`VERIFY_FAILED` means the read-back differed; the error names the snapshot.",
    "- `snapshot restore <id>` imports a snapshot as a new project and never overwrites an "
    "existing one.",
    "- Irreversible deletes are marked `irreversible: true` (with a `warning`) in the dry run "
    "and in the result; read the warning before passing `--force`.",
    "- `--no-snapshot` is refused unless the user sets `DAVINCI_AXI_ALLOW_UNSNAPSHOTTED=1`; "
    "never set it yourself.",
    "- Never open, close, save or delete the user's projects to answer a question; "
    "read the current state instead.",
    "- Every command checks its flags before it connects to Resolve, so a usage error is "
    "reported instantly and never touches the application.",
)


def command_rows(specs: list[tuple[str, CommandSpec]]) -> list[tuple[str, str, str]]:
    """(command path, summary, first example) for each command and subcommand, in order."""
    rows: list[tuple[str, str, str]] = []

    def add(path: str, spec: CommandSpec) -> None:
        if spec.run is not None or not spec.subcommands:
            example = spec.examples[0] if spec.examples else f"{BIN} {path} --help"
            rows.append((path, spec.summary, example))
        for name, child in spec.subcommands.items():
            add(f"{path} {name}", child)

    for name, spec in specs:
        add(name, spec)
    return rows


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _table(rows: list[tuple[str, str, str]]) -> list[str]:
    lines = ["| Command | What it does | Example |", "| --- | --- | --- |"]
    for path, summary, example in rows:
        lines.append(f"| `{BIN} {_cell(path)}` | {_cell(summary)} | `{_cell(example)}` |")
    return lines


def _flag_line(flag: Flag) -> str:
    name = flag.name.lstrip("-")
    if flag.kind == "bool":
        label = f"--{name}/--no-{name}" if flag.default is True else f"--{name}"
    else:
        label = f"--{name} <{flag.kind}>"
    has_default = flag.default is not None and flag.default is not False
    default = f" (default {flag.default})" if has_default else ""
    return f"- `{label}`{default}: {flag.help}"


def render(
    specs: list[tuple[str, CommandSpec]], global_flags: tuple[Flag, ...], home_description: str
) -> str:
    """The whole SKILL.md for `specs` (top-level commands in registry order, `home` first)."""
    lines = [
        "---",
        f"name: {BIN}",
        f"description: {DESCRIPTION}",
        "---",
        "",
        f"# {BIN}",
        "",
        f"{home_description}. Run `{BIN}` with no arguments for the live state of Resolve "
        "and the suggested next commands.",
        "",
        "## Prerequisites",
        "",
        PREREQUISITES,
        "",
        "## Install",
        "",
        "```bash",
        f"uv tool install git+{REPO_URL}",
        f"uvx --from git+{REPO_URL} {BIN}",
        "```",
        "",
        "## Commands",
        "",
        *_table(command_rows(specs)),
        "",
        "## Global flags",
        "",
        *(_flag_line(flag) for flag in global_flags),
        "",
        "## Output and errors",
        "",
        *OUTPUT_NOTES,
        "",
        "## Safety",
        "",
        *SAFETY,
        "",
    ]
    return "\n".join(lines)
