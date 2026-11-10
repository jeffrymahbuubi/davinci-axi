"""Fake render control for U16: the render queue, presets, quick export and frame export.

Builds on `FakeProject.render_jobs/render_status/rendering/render_presets/quick_export_presets/
format_codec/render_mode`. Imports only from `davinci_axi.fake.core|media|world`, never from the
`davinci_axi.fake` package (KTD17). Each write method keeps the `_rec` injection contract: an
injected value is returned (as `bool` unless the real call returns something else), otherwise the
side effect happens. `extend()` is idempotent.

Job progress is driven by reads: a `Rendering` job moves 0 -> 50 % -> `Complete` on successive
`GetRenderJobStatus` calls, and the quick export does the same through `GetRenderWithQuickExport
Status`, so a polling loop finishes without sleeping.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from davinci_axi.fake.core import FakeProject, FakeResolve
from davinci_axi.fake.media import FakeFolder

# The keys of the .pyi RenderSettings TypedDict (the fake refuses anything else).
SETTING_KEYS: tuple[str, ...] = (
    "SelectAllFrames",
    "MarkIn",
    "MarkOut",
    "TargetDir",
    "CustomName",
    "UseUniqueFilenames",
    "UniqueFilenameStyle",
    "ExportVideo",
    "ExportAudio",
    "FormatWidth",
    "FormatHeight",
    "FrameRate",
    "PixelAspectRatio",
    "VideoQuality",
    "AudioFormat",
    "AudioCodec",
    "AudioBitDepth",
    "AudioSampleRate",
    "ColorSpaceTag",
    "GammaTag",
    "ExportAlpha",
    "AlphaMode",
    "EncodingProfile",
    "MultiPassEncode",
    "NetworkOptimization",
    "ClipStartFrame",
    "TimelineStartTimecode",
    "ReplaceExistingFilesInPlace",
    "ExportSubtitle",
    "SubtitleFormat",
    "UseFullExtents",
    "AddFrameHandles",
    "DataBurnIn",
)
# RenderSettings key -> RenderJobInfo key, for the settings a job echoes back.
INFO_KEYS: dict[str, str] = {
    "TargetDir": "TargetDir",
    "CustomName": "OutputFilename",
    "FormatWidth": "FormatWidth",
    "FormatHeight": "FormatHeight",
    "MarkIn": "MarkIn",
    "MarkOut": "MarkOut",
    "ExportAlpha": "ExportAlpha",
    "AlphaMode": "AlphaMode",
    "AudioBitDepth": "AudioBitDepth",
    "AudioSampleRate": "AudioSampleRate",
    "EncodingProfile": "EncodingProfile",
    "MultiPassEncode": "MultiPassEncode",
    "NetworkOptimization": "NetworkOptimization",
    "ClipStartFrame": "ClipStartFrame",
    "TimelineStartTimecode": "TimelineStartTimecode",
    "ReplaceExistingFilesInPlace": "ReplaceExistingFilesInPlace",
}
PRESET_PLACEHOLDER = "fake render preset: "
STILL_PLACEHOLDER = "fake still: "
HALF = 50
FULL = 100


def pending_settings(project: FakeProject) -> dict[str, Any]:
    """The render settings set by `SetRenderSettings` and not yet queued (state on the project)."""
    return project.__dict__.setdefault("pending_settings", {})  # type: ignore[no-any-return]


def mark_offline(project: FakeProject, *names: str) -> None:
    """Build helper (not an API call): give the named pool clips `Online Status` = `Offline`."""

    def walk(folder: FakeFolder) -> None:
        for clip in folder.clips:
            if clip.name in names:
                clip.properties["Online Status"] = "Offline"
        for child in folder.subfolders:
            walk(child)

    walk(project.media_pool.root)


def add_opens_deliver(project: FakeProject, enabled: bool = True) -> None:
    """Build helper (opt-in): `AddRenderJob`, `SetRenderSettings` and `StartRendering` open Deliver.

    That is what live Resolve does (F40b/F44). Off by default so the write-call lists of older
    tests do not gain the restoring `OpenPage`. While a job renders, `GetCurrentPage` is empty and
    `OpenPage` refuses (F45c live).
    """
    project.__dict__["add_opens_deliver"] = enabled


def _page_hidden(resolve: FakeResolve) -> bool:
    project = resolve.pm.current_project
    return bool(project and project.__dict__.get("add_opens_deliver") and project.rendering)


def _get_current_page(self: FakeResolve) -> str:
    return self._ret("GetCurrentPage", "" if _page_hidden(self) else self.page)


def _open_page(self: FakeResolve, pageName: str) -> bool:
    injected, value = self._rec("OpenPage", pageName)
    if injected:
        return bool(value)
    if _page_hidden(self):
        return False
    self.page = pageName
    return True


def _set_render_settings(self: FakeProject, settings: dict[str, Any]) -> bool:
    injected, value = self._rec("SetRenderSettings", settings)
    if injected:
        return bool(value)
    if not settings or any(key not in SETTING_KEYS for key in settings):
        return False
    pending_settings(self).update(settings)
    if self.__dict__.get("add_opens_deliver"):
        self._world.resolve.page = "deliver"
    return True


def _add_render_job(self: FakeProject) -> str:
    injected, value = self._rec("AddRenderJob")
    if injected:
        return str(value) if value else ""
    pending = pending_settings(self)
    timeline = self.current_timeline
    if timeline is None or not pending.get("TargetDir"):
        return ""
    info = self.add_render_job(f"Job {len(self.render_jobs) + 1}", timeline.name)
    for key, info_key in INFO_KEYS.items():
        if key not in pending:
            continue
        shown = pending[key]
        if key == "CustomName":
            shown = f"{shown}.{self.format_codec.get('format', 'mov')}"
        info[info_key] = shown
    preset = self.__dict__.get("loaded_preset")
    if preset:
        info["PresetName"] = preset
    if self.__dict__.get("add_opens_deliver"):
        self._world.resolve.page = "deliver"
    return str(info["JobId"])


def _start_rendering(self: FakeProject, jobIds: list[str], isInteractiveMode: bool = False) -> bool:
    injected, value = self._rec("StartRendering", jobIds, isInteractiveMode)
    if injected:
        return bool(value)
    ids = list(jobIds) or [str(job["JobId"]) for job in self.render_jobs]
    if not ids or any(job_id not in self.render_status for job_id in ids):
        return False
    for job_id in ids:
        self.render_status[job_id] = {"JobStatus": "Rendering", "CompletionPercentage": 0}
    self.rendering = True
    if self.__dict__.get("add_opens_deliver"):
        self._world.resolve.page = "deliver"
    return True


def _advance(self: FakeProject, job_id: str) -> None:
    status = self.render_status.get(job_id)
    if status is None or status.get("JobStatus") != "Rendering":
        return
    if int(status.get("CompletionPercentage", 0)) < HALF:
        status["CompletionPercentage"] = HALF
        return
    self.render_status[job_id] = {
        "JobStatus": "Complete",
        "CompletionPercentage": FULL,
        "TimeTakenToRenderInMs": 1000,
    }
    if not any(s.get("JobStatus") == "Rendering" for s in self.render_status.values()):
        self.rendering = False


def _get_render_job_status(self: FakeProject, jobId: str) -> dict[str, Any]:
    injected, value = self._rec("GetRenderJobStatus", jobId)
    if injected:
        return value  # type: ignore[return-value]
    _advance(self, jobId)
    return dict(self.render_status.get(jobId, {}))


def _stop_rendering(self: FakeProject) -> None:
    injected, _ = self._rec("StopRendering")
    if injected:
        return
    for job_id, status in self.render_status.items():
        if status.get("JobStatus") == "Rendering":
            self.render_status[job_id] = {"JobStatus": "Cancelled", "CompletionPercentage": 0}
    self.rendering = False


def _delete_render_job(self: FakeProject, jobId: str) -> bool:
    injected, value = self._rec("DeleteRenderJob", jobId)
    if injected:
        return bool(value)
    if jobId not in self.render_status:
        return False
    self.render_jobs[:] = [job for job in self.render_jobs if job.get("JobId") != jobId]
    del self.render_status[jobId]
    return True


def _delete_all_render_jobs(self: FakeProject) -> bool:
    injected, value = self._rec("DeleteAllRenderJobs")
    if injected:
        return bool(value)
    self.render_jobs.clear()
    self.render_status.clear()
    self.rendering = False
    return True


def _quick_state(project: FakeProject) -> dict[str, Any]:
    return project.__dict__.setdefault("quick_status", {"JobStatus": "Inactive"})  # type: ignore[no-any-return]


def _render_with_quick_export(
    self: FakeProject, quickExportPresetName: str, presetInfo: dict[str, Any]
) -> dict[str, Any] | None:
    injected, value = self._rec("RenderWithQuickExport", quickExportPresetName, presetInfo)
    if injected:
        return value if isinstance(value, dict) else None
    if quickExportPresetName not in self.quick_export_presets:
        return None
    state = {
        "JobStatus": "Rendering",
        "CompletionPercentage": 0,
        "upload": bool((presetInfo or {}).get("EnableUpload")),
    }
    self.__dict__["quick_status"] = state
    return {k: v for k, v in state.items() if k != "upload"}


_QUICK_NEXT = {"Render Complete": "Uploading", "Uploading": "Upload Completed"}


def _get_quick_export_status(self: FakeProject) -> dict[str, Any]:
    injected, value = self._rec("GetRenderWithQuickExportStatus")
    if injected:
        return value  # type: ignore[return-value]
    state = _quick_state(self)
    status = state.get("JobStatus")
    if status == "Rendering":
        if int(state.get("CompletionPercentage", 0)) < HALF:
            state["CompletionPercentage"] = HALF
        else:
            state.update(JobStatus="Render Complete", CompletionPercentage=FULL)
    elif state.get("upload") and status in _QUICK_NEXT:
        state["JobStatus"] = _QUICK_NEXT[str(status)]
    return {k: v for k, v in state.items() if k != "upload"}


def _export_current_frame(self: FakeProject, filePath: str) -> bool:
    injected, value = self._rec("ExportCurrentFrameAsStill", filePath)
    if injected:
        return bool(value)
    if self.current_timeline is None:
        return False
    timeline = self.current_timeline
    shown = timeline.__dict__.get("viewer_tc")  # set once the playhead was moved (F30)
    at = f"@{shown}" if shown else ""
    Path(filePath).write_text(f"{STILL_PLACEHOLDER}{timeline.name}{at}\n", encoding="utf-8")
    return True


def _load_render_preset(self: FakeProject, presetName: str) -> bool:
    injected, value = self._rec("LoadRenderPreset", presetName)
    if injected:
        return bool(value)
    if presetName not in self.render_presets:
        return False
    self.__dict__["loaded_preset"] = presetName
    return True


def _save_as_new_render_preset(self: FakeProject, presetName: str) -> bool:
    injected, value = self._rec("SaveAsNewRenderPreset", presetName)
    if injected:
        return bool(value)
    if not presetName or presetName in self.render_presets:
        return False
    self.render_presets.append(presetName)
    return True


def _update_render_preset(self: FakeProject, presetName: str) -> bool:
    injected, value = self._rec("UpdateRenderPreset", presetName)
    if injected:
        return bool(value)
    return presetName in self.render_presets


def _delete_render_preset(self: FakeProject, presetName: str) -> bool:
    injected, value = self._rec("DeleteRenderPreset", presetName)
    if injected:
        return bool(value)
    if presetName not in self.render_presets:
        return False
    self.render_presets.remove(presetName)
    if presetName in self.quick_export_presets:
        self.quick_export_presets.remove(presetName)
    return True


def _set_quick_export_enabled(self: FakeProject, presetName: str, isEnabled: bool) -> bool:
    injected, value = self._rec("SetQuickExportEnabledForRenderPreset", presetName, isEnabled)
    if injected:
        return bool(value)
    if presetName not in self.render_presets:
        return False
    enabled = presetName in self.quick_export_presets
    if isEnabled and not enabled:
        self.quick_export_presets.append(presetName)
    elif not isEnabled and enabled:
        self.quick_export_presets.remove(presetName)
    return True


def _set_format_and_codec(self: FakeProject, format: str, codec: str) -> bool:
    injected, value = self._rec("SetCurrentRenderFormatAndCodec", format, codec)
    if injected:
        return bool(value)
    if format not in self.render_formats.values():
        return False
    if codec not in self.render_codecs.get(format, {}).values():
        return False
    self.format_codec = {"format": format, "codec": codec}
    return True


def _set_render_mode(self: FakeProject, renderMode: int) -> bool:
    injected, value = self._rec("SetCurrentRenderMode", renderMode)
    if injected:
        return bool(value)
    if renderMode not in (0, 1):
        return False
    self.render_mode = renderMode
    return True


def _import_render_preset(self: FakeResolve, presetPath: str) -> bool:
    injected, value = self._rec("ImportRenderPreset", presetPath)
    if injected:
        return bool(value)
    project = self.pm.current_project
    try:
        first = Path(presetPath).read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return False
    if project is None or not first.startswith(PRESET_PLACEHOLDER):
        return False
    name = first[len(PRESET_PLACEHOLDER) :]
    if name and name not in project.render_presets:
        project.render_presets.append(name)
    project.__dict__["loaded_preset"] = name
    return True


def _export_render_preset(self: FakeResolve, presetName: str, exportPath: str) -> bool:
    injected, value = self._rec("ExportRenderPreset", presetName, exportPath)
    if injected:
        return bool(value)
    project = self.pm.current_project
    if project is None or presetName not in project.render_presets:
        return False
    Path(exportPath).write_text(f"{PRESET_PLACEHOLDER}{presetName}\n", encoding="utf-8")
    return True


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeProject, "SetRenderSettings"): _set_render_settings,
    (FakeProject, "AddRenderJob"): _add_render_job,
    (FakeProject, "StartRendering"): _start_rendering,
    (FakeProject, "GetRenderJobStatus"): _get_render_job_status,
    (FakeProject, "StopRendering"): _stop_rendering,
    (FakeProject, "DeleteRenderJob"): _delete_render_job,
    (FakeProject, "DeleteAllRenderJobs"): _delete_all_render_jobs,
    (FakeProject, "RenderWithQuickExport"): _render_with_quick_export,
    (FakeProject, "GetRenderWithQuickExportStatus"): _get_quick_export_status,
    (FakeProject, "ExportCurrentFrameAsStill"): _export_current_frame,
    (FakeProject, "LoadRenderPreset"): _load_render_preset,
    (FakeProject, "SaveAsNewRenderPreset"): _save_as_new_render_preset,
    (FakeProject, "UpdateRenderPreset"): _update_render_preset,
    (FakeProject, "DeleteRenderPreset"): _delete_render_preset,
    (FakeProject, "SetQuickExportEnabledForRenderPreset"): _set_quick_export_enabled,
    (FakeProject, "SetCurrentRenderFormatAndCodec"): _set_format_and_codec,
    (FakeProject, "SetCurrentRenderMode"): _set_render_mode,
    (FakeResolve, "GetCurrentPage"): _get_current_page,
    (FakeResolve, "OpenPage"): _open_page,
    (FakeResolve, "ImportRenderPreset"): _import_render_preset,
    (FakeResolve, "ExportRenderPreset"): _export_render_preset,
}


def extend() -> None:
    """Add the render queue, preset, quick export and frame export methods."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)


__all__ = ["INFO_KEYS", "PRESET_PLACEHOLDER", "SETTING_KEYS", "extend", "mark_offline"]
