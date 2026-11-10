"""`audio` verbs on the Extras preflight, factual transcribe errors and `--per-clip` (F11 U3b)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import ai_state, registry
from davinci_axi.fake import FakeMediaPoolItem, FakeWorld, ext_ai, ext_audio
from davinci_axi.fake.ext_extras import write_package

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

POLL = ["--poll-interval", "0"]
SLATE = "AI Slate ID"
SPEECH = "AI Speech Generator"
LANGS = "Extended AI Transcription Language Support 2"


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_audio.extend()
    ext_ai.extend()
    monkeypatch.setitem(registry.COMMANDS, "audio", "davinci_axi.commands.audio:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")
    yield


def _seed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *names: str) -> None:
    root = tmp_path / "extras-seed"
    root.mkdir()
    for name in names:
        write_package(root, name)
    monkeypatch.setenv("DAVINCI_AXI_EXTRAS_DIR", str(root))


def _clip(world: FakeWorld, name: str) -> FakeMediaPoolItem:
    project = world.resolve.pm.current_project
    assert project is not None
    root = project.media_pool.root
    pool = [*root.clips, *(c for folder in root.subfolders for c in folder.clips)]
    return next(c for c in pool if c.name == name)


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


# ---------------------------------------------------------------- transcribe


def test_transcribe_dry_run_has_no_extras_warning(
    run_cli: Run, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["audio", "transcribe", "A001_beach", *POLL])
    assert code == 0 and "AI Extras" not in out
    assert 'ai_extras: "unknown:' in out
    _seed(monkeypatch, tmp_path, SLATE)
    code, out, _ = run_cli(["audio", "transcribe", "A001_beach", *POLL])
    assert code == 0 and "AI Extras" not in out.replace("AI Extras package", "")
    assert f"missing: {LANGS} (built-in languages still work)" in out
    code, out, _ = run_cli(["audio", "transcribe", "A001_beach", *POLL, "--force"])
    assert code == 0 and "applied: true" in out
    assert len(fake_world.calls_to("MediaPoolItem.TranscribeAudio")) == 1


def test_folder_false_is_factual(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("Folder.TranscribeAudio", False)
    code, out, _ = run_cli(["audio", "transcribe", "--folder", "Master", *POLL, "--force"])
    assert code == 1 and "NOT_AVAILABLE" in out
    assert "TranscribeAudio returned False for folder Master (4 clips)" in out
    assert "audio transcribe music_bed,A001_beach,A002_city,A003_dusk" in out
    assert "--per-clip" in out and "AI Extras" not in out
    assert "clips: 4" in out
    assert ai_state.load()["audio transcribe"].code == "NOT_AVAILABLE"


def test_clip_false_is_factual(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("MediaPoolItem.TranscribeAudio", False)
    code, out, _ = run_cli(["audio", "transcribe", "A001_beach", *POLL, "--force"])
    assert code == 1 and "TranscribeAudio returned False for clip A001_beach" in out
    assert "media show" in out and "AI Extras" not in out
    assert "audio transcribe" in ai_state.load()
    fake_world.fail.pop("MediaPoolItem.TranscribeAudio")
    code, _, _ = run_cli(["audio", "transcribe", "A001_beach", *POLL, "--force"])
    assert code == 0 and "audio transcribe" not in ai_state.load()


def test_per_clip_skips_non_audio_and_timeline_clips(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _clip(fake_world, "A001_beach").properties["Type"] = "Timeline"
    _clip(fake_world, "A002_city").properties["Type"] = "Video"
    code, out, _ = run_cli(
        ["audio", "transcribe", "--folder", "Master", "--per-clip", *POLL, "--force"]
    )
    keys = parse_toon_keys(out)
    assert code == 0 and keys["per_clip"] == "2/4 clips"
    assert keys["skipped"].strip('"') == "A001_beach, A002_city"
    assert len(fake_world.calls_to("MediaPoolItem.TranscribeAudio")) == 2
    assert fake_world.calls_to("Folder.TranscribeAudio") == []
    assert len(fake_world.calls_to("ProjectManager.ExportProject")) == 1
    assert "transcribed[2]{clip,segments}:" in out


def test_per_clip_validation(run_cli: Run, fake_world: FakeWorld) -> None:
    for args in (
        ["audio", "transcribe", "A001_beach", "--per-clip"],
        ["audio", "transcribe", "--folder", "Master", "--per-clip", "--no-wait"],
    ):
        assert run_cli([*args, "--force"])[0] == 2
    assert fake_world.calls == []
    for name in ("A001_beach", "A002_city", "A003_dusk", "music_bed"):
        _clip(fake_world, name).properties["Type"] = "Video"
    code, out, _ = run_cli(["audio", "transcribe", "--folder", "Master", "--per-clip", "--force"])
    assert code == 1 and "NOT_FOUND" in out and "no audio clips to transcribe" in out
    assert fake_world.write_calls() == []


# ---------------------------------------------------------------- show


def test_show_notes_no_speech(run_cli: Run, fake_world: FakeWorld) -> None:
    _clip(fake_world, "A001_beach").__dict__["transcription"] = {
        "language": "en-US",
        "segments": [{"start": "01:00:00:00", "end": "01:00:01:00", "text": "", "speaker": None}],
    }
    code, out, _ = run_cli(["audio", "transcription", "show", "A001_beach"])
    assert code == 0 and "note: no speech detected" in out and "total: 1" in out
    assert "audio transcription clear A001_beach" in out


# ---------------------------------------------------------------- speech and classify


def test_speech_missing_refuses_before_snapshot(
    run_cli: Run,
    parse_toon_keys: Keys,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_world: FakeWorld,
) -> None:
    _seed(monkeypatch, tmp_path, SLATE)
    code, out, _ = run_cli(["audio", "speech", "Hello", "--force"])
    assert code == 1 and "NOT_AVAILABLE" in out and "preflight: missing" in out
    assert fake_world.calls_to("Project.GenerateSpeech") == []
    assert fake_world.calls_to("ProjectManager.ExportProject") == []
    assert not _snapshots(tmp_path).exists()
    code, out, _ = run_cli(["audio", "speech", "Hello", "--force", "--ignore-preflight"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert len(fake_world.calls_to("Project.GenerateSpeech")) == 1


def test_speech_installed_runs(
    run_cli: Run,
    parse_toon_keys: Keys,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_world: FakeWorld,
) -> None:
    _seed(monkeypatch, tmp_path, SPEECH)
    code, out, _ = run_cli(["audio", "speech", "Hello", "--force"])
    assert code == 0 and parse_toon_keys(out)["clip"] == "Speech 1"
    assert f'ai_extras: "installed: {SPEECH}"' in out


def test_classify_has_no_preflight_but_remembers(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["audio", "classify", "A001_beach"])
    assert code == 0 and "ai_extras" not in out
    ext_ai.set_ai_extras(fake_world, False)
    fake_world.inject("MediaPoolItem.PerformAudioClassification", False)
    code, out, _ = run_cli(["audio", "classify", "A001_beach", "--force"])
    assert code == 1 and "NOT_AVAILABLE" in out
    assert ai_state.load()["audio classify"].code == "NOT_AVAILABLE"
