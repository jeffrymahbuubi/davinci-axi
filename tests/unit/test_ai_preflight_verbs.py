"""The `ai` verbs wired to the AI Extras preflight (F11 U3a) on the fake."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import ai_state, registry
from davinci_axi.fake import FakeWorld, ext_ai
from davinci_axi.fake.ext_extras import write_package

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

POLL = ["--poll-interval", "0"]
SLATE = "AI Slate ID"
DEBLUR = "AI Motion Deblur"
FASTER = "AI IntelliSearch - Faster"
BETTER = "AI IntelliSearch - Better"


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_ai.extend()
    monkeypatch.setitem(registry.COMMANDS, "ai", "davinci_axi.commands.ai:SPEC")
    yield


def _seed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *names: str) -> None:
    root = tmp_path / "extras-seed"
    root.mkdir(exist_ok=True)
    for name in names:
        write_package(root, name)
    monkeypatch.setenv("DAVINCI_AXI_EXTRAS_DIR", str(root))


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def test_slate_missing_refuses_before_snapshot(
    run_cli: Run,
    parse_toon_keys: Keys,
    fake_world: FakeWorld,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed(monkeypatch, tmp_path, DEBLUR)
    code, out, _ = run_cli(["ai", "slate", "A001_beach", *POLL, "--force"])
    assert code == 1 and "NOT_AVAILABLE" in out and "preflight: missing" in out
    assert "Extras Download Manager" in out and "--ignore-preflight" in out
    assert fake_world.calls_to("MediaPoolItem.AnalyzeForSlate") == []
    assert fake_world.calls_to("ProjectManager.ExportProject") == []
    assert not _snapshots(tmp_path).exists()
    assert ai_state.load()["ai slate"].code == "NOT_AVAILABLE"
    code, out, _ = run_cli(["ai", "slate", "A001_beach", *POLL])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false"
    assert keys["warning"].lstrip('"').startswith("will fail:")
    assert fake_world.write_calls() == []


def test_slate_installed_runs_as_before(
    run_cli: Run,
    parse_toon_keys: Keys,
    fake_world: FakeWorld,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed(monkeypatch, tmp_path, SLATE)
    code, out, _ = run_cli(["ai", "slate", "A001_beach", *POLL])
    assert code == 0 and f"installed: {SLATE}" in parse_toon_keys(out)["ai_extras"]
    code, out, _ = run_cli(["ai", "slate", "A001_beach", *POLL, "--force"])
    assert code == 0 and "status,Inactive,Complete" in out
    assert len(fake_world.calls_to("ProjectManager.ExportProject")) == 1
    assert _snapshots(tmp_path).is_dir()


def test_slate_ignore_preflight_calls_resolve(
    run_cli: Run,
    fake_world: FakeWorld,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed(monkeypatch, tmp_path, DEBLUR)
    ext_ai.set_ai_extras(fake_world, False)
    argv = ["ai", "slate", "A001_beach", *POLL, "--ignore-preflight", "--force"]
    code, out, _ = run_cli(argv)
    assert code == 1 and "NOT_AVAILABLE" in out and "preflight" not in out
    assert len(fake_world.calls_to("MediaPoolItem.AnalyzeForSlate")) == 1
    assert _snapshots(tmp_path).is_dir()
    code, out, _ = run_cli(["ai", "status"])
    assert code == 0
    row = next(line for line in out.splitlines() if line.strip().startswith("slate,"))
    assert "NOT_AVAILABLE at 20" in row


def test_slate_dry_run_force_command_keeps_ignore_preflight(
    run_cli: Run, parse_toon_keys: Keys, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(monkeypatch, tmp_path, DEBLUR)
    code, out, _ = run_cli(["ai", "slate", "A001_beach", *POLL, "--ignore-preflight"])
    assert code == 0 and "--ignore-preflight --poll-interval 0 --force" in out
    assert not parse_toon_keys(out)["warning"].lstrip('"').startswith("will fail:")


def test_analyze_better_needs_the_better_package(
    run_cli: Run,
    fake_world: FakeWorld,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed(monkeypatch, tmp_path, FASTER)
    code, out, _ = run_cli(["ai", "intellisearch", "analyze", "A001_beach", "--force"])
    assert code == 0, out
    calls = len(fake_world.calls_to("MediaPoolItem.AnalyzeForIntellisearch"))
    assert calls == 1
    code, out, _ = run_cli(["ai", "intellisearch", "analyze", "A001_beach", "--better", "--force"])
    assert code == 1 and BETTER in out and "preflight: missing" in out
    assert len(fake_world.calls_to("MediaPoolItem.AnalyzeForIntellisearch")) == 1
    assert ai_state.load()["ai intellisearch analyze"].code == "NOT_AVAILABLE"


def test_reset_has_memory_but_no_preflight(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(monkeypatch, tmp_path)  # empty dir: nothing installed
    code, out, _ = run_cli(["ai", "intellisearch", "reset"])
    assert code == 0 and "ai_extras" not in out
    ext_ai.set_ai_extras(fake_world, False)
    code, _, _ = run_cli(["ai", "intellisearch", "reset", "--force"])
    assert code == 1
    assert ai_state.load()["ai intellisearch reset"].code == "NOT_AVAILABLE"


def test_deblur_missing_refuses_and_installed_runs(
    run_cli: Run,
    fake_world: FakeWorld,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed(monkeypatch, tmp_path, SLATE)
    code, out, _ = run_cli(["ai", "deblur", "A001_beach", "--force"])
    assert code == 1 and "preflight: missing" in out and DEBLUR in out
    assert fake_world.calls_to("MediaPoolItem.RemoveMotionBlur") == []
    assert not _snapshots(tmp_path).exists()
    _seed(monkeypatch, tmp_path, DEBLUR)
    code, out, _ = run_cli(["ai", "deblur", "A001_beach", "--force"])
    assert code == 0 and "A001_beach Deblur" in out
    assert len(fake_world.calls_to("MediaPoolItem.RemoveMotionBlur")) == 1
    assert "ai deblur" not in ai_state.load()


def test_subtitles_missing_language_pack_is_advisory(
    run_cli: Run,
    parse_toon_keys: Keys,
    fake_world: FakeWorld,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed(monkeypatch, tmp_path, SLATE)
    code, out, _ = run_cli(["ai", "subtitles", *POLL])
    keys = parse_toon_keys(out)
    assert code == 0 and "built-in languages still work" in keys["ai_extras"]
    assert "will fail" not in out
    code, out, _ = run_cli(["ai", "subtitles", *POLL, "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert len(fake_world.calls_to("Timeline.CreateSubtitlesFromAudio")) == 1


def test_scene_cuts_has_no_preflight_field_but_remembers_errors(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["ai", "scene-cuts", *POLL])
    assert code == 0 and "ai_extras" not in out and "AI Extras" not in out
    fake_world.inject("Timeline.DetectSceneCuts", False)
    code, out, _ = run_cli(["ai", "scene-cuts", *POLL, "--force"])
    assert code == 1 and "NOT_AVAILABLE" in out
    assert ai_state.load()["ai scene-cuts"].code == "NOT_AVAILABLE"
