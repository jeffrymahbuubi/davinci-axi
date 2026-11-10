"""`render settings set`: write the project's render settings without queueing a job (F36b).

`render add` applies `TargetDir`, `CustomName` and `--set` keys, and they stay on the project even
after the job is deleted. This verb is the way back: it calls `Project.SetRenderSettings` and
nothing else (no preset, no job, no timeline switch). `SetRenderSettings` has no getter, so the
result is verified by the call's return value only (KTD23) and reports what was sent.
"""

from __future__ import annotations

from pathlib import Path

from davinci_axi.commands.render_common import (
    RENDER_SETTING_TYPES,
    coerce_setting,
    methods_of,
    render_project,
    settings_warning,
)
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    keep_page,
    parse_assignments,
    run_mutation,
)
from davinci_axi.output import hint
from davinci_axi.spec import CommandSpec, Invocation, Positional, Result

NO_GETTER_NOTE = (
    "the API has no GetRenderSettings: this result lists the values sent, verified by the "
    "SetRenderSettings return only; render add checks the directory, this command does not"
)

PAGE_NOTE = (
    "SetRenderSettings opens the Deliver page; the page that was open is put back (page_restored)"
)


def parse_all_settings(raw: str) -> dict[str, object]:
    """`Key=value,...` as typed RenderSettings; TargetDir and CustomName are allowed here."""
    pairs = parse_assignments(raw)
    unknown = [key for key in pairs if key not in RENDER_SETTING_TYPES]
    if unknown:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown render setting {', '.join(unknown)}",
            help=[f"Valid settings: {', '.join(RENDER_SETTING_TYPES)}"],
        )
    settings = {key: coerce_setting(key, text) for key, text in pairs.items()}
    if "TargetDir" in settings:
        settings["TargetDir"] = str(Path(str(settings["TargetDir"])).expanduser().resolve())
    return settings


def run_settings_set(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    raw = inv.positionals["settings"]
    settings = parse_all_settings(raw)
    session = inv.connect()
    project = render_project(session)
    plan = MutationPlan(
        command="render settings set",
        target=f"project {project.GetName()}",
        changes=[Change("called", False, True)],
        force_cmd=force_cmd(inv, "render", "settings", "set", raw),
        snapshot="none",
        warning=settings_warning(settings),
        extra={
            "settings": [f"{key}={value}" for key, value in settings.items()],
            "note": NO_GETTER_NOTE,
        },
        next=[hint("render add --target-dir <dir>", "queue a job with these settings")],
    )

    def apply() -> None:
        with keep_page(session) as page_state:
            if not project.SetRenderSettings(dict(settings)):
                raise api_failed("SetRenderSettings", "Resolve rejected the render settings")
        plan.extra.update(page_state)

    def verify() -> dict[str, object]:
        return {"called": True}  # apply raised otherwise; the API has no getter

    return run_mutation(inv, session, plan, apply, verify)


SETTINGS_SET_SPEC = CommandSpec(
    name="set",
    summary="Set the project's render settings without queueing a job (not read back)",
    run=run_settings_set,
    positionals=(
        Positional(
            "settings",
            help="comma-separated Key=value settings, e.g. TargetDir=~/renders,CustomName=edit-01",
        ),
    ),
    flags=MUTATION_FLAGS,
    notes=(
        f"Keys: {', '.join(RENDER_SETTING_TYPES)}",
        "VideoQuality is kbps or Least|Low|Medium|High|Best",
        NO_GETTER_NOTE,
        PAGE_NOTE,
        "Typical use: restore TargetDir and CustomName after a probe `render add`",
    ),
    examples=(
        "davinci-axi render settings set TargetDir=~/renders,CustomName=edit-01",
        "davinci-axi render settings set SelectAllFrames=true --force",
    ),
    reads_only=False,
    methods=methods_of(Resolve="GetCurrentPage OpenPage", Project="GetName SetRenderSettings"),
)

SETTINGS_SPEC = CommandSpec(
    name="settings",
    summary="Set the project's render settings",
    subcommands={"set": SETTINGS_SET_SPEC},
    examples=("davinci-axi render settings set TargetDir=~/renders,CustomName=edit-01",),
    reads_only=False,
    methods=SETTINGS_SET_SPEC.methods,
)
