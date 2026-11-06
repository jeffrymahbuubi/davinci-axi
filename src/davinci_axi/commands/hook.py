"""`hook install`: opt-in SessionStart hook for Claude Code or Codex (no Resolve connection).

The hook entry is found by the marker `# davinci-axi` inside its command, so installing again is a
no-op and a stale command (moved binary) is repaired in place. Only the settings file for the
chosen agent and scope is ever written, and only when this command is run explicitly.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path

from davinci_axi.errors import AxiError
from davinci_axi.output import collapse_home, hint
from davinci_axi.spec import CommandSpec, Flag, Invocation, Result

MARKER = "# davinci-axi"
BIN = "davinci-axi"
HOOK_TIMEOUT = 10
AGENT_DIRS = {"claude-code": (".claude", "settings.json"), "codex": (".codex", "hooks.json")}
CODEX_NOTE = "Codex reads hooks only when `[features].hooks = true` is set in ~/.codex/config.toml"


def hook_command() -> str:
    """Bare `davinci-axi` when PATH resolves it to this executable, else an absolute command."""
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    found = shutil.which(BIN)
    if argv0 is not None and found:
        try:
            if argv0.resolve() == Path(found).resolve():
                return f"{BIN} {MARKER}"
        except OSError:
            pass
    if argv0 is not None and argv0.name in (BIN, f"{BIN}.exe"):
        return f"{shlex.quote(str(argv0.resolve()))} {MARKER}"
    return f"{shlex.quote(sys.executable)} -m davinci_axi {MARKER}"


def settings_path(agent: str, scope: str, directory: object) -> Path:
    """The file to edit: `<dir>/<agent dir>/<file>` (project) or the same under the home dir."""
    folder, name = AGENT_DIRS[agent]
    if scope == "user":
        base = Path.home()
    else:
        base = Path(str(directory)).expanduser() if directory else Path.cwd()
    return base / folder / name


def _invalid(shown: str, reason: str) -> AxiError:
    return AxiError(
        "HOOK_FILE_INVALID",
        f"{shown} {reason}",
        help=["Fix or remove the file by hand; davinci-axi left it untouched"],
    )


def load_settings(path: Path, shown: str) -> dict[str, object]:
    """The parsed settings object; `{}` for a missing or blank file."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise _invalid(shown, f"cannot be read: {exc.strerror or exc}") from None
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise _invalid(shown, f"is not valid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise _invalid(shown, "does not hold a JSON object")
    return data


def _session_start(settings: dict[str, object], shown: str, create: bool) -> list[object] | None:
    hooks = settings.get("hooks")
    if hooks is None:
        if not create:
            return None
        hooks = settings["hooks"] = {}
    if not isinstance(hooks, dict):
        raise _invalid(shown, "has a `hooks` value that is not an object")
    entries = hooks.get("SessionStart")
    if entries is None:
        if not create:
            return None
        entries = hooks["SessionStart"] = []
    if not isinstance(entries, list):
        raise _invalid(shown, "has a `hooks.SessionStart` value that is not a list")
    return entries


def _marked(entries: list[object]) -> list[tuple[dict[str, object], list[object], int]]:
    """Every (entry, its hooks list, index) whose command carries the marker."""
    found: list[tuple[dict[str, object], list[object], int]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            continue
        inner: list[object] = entry["hooks"]
        for index, item in enumerate(inner):
            command = item.get("command") if isinstance(item, dict) else None
            if isinstance(command, str) and MARKER in command:
                found.append((entry, inner, index))
    return found


def _is_current(item: object, command: str) -> bool:
    return (
        isinstance(item, dict)
        and item.get("type") == "command"
        and item.get("command") == command
        and item.get("timeout") == HOOK_TIMEOUT
    )


def apply_install(settings: dict[str, object], command: str, shown: str) -> str:
    """Add or repair the hook in `settings`. Returns `installed`, `repaired` or `unchanged`."""
    entries = _session_start(settings, shown, create=True)
    assert entries is not None
    marked = _marked(entries)
    if not marked:
        entries.append(
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT}],
            }
        )
        return "installed"
    status = "unchanged"
    for _entry, inner, index in marked:
        if _is_current(inner[index], command):
            continue
        item = inner[index]
        assert isinstance(item, dict)
        item.update({"type": "command", "command": command, "timeout": HOOK_TIMEOUT})
        status = "repaired"
    return status


