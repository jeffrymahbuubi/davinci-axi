"""`app priority|keyframe-mode|background-tasks`: Resolve session switches (U23, R35).

All three change the running Resolve session, not the project, so none takes a snapshot; each
still needs `--force`. `keyframe-mode set` is read back with `GetKeyframeMode`. `SetHighPriority`
and `DisableBackgroundTasksForCurrentResolveSession` have no getter (KTD23): the change is
`called: false -> true`, verified by the call's return value, and the warning says it is not read
back. Background tasks stay off until Resolve restarts.
"""

from __future__ import annotations

from typing import Any, Protocol, cast

from davinci_axi.commands.app_reads import SHOW_SPEC
from davinci_axi.commands.project_io import next_steps
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Invocation, Positional, Result

MODES = ("all", "color", "sizing")
STATES = ("on", "off")
NOT_READ_BACK = "Resolve cannot report this setting, so the change is not read back"


class AppSwitches(Protocol):
    """The session switches of the `Resolve` class that this module calls."""

    def GetKeyframeMode(self) -> int: ...
    def SetKeyframeMode(self, keyframeMode: float) -> bool: ...
    def SetHighPriority(self, highPriority: bool) -> bool: ...
    def DisableBackgroundTasksForCurrentResolveSession(self) -> Any: ...


def _app(session: Session) -> AppSwitches:
    return cast(AppSwitches, session.resolve)


def _choice(raw: str, allowed: tuple[str, ...], what: str, example: str) -> str:
    value = raw.strip().lower()
    if value not in allowed:
        raise AxiError(
            "VALIDATION_ERROR",
            f"{raw} is not a valid {what}",
            help=[f"Valid {what}s: {', '.join(allowed)}", f"Valid example: {example}"],
        )
    return value


# --- priority -------------------------------------------------------------------------------


def run_priority(inv: Invocation) -> Result:
    state = _choice(inv.positionals["state"], STATES, "state", "app priority on --force")
    check_snapshot_flags(inv)
    session = inv.connect()
    app = _app(session)
    on = state == "on"
    plan = MutationPlan(
        command="app priority",
        target=f"Resolve process priority {state}",
        changes=[Change("called", False, True)],
        force_cmd=force_cmd(inv, "app", "priority", state),
        warning=f"{NOT_READ_BACK}; the call's return value is checked",
        snapshot="none",
    )
    done = {"ok": False}

    def apply() -> None:
        if not app.SetHighPriority(on):
            raise api_failed("SetHighPriority", f"could not set high priority {state}")
        done["ok"] = True

    return run_mutation(inv, session, plan, apply, lambda: {"called": done["ok"]})


# --- keyframe mode --------------------------------------------------------------------------


def _mode_value(session: Session, mode: str) -> float:
    name = f"KEYFRAME_MODE_{mode.upper()}"
    value = getattr(session.resolve, name, None)
    if value is None:
        raise api_failed("SetKeyframeMode", f"this Resolve has no {name}")
    return float(value)


def _mode_word(session: Session, raw: object) -> str:
    """The word for a `GetKeyframeMode` value, or `unknown (<raw>)` when no constant matches."""
    for mode in MODES:
        value = getattr(session.resolve, f"KEYFRAME_MODE_{mode.upper()}", None)
        if value is not None and isinstance(raw, (int, float)) and float(value) == float(raw):
            return mode
    return f"unknown ({raw})"


def run_mode_show(inv: Invocation) -> Result:
    session = inv.connect()
    raw = _app(session).GetKeyframeMode()
    data: dict[str, object] = {"keyframe_mode": _mode_word(session, raw), "value": raw}
    return Result(data, next_steps(("app keyframe-mode set", "change it (needs --force)")))


def run_mode_set(inv: Invocation) -> Result:
    mode = _choice(inv.positionals["mode"], MODES, "mode", "app keyframe-mode set color --force")
    check_snapshot_flags(inv)
    session = inv.connect()
    app = _app(session)
    value = _mode_value(session, mode)
    before = _mode_word(session, app.GetKeyframeMode())
    plan = MutationPlan(
        command="app keyframe-mode set",
        target="the keyframe mode",
        changes=[] if before == mode else [Change("keyframe mode", before, mode)],
        force_cmd=force_cmd(inv, "app", "keyframe-mode", "set", mode),
        warning="already in this mode" if before == mode else "",
        snapshot="none",
        next=next_steps(("app keyframe-mode show", "read the keyframe mode")),
    )

    def apply() -> None:
        if not app.SetKeyframeMode(value):
            raise api_failed("SetKeyframeMode", f"could not set the {mode} keyframe mode")

    def verify() -> dict[str, object]:
        return {"keyframe mode": _mode_word(session, app.GetKeyframeMode())}

    return run_mutation(inv, session, plan, apply, verify)


