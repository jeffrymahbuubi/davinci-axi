from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_render
from davinci_axi.fake.core import FakeProject

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

START = 108000
MARKS = {"video": {"in": 100, "out": 200}}  # relative to the timeline start (F32)


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_render.extend()
    monkeypatch.setitem(registry.COMMANDS, "render", "davinci_axi.commands.render:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    path = (tmp_path / "out").resolve()
    path.mkdir()
    return path


def _mark(world: FakeWorld) -> None:
    project: FakeProject | None = world.resolve.pm.current_project
    assert project is not None and project.current_timeline is not None
    timeline = project.current_timeline
    timeline.start_frame, timeline.end_frame = START, START + 240
    timeline.mark_in_out = dict(MARKS)


def _add(out_dir: Path, *extra: str) -> list[str]:
    return ["render", "add", "--target-dir", str(out_dir), *extra]


def test_marks_warn_in_the_dry_run_and_write_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    _mark(fake_world)
    code, out, err = run_cli(_add(out_dir))
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["applied"] == "false"
    assert "renders that range" in keys["warning"]
    assert "100-200 (absolute 108100-108200)" in keys["warning"]
    assert keys["timeline_marks"] == "100-200"
    assert keys["timeline_marks_abs"] == "108100-108200"
    assert "settings_note" in keys
    assert "note" not in keys
    assert fake_world.write_calls() == []


def test_all_frames_drops_the_warning_and_sends_select_all_frames(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    _mark(fake_world)
    code, out, _ = run_cli(_add(out_dir, "--all-frames"))
    keys = parse_toon_keys(out)
    assert code == 0 and "warning" not in keys and "timeline_marks" not in keys
    assert "--all-frames --force" in out
    code, out, _ = run_cli(_add(out_dir, "--all-frames", "--force"))
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    sent = fake_world.calls_to("Project.SetRenderSettings")[0][0]
    assert isinstance(sent, dict) and sent["SelectAllFrames"] is True


def test_explicit_mark_range_is_a_note_not_a_warning(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    _mark(fake_world)
    code, out, _ = run_cli(_add(out_dir, "--set", "MarkIn=108100,MarkOut=108200"))
    keys = parse_toon_keys(out)
    assert code == 0 and "warning" not in keys and "timeline_marks" not in keys
    assert "--keep-marks" in keys["note"]
    assert "relative to the timeline start" in keys["note"]


def test_without_marks_there_is_neither_warning_nor_note(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, out, _ = run_cli(_add(out_dir))
    keys = parse_toon_keys(out)
    assert code == 0
    assert not {"warning", "timeline_marks", "note"} & keys.keys()


def test_marks_warning_joins_the_bitrate_warning(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    _mark(fake_world)
    _, out, _ = run_cli(_add(out_dir, "--set", "VideoQuality=20000000"))
    warning = parse_toon_keys(out)["warning"]
    assert "bits per second" in warning and "renders that range" in warning