def apply_remove(settings: dict[str, object], shown: str) -> bool:
    """Delete only the marked hooks, pruning containers that became empty. True if changed."""
    entries = _session_start(settings, shown, create=False)
    if entries is None:
        return False
    marked = _marked(entries)
    if not marked:
        return False
    for entry, inner, index in sorted(marked, key=lambda m: m[2], reverse=True):
        del inner[index]
        if not inner:
            entries[:] = [e for e in entries if e is not entry]
    hooks = settings["hooks"]
    assert isinstance(hooks, dict)
    if not entries:
        del hooks["SessionStart"]
    if not hooks:
        del settings["hooks"]
    return True


def check_status(settings: dict[str, object], command: str, shown: str) -> str:
    """`installed`, `stale` or `missing` for the hook in `settings`."""
    entries = _session_start(settings, shown, create=False)
    marked = _marked(entries) if entries else []
    if not marked:
        return "missing"
    return "installed" if all(_is_current(i[index], command) for _, i, index in marked) else "stale"


def write_settings(path: Path, settings: dict[str, object], shown: str) -> None:
    text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    temp = path.with_name(path.name + ".davinci-axi.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise AxiError(
            "HOOK_WRITE_FAILED",
            f"cannot write {shown}: {exc.strerror or exc}",
            help=["Check the directory exists and is writable, or pass --dir <path>"],
        ) from None


def run_install(inv: Invocation) -> Result:
    agent, scope = str(inv.args["agent"]), str(inv.args["scope"])
    remove, check = bool(inv.args.get("remove")), bool(inv.args.get("check"))
    if remove and check:
        raise AxiError(
            "VALIDATION_ERROR",
            "--remove and --check cannot be combined",
            help=["Run `davinci-axi hook install --check` or `davinci-axi hook install --remove`"],
        )
    if scope == "user" and inv.args.get("dir"):
        raise AxiError(
            "VALIDATION_ERROR",
            "--dir only applies to --scope project",
            help=["Drop --dir to use the user-level file, or use --scope project"],
        )
    path = settings_path(agent, scope, inv.args.get("dir"))
    shown = collapse_home(path)
    command = hook_command()
    settings = load_settings(path, shown)
    base: dict[str, object] = {"agent": agent, "scope": scope, "file": shown}
    if check:
        status = check_status(settings, command, shown)
        if status != "installed":
            raise AxiError(
                f"HOOK_{status.upper()}",
                f"SessionStart hook is {status} in {shown}",
                help=["Run `davinci-axi hook install` to fix it"],
            )
        return Result({**base, "status": status, "modified": False, "command": command})
    if remove:
        changed = apply_remove(settings, shown)
        if changed:
            write_settings(path, settings, shown)
        return Result(
            {**base, "status": "removed" if changed else "absent", "modified": changed},
            [] if changed else ["No davinci-axi hook found; nothing to remove"],
        )
    status = apply_install(settings, command, shown)
    changed = status != "unchanged"
    if changed:
        write_settings(path, settings, shown)
    help_lines = [hint("hook install --check", "verify the hook is current")]
    if agent == "codex":
        help_lines.append(CODEX_NOTE)
    return Result({**base, "status": status, "modified": changed, "command": command}, help_lines)


INSTALL_SPEC = CommandSpec(
    name="install",
    summary="Install (or repair, check, remove) the opt-in SessionStart hook",
    run=run_install,
    flags=(
        Flag("agent", "str", "claude-code", help="agent to configure", choices=tuple(AGENT_DIRS)),
        Flag(
            "scope",
            "str",
            "project",
            help="project file under --dir, or the user-level file",
            choices=("project", "user"),
        ),
        Flag("dir", "str", None, help="project directory (default: cwd; project scope only)"),
        Flag("remove", "bool", False, help="delete the davinci-axi hook entry"),
        Flag("check", "bool", False, help="exit 1 if the hook is missing or stale; write nothing"),
    ),
    examples=(
        "davinci-axi hook install",
        "davinci-axi hook install --agent codex --dir .",
        "davinci-axi hook install --check",
        "davinci-axi hook install --remove",
    ),
    reads_only=False,
)

SPEC = CommandSpec(
    name="hook",
    summary="Manage the opt-in SessionStart hook (no Resolve connection)",
    subcommands={"install": INSTALL_SPEC},
    examples=("davinci-axi hook install", "davinci-axi hook install --check"),
)
