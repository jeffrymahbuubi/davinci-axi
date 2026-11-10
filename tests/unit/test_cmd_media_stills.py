"""`media stills` (U3): stills of pool clips through a scratch timeline that is deleted again."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import media_extra, media_stills, stills_common
from davinci_axi.commands.media_stills import MEDIA_STILLS_SPEC, SCRATCH_PREFIX
from davinci_axi.fake import (
    FakeProject,
    FakeWorld,
    ext_edit_extra,
    ext_media_edit,
    ext_render,
    ext_timeline_edit,
)
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

SPEC: CommandSpec = replace(
    media_extra.SPEC,
    subcommands={**media_extra.SPEC.subcommands, "stills": MEDIA_STILLS_SPEC},
)
EXPORT = "Project.ExportCurrentFrameAsStill"
DELETE = "MediaPool.DeleteTimelines"
CREATE = "MediaPool.CreateTimelineFromClips"
SET_CURRENT = "Project.SetCurrentTimeline"
FOOTAGE = "Master/Footage"


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_render.extend()
    ext_timeline_edit.extend()
    ext_edit_extra.extend()
    ext_media_edit.extend()
    monkeypatch.setitem(registry.COMMANDS, "media", f"{__name__}:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")


class _Sips:
    """Stands in for `subprocess.run`: records the call and writes the --out file."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        Path(command[command.index("--out") + 1]).write_text("jpeg\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def sips(monkeypatch: pytest.MonkeyPatch) -> _Sips:
    fake = _Sips()
    monkeypatch.setattr(stills_common.shutil, "which", lambda _: "/usr/bin/sips")
    monkeypatch.setattr(stills_common.subprocess, "run", fake)
    return fake


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _manifest(folder: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = json.loads(
        (folder / "manifest.json").read_text(encoding="utf-8")
    )
    return rows


def _names(world: FakeWorld) -> list[str]:
    return [t.name for t in _project(world).timelines]


def test_dry_run_plans_and_skips_audio_without_any_write(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, sips: _Sips, tmp_path: Path
) -> None:
    folder = tmp_path / "sb"
    code, out, _ = run_cli(["media", "stills", "A001_beach,music_bed", str(folder)])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false"
    assert "stills" in out and "music_bed (Audio)" in keys["skipped"]
    assert keys["clips"] == "1" and SCRATCH_PREFIX in keys["warning"]
    assert "--force" in out and "A001_beach,music_bed" in out
    assert not folder.exists() and sips.calls == []
    for method in (CREATE, DELETE, SET_CURRENT, EXPORT):
        assert fake_world.calls_to(method) == []


def test_force_builds_exports_and_removes_the_scratch_timeline(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, sips: _Sips, tmp_path: Path
) -> None:
    folder = tmp_path / "sb"
    before = _names(fake_world)
    code, out, _ = run_cli(["media", "stills", "A001_beach,music_bed", str(folder), "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["stills"] == "1"
    assert keys["snapshot"] == "none"
    creates = fake_world.calls_to(CREATE)
    assert len(creates) == 1 and len(creates[0][1]) == 1  # type: ignore[arg-type]
    assert str(creates[0][0]).startswith(SCRATCH_PREFIX)
    switches = [getattr(a[0], "name", None) for a in fake_world.calls_to(SET_CURRENT)]
    assert switches[-1] == "Edit v1" and str(switches[0]).startswith(SCRATCH_PREFIX)
    assert len(fake_world.calls_to(EXPORT)) == 1 and len(fake_world.calls_to(DELETE)) == 1
    assert _names(fake_world) == before
    current = _project(fake_world).current_timeline
    assert current is not None and current.name == "Edit v1"
    rows = _manifest(folder)
    assert rows[0]["item"] == "A001_beach" and rows[0]["file"] == "01-V1-A001_beach-50.jpg"
    assert sorted(p.name for p in folder.iterdir()) == ["01-V1-A001_beach-50.jpg", "manifest.json"]
    assert len(sips.calls) == 1 and fake_world.page_changes()[0] == "color"


def test_at_flags_reach_sips_and_the_manifest(
    run_cli: Run, parse_toon_keys: Keys, sips: _Sips, tmp_path: Path
) -> None:
    folder = tmp_path / "sb"
    code, out, _ = run_cli(
        [
            "media",
            "stills",
            "A001_beach",
            str(folder),
            "--at",
            "0,100",
            "--max-size",
            "200",
            "--quality",
            "33",
            "--force",
        ]
    )
    assert code == 0 and parse_toon_keys(out)["stills"] == "2"
    assert sips.calls[0][5:9] == ["formatOptions", "33", "-Z", "200"]
    frames = [row["frame"] for row in _manifest(folder)]
    assert frames[1] - frames[0] == 119 and [r["pct"] for r in _manifest(folder)] == [0, 100]


def test_export_failure_is_api_failed_and_the_scratch_is_still_deleted(
    run_cli: Run, fake_world: FakeWorld, sips: _Sips, tmp_path: Path
) -> None:
    fake_world.inject(EXPORT, False)
    before = _names(fake_world)
    code, out, _ = run_cli(["media", "stills", "A001_beach", str(tmp_path / "sb"), "--force"])
    assert code == 1 and "API_FAILED" in out
    assert len(fake_world.calls_to(DELETE)) == 1 and _names(fake_world) == before
    current = _project(fake_world).current_timeline
    assert current is not None and current.name == "Edit v1"


def test_failed_cleanup_is_reported(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, sips: _Sips, tmp_path: Path
) -> None:
    fake_world.inject(DELETE, False)
    code, out, _ = run_cli(["media", "stills", "A001_beach", str(tmp_path / "sb"), "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and "could not delete" in keys["cleanup"]
    assert f"timeline delete {SCRATCH_PREFIX}" in keys["cleanup"]


def test_folder_exports_every_video_clip(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, sips: _Sips, tmp_path: Path
) -> None:
    folder = tmp_path / "sb"
    code, out, _ = run_cli(["media", "stills", "--folder", FOOTAGE, str(folder), "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["stills"] == "3" and keys["clips"] == "3"
    assert [row["item"] for row in _manifest(folder)] == ["A001_beach", "A002_city", "A003_dusk"]
    assert len(fake_world.calls_to(EXPORT)) == 3 and len(fake_world.calls_to(CREATE)) == 1


def test_folder_dry_run_repeats_folder_and_flags_in_the_force_command(
    run_cli: Run, tmp_path: Path
) -> None:
    folder = str(tmp_path / "sb")
    code, out, _ = run_cli(["media", "stills", "--folder", FOOTAGE, folder, "--at", "10,90"])
    assert code == 0 and f"stills --folder {FOOTAGE} {folder} --at 10,90 --force" in out


def test_folder_with_clips_is_exit_2(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(
        ["media", "stills", "A001_beach", str(tmp_path / "sb"), "--folder", FOOTAGE]
    )
    assert code == 2 and "exactly one" in out and fake_world.calls == []


@pytest.mark.parametrize(
    "argv",
    [["A001_beach"], ["--folder", FOOTAGE]],
)
def test_a_missing_part_is_exit_2(run_cli: Run, fake_world: FakeWorld, argv: list[str]) -> None:
    code, out, _ = run_cli(["media", "stills", *argv])
    assert code == 2 and "VALIDATION_ERROR" in out and fake_world.calls == []


@pytest.mark.parametrize(
    "extra",
    [["--quality", "0"], ["--max-size", "10"], ["--columns", "0"], ["--at", "150"]],
)
def test_bad_flags_exit_2_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, extra: list[str]
) -> None:
    code, out, _ = run_cli(["media", "stills", "A001_beach", str(tmp_path / "sb"), *extra])
    assert code == 2 and "VALIDATION_ERROR" in out and fake_world.calls == []


def test_only_audio_clips_are_refused(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(["media", "stills", "music_bed", str(tmp_path / "sb"), "--force"])
    assert code == 2 and "video clip" in out and fake_world.calls_to(CREATE) == []


def test_no_snapshot_without_env_is_still_refused(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DAVINCI_AXI_ALLOW_UNSNAPSHOTTED", raising=False)
    code, out, _ = run_cli(
        ["media", "stills", "A001_beach", str(tmp_path / "sb"), "--force", "--no-snapshot"]
    )
    assert code == 1 and "WRITE_REFUSED" in out and fake_world.calls_to(CREATE) == []


def test_busy_folder_needs_overwrite(
    run_cli: Run, fake_world: FakeWorld, sips: _Sips, tmp_path: Path
) -> None:
    folder = tmp_path / "sb"
    folder.mkdir()
    (folder / "old.txt").write_text("x", encoding="utf-8")
    code, out, _ = run_cli(["media", "stills", "A001_beach", str(folder), "--force"])
    assert code == 1 and "FILE_EXISTS" in out and fake_world.calls == []
    code, _, _ = run_cli(["media", "stills", "A001_beach", str(folder), "--force", "--overwrite"])
    assert code == 0 and (folder / "manifest.json").exists()


def test_missing_sips_keeps_png_and_says_so(
    run_cli: Run, parse_toon_keys: Keys, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stills_common.shutil, "which", lambda _: None)
    folder = tmp_path / "sb"
    code, out, _ = run_cli(["media", "stills", "A001_beach", str(folder), "--force"])
    assert code == 0 and stills_common.SIPS_NOTE in parse_toon_keys(out)["note"]
    assert [row["file"] for row in _manifest(folder)] == ["01-V1-A001_beach-50.png"]


def test_sheet_is_composed_from_the_rows(
    run_cli: Run,
    parse_toon_keys: Keys,
    monkeypatch: pytest.MonkeyPatch,
    sips: _Sips,
    tmp_path: Path,
) -> None:
    pytest.importorskip("PIL")
    seen: list[tuple[int, int]] = []

    def compose(
        rows: list[stills_common.StillRow], folder: Path, sheet: Path, columns: int
    ) -> None:
        seen.append((len(rows), columns))
        sheet.write_text("sheet", encoding="utf-8")

    monkeypatch.setattr(media_stills, "compose_sheet", compose)
    sheet = tmp_path / "sheet.jpg"
    code, out, _ = run_cli(
        [
            "media",
            "stills",
            "--folder",
            FOOTAGE,
            str(tmp_path / "sb"),
            "--sheet",
            str(sheet),
            "--columns",
            "2",
            "--force",
        ]
    )
    assert code == 0 and seen == [(3, 2)] and "sheet" in parse_toon_keys(out)


def test_sheet_without_pillow_fails_before_connecting(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(sys.modules, "PIL", None)
    monkeypatch.setattr("davinci_axi.commands.stills_common.shutil.which", lambda _name: None)
    code, out, _ = run_cli(
        ["media", "stills", "A001_beach", str(tmp_path / "sb"), "--sheet", str(tmp_path / "s.jpg")]
    )
    assert code == 1 and "NOT_AVAILABLE" in out and fake_world.calls == []


def test_help_names_the_scratch_timeline() -> None:
    assert SCRATCH_PREFIX in "".join(MEDIA_STILLS_SPEC.notes)
    assert MEDIA_STILLS_SPEC.reads_only is False
