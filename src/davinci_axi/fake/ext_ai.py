"""Fake Studio AI analysis methods for U20 (KTD17).

Each analysis job (slate, reframe, scene cuts, subtitles) has a status dict on the project that
advances on reads (0 -> 50 % -> `Complete`) so a polling loop finishes without sleeping; the
job's effect (markers, a subtitle track, a recorded flag) lands when it completes. A project flag
`ai_extras` (default True; `set_ai_extras` flips it) makes every AI call return False or None, as
a Free edition or a missing AI Extras install does. Imports only from `davinci_axi.fake.core|
media|timeline`, never from the `davinci_axi.fake` package, because the package is still
mid-import when `EXTENSIONS` run. Each write keeps the `_rec` injection contract: an injected
value is returned (as `bool`, or the clip for `RemoveMotionBlur`), otherwise the side effect
happens. `extend()` is idempotent. The enum constants of KTD24 are set on the `FakeResolve`
class, so every instance (and `getattr(session.resolve, ...)`) sees them.

Test helpers (not API calls): `set_ai_extras`, `ai_ops`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from davinci_axi.fake.core import FakeProject, FakeResolve
from davinci_axi.fake.media import FakeFolder, FakeMediaPool, FakeMediaPoolItem
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem
from davinci_axi.fake.world import FakeWorld


def _words(text: str) -> list[str]:
    return text.split()


_ENUMS = {
    "AUTO_CAPTION_": (
        "AUTO MANDARIN_SIMPLIFIED DUTCH ENGLISH FINNISH FRENCH GERMAN HINDI INDONESIAN ITALIAN "
        "JAPANESE KOREAN MALAY NORWEGIAN POLISH PORTUGUESE ROMANIAN RUSSIAN SPANISH SWEDISH "
        "TURKISH VIETNAMESE TAMIL THAI DANISH MANDARIN_TRADITIONAL SUBTITLE_DEFAULT TELETEXT "
        "NETFLIX LINE_SINGLE LINE_DOUBLE"
    ),
    "MULTICAM_": (
        "ANGLE_SYNC_IN ANGLE_SYNC_OUT ANGLE_SYNC_TIMECODE ANGLE_SYNC_AUDIO ANGLE_SYNC_MARKER "
        "ANGLE_NAME_SEQUENTIAL ANGLE_NAME_ANGLE ANGLE_NAME_CAMERA ANGLE_NAME_CLIP "
        "ANGLE_NAME_FILE DETECT_BY_CAMERA_NUMBER DETECT_BY_ANGLE DETECT_BY_REEL_NUMBER "
        "DETECT_BY_REEL_NAME DETECT_BY_ROLL_CARD DETECT_NONE AUDIO_ADAPTIVE AUDIO_SOURCE "
        "AUDIO_REFERENCE AUDIO_ALL"
    ),
    "SMART_SWITCH_": (
        "QUALITY_FASTER QUALITY_BETTER WIDE_ANGLE_FREQ_LOW WIDE_ANGLE_FREQ_MEDIUM "
        "WIDE_ANGLE_FREQ_HIGH ANALYSIS_MODE_NONE ANALYSIS_MODE_DETECT_WIDE_ANGLE "
        "ANALYSIS_MODE_AUDIO_ONLY"
    ),
    "FLATTEN_MULTICAM_": "COPY_GRADE RETAIN_GRADE_FROM_ANGLE",
}
CAPTION_KEYS = ("language", "captionPreset", "charsPerLine", "lineBreak", "gap")
DEBLUR_KEYS = _words(
    "FileName Format Codec EncodingProfile Encoder UseExtremeMode UseMarkInMarkOut "
    "RenderAtSourceRes UseMoreGpuMemory"
)
MULTICAM_KEYS = _words(
    "name startTimecode frameRate angleSyncMode channelConfig multicamAudioMode angleNameMode "
    "splitAtGaps useFullClipExtents createBinForSourceClips detectSameCameraClipsMode"
)
SMART_KEYS = _words(
    "minEditDuration editChangeDelay isAutoDetectWideAngle analysisMode wideAngleID "
    "wideAngleFrequency isUseWideAngleForIntroOutro isUseWideAngleForSilence switchOnVideoOnly "
    "quality"
)
MASK_MODES = ("F", "B", "BI")
CUT_FRAMES = (60, 120)
HALF = 50
FULL = 100
FIRST_ENUM = 4000.0


def _state(owner: Any, key: str, default: Any) -> Any:
    return owner.__dict__.setdefault(key, default)


def _project(owner: Any) -> FakeProject | None:
    return owner._world.resolve.pm.current_project  # type: ignore[no-any-return]


def _available(owner: Any) -> bool:
    project = _project(owner)
    return project is not None and bool(getattr(project, "ai_extras", True))


def set_ai_extras(world: FakeWorld, available: bool) -> None:
    """Test helper: the current project has (or lacks) Studio with AI Extras."""
    project = world.resolve.pm.current_project
    assert project is not None
    project.__dict__["ai_extras"] = available


def ai_ops(owner: Any) -> list[Any]:
    """Test helper: the AI operations recorded on a clip, timeline item or timeline."""
    return list(_state(owner, "ai_ops", []))


def _record(owner: Any, op: str, arg: Any = None) -> None:
    _state(owner, "ai_ops", []).append((op, arg))


def _tree_folders(folder: FakeFolder) -> list[FakeFolder]:
    folders = [folder]
    for child in folder.subfolders:
        folders.extend(_tree_folders(child))
    return folders


# --- jobs ----------------------------------------------------------------------------------


def _jobs(project: FakeProject) -> dict[str, dict[str, Any]]:
    return _state(project, "ai_jobs", {})  # type: ignore[no-any-return]


def _begin(owner: Any, key: str, effect: Callable[[], None]) -> None:
    project = _project(owner)
    assert project is not None
    job = _jobs(project).setdefault(key, {"status": {"JobStatus": "Inactive"}, "effects": []})
    job["effects"].append(effect)
    job["status"] = {"JobStatus": "Analyzing", "CompletionPercentage": 0}
    job["lag"] = int(owner._world.__dict__.get("start_lag", 0))


def set_start_lag(world: FakeWorld, reads: int) -> None:
    """Make the first `reads` status reads after a job start say `Inactive` (live B3 lag).

    `ext_audio` reads the same key for transcription. Default 0: jobs start at once.
    """
    world.__dict__["start_lag"] = reads


def _advance(project: FakeProject, key: str) -> dict[str, Any]:
    job = _jobs(project).setdefault(key, {"status": {"JobStatus": "Inactive"}, "effects": []})
    if int(job.get("lag", 0)) > 0:
        job["lag"] -= 1
        return {"JobStatus": "Inactive"}
    status = job["status"]
    if status.get("JobStatus") == "Analyzing":
        if int(status.get("CompletionPercentage", 0)) < HALF:
            status["CompletionPercentage"] = HALF
        else:
            for effect in job["effects"]:
                effect()
            job["effects"].clear()
            job["status"] = status = {"JobStatus": "Complete", "CompletionPercentage": FULL}
    return dict(status)


def _status_getter(key: str, method: str) -> Callable[[FakeProject], dict[str, Any]]:
    def getter(self: FakeProject) -> dict[str, Any]:
        injected, value = self._rec(method)
        if injected:
            return value  # type: ignore[return-value]
        return _advance(self, key)

    getter.__name__ = method
    return getter


# --- clips and folders ------------------------------------------------------------------------


def _intellisearch(self: Any, identifyFaces: bool, isBetterMode: bool) -> bool:
    injected, value = self._rec("AnalyzeForIntellisearch", identifyFaces, isBetterMode)
    if injected:
        return bool(value)
    if not _available(self):
        return False
    clips = (
        [c for f in _tree_folders(self) for c in f.clips]
        if isinstance(self, FakeFolder)
        else [self]
    )
    for clip in clips:
        clip.__dict__["intellisearch"] = (identifyFaces, isBetterMode)
    return True


def _slate(self: Any, markerColor: Any) -> bool:
    injected, value = self._rec("AnalyzeForSlate", markerColor)
    if injected:
        return bool(value)
    if not _available(self):
        return False
    clips = (
        [c for f in _tree_folders(self) for c in f.clips]
        if isinstance(self, FakeFolder)
        else [self]
    )

    def effect() -> None:
        for clip in clips:
            clip.__dict__["slate_marker"] = markerColor

    _begin(self, "slate", effect)
    return True


def _deblur_into(
    folder: FakeFolder, clip: FakeMediaPoolItem, options: dict[str, Any]
) -> FakeMediaPoolItem:
    pattern = str(options.get("FileName", "%{Source Name} Deblur"))
    name = pattern.replace("%{Source Name}", clip.name)
    extension = str(options.get("Format", "mov"))
    created = folder.add_clip(
        name,
        f"/media/davinci-axi-test/{name}.{extension}",
        clip_type=str(clip.properties.get("Type", "Video + Audio")),
        duration=str(clip.properties.get("Duration", "00:00:10:00")),
    )
    created.__dict__["deblur_options"] = dict(options)
    return created


def _deblur_ok(self: Any, options: Any) -> bool:
    return _available(self) and all(key in DEBLUR_KEYS for key in dict(options or {}))


def _clip_deblur(self: FakeMediaPoolItem, deblurOption: Any) -> FakeMediaPoolItem | None:
    injected, value = self._rec("RemoveMotionBlur", deblurOption)
    if injected:
        return value if isinstance(value, FakeMediaPoolItem) else None
    project = _project(self)
    if project is None or not _deblur_ok(self, deblurOption):
        return None
    for folder in _tree_folders(project.media_pool.root):
        if self in folder.clips:
            return _deblur_into(folder, self, dict(deblurOption or {}))
    return None


def _folder_deblur(self: FakeFolder, deblurOption: Any = None) -> list[list[Any]] | None:
    injected, value = self._rec("RemoveMotionBlur", deblurOption)
    if injected:
        return value if isinstance(value, list) else None
    if not _deblur_ok(self, deblurOption):
        return None
    pairs: list[list[Any]] = []
    for folder in _tree_folders(self):
        for clip in list(folder.clips):
            pairs.append([clip, _deblur_into(folder, clip, dict(deblurOption or {}))])
    return pairs


def _create_multicam(
    self: FakeMediaPool, clips: list[Any], multicamOptions: Any
) -> list[FakeMediaPoolItem]:
    injected, value = self._rec("CreateMulticamClip", clips, multicamOptions)
    if injected:
        return value if isinstance(value, list) else []
    options = dict(multicamOptions or {})
    if not clips or any(key not in MULTICAM_KEYS for key in options):
        return []
    count = _state(self, "multicam_count", [0])
    count[0] += 1
    name = str(options.get("name") or f"Multicam {count[0]}")
    created = self.current.add_clip(
        name, "", duration=str(clips[0].properties.get("Duration", "00:00:10:00"))
    )
    created.__dict__["multicam"] = {"angles": [c.name for c in clips], "options": options}
    return [created]


# --- timeline and items -----------------------------------------------------------------------


def _scene_cuts(self: FakeTimeline) -> bool:
    injected, value = self._rec("DetectSceneCuts")
    if injected:
        return bool(value)
    if not _available(self):
        return False

    def effect() -> None:
        for frame in CUT_FRAMES:
            self.markers[frame] = {
                "color": "Blue",
                "duration": 1,
                "note": "scene cut",
                "name": "Cut",
                "customData": "",
            }

    _begin(self, "scene-cuts", effect)
    return True


def _subtitles(self: FakeTimeline, autoCaptionSettings: Any = None) -> bool:
    injected, value = self._rec("CreateSubtitlesFromAudio", autoCaptionSettings)
    if injected:
        return bool(value)
    settings = dict(autoCaptionSettings or {})
    chars, gap = settings.get("charsPerLine", 42), settings.get("gap", 0)
    if not _available(self) or any(key not in CAPTION_KEYS for key in settings):
        return False
    if not (1 <= chars <= 60 and 0 <= gap <= 10):
        return False

    def effect() -> None:
        self.add_track("subtitle")
        self.__dict__["subtitle_settings"] = settings

    _begin(self, "subtitles", effect)
    return True


def _item_op(method: str, op: str) -> Callable[..., bool]:
    def call(self: FakeTimelineItem) -> bool:
        injected, value = self._rec(method)
        if injected:
            return bool(value)
        if not _available(self):
            return False
        _record(self, op)
        return True

    call.__name__ = method
    return call


def _create_mask(self: FakeTimelineItem, mode: str) -> bool:
    injected, value = self._rec("CreateMagicMask", mode)
    if injected:
        return bool(value)
    if not _available(self) or mode not in MASK_MODES:
        return False
    _record(self, "magic-mask", mode)
    return True


def _reframe(self: FakeTimelineItem) -> bool:
    injected, value = self._rec("SmartReframe")
    if injected:
        return bool(value)
    if not _available(self):
        return False
    _begin(self, "reframe", lambda: _record(self, "reframe"))
    return True


def _smart_switch(self: FakeTimelineItem, smartSwitchSettings: Any) -> bool:
    injected, value = self._rec("PerformMulticamSmartSwitch", smartSwitchSettings)
    if injected:
        return bool(value)
    settings = dict(smartSwitchSettings or {})
    if not _available(self) or any(key not in SMART_KEYS for key in settings):
        return False
    _record(self, "smart-switch", settings)
    return True


def _flatten(self: FakeTimelineItem, gradeOption: Any) -> bool:
    injected, value = self._rec("FlattenMulticam", gradeOption)
    if injected:
        return bool(value)
    if not _available(self):
        return False
    _record(self, "flatten", gradeOption)
    return True


def _reset_intellisearch(self: FakeProject) -> bool:
    injected, value = self._rec("ResetIntellisearchAnalysis")
    if injected:
        return bool(value)
    if not bool(getattr(self, "ai_extras", True)):
        return False
    for folder in _tree_folders(self.media_pool.root):
        for clip in folder.clips:
            clip.__dict__.pop("intellisearch", None)
    _record(self, "intellisearch-reset")
    return True


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeProject, "ResetIntellisearchAnalysis"): _reset_intellisearch,
    (FakeProject, "GetAnalyzeForSlateStatus"): _status_getter("slate", "GetAnalyzeForSlateStatus"),
    (FakeProject, "GetSmartReframeStatus"): _status_getter("reframe", "GetSmartReframeStatus"),
    (FakeProject, "GetDetectSceneCutsStatus"): _status_getter(
        "scene-cuts", "GetDetectSceneCutsStatus"
    ),
    (FakeProject, "GetCreateSubtitlesFromAudioStatus"): _status_getter(
        "subtitles", "GetCreateSubtitlesFromAudioStatus"
    ),
    (FakeMediaPoolItem, "AnalyzeForIntellisearch"): _intellisearch,
    (FakeMediaPoolItem, "AnalyzeForSlate"): _slate,
    (FakeMediaPoolItem, "RemoveMotionBlur"): _clip_deblur,
    (FakeFolder, "AnalyzeForIntellisearch"): _intellisearch,
    (FakeFolder, "AnalyzeForSlate"): _slate,
    (FakeFolder, "RemoveMotionBlur"): _folder_deblur,
    (FakeMediaPool, "CreateMulticamClip"): _create_multicam,
    (FakeTimeline, "DetectSceneCuts"): _scene_cuts,
    (FakeTimeline, "CreateSubtitlesFromAudio"): _subtitles,
    (FakeTimelineItem, "CreateMagicMask"): _create_mask,
    (FakeTimelineItem, "RegenerateMagicMask"): _item_op(
        "RegenerateMagicMask", "magic-mask-regenerate"
    ),
    (FakeTimelineItem, "Stabilize"): _item_op("Stabilize", "stabilize"),
    (FakeTimelineItem, "SmartReframe"): _reframe,
    (FakeTimelineItem, "PerformMulticamSmartSwitch"): _smart_switch,
    (FakeTimelineItem, "FlattenMulticam"): _flatten,
}


def extend() -> None:
    """Add the AI methods and the KTD24 enum constants (idempotent)."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)
    number = FIRST_ENUM
    for prefix, names in _ENUMS.items():
        for name in names.split():
            number += 1.0
            setattr(FakeResolve, prefix + name, number)


__all__ = ["ai_ops", "extend", "set_ai_extras", "set_start_lag"]
