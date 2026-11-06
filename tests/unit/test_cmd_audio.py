"""`audio normalize|align|voice-isolation|mappings|preset|insert` (U18) on the fake."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import audio
from davinci_axi.fake import FakeProject, FakeWorld, ext_audio
from davinci_axi.fake.timeline import FakeTimeline
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

SAVE = ["ProjectManager.SaveProject", "ProjectManager.ExportProject"]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Neither the registry line nor the EXTENSIONS entry exists in the worktree."""
    ext_audio.extend()
    ext_audio.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "audio", "davinci_axi.commands.audio:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")
    yield


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _timeline(world: FakeWorld, name: str = "Edit v1") -> FakeTimeline:
    return next(t for t in _project(world).timelines if t.name == name)


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _untouched(world: FakeWorld, tmp_path: Path) -> None:
    assert world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


def _enum(world: FakeWorld, name: str) -> object:
    return getattr(world.resolve, name)


# ---------------------------------------------------------------- normalize


def test_normalize_dry_run_shows_the_options_and_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["audio", "normalize", "A1:1,A1:2", "--loudness", "-24"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["applied"] == "false" and keys["options"] == "targetLoudness=-24.0"
    assert "A1:1, A1:2" in keys["target"] and "not read back" in keys["warning"]
    assert "--force` to apply" in out
    _untouched(fake_world, tmp_path)


def test_normalize_force_makes_one_call_with_both_items_and_the_options(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["audio", "normalize", "A1:1,A1:2", "--loudness", "-24", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true"
    assert fake_world.write_calls() == [*SAVE, "Timeline.NormalizeAudioLevel"]
    (call,) = fake_world.calls_to("Timeline.NormalizeAudioLevel")
    assert [item.name for item in call[0]] == ["A001_beach", "music_bed"]  # type: ignore[attr-defined]
    assert call[1] == {"targetLoudness": -24.0}
    assert (_snapshots(tmp_path) / keys["snapshot"]).is_dir()
    items = _timeline(fake_world).tracks["audio"][0].items
    assert [item.properties["AudioVolume"] for item in items] == [-24.0, -24.0]


def test_normalize_passes_the_enum_and_a_valid_mode(run_cli: Run, fake_world: FakeWorld) -> None:
    args = ["audio", "normalize", "A1:1", "--mode", "LUFS", "--level", "-9", "--independent"]
    code, _, _ = run_cli([*args, "--force"])
    assert code == 0
    options = fake_world.calls_to("Timeline.NormalizeAudioLevel")[0][1]
    assert options == {
        "normalizationMode": "LUFS",
        "targetLevel": -9.0,
        "setLevelMode": _enum(fake_world, "NORMALIZE_AUDIO_SET_LEVEL_INDEPENDENT"),
    }


def test_normalize_unknown_mode_exits_2_after_connecting_and_writes_nothing(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["audio", "normalize", "A1:1", "--mode", "Loudest", "--force"])
    assert code == 2 and "Valid modes: Sample Peak, True Peak, LUFS" in out
    _untouched(fake_world, tmp_path)


def test_normalize_unknown_item_is_not_found_and_a_false_return_is_not_available(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["audio", "normalize", "A1:9", "--force"])
    assert code == 1 and "NOT_FOUND" in out and fake_world.write_calls() == []
    fake_world.inject("Timeline.NormalizeAudioLevel", False)
    code, out, _ = run_cli(["audio", "normalize", "A1:1", "--force"])
    assert code == 1 and "NOT_AVAILABLE" in out and "Studio" in out


# ---------------------------------------------------------------- align


def test_align_passes_the_enum_values(run_cli: Run, fake_world: FakeWorld) -> None:
    args = ["audio", "align", "V1:1,V1:2", "--using", "waveform", "--track", "mix", "--force"]
    code, _, _ = run_cli(args)
    assert code == 0 and fake_world.write_calls() == [*SAVE, "Timeline.AutoAlignClips"]
    options = fake_world.calls_to("Timeline.AutoAlignClips")[0][1]
    assert options == {
        "SyncUsing": _enum(fake_world, "AUTO_ALIGN_CLIPS_USING_WAVEFORM"),
        "UseTrack": _enum(fake_world, "AUTO_ALIGN_CLIPS_WAVEFORM_TRACK_MIX"),
    }
    args = ["audio", "align", "V1:1", "--using", "waveform", "--track", "2", "--force"]
    code, _, _ = run_cli(args)
    assert code == 0 and fake_world.calls_to("Timeline.AutoAlignClips")[1][1]["UseTrack"] == 2


def test_align_rejects_a_track_without_waveform_before_connecting(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    for extra in (["--track", "2"], ["--using", "timecode", "--track", "2"]):
        code, out, _ = run_cli(["audio", "align", "V1:1", *extra])
        assert code == 2 and "--using waveform" in out
    code, out, _ = run_cli(["audio", "align", "V1:1", "--using", "waveform", "--track", "x"])
    assert code == 2
    assert fake_world.calls == []


def test_align_without_options_sends_none_and_dry_run_writes_nothing(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["audio", "align", "V1:1,V1:2"])
    assert code == 0 and "options: (defaults)" in out
    _untouched(fake_world, tmp_path)


# ---------------------------------------------------------------- voice isolation


def test_voice_isolation_get_reads_an_item_and_a_track(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["audio", "voice-isolation", "get", "A1:1"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "") and keys["enabled"] == "false" and keys["amount"] == "0"
    code, out, _ = run_cli(["audio", "voice-isolation", "get", "--track", "1"])
    assert code == 0 and "audio track A1" in out
    assert fake_world.write_calls() == [] and fake_world.page_changes() == []


def test_voice_isolation_set_dry_run_then_force_reads_back(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = ["audio", "voice-isolation", "set", "A1:1", "--on", "--amount", "60"]
    code, out, _ = run_cli(args)
    assert code == 0 and "changes[2]{field,before,after}:" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true"
    assert fake_world.write_calls() == [*SAVE, "TimelineItem.SetVoiceIsolationState"]
    assert fake_world.calls_to("TimelineItem.SetVoiceIsolationState") == [
        ({"isEnabled": True, "amount": 60},)
    ]
    code, out, _ = run_cli(["audio", "voice-isolation", "get", "A1:1"])
    assert "enabled: true" in out and "amount: 60" in out
    assert "timeline_restored" not in keys


def test_voice_isolation_track_form_on_another_timeline_restores_it(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    args = ["audio", "voice-isolation", "set", "--track", "1", "--on", "--amount", "40"]
    code, out, _ = run_cli([*args, "--timeline", "Edit v2", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["timeline_restored"] == "true"
    assert "Timeline.SetVoiceIsolationState" in fake_world.write_calls()
    assert _project(fake_world).current_timeline is _timeline(fake_world)
    state = _timeline(fake_world, "Edit v2").tracks["audio"][0].__dict__["voice_isolation"]
    assert state == {"isEnabled": True, "amount": 40}
    off = ["audio", "voice-isolation", "set", "--track", "1", "--off"]
    code, out, _ = run_cli([*off, "--timeline", "Edit v2", "--force"])
    assert code == 0 and "isEnabled,true,false" in out


def test_voice_isolation_set_without_a_change_is_a_no_op(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["audio", "voice-isolation", "set", "A1:1", "--off", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    _untouched(fake_world, tmp_path)


def test_voice_isolation_validates_its_arguments(run_cli: Run, fake_world: FakeWorld) -> None:
    base = ["audio", "voice-isolation"]
    for argv, code in (
        ([*base, "get"], 2),
        ([*base, "get", "A1:1", "--track", "1"], 2),
        ([*base, "set", "A1:1"], 2),
        ([*base, "set", "A1:1", "--on", "--off"], 2),
        ([*base, "set", "A1:1", "--amount", "101"], 2),
        ([*base, "get", "--track", "9"], 1),
        ([*base, "get", "A1:9"], 1),
    ):
        assert run_cli(argv)[0] == code, argv
    assert fake_world.write_calls() == []


# ---------------------------------------------------------------- mappings


def _mapping(channels: list[int], mute: bool = False) -> str:
    track = {"channel_idx": channels, "mute": mute, "type": "Stereo"}
    return json.dumps({"track_mapping": {"1": track}})


def test_mapping_get_prints_the_string_and_a_track_table(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["audio", "mapping", "get", "music_bed"])
    assert (code, err) == (0, "") and "tracks[1]{track,type,mute,channels}:" in out
    assert "track_mapping" in parse_toon_keys(out)["mapping"]
    assert fake_world.write_calls() == []


def test_mapping_set_round_trips_and_a_dry_run_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    wanted = _mapping([1, 1], mute=True)
    code, out, _ = run_cli(["audio", "mapping", "set", "music_bed", wanted])
    assert code == 0 and "track_mapping" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli(["audio", "mapping", "set", "music_bed", wanted, "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [*SAVE, "MediaPoolItem.SetAudioMapping"]
    _, out, _ = run_cli(["audio", "mapping", "get", "music_bed", "--json"])
    assert '\\"mute\\": true' in out
    code, out, _ = run_cli(["audio", "mapping", "set", "music_bed", wanted, "--force"])
    assert parse_toon_keys(out)["applied"] == "false"  # already that mapping: no-op


def test_mapping_set_refuses_bad_json_and_a_rejected_write(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    for text in ("not json", '{"embedded_audio_channels": 2}', "[]"):
        code, out, _ = run_cli(["audio", "mapping", "set", "music_bed", text, "--force"])
        assert code == 2 and "track_mapping" in out
    assert fake_world.write_calls() == []
    fake_world.inject("MediaPoolItem.SetAudioMapping", False)
    code, out, _ = run_cli(["audio", "mapping", "set", "music_bed", _mapping([2, 1]), "--force"])
    assert code == 1 and "API_FAILED" in out


def test_channel_mapping_round_trip_on_an_item(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["audio", "channel-mapping", "get", "A1:1"])
    assert code == 0 and "tracks[1]" in out
    wanted = _mapping([2, 1])
    code, out, _ = run_cli(["audio", "channel-mapping", "set", "A1:1", wanted, "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [*SAVE, "TimelineItem.SetSourceAudioChannelMapping"]
    _, out, _ = run_cli(["audio", "channel-mapping", "get", "A1:1"])
    assert "2,1" in out


# ---------------------------------------------------------------- presets and insert


def test_preset_list_and_apply(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["audio", "preset", "list"])
    assert code == 0 and "presets[1]: Dialogue" in out
    code, out, _ = run_cli(["audio", "preset", "apply", "Dialogue"])
    assert code == 0 and "not read back" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli(["audio", "preset", "apply", "Dialogue", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [*SAVE, "Project.ApplyFairlightPresetToCurrentTimeline"]
    assert _timeline(fake_world).__dict__["fairlight_preset"] == "Dialogue"


def test_preset_apply_unknown_name_is_not_found_before_any_write(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["audio", "preset", "apply", "Nope", "--force"])
    assert code == 1 and "Fairlight presets: Dialogue" in out
    assert fake_world.write_calls() == []


def test_insert_adds_an_audio_item_and_restores_the_page(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    sound = tmp_path / "a.wav"
    sound.write_bytes(b"RIFF")
    code, out, _ = run_cli(["audio", "insert", str(sound)])
    assert code == 0 and "audio items,2,3" in out
    _untouched(fake_world, tmp_path)
    args = ["audio", "insert", str(sound), "--duration-samples", "96000", "--force"]
    code, out, _ = run_cli(args)
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["page_restored"] == "true"
    insert = "Project.InsertAudioToCurrentTrackAtPlayhead"
    assert fake_world.write_calls() == [*SAVE, "Resolve.OpenPage", insert, "Resolve.OpenPage"]
    assert fake_world.calls_to("Project.InsertAudioToCurrentTrackAtPlayhead") == [
        (str(sound), 0, 96000)
    ]
    assert fake_world.page_changes() == ["fairlight", "edit"]
    items = _timeline(fake_world).tracks["audio"][0].items
    assert items[-1].name == "a.wav" and items[-1].duration == 48


def test_insert_missing_file_and_rejected_call(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["audio", "insert", str(tmp_path / "none.wav"), "--force"])
    assert code == 1 and "NOT_FOUND" in out and fake_world.calls == []
    sound = tmp_path / "a.wav"
    sound.write_bytes(b"RIFF")
    code, out, _ = run_cli(["audio", "insert", str(sound), "--offset-samples", "-1"])
    assert code == 2
    fake_world.inject("Project.InsertAudioToCurrentTrackAtPlayhead", False)
    code, out, _ = run_cli(["audio", "insert", str(sound), "--force"])
    assert code == 1 and "API_FAILED" in out
    assert fake_world.page_changes() == ["fairlight", "edit"]


# ---------------------------------------------------------------- safety and help


def test_a_failed_save_leaves_the_project_untouched(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("ProjectManager.SaveProject", False)
    for argv in (
        ["audio", "normalize", "A1:1", "--force"],
        ["audio", "voice-isolation", "set", "A1:1", "--on", "--force"],
        ["audio", "mapping", "set", "music_bed", _mapping([2, 1]), "--force"],
    ):
        code, out, _ = run_cli(argv)
        assert code == 1 and "SNAPSHOT_FAILED" in out, argv
    assert fake_world.write_calls() == ["ProjectManager.SaveProject"] * 3
    assert not _snapshots(tmp_path).exists()


def test_every_audio_help_exits_zero(run_cli: Run) -> None:
    def walk(path: list[str], spec: CommandSpec) -> Iterator[list[str]]:
        yield path
        for name, child in spec.subcommands.items():
            yield from walk([*path, name], child)

    paths = list(walk(["audio"], audio.SPEC))
    assert len(paths) > 20
    for path in paths:
        code, out, err = run_cli([*path, "--help"])
        assert (code, err) == (0, "") and "examples[" in out, path


def test_spec_methods_cover_the_calls_the_module_makes() -> None:
    methods = set(audio.SPEC.methods)
    for name in (
        "Timeline.NormalizeAudioLevel",
        "Timeline.AutoAlignClips",
        "Timeline.GetVoiceIsolationState",
        "TimelineItem.SetVoiceIsolationState",
        "MediaPoolItem.SetAudioMapping",
        "TimelineItem.SetSourceAudioChannelMapping",
        "Resolve.GetFairlightPresets",
        "Project.ApplyFairlightPresetToCurrentTimeline",
        "Project.InsertAudioToCurrentTrackAtPlayhead",
        "MediaPool.AutoSyncAudio",
        "MediaPoolItem.TranscribeAudio",
        "Folder.TranscribeAudio",
        "Project.GetTranscribeAudioStatus",
        "MediaPoolItem.GetTranscription",
        "MediaPoolItem.ClearTranscription",
        "MediaPoolItem.PerformAudioClassification",
        "Folder.ClearAudioClassification",
        "Project.GenerateSpeech",
    ):
        assert name in methods, name
    assert all("." in name for name in methods)
