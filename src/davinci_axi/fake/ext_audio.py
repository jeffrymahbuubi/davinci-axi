"""Fake Fairlight and audio methods for U18 (KTD17).

Voice isolation lives on tracks and items, audio mappings are JSON strings on clips and items,
transcription and classification are flags and dicts on clips, and `GetTranscribeAudioStatus`
advances on reads (0 -> 50 % -> `Complete`) so a polling loop finishes without sleeping. Imports
only from `davinci_axi.fake.core|media|timeline`, never from the `davinci_axi.fake` package,
because the package is still mid-import when `EXTENSIONS` run. Each write keeps the `_rec`
injection contract: an injected value is returned (as `bool`, or the clip for `GenerateSpeech`),
otherwise the side effect happens. `extend()` is idempotent. The enum constants of KTD24 are set
on the `FakeResolve` class, so every instance (and `getattr(session.resolve, ...)`) sees them.

Test helpers (not API calls): `transcription_of`, `is_classified`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from davinci_axi.fake.core import FakeProject, FakeResolve
from davinci_axi.fake.media import FakeFolder, FakeMediaPool, FakeMediaPoolItem
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem

_ENUMS = {
    "NORMALIZE_AUDIO_": "SET_LEVEL_RELATIVE SET_LEVEL_INDEPENDENT",
    "AUTO_ALIGN_CLIPS_": (
        "USING_WAVEFORM USING_TIMECODE WAVEFORM_TRACK_MIX WAVEFORM_TRACK_AUTOMATIC"
    ),
    "AUDIO_SYNC_": "WAVEFORM TIMECODE IN OUT MARKER CHANNEL_AUTOMATIC CHANNEL_MIX",
}
_FIXED = {"AUDIO_SYNC_CHANNEL_AUTOMATIC": -1.0, "AUDIO_SYNC_CHANNEL_MIX": -2.0}
SYNC_KEYS = ("syncMode", "channelNumber", "retainEmbeddedAudio", "retainVideoMetadata")
SPEECH_KEYS = (
    "TextInput",
    "VoiceModel",
    "CustomVoiceFile",
    "Speed",
    "Variation",
    "Pitch",
    "GenerationID",
    "Filename",
    "AddToTimeline",
)
NORMALIZE_KEYS = ("normalizationMode", "targetLevel", "targetLoudness", "setLevelMode")
ALIGN_KEYS = ("SyncUsing", "UseTrack")
HALF = 50
FULL = 100
FPS = 24
SAMPLE_RATE = 48000
SPEECH_MAX = 350


def _state(owner: Any, key: str, default: Any) -> Any:
    return owner.__dict__.setdefault(key, default)


def transcription_of(clip: FakeMediaPoolItem, nested: bool = False) -> dict[str, Any]:
    """The stored transcription of `clip` (empty when it has none)."""
    return dict(_state(clip, "nested_transcription" if nested else "transcription", {}))


def is_classified(clip: FakeMediaPoolItem) -> bool:
    return bool(_state(clip, "classified", False))


def _tree_clips(folder: FakeFolder) -> list[FakeMediaPoolItem]:
    clips = list(folder.clips)
    for child in folder.subfolders:
        clips.extend(_tree_clips(child))
    return clips


def _timecode_frames(text: str) -> int:
    hours, minutes, seconds, frames = (int(part) for part in text.replace(";", ":").split(":"))
    return ((hours * 60 + minutes) * 60 + seconds) * FPS + frames


# --- timeline: normalize, align, voice isolation ------------------------------------------------


def _normalize(
    self: FakeTimeline, timelineItems: list[FakeTimelineItem], normalizeAudioOptions: Any = None
) -> bool:
    injected, value = self._rec("NormalizeAudioLevel", timelineItems, normalizeAudioOptions)
    if injected:
        return bool(value)
    options = dict(normalizeAudioOptions or {})
    modes = self.GetNormalizeAudioModes()
    if not timelineItems or any(key not in NORMALIZE_KEYS for key in options):
        return False
    if "normalizationMode" in options and options["normalizationMode"] not in modes:
        return False
    target = float(options.get("targetLoudness") or options.get("targetLevel") or -9.0)
    for item in timelineItems:
        item.properties["AudioVolume"] = target
    return True


def _align(
    self: FakeTimeline, timelineItems: list[FakeTimelineItem], autoAlignOptions: Any = None
) -> bool:
    injected, value = self._rec("AutoAlignClips", timelineItems, autoAlignOptions)
    if injected:
        return bool(value)
    if not timelineItems or any(key not in ALIGN_KEYS for key in dict(autoAlignOptions or {})):
        return False
    _state(self, "aligned", []).append([item.uid for item in timelineItems])
    return True


def _valid_state(state: Any) -> bool:
    if (
        not isinstance(state, dict)
        or not state
        or any(k not in ("isEnabled", "amount") for k in state)
    ):
        return False
    amount = state.get("amount", 0)
    return isinstance(amount, int) and 0 <= amount <= FULL


def _voice(owner: Any) -> dict[str, Any]:
    return _state(owner, "voice_isolation", {"isEnabled": False, "amount": 0})  # type: ignore[no-any-return]


def _timeline_get_voice(self: FakeTimeline, trackIndex: int) -> dict[str, Any]:
    track = self._track("audio", trackIndex)
    return self._ret("GetVoiceIsolationState", dict(_voice(track)) if track else {}, trackIndex)


def _timeline_set_voice(self: FakeTimeline, trackIndex: int, voiceIsolationState: Any) -> bool:
    injected, value = self._rec("SetVoiceIsolationState", trackIndex, voiceIsolationState)
    track = self._track("audio", trackIndex)
    if injected:
        return bool(value)
    if track is None or not _valid_state(voiceIsolationState):
        return False
    _voice(track).update(voiceIsolationState)
    return True


def _item_get_voice(self: FakeTimelineItem) -> dict[str, Any]:
    return self._ret("GetVoiceIsolationState", dict(_voice(self)))


def _item_set_voice(self: FakeTimelineItem, state: Any) -> bool:
    injected, value = self._rec("SetVoiceIsolationState", state)
    if injected:
        return bool(value)
    if not _valid_state(state):
        return False
    _voice(self).update(state)
    return True


# --- audio mappings ----------------------------------------------------------------------------


def _mapping_text(owner: Any) -> str:
    tracks = _state(
        owner, "track_mapping", {"1": {"channel_idx": [1, 2], "mute": False, "type": "Stereo"}}
    )
    return json.dumps({"embedded_audio_channels": 2, "linked_audio": {}, "track_mapping": tracks})


def _store_mapping(owner: Any, text: str) -> bool:
    try:
        parsed = json.loads(text)
    except ValueError:
        return False
    section = parsed.get("track_mapping") if isinstance(parsed, dict) else None
    if not isinstance(section, dict) or not section:
        return False
    owner.__dict__["track_mapping"] = section
    return True


def _clip_get_mapping(self: FakeMediaPoolItem) -> str:
    return self._ret("GetAudioMapping", _mapping_text(self))


def _clip_set_mapping(self: FakeMediaPoolItem, audioMapping: str) -> bool:
    injected, value = self._rec("SetAudioMapping", audioMapping)
    return bool(value) if injected else _store_mapping(self, audioMapping)


def _item_get_mapping(self: FakeTimelineItem) -> str:
    return self._ret("GetSourceAudioChannelMapping", _mapping_text(self))


def _item_set_mapping(self: FakeTimelineItem, audioMapping: str) -> bool:
    injected, value = self._rec("SetSourceAudioChannelMapping", audioMapping)
    return bool(value) if injected else _store_mapping(self, audioMapping)


# --- transcription and classification ---------------------------------------------------------


def _current(world_owner: Any) -> FakeProject | None:
    return world_owner._world.resolve.pm.current_project  # type: ignore[no-any-return]


def _status(project: FakeProject) -> dict[str, Any]:
    return _state(project, "transcribe_status", {"JobStatus": "Inactive"})  # type: ignore[no-any-return]


def _start(owner: Any, clips: list[FakeMediaPoolItem], speakers: Any, nested: bool) -> bool:
    project = _current(owner)
    capable = [c for c in clips if "Audio" in str(c.properties.get("Type", ""))]
    if project is None or not capable:
        return False
    _state(project, "transcribe_pending", []).extend((c, bool(speakers), nested) for c in capable)
    _status(project).clear()
    _status(project).update(JobStatus="Analyzing", CompletionPercentage=0)
    project.__dict__["transcribe_lag"] = int(owner._world.__dict__.get("start_lag", 0))
    return True


def _segments(clip: FakeMediaPoolItem, speakers: bool) -> dict[str, Any]:
    speaker = "Speaker 1" if speakers else None
    words = [{"start": "01:00:00:00", "end": "01:00:02:00", "text": f"Hello from {clip.name}"}]
    return {
        "language": "en-US",
        "segments": [
            {
                "start": "01:00:00:00",
                "end": "01:00:02:00",
                "text": f"Hello from {clip.name}",
                "speaker": speaker,
                "words": words,
            },
            {
                "start": "01:00:02:00",
                "end": "01:00:03:00",
                "text": "(...)",
                "speaker": None,
                "words": [],
            },
        ],
    }


def _get_transcribe_status(self: FakeProject) -> dict[str, Any]:
    injected, value = self._rec("GetTranscribeAudioStatus")
    if injected:
        return value  # type: ignore[return-value]
    if int(self.__dict__.get("transcribe_lag", 0)) > 0:  # `ext_ai.set_start_lag`
        self.__dict__["transcribe_lag"] -= 1
        return {"JobStatus": "Inactive"}
    status = _status(self)
    if status.get("JobStatus") == "Analyzing":
        if int(status.get("CompletionPercentage", 0)) < HALF:
            status["CompletionPercentage"] = HALF
        else:
            for clip, speakers, nested in _state(self, "transcribe_pending", []):
                key = "nested_transcription" if nested else "transcription"
                clip.__dict__[key] = _segments(clip, speakers)
            _state(self, "transcribe_pending", []).clear()
            status.clear()
            status.update(JobStatus="Complete", CompletionPercentage=FULL)
    return dict(status)


def _clip_transcribe(
    self: FakeMediaPoolItem, useSpeakerDetection: Any = None, transcribeAsNestedClip: bool = False
) -> bool:
    injected, value = self._rec("TranscribeAudio", useSpeakerDetection, transcribeAsNestedClip)
    if injected:
        return bool(value)
    return _start(self, [self], useSpeakerDetection, bool(transcribeAsNestedClip))


def _folder_transcribe(
    self: FakeFolder, useSpeakerDetection: Any = None, transcribeAsNestedClip: bool = False
) -> bool:
    injected, value = self._rec("TranscribeAudio", useSpeakerDetection, transcribeAsNestedClip)
    if injected:
        return bool(value)
    return _start(self, _tree_clips(self), useSpeakerDetection, bool(transcribeAsNestedClip))


def _clip_get_transcription(
    self: FakeMediaPoolItem, useNestedClipTranscription: bool = False
) -> Any:
    return self._ret(
        "GetTranscription",
        transcription_of(self, bool(useNestedClipTranscription)),
        useNestedClipTranscription,
    )


def _wipe(clip: FakeMediaPoolItem, nested: bool) -> None:
    clip.__dict__["transcription"] = {}
    if nested:
        clip.__dict__["nested_transcription"] = {}


def _clip_clear_transcription(
    self: FakeMediaPoolItem, clearNestedClipTranscription: bool = False
) -> bool:
    injected, value = self._rec("ClearTranscription", clearNestedClipTranscription)
    if injected:
        return bool(value)
    _wipe(self, bool(clearNestedClipTranscription))
    return True


def _folder_clear_transcription(self: FakeFolder) -> bool:
    injected, value = self._rec("ClearTranscription")
    if injected:
        return bool(value)
    for clip in _tree_clips(self):
        _wipe(clip, True)
    return True


def _classify(clip_or_folder: Any, method: str, flag: bool) -> bool:
    injected, value = clip_or_folder._rec(method)
    if injected:
        return bool(value)
    targets = (
        _tree_clips(clip_or_folder) if isinstance(clip_or_folder, FakeFolder) else [clip_or_folder]
    )
    for clip in targets:
        clip.__dict__["classified"] = flag
    return True


def _perform(self: Any) -> bool:
    return _classify(self, "PerformAudioClassification", True)


def _clear_classification(self: Any) -> bool:
    return _classify(self, "ClearAudioClassification", False)


# --- media pool sync, project insert, presets, speech ------------------------------------------


def _auto_sync(self: FakeMediaPool, mediaPoolItems: list[Any], audioSyncSettings: Any) -> bool:
    injected, value = self._rec("AutoSyncAudio", mediaPoolItems, audioSyncSettings)
    if injected:
        return bool(value)
    settings = dict(audioSyncSettings or {})
    if len(mediaPoolItems) < 2 or any(key not in SYNC_KEYS for key in settings):
        return False
    _state(self, "synced", []).append(([clip.name for clip in mediaPoolItems], settings))
    return True


def _insert_audio(
    self: FakeProject, mediaPath: str, startOffsetInSamples: int, durationInSamples: int
) -> bool:
    injected, value = self._rec(
        "InsertAudioToCurrentTrackAtPlayhead", mediaPath, startOffsetInSamples, durationInSamples
    )
    if injected:
        return bool(value)
    timeline = self.current_timeline
    if timeline is None or timeline._track("audio", 1) is None:
        return False
    playhead = (
        timeline.start_frame
        + _timecode_frames(timeline.current_tc)
        - _timecode_frames(timeline.start_tc)
    )
    frames = max(1, durationInSamples * FPS // SAMPLE_RATE) if durationInSamples else FPS
    timeline.add_item("audio", 1, Path(mediaPath).name, playhead, frames)
    return True


def _apply_preset(self: FakeProject, presetName: str) -> bool:
    injected, value = self._rec("ApplyFairlightPresetToCurrentTimeline", presetName)
    if injected:
        return bool(value)
    timeline = self.current_timeline
    if timeline is None or presetName not in self._world.resolve.fairlight_presets:
        return False
    timeline.__dict__["fairlight_preset"] = presetName
    return True


def _generate_speech(self: FakeProject, speechSettings: Any) -> FakeMediaPoolItem | None:
    injected, value = self._rec("GenerateSpeech", speechSettings)
    if injected:
        return value if isinstance(value, FakeMediaPoolItem) else None
    settings = dict(speechSettings or {})
    text = settings.get("TextInput")
    if not isinstance(text, str) or not text or len(text) > SPEECH_MAX:
        return None
    if any(key not in SPEECH_KEYS for key in settings):
        return None
    number = _state(self, "speech_count", [0])
    number[0] += 1
    clip = self.media_pool.current.add_clip(
        f"Speech {number[0]}",
        f"/media/davinci-axi-test/speech-{number[0]}.wav",
        clip_type="Audio",
        duration="00:00:03:00",
    )
    clip.__dict__["speech_settings"] = settings
    return clip


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeTimeline, "NormalizeAudioLevel"): _normalize,
    (FakeTimeline, "AutoAlignClips"): _align,
    (FakeTimeline, "GetVoiceIsolationState"): _timeline_get_voice,
    (FakeTimeline, "SetVoiceIsolationState"): _timeline_set_voice,
    (FakeTimelineItem, "GetVoiceIsolationState"): _item_get_voice,
    (FakeTimelineItem, "SetVoiceIsolationState"): _item_set_voice,
    (FakeTimelineItem, "GetSourceAudioChannelMapping"): _item_get_mapping,
    (FakeTimelineItem, "SetSourceAudioChannelMapping"): _item_set_mapping,
    (FakeMediaPoolItem, "GetAudioMapping"): _clip_get_mapping,
    (FakeMediaPoolItem, "SetAudioMapping"): _clip_set_mapping,
    (FakeMediaPoolItem, "TranscribeAudio"): _clip_transcribe,
    (FakeMediaPoolItem, "GetTranscription"): _clip_get_transcription,
    (FakeMediaPoolItem, "ClearTranscription"): _clip_clear_transcription,
    (FakeMediaPoolItem, "PerformAudioClassification"): _perform,
    (FakeMediaPoolItem, "ClearAudioClassification"): _clear_classification,
    (FakeFolder, "TranscribeAudio"): _folder_transcribe,
    (FakeFolder, "ClearTranscription"): _folder_clear_transcription,
    (FakeFolder, "PerformAudioClassification"): _perform,
    (FakeFolder, "ClearAudioClassification"): _clear_classification,
    (FakeMediaPool, "AutoSyncAudio"): _auto_sync,
    (FakeProject, "GetTranscribeAudioStatus"): _get_transcribe_status,
    (FakeProject, "InsertAudioToCurrentTrackAtPlayhead"): _insert_audio,
    (FakeProject, "ApplyFairlightPresetToCurrentTimeline"): _apply_preset,
    (FakeProject, "GenerateSpeech"): _generate_speech,
}


def extend() -> None:
    """Add the audio methods and the KTD24 enum constants (idempotent)."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)
    number = 3000.0
    for prefix, names in _ENUMS.items():
        for name in names.split():
            full = prefix + name
            number += 1.0
            setattr(FakeResolve, full, _FIXED.get(full, number))


__all__ = ["extend", "is_classified", "transcription_of"]
