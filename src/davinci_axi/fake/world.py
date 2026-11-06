"""Shared fake plumbing: the world, the call recorder and the write-method ledger (KTD9)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, TypeVar, cast

if TYPE_CHECKING:
    from davinci_axi.fake.core import FakeResolve

T = TypeVar("T")

# Every method the API tables tag W (changes project or app state), U (changes UI or session
# state) or F (writes or reads files outside the project), per class, from the tables in
# docs/context/resolve-api-*.md. The fake models a subset; the ledger covers all of them so
# later phases need no edit here.
_WRITES: dict[str, str] = {
    "Resolve": (
        "OpenPage SetHighPriority LoadLayoutPreset UpdateLayoutPreset ExportLayoutPreset "
        "DeleteLayoutPreset SaveLayoutPreset ImportLayoutPreset Quit ImportRenderPreset "
        "ExportRenderPreset DeleteBurnInPreset ImportBurnInPreset ExportBurnInPreset "
        "LoadKeyboardPreset DeleteKeyboardPreset ImportKeyboardPreset ExportKeyboardPreset "
        "SetKeyframeMode DisableBackgroundTasksForCurrentResolveSession ValidateDCTL "
        "EncryptDCTL LoadUserPreferencesPreset SaveUserPreferencesPreset "
        "DeleteUserPreferencesPreset ImportUserPreferencesPreset ExportUserPreferencesPreset"
    ),
    "ProjectManager": (
        "LoadProject CreateProject DeleteProject SaveProject CreateFolder GotoRootFolder "
        "GotoParentFolder OpenFolder ImportProject ExportProject ArchiveProject "
        "RestoreProject CloseProject DeleteFolder SetCurrentDatabase LoadCloudProject "
        "CreateCloudProject ImportCloudProject RestoreCloudProject"
    ),
    "Project": (
        "SetCurrentTimeline SetName SetProjectSettingsPreset DeleteProjectSettingsPreset "
        "SaveCurrentProjectSettingsAsNewPreset UpdateProjectSettingsPreset "
        "ExportProjectSettingsPreset ImportProjectSettingsPreset LoadRenderPreset "
        "SaveAsNewRenderPreset DeleteRenderPreset UpdateRenderPreset "
        "SetQuickExportEnabledForRenderPreset StartRendering StopRendering AddRenderJob "
        "DeleteRenderJob DeleteAllRenderJobs SetRenderSettings SetSettings "
        "RenderWithQuickExport SetCurrentRenderFormatAndCodec SetCurrentRenderMode "
        "RefreshLUTList InsertAudioToCurrentTrackAtPlayhead LoadBurnInPreset "
        "ExportCurrentFrameAsStill AddColorGroup DeleteColorGroup "
        "ApplyFairlightPresetToCurrentTimeline ResetIntellisearchAnalysis GenerateSpeech"
    ),
    "Graph": (
        "SetLUT SetNodeCacheMode SetNodeEnabled ApplyArriCdlLut ApplyGradeFromDRX ResetAllGrades"
    ),
    "ColorGroup": ("SetName"),
    "Gallery": (
        "SetAlbumName SetCurrentStillAlbum CreateGalleryStillAlbum CreateGalleryPowerGradeAlbum"
    ),
    "GalleryStillAlbum": ("SetLabel ImportStills ExportStills DeleteStills"),
    "MediaPool": (
        "AddSubFolder RefreshFolders SetCurrentFolder CreateTimelineFromClips "
        "AppendToTimeline CreateEmptyTimeline ImportTimelineFromFile DeleteTimelines "
        "ExportMetadata DeleteClips ImportFolderFromFile DeleteFolders MoveClips MoveFolders "
        "DeleteClipMattes RelinkClips UnlinkClips ImportMedia CreateStereoClip "
        "CreateMulticamClip AutoSyncAudio SetSelectedClip"
    ),
    "MediaPoolItem": (
        "SetName SetMetadata SetThirdPartyMetadata AddMarker DeleteMarkersByColor "
        "DeleteMarkerAtFrame DeleteMarkerByCustomData UpdateMarkerCustomData AddFlag "
        "ClearFlags SetClipColor ClearClipColor LinkFullResolutionMedia LinkProxyMedia "
        "UnlinkProxyMedia ReplaceClip ReplaceClipPreserveSubClip SetClipProperty "
        "TranscribeAudio ClearTranscription PerformAudioClassification "
        "ClearAudioClassification SetAudioMapping SetMarkInOut ClearMarkInOut "
        "MonitorGrowingFile RemoveMotionBlur AnalyzeForIntellisearch AnalyzeForSlate"
    ),
    "Folder": (
        "Export TranscribeAudio ClearTranscription PerformAudioClassification "
        "ClearAudioClassification RemoveMotionBlur AnalyzeForIntellisearch AnalyzeForSlate"
    ),
    "MediaStorage": (
        "RevealInStorage AddItemListToMediaPool AddClipMattesToMediaPool "
        "AddTimelineMattesToMediaPool StartCloneMedia SetCloneToolSettings StopCloneMedia"
    ),
    "Timeline": (
        "SetName SetCurrentTimecode AddMarker DeleteMarkersByColor DeleteMarkerAtFrame "
        "DeleteMarkerByCustomData UpdateMarkerCustomData AddTrack DeleteTrack SetTrackEnable "
        "SetTrackLock DeleteClips SetClipsLinked NormalizeAudioLevel AutoAlignClips "
        "SetTrackName DuplicateTimeline GrabStill GrabAllStills CreateCompoundClip "
        "CreateFusionClip Export SetSettings SetStartTimecode ImportIntoTimeline "
        "InsertGeneratorIntoTimeline InsertFusionGeneratorIntoTimeline "
        "InsertFusionCompositionIntoTimeline InsertOFXGeneratorIntoTimeline "
        "InsertTitleIntoTimeline InsertFusionTitleIntoTimeline CreateSubtitlesFromAudio "
        "DetectSceneCuts ConvertTimelineToStereo AnalyzeDolbyVision SetMarkInOut "
        "ClearMarkInOut SetVoiceIsolationState SetOutputBlanking"
    ),
    "TimelineItem": (
        "AddTransition SetName AddFusionComp AddMarker DeleteMarkersByColor "
        "DeleteMarkerAtFrame DeleteMarkerByCustomData UpdateMarkerCustomData SetProperties "
        "SetSpeed AddFlag ClearFlags SetClipColor ClearClipColor ImportFusionComp "
        "ExportFusionComp DeleteFusionCompByName LoadFusionCompByName RenameFusionCompByName "
        "RenameVersionByName DeleteVersionByName LoadVersionByName AddVersion SetCDL AddTake "
        "DeleteTakeByIndex SelectTakeByIndex FinalizeTake CopyGrades SetClipEnabled "
        "UpdateSidecar LoadBurnInPreset CreateMagicMask RegenerateMagicMask Stabilize "
        "SmartReframe AssignToColorGroup RemoveFromColorGroup ExportLUT "
        "SetSourceAudioChannelMapping SetColorOutputCache SetFusionOutputCache "
        "SetVoiceIsolationState ResetAllNodeColors SetOutputBlanking "
        "SetUseTimelineForOutputBlanking PerformMulticamSmartSwitch FlattenMulticam SetFades"
    ),
}

WRITE_METHODS: frozenset[str] = frozenset(
    f"{cls}.{name}" for cls, names in _WRITES.items() for name in names.split()
)

Connection = Literal["ok", "not_running", "scripting_disabled", "free", "not_installed", "hang"]
HANG_SECONDS = 3.0


@dataclass
class FakeWorld:
    """Everything a fake Resolve session holds, plus the call log and failure injection."""

    connection: Connection = "ok"
    resolve: FakeResolve = field(init=False)
    calls: list[tuple[str, str, tuple[object, ...]]] = field(default_factory=list)
    fail: dict[str, object] = field(default_factory=dict)
    hang_gate: threading.Event = field(default_factory=threading.Event)
    _ids: dict[str, int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        from davinci_axi.fake.core import FakeResolve

        self.resolve = FakeResolve(self)

    def write_calls(self) -> list[str]:
        """`Class.Method` for every logged call that is a W, U or F method."""
        names = (f"{cls}.{method}" for cls, method, _ in self.calls)
        return [name for name in names if name in WRITE_METHODS]

    def page_changes(self) -> list[str]:
        """The page names passed to every `OpenPage` call."""
        return [
            str(args[0])
            for cls, method, args in self.calls
            if (cls, method) == ("Resolve", "OpenPage") and args
        ]

    def inject(self, method: str, value: object) -> None:
        """Make `Class.Method` return `value` (an exception instance is raised instead)."""
        self.fail[method] = value

    def new_id(self, prefix: str) -> str:
        """A deterministic unique id such as `item-3`, counted per prefix within this world."""
        self._ids[prefix] = self._ids.get(prefix, 0) + 1
        return f"{prefix}-{self._ids[prefix]}"

    def calls_to(self, method: str) -> list[tuple[object, ...]]:
        """Arguments of every logged call to `Class.Method`."""
        return [args for cls, name, args in self.calls if f"{cls}.{name}" == method]


class FakeBase:
    """Base of every fake API object: records each call and honours injected returns."""

    CLASS = ""

    def __init__(self, world: FakeWorld) -> None:
        self._world = world

    def _rec(self, method: str, *args: object) -> tuple[bool, object]:
        self._world.calls.append((self.CLASS, method, args))
        key = f"{self.CLASS}.{method}"
        if key in self._world.fail:
            value = self._world.fail[key]
            if isinstance(value, BaseException):
                raise value
            return True, value
        return False, None

    def _ret(self, method: str, default: T, *args: object) -> T:
        """Log the call and return `default`, or the injected value when one is set."""
        injected, value = self._rec(method, *args)
        return cast(T, value) if injected else default
