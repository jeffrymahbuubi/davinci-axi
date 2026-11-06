"""`render preset ...` and `render quick-export` (U16, R33).

`save`, `update`, `delete`, `import` and `quick-export` are mutations (`mutate.run_mutation`):
a dry run until `--force`, then save, snapshot, write and read-back. `update` and `delete` cannot
be undone in Resolve ("This action can not be undone"), so they carry `irreversible: true`.
`load` selects a preset (UI state, no snapshot) and has no getter, so it follows KTD23: the call's
return value is the check, and the summary says "not read back". `export` only writes a file.
`render quick-export` renders the current timeline with a quick export preset and polls it.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol, cast

from davinci_axi.commands.project_io import clean_name, exists_error, tree_bytes
from davinci_axi.commands.render_common import (
    DIR_BOOLS,
    DIR_FLAGS,
    RenderProject,
    force_words,
    methods_of,
    refuse_offline,
    render_project,
    target_dir,
)
from davinci_axi.commands.timeline_tracks import text_arg
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    SnapshotPolicy,
    check_snapshot_flags,
    force_cmd,
    refuse_existing,
    run_mutation,
)
from davinci_axi.output import collapse_home, hint
from davinci_axi.poll import POLL_FLAGS, QUICK_EXPORT, JobKind, poll_args, poll_status
from davinci_axi.resolve_types_edit import ResolveEdit
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result


class RenderResolve(ResolveEdit, Protocol):
    def ImportRenderPreset(self, presetPath: str) -> bool: ...
    def ExportRenderPreset(self, presetName: str, exportPath: str) -> bool: ...


NAME = Positional("name", help="render preset name (see `render presets`)")
SHOW = "render presets"
LIST = "Project.GetRenderPresetList"


def _presets(project: RenderProject) -> list[str]:
    return list(project.GetRenderPresetList() or [])


def _existing(project: RenderProject, name: str) -> None:
    presets = _presets(project)
    if name not in presets:
        listing = ", ".join(presets) if presets else "(none)"
        raise not_found("render preset", name, f"Render presets in this project: {listing}")


def _plan(
    inv: Invocation,
    project: RenderProject,
    verb: str,
    name: str,
    changes: list[Change],
    *,
    irreversible: bool = False,
    warning: str = "",
    snapshot: SnapshotPolicy = "required",
) -> MutationPlan:
    return MutationPlan(
        command=f"render preset {verb}",
        target=f"project {project.GetName()}",
        changes=changes,
        force_cmd=force_cmd(inv, "render", "preset", verb, name),
        irreversible=irreversible,
        warning=warning,
        snapshot=snapshot,
        next=[hint(SHOW, "list the render presets")],
    )


def _call(method: str, ok: bool, detail: str) -> None:
    if not ok:
        raise api_failed(method, detail)


def _open(inv: Invocation) -> tuple[Session, RenderProject, str]:
    check_snapshot_flags(inv)
    name = clean_name(inv.positionals["name"], "preset name")
    session = inv.connect()
    return session, render_project(session), name


def run_save(inv: Invocation) -> Result:
    session, project, name = _open(inv)
    if name in _presets(project):
        raise exists_error("render preset", name, f"project {project.GetName()}")
    plan = _plan(inv, project, "save", name, [Change("preset", None, name)])
    plan.extra["note"] = "saves the project's current render settings under this name"

    def apply() -> None:
        _call(
            "SaveAsNewRenderPreset", project.SaveAsNewRenderPreset(name), f"could not save {name}"
        )

    def verify() -> dict[str, object]:
        return {"preset": name if name in _presets(project) else None}

    return run_mutation(inv, session, plan, apply, verify)


def run_update(inv: Invocation) -> Result:
    session, project, name = _open(inv)
    _existing(project, name)
    plan = _plan(
        inv,
        project,
        "update",
        name,
        [Change("updated", False, True)],
        irreversible=True,
        warning="overwrites the saved preset with the current render settings; not read back",
    )

    def apply() -> None:
        _call("UpdateRenderPreset", project.UpdateRenderPreset(name), f"could not update {name}")

    return run_mutation(inv, session, plan, apply, lambda: {"updated": name in _presets(project)})


def run_delete(inv: Invocation) -> Result:
    session, project, name = _open(inv)
    _existing(project, name)
    plan = _plan(
        inv,
        project,
        "delete",
        name,
        [Change("preset", name, None)],
        irreversible=True,
        warning="Resolve cannot undo deleting a render preset; the project snapshot lacks it",
    )

    def apply() -> None:
        _call("DeleteRenderPreset", project.DeleteRenderPreset(name), f"could not delete {name}")

    def verify() -> dict[str, object]:
        return {"preset": name if name in _presets(project) else None}

    return run_mutation(inv, session, plan, apply, verify)


def run_load(inv: Invocation) -> Result:
    session, project, name = _open(inv)
    _existing(project, name)
    plan = _plan(inv, project, "load", name, [Change("called", False, True)], snapshot="none")
    plan.extra["note"] = "loads the preset into the current render settings; not read back"
    result = {"ok": False}

    def apply() -> None:
        result["ok"] = bool(project.LoadRenderPreset(name))
        _call("LoadRenderPreset", result["ok"], f"could not load {name}")

    return run_mutation(inv, session, plan, apply, lambda: {"called": result["ok"]})


def _preset_file(inv: Invocation) -> Path:
    return Path(inv.positionals["file"]).expanduser().resolve()


def run_import(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    path = _preset_file(inv)
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path; nothing was changed")
    session = inv.connect()
    project = render_project(session)
    resolve = cast(RenderResolve, session.resolve)
    before = _presets(project)
    plan = MutationPlan(
        command="render preset import",
        target=f"project {project.GetName()}",
        changes=[Change("presets", len(before), len(before) + 1)],
        force_cmd=force_cmd(inv, "render", "preset", "import", inv.positionals["file"]),
        warning="the imported preset becomes the selected preset",
        extra={"file": collapse_home(path)},
        next=[hint(SHOW, "list the render presets")],
    )

    def apply() -> None:
        ok = resolve.ImportRenderPreset(str(path))
        _call("ImportRenderPreset", ok, f"could not import {path.name}")
        now = _presets(project)
        added = [preset for preset in now if preset not in before]
        plan.changes[:] = [Change("presets", len(before), len(now))]
        plan.extra["imported"] = added or "(no new name; Resolve may have replaced a preset)"

    return run_mutation(inv, session, plan, apply, lambda: {"presets": len(_presets(project))})


def run_export(inv: Invocation) -> Result:
    name = clean_name(inv.positionals["name"], "preset name")
    path = _preset_file(inv)
    if path.is_dir():
        raise AxiError(
            "VALIDATION_ERROR",
            f"{collapse_home(path)} is a directory",
            help=["Pass a file path such as ~/preset.xml"],
        )
    refuse_existing(path, bool(inv.args.get("overwrite")))
    session = inv.connect()
    _existing(render_project(session), name)
    path.parent.mkdir(parents=True, exist_ok=True)
    _call(
        "ExportRenderPreset",
        cast(RenderResolve, session.resolve).ExportRenderPreset(name, str(path)),
        f"could not export {name}",
    )
    if not path.exists():
        raise api_failed("ExportRenderPreset", f"Resolve reported success but wrote no {path.name}")
    data: dict[str, object] = {
        "preset": name,
        "path": collapse_home(path),
        "bytes": tree_bytes(path),
    }
    return Result(data, [hint(f"render preset import {path}", "bring the preset back in")])


def run_quick_export_toggle(inv: Invocation) -> Result:
    session, project, name = _open(inv)
    on, off = bool(inv.args.get("on")), bool(inv.args.get("off"))
    if on == off:
        raise AxiError(
            "VALIDATION_ERROR",
            "pass exactly one of --on or --off",
            help=["Valid example: davinci-axi render preset quick-export 'H.264 Master' --on"],
        )
    _existing(project, name)
    enabled = name in (project.GetQuickExportRenderPresets() or [])
    plan = _plan(
        inv,
        project,
        "quick-export",
        name,
        [Change("quick export", enabled, on)] if enabled != on else [],
    )
    plan.force_cmd = force_cmd(
        inv, "render", "preset", "quick-export", name, f"--{'on' if on else 'off'}"
    )

    def apply() -> None:
        ok = project.SetQuickExportEnabledForRenderPreset(name, on)
        _call(
            "SetQuickExportEnabledForRenderPreset", ok, f"could not change quick export of {name}"
        )

    def verify() -> dict[str, object]:
        return {"quick export": name in (project.GetQuickExportRenderPresets() or [])}

    return run_mutation(inv, session, plan, apply, verify)


def run_quick_export(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    wait, timeout, interval = poll_args(inv)
    preset = inv.positionals["preset"].strip()
    name = text_arg(inv, "name")
    quality = inv.args.get("quality")
    if quality is not None and (not isinstance(quality, int) or quality < 0):
        raise AxiError(
            "VALIDATION_ERROR",
            "--quality must be 0 (automatic) or a bit rate",
            help=["Valid example: --quality 0"],
        )
    path = target_dir(inv)
    session = inv.connect()
    project = render_project(session)
    presets = project.GetQuickExportRenderPresets() or []
    if preset not in presets:
        listing = ", ".join(presets) if presets else "(none)"
        raise not_found("quick export preset", preset, f"Quick export presets: {listing}")
    timeline = session.require_timeline(None)
    refuse_offline(timeline, inv)
    upload = bool(inv.args.get("upload"))
    kind: JobKind = QUICK_EXPORT
    final = "Render Complete"
    if upload:
        kind = replace(QUICK_EXPORT, done=frozenset({"Upload Completed"}))
        final = "Upload Completed"
    settings: dict[str, Any] = {"TargetDir": str(path)}
    if name is not None:
        settings["CustomName"] = name
    if quality is not None:
        settings["VideoQuality"] = quality
    if upload:
        settings["EnableUpload"] = True
    cmd = ["render", "quick-export", preset, "--target-dir", str(inv.args["target_dir"])]
    for flag, value in (("--name", name), ("--quality", quality)):
        if value is not None:
            cmd.extend([flag, str(value)])
    before = str((project.GetRenderWithQuickExportStatus() or {}).get("JobStatus") or "Inactive")
    plan = MutationPlan(
        command="render quick-export",
        target=f"timeline {timeline.GetName()}",
        changes=[Change("status", before, final if wait else "started")],
        force_cmd=force_words(inv, cmd, (*DIR_BOOLS, "upload")),
        extra={"preset": preset, "target_dir": collapse_home(path)},
        next=[hint("render status", "list the render queue")],
    )

    def apply() -> None:
        if not path.exists() and inv.args.get("mkdir"):
            path.mkdir(parents=True, exist_ok=True)
        if project.RenderWithQuickExport(preset, dict(settings)) is None:
            raise api_failed("RenderWithQuickExport", f"Resolve did not start {preset}")
        if wait:
            read = project.GetRenderWithQuickExportStatus
            done = poll_status(read, kind, timeout=timeout, interval=interval, err=sys.stderr)
            plan.extra["percent"] = done.percent

    def verify() -> dict[str, object]:
        status = str((project.GetRenderWithQuickExportStatus() or {}).get("JobStatus") or "")
        if not wait and status not in QUICK_EXPORT.idle and status not in QUICK_EXPORT.failed:
            status = "started"
        return {"status": status}

    return run_mutation(inv, session, plan, apply, verify)


def _one(
    name: str,
    summary: str,
    run: Callable[[Invocation], Result],
    methods: tuple[str, ...],
    **kwargs: Any,
) -> CommandSpec:
    kwargs.setdefault("flags", MUTATION_FLAGS)
    return CommandSpec(name, summary, run, reads_only=False, methods=methods, **kwargs)


SUBCOMMANDS: dict[str, CommandSpec] = {
    "save": _one(
        "save",
        "Save the current render settings as a new preset",
        run_save,
        (LIST, "Project.SaveAsNewRenderPreset"),
        positionals=(NAME,),
        examples=(
            "davinci-axi render preset save 'Review 720p'",
            "davinci-axi render preset save mine --force",
        ),
    ),
    "update": _one(
        "update",
        "Overwrite a preset with the current render settings (irreversible)",
        run_update,
        (LIST, "Project.UpdateRenderPreset"),
        positionals=(NAME,),
        examples=(
            "davinci-axi render preset update mine",
            "davinci-axi render preset update mine --force",
        ),
    ),
    "delete": _one(
        "delete",
        "Delete a render preset (irreversible)",
        run_delete,
        (LIST, "Project.DeleteRenderPreset"),
        positionals=(NAME,),
        examples=(
            "davinci-axi render preset delete mine",
            "davinci-axi render preset delete mine --force",
        ),
    ),
    "load": _one(
        "load",
        "Load a render preset into the current render settings (not read back)",
        run_load,
        (LIST, "Project.LoadRenderPreset"),
        positionals=(NAME,),
        examples=("davinci-axi render preset load 'H.264 Master' --force",),
    ),
    "import": _one(
        "import",
        "Import a render preset from a file and select it",
        run_import,
        (LIST, "Resolve.ImportRenderPreset"),
        positionals=(Positional("file", help="preset file (.xml)"),),
        examples=("davinci-axi render preset import ~/preset.xml --force",),
    ),
    "export": _one(
        "export",
        "Export a render preset to a file (writes only that file)",
        run_export,
        (LIST, "Resolve.ExportRenderPreset"),
        positionals=(NAME, Positional("file", help="output file (.xml)")),
        flags=(Flag("overwrite", "bool", False, help="replace the file if it exists"),),
        examples=(
            "davinci-axi render preset export 'H.264 Master' ~/h264.xml",
            "davinci-axi render preset export mine ~/mine.xml --overwrite",
        ),
    ),
    "quick-export": _one(
        "quick-export",
        "Turn quick export on or off for a render preset",
        run_quick_export_toggle,
        methods_of(
            Project="GetRenderPresetList GetQuickExportRenderPresets "
            "SetQuickExportEnabledForRenderPreset"
        ),
        positionals=(NAME,),
        flags=(
            Flag("on", "bool", False, help="enable quick export for the preset"),
            Flag("off", "bool", False, help="disable quick export for the preset"),
            *MUTATION_FLAGS,
        ),
        examples=(
            "davinci-axi render preset quick-export 'ProRes 422 HQ' --on",
            "davinci-axi render preset quick-export YouTube --off --force",
        ),
    ),
}
PRESET_SPEC = CommandSpec(
    name="preset",
    summary="Save, update, delete, load, import, export render presets; toggle quick export",
    subcommands=SUBCOMMANDS,
    examples=(
        "davinci-axi render preset save mine --force",
        "davinci-axi render preset export mine ~/mine.xml",
    ),
    reads_only=False,
    methods=tuple(dict.fromkeys(m for spec in SUBCOMMANDS.values() for m in spec.methods)),
)
QUICK_EXPORT_SPEC = _one(
    "quick-export",
    "Render the current timeline with a quick export preset and wait for it",
    run_quick_export,
    methods_of(
        Project="GetQuickExportRenderPresets GetRenderWithQuickExportStatus RenderWithQuickExport",
        Timeline="GetName GetTrackCount GetItemListInTrack",
        TimelineItem="GetMediaPoolItem",
        MediaPoolItem="GetClipProperty GetName",
    ),
    positionals=(Positional("preset", help="a quick export preset (see `render presets`)"),),
    flags=(
        Flag("target-dir", "str", None, required=True, help="output directory"),
        Flag("name", "str", None, help="output file name (CustomName)"),
        Flag("quality", "int", None, help="bit rate limit (0 = automatic)"),
        Flag("upload", "bool", False, help="also upload, for presets that support it"),
        *DIR_FLAGS,
        *POLL_FLAGS,
        *MUTATION_FLAGS,
    ),
    examples=(
        "davinci-axi render quick-export 'H.264 Master' --target-dir ~/out --mkdir",
        "davinci-axi render quick-export YouTube --target-dir ~/out --force --no-wait",
    ),
)
PRESET_SPECS: dict[str, CommandSpec] = {"preset": PRESET_SPEC, "quick-export": QUICK_EXPORT_SPEC}