# --- background tasks -----------------------------------------------------------------------


def run_background_disable(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    session = inv.connect()
    app = _app(session)
    plan = MutationPlan(
        command="app background-tasks disable",
        target="Resolve background tasks for this session",
        changes=[Change("called", False, True)],
        force_cmd=force_cmd(inv, "app", "background-tasks", "disable"),
        warning=f"lasts until Resolve restarts; {NOT_READ_BACK}",
        snapshot="none",
    )
    done = {"ok": False}

    def apply() -> None:
        # The .pyi gives this call no return type; only an explicit False counts as a refusal.
        if app.DisableBackgroundTasksForCurrentResolveSession() is False:
            raise api_failed(
                "DisableBackgroundTasksForCurrentResolveSession",
                "Resolve refused to disable background tasks",
            )
        done["ok"] = True

    return run_mutation(inv, session, plan, apply, lambda: {"called": done["ok"]})


# --- specs ----------------------------------------------------------------------------------

PRIORITY_SPEC = CommandSpec(
    name="priority",
    summary="Give Resolve high process priority on or off (dry run until --force; no snapshot)",
    run=run_priority,
    positionals=(Positional("state", help="on or off"),),
    flags=MUTATION_FLAGS,
    examples=("davinci-axi app priority on", "davinci-axi app priority off --force"),
    reads_only=False,
    methods=("Resolve.SetHighPriority",),
)

MODE_SHOW_SPEC = CommandSpec(
    name="show",
    summary="Show the keyframe mode (all, color or sizing)",
    run=run_mode_show,
    examples=("davinci-axi app keyframe-mode show",),
    methods=("Resolve.GetKeyframeMode",),
)

MODE_SET_SPEC = CommandSpec(
    name="set",
    summary="Set the keyframe mode to all, color or sizing (dry run until --force; no snapshot)",
    run=run_mode_set,
    positionals=(Positional("mode", help="all, color or sizing"),),
    flags=MUTATION_FLAGS,
    examples=(
        "davinci-axi app keyframe-mode set color",
        "davinci-axi app keyframe-mode set color --force",
    ),
    reads_only=False,
    methods=("Resolve.SetKeyframeMode", "Resolve.GetKeyframeMode"),
)

MODE_SPEC = CommandSpec(
    name="keyframe-mode",
    summary="Show or set which keyframes the viewer edits (all, color, sizing)",
    subcommands={"show": MODE_SHOW_SPEC, "set": MODE_SET_SPEC},
    examples=(
        "davinci-axi app keyframe-mode show",
        "davinci-axi app keyframe-mode set all --force",
    ),
    reads_only=False,
    methods=("Resolve.GetKeyframeMode", "Resolve.SetKeyframeMode"),
)

DISABLE_SPEC = CommandSpec(
    name="disable",
    summary="Turn off Resolve background tasks until it restarts (dry run until --force)",
    run=run_background_disable,
    flags=MUTATION_FLAGS,
    examples=(
        "davinci-axi app background-tasks disable",
        "davinci-axi app background-tasks disable --force",
    ),
    reads_only=False,
    methods=("Resolve.DisableBackgroundTasksForCurrentResolveSession",),
)

BACKGROUND_SPEC = CommandSpec(
    name="background-tasks",
    summary="Turn off Resolve background tasks for this session",
    subcommands={"disable": DISABLE_SPEC},
    examples=("davinci-axi app background-tasks disable --force",),
    reads_only=False,
    methods=DISABLE_SPEC.methods,
)

_SUBCOMMANDS = {
    "show": SHOW_SPEC,
    "priority": PRIORITY_SPEC,
    "keyframe-mode": MODE_SPEC,
    "background-tasks": BACKGROUND_SPEC,
}

SPEC = CommandSpec(
    name="app",
    summary="Switch Resolve session settings: process priority, keyframe mode, background tasks",
    subcommands=_SUBCOMMANDS,
    examples=(
        "davinci-axi app show",
        "davinci-axi app keyframe-mode show",
        "davinci-axi app priority on --force",
        "davinci-axi app background-tasks disable --force",
    ),
    reads_only=False,
    methods=tuple(dict.fromkeys(m for s in _SUBCOMMANDS.values() for m in s.methods)),
)
