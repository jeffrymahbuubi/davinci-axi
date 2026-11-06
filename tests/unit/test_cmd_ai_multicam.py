"""`ai deblur` and `ai multicam create|smart-switch|flatten` (U20) on the fake."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeMediaPoolItem, FakeProject, FakeTimeline, FakeWorld, ext_ai

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

SAVE = ["ProjectManager.SaveProject", "ProjectManager.ExportProject"]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_ai.extend()
    monkeypatch.setitem(registry.COMMANDS, "ai", "davinci_axi.commands.ai:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")
    yield


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _timeline(world: FakeWorld) -> FakeTimeline:
    return next(t for t in _project(world).timelines if t.name == "Edit v1")


def _pool(world: FakeWorld) -> list[FakeMediaPoolItem]:
    root = _project(world).media_pool.root
    return [*root.clips, *(c for folder in root.subfolders for c in folder.clips)]


def _names(world: FakeWorld) -> list[str]:
    return [clip.name for clip in _pool(world)]


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _untouched(world: FakeWorld, tmp_path: Path) -> None:
    assert world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


# ---------------------------------------------------------------- deblur


def test_deblur_clip_adds_a_deblur_clip_and_verifies_it(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = ["ai", "deblur", "A001_beach", "--set", "Format=mp4,UseExtremeMode=false"]
    code, out, _ = run_cli(args)
    assert code == 0 and "(new clips)" in out
    assert parse_toon_keys(out)["applied"] == "false"
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and "A001_beach Deblur" in out
    assert fake_world.write_calls() == [*SAVE, "MediaPoolItem.RemoveMotionBlur"]
    assert fake_world.calls_to("MediaPoolItem.RemoveMotionBlur") == [
        ({"Format": "mp4", "UseExtremeMode": False},)
    ]
    assert "A001_beach Deblur" in _names(fake_world)
    assert (_snapshots(tmp_path) / keys["snapshot"]).is_dir()


def test_deblur_folder_calls_the_folder_method(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["ai", "deblur", "--folder", "Master/Footage", "--force"])
    assert code == 0 and "A002_city Deblur" in out
    assert fake_world.write_calls() == [*SAVE, "Folder.RemoveMotionBlur"]
    assert fake_world.calls_to("Folder.RemoveMotionBlur") == [(None,)]
    assert [n for n in _names(fake_world) if n.endswith("Deblur")] == [
        "A001_beach Deblur",
        "A002_city Deblur",
        "A003_dusk Deblur",
    ]


def test_deblur_option_validation_exits_two_before_any_call(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    for argv in (
        ["ai", "deblur", "A001_beach", "--set", "Bogus=1"],
        ["ai", "deblur", "A001_beach", "--set", "UseExtremeMode=maybe"],
        ["ai", "deblur", "A001_beach", "--set", "Encoder=Fast"],
        ["ai", "deblur", "A001_beach", "--set", "Format"],
        ["ai", "deblur"],
    ):
        assert run_cli(argv)[0] == 2, argv
    assert fake_world.calls == []


def test_deblur_without_ai_extras_is_not_available(run_cli: Run, fake_world: FakeWorld) -> None:
    ext_ai.set_ai_extras(fake_world, False)
    for argv in (
        ["ai", "deblur", "A001_beach", "--force"],
        ["ai", "deblur", "--folder", "Master/Footage", "--force"],
    ):
        code, out, _ = run_cli(argv)
        assert code == 1 and "NOT_AVAILABLE" in out and "snapshot" in out, argv
    assert "A001_beach Deblur" not in _names(fake_world)


# ---------------------------------------------------------------- multicam create


def test_multicam_create_adds_a_clip(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = ["ai", "multicam", "create", "A001_beach,A002_city", "--sync", "audio"]
    code, out, _ = run_cli(args)
    assert code == 0 and "angleSyncMode=audio" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true" and "Multicam 1" in out
    assert fake_world.write_calls() == [*SAVE, "MediaPool.CreateMulticamClip"]
    ((clips, options),) = fake_world.calls_to("MediaPool.CreateMulticamClip")
    assert [c.name for c in clips] == ["A001_beach", "A002_city"]  # type: ignore[attr-defined]
    resolve = fake_world.resolve
    assert options == {"angleSyncMode": resolve.MULTICAM_ANGLE_SYNC_AUDIO}  # type: ignore[attr-defined]
    assert "Multicam 1" in _names(fake_world)


def test_multicam_create_name_and_options(run_cli: Run, fake_world: FakeWorld) -> None:
    sets = "multicamAudioMode=all,useFullClipExtents=true,detectSameCameraClipsMode=by-angle"
    args = ["ai", "multicam", "create", "A001_beach,A002_city", "--name", "Interview"]
    code, out, _ = run_cli([*args, "--set", sets, "--force"])
    assert code == 0 and "Interview" in out
    ((_, options),) = fake_world.calls_to("MediaPool.CreateMulticamClip")
    resolve = fake_world.resolve
    assert options == {
        "multicamAudioMode": resolve.MULTICAM_AUDIO_ALL,  # type: ignore[attr-defined]
        "useFullClipExtents": True,
        "detectSameCameraClipsMode": resolve.MULTICAM_DETECT_BY_ANGLE,  # type: ignore[attr-defined]
        "name": "Interview",
    }
    assert "Interview" in _names(fake_world)


def test_multicam_create_audio_only_options(run_cli: Run, fake_world: FakeWorld) -> None:
    clips = "A001_beach,A002_city"
    code, _, _ = run_cli(
        [
            "ai",
            "multicam",
            "create",
            clips,
            "--sync",
            "audio",
            "--set",
            "channelConfig=2",
            "--force",
        ]
    )
    assert code == 0
    assert fake_world.calls_to("MediaPool.CreateMulticamClip")[0][1] == {
        "angleSyncMode": fake_world.resolve.MULTICAM_ANGLE_SYNC_AUDIO,  # type: ignore[attr-defined]
        "channelConfig": 2,
    }
    code, _, _ = run_cli(
        [
            "ai",
            "multicam",
            "create",
            clips,
            "--sync",
            "audio",
            "--set",
            "channelConfig=mix",
            "--force",
        ]
    )
    assert code == 0
    mix = fake_world.resolve.AUDIO_SYNC_CHANNEL_MIX  # type: ignore[attr-defined]
    assert fake_world.calls_to("MediaPool.CreateMulticamClip")[1][1]["channelConfig"] == mix


def test_multicam_create_validation_exits_two(run_cli: Run, fake_world: FakeWorld) -> None:
    clips = "A001_beach,A002_city"
    for sets in (
        "name=x",
        "angleSyncMode=audio",
        "channelConfig=1",
        "splitAtGaps=true",
        "multicamAudioMode=loud",
        "startTimecode=1:00",
        "frameRate=0",
        "createBinForSourceClips=perhaps",
        "bogus=1",
    ):
        code, _, _ = run_cli(["ai", "multicam", "create", clips, "--set", sets])
        assert code == 2, sets
    code, _, _ = run_cli(
        ["ai", "multicam", "create", clips, "--sync", "audio", "--set", "channelConfig=9"]
    )
    assert code == 2
    assert run_cli(["ai", "multicam", "create", clips, "--sync", "sideways"])[0] == 2
    assert run_cli(["ai", "multicam", "create", "A001_beach,nope", "--force"])[0] == 1
    assert fake_world.write_calls() == []


def test_multicam_create_refused_by_resolve_is_api_failed(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject("MediaPool.CreateMulticamClip", [])
    code, out, _ = run_cli(["ai", "multicam", "create", "A001_beach,A002_city", "--force"])
    assert code == 1 and "API_FAILED" in out and "no multicam clip" in out


# ---------------------------------------------------------------- smart switch and flatten


def test_smart_switch_passes_typed_settings(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    sets = "minEditDuration=2,quality=better,wideAngleFrequency=high,switchOnVideoOnly=true"
    args = ["ai", "multicam", "smart-switch", "V1:1", "--set", sets]
    code, out, _ = run_cli(args)
    assert code == 0 and "not read back" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [*SAVE, "TimelineItem.PerformMulticamSmartSwitch"]
    resolve = fake_world.resolve
    assert fake_world.calls_to("TimelineItem.PerformMulticamSmartSwitch") == [
        (
            {
                "minEditDuration": 2.0,
                "quality": resolve.SMART_SWITCH_QUALITY_BETTER,  # type: ignore[attr-defined]
                "wideAngleFrequency": resolve.SMART_SWITCH_WIDE_ANGLE_FREQ_HIGH,  # type: ignore[attr-defined]
                "switchOnVideoOnly": True,
            },
        )
    ]
    item = _timeline(fake_world).tracks["video"][0].items[0]
    assert [op for op, _ in ext_ai.ai_ops(item)] == ["smart-switch"]


def test_smart_switch_defaults_validation_and_availability(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, _, _ = run_cli(["ai", "multicam", "smart-switch", "V1:1", "--force"])
    assert code == 0
    assert fake_world.calls_to("TimelineItem.PerformMulticamSmartSwitch") == [({},)]
    for sets in ("minEditDuration=0.1", "editChangeDelay=3", "quality=best", "bogus=1"):
        argv = ["ai", "multicam", "smart-switch", "V1:1", "--set", sets]
        assert run_cli(argv)[0] == 2, sets
    ext_ai.set_ai_extras(fake_world, False)
    code, out, _ = run_cli(["ai", "multicam", "smart-switch", "V1:1", "--force"])
    assert code == 1 and "NOT_AVAILABLE" in out


def test_flatten_is_irreversible_and_passes_the_grade_constant(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["ai", "multicam", "flatten", "V1:1", "--grade", "copy"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and keys["irreversible"] == "true"
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli(["ai", "multicam", "flatten", "V1:1", "--grade", "copy", "--force"])
    assert code == 0 and parse_toon_keys(out)["irreversible"] == "true"
    assert fake_world.write_calls() == [*SAVE, "TimelineItem.FlattenMulticam"]
    resolve = fake_world.resolve
    assert fake_world.calls_to("TimelineItem.FlattenMulticam") == [
        (resolve.FLATTEN_MULTICAM_COPY_GRADE,)  # type: ignore[attr-defined]
    ]
    run_cli(["ai", "multicam", "flatten", "V1:2", "--grade", "angle", "--force"])
    assert fake_world.calls_to("TimelineItem.FlattenMulticam")[-1] == (
        resolve.FLATTEN_MULTICAM_RETAIN_GRADE_FROM_ANGLE,  # type: ignore[attr-defined]
    )


def test_flatten_needs_a_valid_grade(run_cli: Run, fake_world: FakeWorld) -> None:
    assert run_cli(["ai", "multicam", "flatten", "V1:1"])[0] == 2
    assert run_cli(["ai", "multicam", "flatten", "V1:1", "--grade", "both"])[0] == 2
    assert fake_world.calls == []


def test_snapshot_failure_leaves_every_multicam_call_unmade(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("ProjectManager.SaveProject", False)
    for argv in (
        ["ai", "deblur", "A001_beach", "--force"],
        ["ai", "multicam", "create", "A001_beach,A002_city", "--force"],
        ["ai", "multicam", "smart-switch", "V1:1", "--force"],
        ["ai", "multicam", "flatten", "V1:1", "--grade", "copy", "--force"],
    ):
        code, out, _ = run_cli(argv)
        assert code == 1 and "SNAPSHOT_FAILED" in out, argv
    assert fake_world.write_calls() == ["ProjectManager.SaveProject"] * 4
    assert not _snapshots(tmp_path).exists()
