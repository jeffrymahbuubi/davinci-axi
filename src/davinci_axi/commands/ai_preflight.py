"""`ai_mutation`: `run_mutation` plus the AI Extras preflight and error memory (F11).

The preflight reads the Extras directory (`davinci_axi.extras`). Under `--force` a package known to
be missing refuses with NOT_AVAILABLE *before* `run_mutation`, so no save, export or snapshot
happens for a call that cannot work. An `unknown` status only warns; an advisory package (one with
a built-in fallback) never refuses. Failures of AI verbs under `--force` are remembered in
`ai_state` for `ai status`, and a later success of the same verb forgets them.
"""

from __future__ import annotations

from collections.abc import Callable

from davinci_axi import ai_state, extras
from davinci_axi.errors import AxiError
from davinci_axi.mutate import MutationPlan, run_mutation
from davinci_axi.output import collapse_home, hint
from davinci_axi.session import Session
from davinci_axi.spec import Flag, Invocation, Result

IGNORE_FLAG = Flag(
    "ignore-preflight",
    "bool",
    False,
    help="call Resolve even when `ai extras` says the needed AI package is missing",
)


def _project_name(session: Session) -> str | None:
    try:
        project = session.current_project()
        return str(project.GetName()) if project is not None else None
    except Exception:  # a failure to name the project must not mask the real error
        return None


def _field(chk: extras.Check, advisory: bool) -> str:
    package, report = chk.package, chk.report
    if chk.status == "installed":
        return f"installed: {package}"
    if chk.status == "missing":
        note = "built-in languages still work" if advisory else "will fail"
        return f"missing: {package} ({note})"
    where = collapse_home(report.dir) if report.dir is not None else "the Extras directory"
    reason = "has unnamed packages" if report.readable else "not found"
    return f"unknown: {where} {reason} (may fail)"


def _refusal(plan: MutationPlan, chk: extras.Check) -> AxiError:
    package = chk.package
    where = collapse_home(chk.report.dir) if chk.report.dir is not None else None
    return AxiError(
        "NOT_AVAILABLE",
        f"AI Extras package {package} is not installed, so {plan.command} would fail",
        help=[
            "No change was made and no snapshot was taken",
            extras.install_hint(package),
            hint("ai extras", "list the installed AI packages"),
            "Pass --ignore-preflight to call Resolve anyway",
        ],
        detail={"package": package, "preflight": "missing", "extras_dir": where},
    )


def ai_mutation(
    inv: Invocation,
    session: Session,
    plan: MutationPlan,
    apply: Callable[[], None],
    verify: Callable[[], dict[str, object]],
    *,
    package: str | None = None,
    advisory: bool = False,
) -> Result:
    """`run_mutation` with the Extras preflight for `package` and error memory for the verb."""
    force = bool(inv.args.get("force"))
    ignore = bool(inv.args.get("ignore_preflight"))
    chk = extras.check(package) if package else None
    gated_missing = chk is not None and chk.status == "missing" and not advisory
    if chk is not None:
        plan.extra["ai_extras"] = _field(chk, advisory)
        if gated_missing and not ignore:
            lead = f"will fail: AI Extras package {chk.package} is not installed"
            plan.warning = f"{lead}; {plan.warning}" if plan.warning else lead
    if chk is not None and gated_missing and force and not ignore:
        err = _refusal(plan, chk)
        ai_state.record(plan.command, err, _project_name(session))
        raise err
    try:
        result = run_mutation(inv, session, plan, apply, verify)
    except AxiError as err:
        if force and err.exit_code == 1:
            ai_state.record(plan.command, err, _project_name(session))
        raise
    if force:
        ai_state.forget(plan.command)
    if chk is not None and chk.status != "installed":
        tip = extras.install_hint(chk.package)
        if advisory:
            tip = f"Optional, for more languages: {tip.removesuffix(', then retry')}"
        result.help.extend([tip, hint("ai extras", "list the installed AI packages")])
    return result
