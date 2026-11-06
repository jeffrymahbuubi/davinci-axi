"""What the render command modules share (U16): settings vocabulary, input checks, helpers.

Resolve shows blocking dialogs when a render has no location, a folder that already holds media,
or offline media (manual notes, section 3). `check_target_dir` and `refuse_offline` turn each of
them into a coded error before any write. `RenderProject` declares the render write calls that
`resolve_types_edit` does not cover (KTD5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, cast

from davinci_axi.commands.item_detail import TRACK_TYPES, walk_items
from davinci_axi.commands.timeline_tracks import context_words
from davinci_axi.errors import AxiError, not_found
from davinci_axi.mutate import coerce_like, force_cmd, parse_assignments
from davinci_axi.output import collapse_home
from davinci_axi.poll import DEFAULT_POLL_INTERVAL, DEFAULT_POLL_TIMEOUT, poll_args
from davinci_axi.resolve_types import Info, TimelineApi
from davinci_axi.resolve_types_edit import ProjectEdit
from davinci_axi.session import Session
from davinci_axi.spec import Flag, Invocation


def words(text: str) -> list[str]:
    return text.split()


def methods_of(**groups: str) -> tuple[str, ...]:
    """`methods_of(Project="A B", Timeline="C")` -> `("Project.A", "Project.B", "Timeline.C")`."""
    return tuple(f"{cls}.{name}" for cls, names in groups.items() for name in words(names))


# The RenderSettings keys of the .pyi (33), grouped by the Python type each value is read as.
_TYPE_KEYS: dict[type, str] = {
    bool: (
        "SelectAllFrames UseUniqueFilenames ExportVideo ExportAudio ExportAlpha MultiPassEncode "
        "NetworkOptimization ReplaceExistingFilesInPlace ExportSubtitle UseFullExtents"
    ),
    int: (
        "MarkIn MarkOut UniqueFilenameStyle FormatWidth FormatHeight VideoQuality AudioBitDepth "
        "AudioSampleRate AlphaMode ClipStartFrame AddFrameHandles"
    ),
    float: "FrameRate",
    str: (
        "TargetDir CustomName PixelAspectRatio AudioFormat AudioCodec ColorSpaceTag GammaTag "
        "EncodingProfile TimelineStartTimecode SubtitleFormat DataBurnIn"
    ),
}
RENDER_SETTING_TYPES: dict[str, type] = {
    key: kind for kind, keys in _TYPE_KEYS.items() for key in words(keys)
}
# The settings a queued job echoes back in RenderJobInfo, so `render add` can verify them.
JOB_INFO_KEYS: dict[str, str] = {
    **{
        key: key
        for key in words(
            "TargetDir FormatWidth FormatHeight MarkIn MarkOut ExportAlpha AlphaMode "
            "AudioBitDepth AudioSampleRate EncodingProfile MultiPassEncode NetworkOptimization "
            "ClipStartFrame TimelineStartTimecode ReplaceExistingFilesInPlace"
        )
    },
    "CustomName": "OutputFilename",
}
VIDEO_QUALITY_WORDS = ("Least", "Low", "Medium", "High", "Best")
RESERVED_SETTINGS = ("TargetDir", "CustomName")

DIR_FLAGS: tuple[Flag, ...] = (
    Flag("mkdir", "bool", False, help="create the target directory if it does not exist"),
    Flag("allow-existing", "bool", False, help="render into a directory that already holds files"),
    Flag("allow-offline", "bool", False, help="render although the timeline has offline media"),
)
DIR_BOOLS = ("mkdir", "allow-existing", "allow-offline")


class RenderProject(ProjectEdit, Protocol):
    def LoadRenderPreset(self, presetName: str) -> bool: ...
    def SaveAsNewRenderPreset(self, presetName: str) -> bool: ...
    def UpdateRenderPreset(self, presetName: str) -> bool: ...
    def DeleteRenderPreset(self, presetName: str) -> bool: ...
    def SetQuickExportEnabledForRenderPreset(self, presetName: str, isEnabled: bool) -> bool: ...
    def StartRendering(self, jobIds: list[str], isInteractiveMode: bool = False) -> bool: ...
    def StopRendering(self) -> None: ...
    def AddRenderJob(self) -> str: ...
    def DeleteRenderJob(self, jobId: str) -> bool: ...
    def DeleteAllRenderJobs(self) -> bool: ...
    def SetRenderSettings(self, settings: Info) -> bool: ...
    def RenderWithQuickExport(
        self, quickExportPresetName: str, presetInfo: Info
    ) -> Info | None: ...
    def GetRenderWithQuickExportStatus(self) -> Info: ...
    def ExportCurrentFrameAsStill(self, filePath: str) -> bool: ...
    def SetCurrentRenderFormatAndCodec(self, format: str, codec: str) -> bool: ...
    def SetCurrentRenderMode(self, renderMode: int) -> bool: ...


def render_project(session: Session) -> RenderProject:
    return cast(RenderProject, session.require_project())


def coerce_setting(key: str, text: str) -> object:
    """`text` as the type of the RenderSettings key; a bad value is VALIDATION_ERROR."""
    if key == "VideoQuality":
        if text.isdigit():
            return int(text)
        if text in VIDEO_QUALITY_WORDS:
            return text
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot read {text} as a VideoQuality",
            help=[f"Pass a bit rate (0 = automatic) or one of {', '.join(VIDEO_QUALITY_WORDS)}"],
        )
    return coerce_like(text, RENDER_SETTING_TYPES[key]())


def parse_settings(raw: str | None) -> dict[str, object]:
    """`--set Key=value,...` as typed RenderSettings; unknown or reserved keys exit 2."""
    if raw is None:
        return {}
    pairs = parse_assignments(raw)
    unknown = [key for key in pairs if key not in RENDER_SETTING_TYPES]
    if unknown:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown render setting {', '.join(unknown)}",
            help=[f"Valid settings: {', '.join(RENDER_SETTING_TYPES)}"],
        )
    reserved = [key for key in pairs if key in RESERVED_SETTINGS]
    if reserved:
        raise AxiError(
            "VALIDATION_ERROR",
            f"{', '.join(reserved)} has its own flag",
            help=["Use --target-dir for TargetDir and --name for CustomName"],
        )
    return {key: coerce_setting(key, text) for key, text in pairs.items()}


def target_dir(inv: Invocation) -> Path:
    """The absolute `--target-dir`, or the error for each dialog Resolve would otherwise show."""
    path = Path(str(inv.args["target_dir"])).expanduser().resolve()
    shown = collapse_home(path)
    if path.exists() and not path.is_dir():
        raise AxiError(
            "VALIDATION_ERROR", f"{shown} is a file, not a directory", help=["Pass a directory"]
        )
    if not path.exists():
        if inv.args.get("mkdir"):
            return path
        raise not_found(
            "directory", shown, "Pass --mkdir to create it, or choose an existing directory"
        )
    if not inv.args.get("allow_existing") and any(path.iterdir()):
        raise AxiError(
            "TARGET_DIR_NOT_EMPTY",
            f"{shown} already holds files, and Resolve would ask before overwriting them",
            help=[
                "Choose an empty directory (add --mkdir to create a new one)",
                "Or pass --allow-existing to render into it anyway",
            ],
        )
    return path


def offline_clips(timeline: TimelineApi) -> list[str]:
    """Names of the media pool clips on `timeline` whose `Online Status` is Offline."""
    names: set[str] = set()
    for located in walk_items(timeline, TRACK_TYPES):
        clip = located.item.GetMediaPoolItem()
        if clip is None:
            continue
        if str(clip.GetClipProperty("Online Status") or "").strip().lower() == "offline":
            names.add(clip.GetName())
    return sorted(names)


def refuse_offline(timeline: TimelineApi, inv: Invocation) -> None:
    """OFFLINE_MEDIA unless `--allow-offline`."""
    names = offline_clips(timeline)
    if not names or inv.args.get("allow_offline"):
        return
    shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    raise AxiError(
        "OFFLINE_MEDIA",
        f"timeline {timeline.GetName()} has offline media ({shown}); Resolve would warn first",
        help=[
            "Relink the media, then retry",
            "Or pass --allow-offline to render with the offline clips",
        ],
    )


def force_words(inv: Invocation, cmd: list[str], bools: tuple[str, ...] = ()) -> str:
    """The force command: `cmd`, the given bool flags, `--no-wait`/poll flags, context flags."""
    given = [f"--{flag}" for flag in bools if inv.args.get(flag.replace("-", "_"))]
    wait, timeout, interval = poll_args(inv) if "wait" in inv.args else (True, 0.0, 0.0)
    poll = [] if wait else ["--no-wait"]
    if "wait" in inv.args and timeout != DEFAULT_POLL_TIMEOUT:
        poll.extend(["--poll-timeout", f"{timeout:g}"])
    if "wait" in inv.args and interval != DEFAULT_POLL_INTERVAL:
        poll.extend(["--poll-interval", f"{interval:g}"])
    return force_cmd(inv, *cmd, *given, *poll, *context_words(inv))
