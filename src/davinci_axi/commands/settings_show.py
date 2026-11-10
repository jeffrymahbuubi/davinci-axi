"""`project settings show` and `timeline settings show`: read settings without a snapshot (F15a).

Read-only. With no keys every setting is listed, sorted by name; with a comma-separated list only
those keys, and an unknown key is a `VALIDATION_ERROR` that lists the valid ones.
`project_extra.py` wires the project verb; `timeline_extra.py` wires the timeline verb.
"""

from __future__ import annotations

from collections.abc import Mapping

from davinci_axi.commands.timeline_tracks import TIMELINE_FLAG, is_custom, target_timeline
from davinci_axi.errors import AxiError
from davinci_axi.output import hint
from davinci_axi.spec import CommandSpec, Invocation, Positional, Result

SHOWN_KEYS = 12
CUSTOM = "useCustomSettings"
KEYS_ARG = Positional("keys", required=False, help="comma-separated setting names (default: all)")


def _pick(live: Mapping[str, object], keys_text: str | None, noun: str) -> dict[str, object]:
    """All settings sorted by name, or the named ones in the order given."""
    if keys_text is None or not keys_text.strip():
        return {key: live[key] for key in sorted(live)}
    wanted = list(dict.fromkeys(k.strip() for k in keys_text.split(",") if k.strip()))
    unknown = [key for key in wanted if key not in live]
    if unknown:
        keys = sorted(live)
        shown = ", ".join(keys[:SHOWN_KEYS]) + (" ..." if len(keys) > SHOWN_KEYS else "")
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown {noun} setting {', '.join(unknown)}",
            help=[f"Valid settings ({len(keys)}): {shown}"],
        )
    return {key: live[key] for key in wanted}


def _keys(inv: Invocation) -> str | None:
    return inv.positionals.get("keys")


def run_project_show(inv: Invocation) -> Result:
    project = inv.connect().require_project()
    live = dict(project.GetSettings() or {})
    picked = _pick(live, _keys(inv), "project")
    data: dict[str, object] = {
        "project": project.GetName(),
        "count": len(picked),
        "settings": picked,
    }
    return Result(
        data,
        [hint("project settings set <key>=<value>", "change a setting (dry run until --force)")],
    )


def run_timeline_show(inv: Invocation) -> Result:
    timeline = target_timeline(inv.connect(), inv)
    live = dict(timeline.GetSettings() or {})
    picked = _pick(live, _keys(inv), "timeline")
    custom = is_custom(live.get(CUSTOM))
    data: dict[str, object] = {
        "timeline": timeline.GetName(),
        "custom": custom,
        "count": len(picked),
        "settings": picked,
    }
    if not custom:
        data["note"] = "timeline follows the project settings (useCustomSettings is 0)"
    return Result(
        data,
        [hint("timeline settings set <key>=<value>", "change a setting (dry run until --force)")],
    )


PROJECT_SETTINGS_SHOW_SPEC = CommandSpec(
    name="show",
    summary="Show project settings, all or the named keys",
    run=run_project_show,
    positionals=(KEYS_ARG,),
    examples=(
        "davinci-axi project settings show",
        "davinci-axi project settings show timelineResolutionWidth,timelineResolutionHeight",
    ),
    methods=("Project.GetSettings",),
)

TIMELINE_SETTINGS_SHOW_SPEC = CommandSpec(
    name="show",
    summary="Show timeline settings, all or the named keys",
    run=run_timeline_show,
    positionals=(KEYS_ARG,),
    flags=(TIMELINE_FLAG,),
    examples=(
        "davinci-axi timeline settings show",
        'davinci-axi timeline settings show timelineFrameRate --timeline "Edit v2"',
    ),
    methods=("Timeline.GetSettings",),
)
