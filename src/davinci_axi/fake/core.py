"""Fake Resolve app, project manager and project (the shared core of the fake world)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from davinci_axi.fake.media import FakeMediaPool
from davinci_axi.fake.timeline import FakeTimeline
from davinci_axi.fake.world import FakeBase, FakeWorld

_ENUMS = {
    "EXPORT_": (
        "AAF DRT EDL FCP_7_XML FCPXML_1_8 FCPXML_1_9 FCPXML_1_10 HDR_10_PROFILE_A HDR_10_PROFILE_B "
        "TEXT_CSV TEXT_TAB DOLBY_VISION_VER_2_9 DOLBY_VISION_VER_4_0 DOLBY_VISION_VER_5_1 OTIO ALE "
        "ALE_CDL NONE AAF_NEW AAF_EXISTING CDL SDL MISSING_CLIPS"
    ),
    "KEYFRAME_MODE_": "ALL COLOR SIZING",
    "CACHE_": "AUTO_ENABLED DISABLED ENABLED",
    "MARKER_": (
        "NONE BLUE CYAN GREEN YELLOW RED PINK PURPLE FUCHSIA ROSE LAVENDER SKY MINT LEMON SAND "
        "COCOA CREAM"
    ),
}


class FakeResolve(FakeBase):
    CLASS = "Resolve"

    def __init__(self, world: FakeWorld) -> None:
        super().__init__(world)
        self.version: list[Any] = [21, 1, 1, 10010, ""]
        self.version_string = "21.1.1.10010"
        self.product = "DaVinci Resolve Studio"
        self.studio = True
        self.page = "edit"
        self.pm = FakeProjectManager(world)
        self.layout_presets = ["Default"]
        self.burn_in_presets = ["Source Timecode"]
        self.keyboard_presets = ["DaVinci Resolve", "Premiere Pro"]
        self.keyboard_preset = "DaVinci Resolve"
        self.keyframe_mode = 0
        self.fairlight_presets = ["Dialogue"]
        self.user_presets = ["Default"]
        number = 0.0
        for prefix, names in _ENUMS.items():
            for name in names.split():
                number += 1.0
                setattr(self, prefix + name, number)

    def GetProjectManager(self) -> FakeProjectManager:
        return self._ret("GetProjectManager", self.pm)

    def GetCurrentProject(self) -> FakeProject | None:
        return self._ret("GetCurrentProject", self.pm.current_project)

    def GetCurrentTimeline(self) -> FakeTimeline | None:
        project = self.pm.current_project
        return self._ret("GetCurrentTimeline", project.current_timeline if project else None)

    def GetMediaPool(self) -> FakeMediaPool | None:
        project = self.pm.current_project
        return self._ret("GetMediaPool", project.media_pool if project else None)

    def OpenPage(self, pageName: str) -> bool:
        injected, value = self._rec("OpenPage", pageName)
        if injected:
            return bool(value)
        self.page = pageName
        return True

    def GetCurrentPage(self) -> str:
        return self._ret("GetCurrentPage", self.page)

    def GetVersion(self) -> list[Any]:
        return self._ret("GetVersion", list(self.version))

    def GetVersionString(self) -> str:
        return self._ret("GetVersionString", self.version_string)

    def GetProductName(self) -> str:
        return self._ret("GetProductName", self.product)

    def IsStudio(self) -> bool:
        return self._ret("IsStudio", self.studio)

    def GetLayoutPresetList(self) -> list[str]:
        return self._ret("GetLayoutPresetList", list(self.layout_presets))

    def GetBurnInPresetList(self) -> list[str]:
        return self._ret("GetBurnInPresetList", list(self.burn_in_presets))

    def GetKeyboardPresetList(self) -> list[str]:
        return self._ret("GetKeyboardPresetList", list(self.keyboard_presets))

    def GetCurrentKeyboardPreset(self) -> str:
        return self._ret("GetCurrentKeyboardPreset", self.keyboard_preset)

    def GetKeyframeMode(self) -> int:
        return self._ret("GetKeyframeMode", self.keyframe_mode)

    def GetFairlightPresets(self) -> list[str]:
        return self._ret("GetFairlightPresets", list(self.fairlight_presets))

    def GetUserPreferencesPresetList(self) -> list[str]:
        return self._ret("GetUserPreferencesPresetList", list(self.user_presets))


@dataclass
class FakeProjectFolder:
    """A project-manager folder: projects by name plus child folders."""

    name: str
    projects: dict[str, FakeProject] = field(default_factory=dict)
    children: dict[str, FakeProjectFolder] = field(default_factory=dict)


class FakeProjectManager(FakeBase):
    CLASS = "ProjectManager"

    def __init__(self, world: FakeWorld) -> None:
        super().__init__(world)
        self.root = FakeProjectFolder("Root")
        self.path: list[str] = []
        self.current_project: FakeProject | None = None
        self.database: dict[str, Any] = {"DbType": "Disk", "DbName": "Local Database"}

    @property
    def folder(self) -> FakeProjectFolder:
        node = self.root
        for part in self.path:
            node = node.children[part]
        return node

    def add_project(self, name: str, *, current: bool = False, modified: str = "") -> FakeProject:
        """Build helper (not an API call): add a project to the current folder."""
        project = FakeProject(self._world, name, modified=modified)
        self.folder.projects[name] = project
        if current:
            self.current_project = project
        return project

    def add_folder(self, name: str) -> FakeProjectFolder:
        """Build helper (not an API call): add a child folder to the current folder."""
        child = FakeProjectFolder(name)
        self.folder.children[name] = child
        return child

    def GetCurrentProject(self) -> FakeProject | None:
        return self._ret("GetCurrentProject", self.current_project)

    def GetProjectListInCurrentFolder(self) -> list[str]:
        return self._ret("GetProjectListInCurrentFolder", list(self.folder.projects))

    def GetFolderListInCurrentFolder(self) -> list[str]:
        return self._ret("GetFolderListInCurrentFolder", list(self.folder.children))

    def GetProjectLastModifiedTime(self, projectName: str) -> int:
        return self._ret("GetProjectLastModifiedTime", 1_760_000_000, projectName)

    def GetProjectAttributesInCurrentFolder(self) -> dict[str, dict[str, Any]]:
        attrs = {n: dict(p.attributes) for n, p in self.folder.projects.items()}
        return self._ret("GetProjectAttributesInCurrentFolder", attrs)

    def GetCurrentFolder(self) -> str:
        return self._ret("GetCurrentFolder", self.folder.name)

    def GetCurrentDatabase(self) -> dict[str, Any]:
        return self._ret("GetCurrentDatabase", dict(self.database))

    def GetDatabaseList(self) -> list[dict[str, Any]]:
        return self._ret("GetDatabaseList", [dict(self.database)])

    def LoadProject(self, projectName: str) -> FakeProject | None:
        injected, value = self._rec("LoadProject", projectName)
        if injected:
            return value if isinstance(value, FakeProject) else None
        project = self.folder.projects.get(projectName)
        if project is not None:
            self.current_project = project
        return project

    def CreateProject(
        self, projectName: str, mediaLocationPath: str | None = None
    ) -> FakeProject | None:
        injected, value = self._rec("CreateProject", projectName, mediaLocationPath)
        if injected:
            return value if isinstance(value, FakeProject) else None
        if projectName in self.folder.projects:
            return None
        return self.add_project(projectName, current=True)

    def SaveProject(self) -> bool:
        return self._ret("SaveProject", self.current_project is not None)

    def ExportProject(
        self, projectName: str, filePath: str, withStillsAndLUTs: bool = True
    ) -> bool:
        injected, value = self._rec("ExportProject", projectName, filePath, withStillsAndLUTs)
        if injected:
            return bool(value)
        if projectName not in self.folder.projects:
            return False
        with open(filePath, "w", encoding="utf-8") as handle:
            handle.write(f"fake drp: {projectName}\n")
        return True


class FakeProject(FakeBase):
    CLASS = "Project"

    def __init__(self, world: FakeWorld, name: str, *, modified: str = "") -> None:
        super().__init__(world)
        self.name = name
        self.uid = world.new_id("project")
        self.settings: dict[str, Any] = {
            "timelineFrameRate": 24.0,
            "timelineResolutionWidth": "1920",
            "timelineResolutionHeight": "1080",
            "colorScienceMode": "davinciYRGB",
        }
        self.attributes: dict[str, Any] = {
            "lastModifiedDate": modified or "2026-10-27T09:30:00+00:00",
            "creationDate": "2026-10-01T09:30:00+00:00",
            "notes": "",
            "liveCollaborationMode": "single_user",
        }
        self.timelines: list[FakeTimeline] = []
        self.current_timeline: FakeTimeline | None = None
        self.media_pool = FakeMediaPool(world)
        self.render_presets: list[str] = []
        self.quick_export_presets: list[str] = []
        self.render_jobs: list[dict[str, Any]] = []
        self.render_status: dict[str, dict[str, Any]] = {}
        self.rendering = False
        self.render_formats: dict[str, str] = {"QuickTime": "mov", "MP4": "mp4"}
        self.render_codecs: dict[str, dict[str, str]] = {
            "mov": {"H.264": "H264", "ProRes 422 HQ": "ProRes422HQ"},
            "mp4": {"H.264": "H264", "H.265": "H265"},
        }
        self.format_codec: dict[str, str] = {"format": "mov", "codec": "H264"}
        self.render_mode = 1
        self.settings_presets: list[dict[str, Any]] = []

    def add_timeline(self, name: str, *, current: bool = False, **kwargs: Any) -> FakeTimeline:
        """Build helper (not an API call): add a timeline; the first one becomes current."""
        kwargs.setdefault("frame_rate", self.settings["timelineFrameRate"])
        timeline = FakeTimeline(self._world, name, **kwargs)
        self.timelines.append(timeline)
        if current or self.current_timeline is None:
            self.current_timeline = timeline
        return timeline

    def add_render_job(
        self, name: str, timeline: str, status: str = "Ready", percent: int = 0
    ) -> dict[str, Any]:
        """Build helper (not an API call): queue a render job with the given status."""
        job_id = self._world.new_id("job")
        info: dict[str, Any] = {
            "JobId": job_id,
            "RenderJobName": name,
            "TimelineName": timeline,
            "TargetDir": "/tmp/davinci-axi-renders",
            "OutputFilename": f"{name}.mov",
            "IsExportVideo": True,
            "IsExportAudio": True,
            "FormatWidth": 1920,
            "FormatHeight": 1080,
            "FrameRate": "24",
            "VideoFormat": "mov",
            "VideoCodec": "H264",
        }
        self.render_jobs.append(info)
        self.render_status[job_id] = {"JobStatus": status, "CompletionPercentage": percent}
        return info

    def GetMediaPool(self) -> FakeMediaPool:
        return self._ret("GetMediaPool", self.media_pool)

    def GetCurrentTimeline(self) -> FakeTimeline | None:
        return self._ret("GetCurrentTimeline", self.current_timeline)

    def SetCurrentTimeline(self, timeline: FakeTimeline) -> bool:
        injected, value = self._rec("SetCurrentTimeline", timeline)
        if injected:
            return bool(value)
        self.current_timeline = timeline
        return True

    def GetTimelineCount(self) -> int:
        return self._ret("GetTimelineCount", len(self.timelines))

    def GetTimelineByIndex(self, idx: int) -> FakeTimeline | None:
        timeline = self.timelines[idx - 1] if 1 <= idx <= len(self.timelines) else None
        return self._ret("GetTimelineByIndex", timeline, idx)

    def GetName(self) -> str:
        return self._ret("GetName", self.name)

    def GetUniqueId(self) -> str:
        return self._ret("GetUniqueId", self.uid)

    def GetSettings(self) -> dict[str, Any]:
        return self._ret("GetSettings", dict(self.settings))

    def GetProjectSettingsPresetList(self) -> list[dict[str, Any]]:
        return self._ret("GetProjectSettingsPresetList", list(self.settings_presets))

    def GetRenderJobList(self) -> list[dict[str, Any]]:
        return self._ret("GetRenderJobList", [dict(j) for j in self.render_jobs])

    def GetRenderJobStatus(self, jobId: str) -> dict[str, Any]:
        return self._ret("GetRenderJobStatus", dict(self.render_status.get(jobId, {})), jobId)

    def GetRenderPresetList(self) -> list[str]:
        return self._ret("GetRenderPresetList", list(self.render_presets))

    def GetQuickExportRenderPresets(self) -> list[str]:
        return self._ret("GetQuickExportRenderPresets", list(self.quick_export_presets))

    def IsRenderingInProgress(self) -> bool:
        return self._ret("IsRenderingInProgress", self.rendering)

    def GetRenderResolutions(
        self, format: str | None = None, codec: str | None = None
    ) -> list[dict[str, Any]]:
        return self._ret("GetRenderResolutions", [{"Width": 1920, "Height": 1080}], format, codec)

    def GetRenderFormats(self) -> dict[str, Any]:
        return self._ret("GetRenderFormats", dict(self.render_formats))

    def GetAudioRenderFormats(self) -> dict[str, Any]:
        return self._ret("GetAudioRenderFormats", {"Wave": "wav"})

    def GetRenderCodecs(self, renderFormatFileExtension: str) -> dict[str, Any]:
        codecs = self.render_codecs.get(renderFormatFileExtension, {})
        return self._ret("GetRenderCodecs", dict(codecs), renderFormatFileExtension)

    def GetAudioRenderCodecs(self, audioRenderFormatFileExtension: str) -> dict[str, Any]:
        return self._ret(
            "GetAudioRenderCodecs", {"Linear PCM": "LinearPCM"}, audioRenderFormatFileExtension
        )

    def GetCurrentRenderFormatAndCodec(self) -> dict[str, str]:
        return self._ret("GetCurrentRenderFormatAndCodec", dict(self.format_codec))

    def GetCurrentRenderMode(self) -> int:
        return self._ret("GetCurrentRenderMode", self.render_mode)

    def GetColorGroupsList(self) -> list[Any]:
        return self._ret("GetColorGroupsList", [])
