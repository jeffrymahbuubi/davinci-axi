"""`audio sync|transcribe|transcription|classify|speech` (U18) on the fake."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeMediaPoolItem, FakeProject, FakeWorld, ext_audio

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

SAVE = ["ProjectManager.SaveProject", "ProjectManager.ExportProject"]
POLL = ["--poll-interval", "0"]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_audio.extend()
    ext_audio.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "audio", "davinci_axi.commands.audio:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")
    yield


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _clip(world: FakeWorld, name: str) -> FakeMediaPoolItem:
    root = _project(world).media_pool.root
    pool = [*root.clips, *(c for folder in root.subfolders for c in folder.clips)]
    return next(c for c in pool if c.name == name)


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _untouched(world: FakeWorld, tmp_path: Path) -> None:
    assert world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


def _seed(clip: FakeMediaPoolItem, count: int, text: str = "word") -> None:
    segments = [
        {"start": "01:00:00:00", "end": "01:00:01:00", "text": text, "speaker": None}
        for _ in range(count)
    ]
    clip.__dict__["transcription"] = {"language": "en-US", "segments": segments}


# ---------------------------------------------------------------- sync


def test_sync_dry_run_then_force_passes_the_enums(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = ["audio", "sync", "A001_beach,A002_city", "--mode", "waveform", "--channel", "mix"]
    code, out, _ = run_cli(args)
    assert code == 0 and "not read back" in out and "syncMode=" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--keep-embedded", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [*SAVE, "MediaPool.AutoSyncAudio"]
    (call,) = fake_world.calls_to("MediaPool.AutoSyncAudio")
    assert [clip.name for clip in call[0]] == ["A001_beach", "A002_city"]  # type: ignore[attr-defined]
    resolve = fake_world.resolve
    assert call[1] == {
        "syncMode": resolve.AUDIO_SYNC_WAVEFORM,  # type: ignore[attr-defined]
        "channelNumber": resolve.AUDIO_SYNC_CHANNEL_MIX,  # type: ignore[attr-defined]
        "retainEmbeddedAudio": True,
    }


def test_sync_channel_number_and_validation(run_cli: Run, fake_world: FakeWorld) -> None:
    pair = "A001_beach,music_bed"
    code, _, _ = run_cli(["audio", "sync", pair, "--mode", "waveform", "--channel", "2", "--force"])
    assert code == 0 and fake_world.calls_to("MediaPool.AutoSyncAudio")[0][1]["channelNumber"] == 2
    for argv, wanted in (
        (["audio", "sync", pair, "--channel", "2"], 2),
        (["audio", "sync", pair, "--mode", "waveform", "--channel", "zero"], 2),
        (["audio", "sync", "A001_beach", "--force"], 2),
        (["audio", "sync", pair, "--mode", "sideways"], 2),
        (["audio", "sync", "A001_beach,nope", "--force"], 1),
    ):
        assert run_cli(argv)[0] == wanted, argv
    assert len(fake_world.calls_to("MediaPool.AutoSyncAudio")) == 1


def test_sync_rejected_by_resolve_is_api_failed(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("MediaPool.AutoSyncAudio", False)
    code, out, _ = run_cli(["audio", "sync", "A001_beach,music_bed", "--force"])
    assert code == 1 and "API_FAILED" in out and "video and one audio" in out


# ---------------------------------------------------------------- transcribe


def test_transcribe_dry_run_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["audio", "transcribe", "A001_beach", *POLL])
    assert (code, err) == (0, "") and "status,Inactive,Complete" in out
    assert parse_toon_keys(out)["applied"] == "false"
    _untouched(fake_world, tmp_path)


def test_transcribe_force_polls_with_progress_on_stderr_then_show_lists_segments(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["audio", "transcribe", "A001_beach", *POLL, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true"
    assert err.splitlines() == ["transcribe: Analyzing 50%", "transcribe: Complete 100%"]
    assert "status,Inactive,Complete" in out and "A001_beach,2" in out
    assert fake_world.write_calls() == [*SAVE, "MediaPoolItem.TranscribeAudio"]
    assert fake_world.calls_to("MediaPoolItem.TranscribeAudio") == [(None, False)]
    assert (_snapshots(tmp_path) / keys["snapshot"]).is_dir()
    code, out, _ = run_cli(["audio", "transcription", "show", "A001_beach"])
    assert code == 0 and "total: 2" in out and "segments[2]{start,end,speaker,text}:" in out
    assert "Hello from A001_beach" in out


def test_transcribe_passes_speakers_and_nested(run_cli: Run, fake_world: FakeWorld) -> None:
    args = ["audio", "transcribe", "A001_beach", "--speakers", "--nested", *POLL, "--force"]
    code, out, _ = run_cli(args)
    assert code == 0
    assert fake_world.calls_to("MediaPoolItem.TranscribeAudio") == [(True, True)]
    assert "transcribed[1]{clip,segments}:" in out
    assert _clip(fake_world, "A001_beach").__dict__["nested_transcription"]["segments"]


def test_transcribe_folder_calls_the_folder_method(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["audio", "transcribe", "--folder", "Master/Footage", *POLL, "--force"])
    assert code == 0 and "media pool folder Master/Footage" in out
    assert fake_world.write_calls() == [*SAVE, "Folder.TranscribeAudio"]
    for name in ("A001_beach", "A002_city", "A003_dusk"):
        assert ext_audio.transcription_of(_clip(fake_world, name))["segments"]


def test_transcribe_needs_clips_or_a_folder_exactly_one(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    for argv in (
        ["audio", "transcribe"],
        ["audio", "transcribe", "A001_beach", "--folder", "Master/Footage"],
    ):
        code, out, _ = run_cli(argv)
        assert code == 2 and "exactly one" in out
    code, out, _ = run_cli(["audio", "transcribe", "--folder", "Master/Nope"])
    assert code == 1 and "NOT_FOUND" in out
    assert fake_world.write_calls() == []


def test_transcribe_no_wait_returns_after_the_start_call(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["audio", "transcribe", "A001_beach", "--no-wait", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true" and err == ""
    assert "status,Inactive,started" in out
    assert ext_audio.transcription_of(_clip(fake_world, "A001_beach")) == {}


def test_transcribe_false_return_is_not_available(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("MediaPoolItem.TranscribeAudio", False)
    code, out, _ = run_cli(["audio", "transcribe", "A001_beach", *POLL, "--force"])
    assert code == 1 and "NOT_AVAILABLE" in out and "AI Extras" in out
    assert "snapshot" in out  # the snapshot was taken, then the call refused


def test_transcribe_job_failure_timeout_and_busy(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject(
        "Project.GetTranscribeAudioStatus", {"JobStatus": "Failed", "Error": "no audio"}
    )
    code, out, err = run_cli(["audio", "transcribe", "A001_beach", *POLL, "--force"])
    assert code == 1 and "JOB_FAILED" in out and "no audio" in out
    assert "transcribe: Failed" in err
    fake_world.inject("Project.GetTranscribeAudioStatus", {"JobStatus": "Inactive"})
    args = [
        "audio",
        "transcribe",
        "A001_beach",
        "--poll-timeout",
        "0.05",
        "--poll-interval",
        "0.01",
    ]
    code, out, _ = run_cli([*args, "--force"])
    assert code == 1 and "POLL_TIMEOUT" in out
    fake_world.inject("Project.GetTranscribeAudioStatus", {"JobStatus": "Analyzing"})
    before = len(fake_world.write_calls())
    code, out, _ = run_cli(["audio", "transcribe", "A001_beach", *POLL, "--force"])
    assert code == 1 and "TRANSCRIBE_BUSY" in out
    assert len(fake_world.write_calls()) == before


def test_transcribe_a_video_only_clip_is_refused_by_the_fake_as_not_available(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _clip(fake_world, "A001_beach").properties["Type"] = "Video"
    code, out, _ = run_cli(["audio", "transcribe", "A001_beach", *POLL, "--force"])
    assert code == 1 and "NOT_AVAILABLE" in out


# ---------------------------------------------------------------- transcription show and clear


def test_transcription_show_empty_state_and_truncation(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["audio", "transcription", "show", "A002_city"])
    assert code == 0 and "0 segments in the transcription of clip A002_city" in out
    _seed(_clip(fake_world, "A002_city"), 200, "x" * 30)
    code, out, _ = run_cli(["audio", "transcription", "show", "A002_city"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["total"] == "200" and "of 200 segments" in out
    assert "--full" in out and "segments[200]" not in out
    code, out, _ = run_cli(["audio", "transcription", "show", "A002_city", "--full"])
    assert "segments[200]{start,end,speaker,text}:" in out and "note" not in parse_toon_keys(out)
    assert fake_world.write_calls() == []


def test_transcription_show_nested(run_cli: Run, fake_world: FakeWorld) -> None:
    clip = _clip(fake_world, "A002_city")
    clip.__dict__["nested_transcription"] = {
        "language": "de-DE",
        "segments": [{"start": "a", "end": "b", "text": "nested words", "speaker": "Ann"}],
    }
    code, out, _ = run_cli(["audio", "transcription", "show", "A002_city", "--nested"])
    assert code == 0 and "nested words" in out and "language: de-DE" in out
    assert fake_world.calls_to("MediaPoolItem.GetTranscription") == [(True,)]


def test_transcription_clear_is_irreversible_and_verified(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    clip = _clip(fake_world, "A001_beach")
    _seed(clip, 3)
    code, out, _ = run_cli(["audio", "transcription", "clear", "A001_beach"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and keys["irreversible"] == "true"
    assert "Master/Footage/A001_beach segments,3,0" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli(["audio", "transcription", "clear", "A001_beach", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [*SAVE, "MediaPoolItem.ClearTranscription"]
    assert fake_world.calls_to("MediaPoolItem.ClearTranscription") == [(False,)]
    assert ext_audio.transcription_of(clip) == {}
    code, out, _ = run_cli(["audio", "transcription", "clear", "A001_beach", "--force"])
    assert parse_toon_keys(out)["applied"] == "false"  # nothing left to clear


def test_transcription_clear_folder(run_cli: Run, fake_world: FakeWorld) -> None:
    for name in ("A001_beach", "A003_dusk"):
        _seed(_clip(fake_world, name), 2)
    code, out, _ = run_cli(
        ["audio", "transcription", "clear", "--folder", "Master/Footage", "--force"]
    )
    assert code == 0 and "changes[2]{field,before,after}:" in out
    assert fake_world.calls_to("Folder.ClearTranscription") == [()]
    assert ext_audio.transcription_of(_clip(fake_world, "A003_dusk")) == {}


# ---------------------------------------------------------------- classify


def test_classify_and_clear_on_clips_and_folders(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["audio", "classify", "A001_beach"])
    assert code == 0 and "not read back" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli(["audio", "classify", "A001_beach", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [*SAVE, "MediaPoolItem.PerformAudioClassification"]
    assert ext_audio.is_classified(_clip(fake_world, "A001_beach"))
    code, out, _ = run_cli(["audio", "classify", "clear", "A001_beach", "--force"])
    keys = parse_toon_keys(out)
    assert (
        code == 0 and keys["irreversible"] == "true" and keys["command"] == "audio classify clear"
    )
    assert not ext_audio.is_classified(_clip(fake_world, "A001_beach"))
    code, _, _ = run_cli(["audio", "classify", "--folder", "Master/Footage", "--force"])
    assert code == 0 and "Folder.PerformAudioClassification" in fake_world.write_calls()
    assert all(ext_audio.is_classified(_clip(fake_world, n)) for n in ("A001_beach", "A002_city"))
    code, _, _ = run_cli(["audio", "classify", "clear", "--folder", "Master/Footage", "--force"])
    assert code == 0 and "Folder.ClearAudioClassification" in fake_world.write_calls()


def test_classify_false_return_is_not_available(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("MediaPoolItem.PerformAudioClassification", False)
    code, out, _ = run_cli(["audio", "classify", "A001_beach", "--force"])
    assert code == 1 and "NOT_AVAILABLE" in out
    assert run_cli(["audio", "classify"])[0] == 2


# ---------------------------------------------------------------- speech


def test_speech_creates_a_clip_and_passes_the_settings(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["audio", "speech", "hello there"])
    assert code == 0 and "clip,null,(new clip)" in out
    _untouched(fake_world, tmp_path)
    args = ["audio", "speech", "hello there", "--voice", "Female 1", "--speed", "1.5", "--force"]
    code, out, _ = run_cli(args)
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["clip"] == "Speech 1"
    assert "clip,null,Speech 1" in out
    assert fake_world.write_calls() == [*SAVE, "Project.GenerateSpeech"]
    assert fake_world.calls_to("Project.GenerateSpeech") == [
        ({"TextInput": "hello there", "Speed": 1.5, "VoiceModel": "Female 1"},)
    ]
    assert _clip(fake_world, "Speech 1").properties["Type"] == "Audio"


def test_speech_custom_voice_file(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    voice = tmp_path / "me.voice"
    voice.write_text("v")
    code, _, _ = run_cli(["audio", "speech", "hi", "--voice-file", str(voice), "--force"])
    assert code == 0
    settings = fake_world.calls_to("Project.GenerateSpeech")[0][0]
    assert settings["VoiceModel"] == "Custom Voice"  # type: ignore[index]
    assert settings["CustomVoiceFile"] == str(voice.resolve())  # type: ignore[index]


def test_speech_validates_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    voice = tmp_path / "me.voice"
    voice.write_text("v")
    for argv, wanted in (
        (["audio", "speech", "x" * 351], 2),
        (["audio", "speech", "  "], 2),
        (["audio", "speech", "hi", "--speed", "11"], 2),
        (["audio", "speech", "hi", "--variation", "1.5"], 2),
        (["audio", "speech", "hi", "--pitch", "-3"], 2),
        (["audio", "speech", "hi", "--voice-file", str(tmp_path / "gone.voice")], 1),
        (["audio", "speech", "hi", "--voice-file", str(voice), "--voice", "Male 1"], 2),
    ):
        assert run_cli(argv)[0] == wanted, argv
    assert fake_world.calls == []


def test_speech_none_return_is_not_available(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("Project.GenerateSpeech", None)
    code, out, _ = run_cli(["audio", "speech", "hello", "--force"])
    assert code == 1 and "NOT_AVAILABLE" in out and "AI Extras" in out


# ---------------------------------------------------------------- safety


def test_a_failed_save_stops_every_job_before_any_call(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _seed(_clip(fake_world, "A001_beach"), 1)
    fake_world.inject("ProjectManager.SaveProject", False)
    for argv in (
        ["audio", "transcribe", "A001_beach", *POLL, "--force"],
        ["audio", "sync", "A001_beach,music_bed", "--force"],
        ["audio", "classify", "A001_beach", "--force"],
        ["audio", "speech", "hello", "--force"],
        ["audio", "transcription", "clear", "--folder", "Master/Footage", "--force"],
    ):
        code, out, _ = run_cli(argv)
        assert code == 1 and "SNAPSHOT_FAILED" in out, argv
    assert set(fake_world.write_calls()) == {"ProjectManager.SaveProject"}
    assert not _snapshots(tmp_path).exists()
