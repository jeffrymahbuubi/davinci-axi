from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import stills_common, timeline_extra, timeline_stills
from davinci_axi.commands.timeline_stills import TIMELINE_STILLS_SPEC
from davinci_axi.fake import FakeProject, FakeWorld, ext_render, ext_timeline_edit
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

SPEC: CommandSpec = replace(
    timeline_extra.SPEC,
    subcommands={**timeline_extra.SPEC.subcommands, "stills": TIMELINE_STILLS_SPEC},
)
EXPORT = "Project.ExportCurrentFrameAsStill"


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_render.extend()
    ext_timeline_edit.extend()
    monkeypatch.setitem(registry.COMMANDS, "timeline", f"{__name__}:SPEC")
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


def _manifest(folder: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = json.loads(
        (folder / "manifest.json").read_text(encoding="utf-8")
    )
    return rows


def test_default_exports_one_still_per_v1_item(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, sips: _Sips, tmp_path: Path
) -> None:
    folder = tmp_path / "sb"
    code, out, _ = run_cli(["timeline", "stills", str(folder)])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["stills"] == "3" and keys["timeline"] == "Edit v1"
    names = sorted(p.name for p in folder.iterdir())
    assert names == [
        "01-V1-clip-1-50.jpg",
        "02-V1-clip-2-50.jpg",
        "03-V1-clip-3-50.jpg",
        "manifest.json",
    ]
    rows = _manifest(folder)
    assert [row["frame"] for row in rows] == [86440, 86520, 86600]
    assert rows[0]["timecode"] == "01:00:01:16" and rows[0]["item"] == "V1:1"
    assert rows[0]["file"] == "01-V1-clip-1-50.jpg" and rows[0]["pct"] == 50
    assert len(fake_world.calls_to("Timeline.SetCurrentTimecode")) == 3
    assert len(sips.calls) == 3
    assert sips.calls[0][5:9] == ["formatOptions", "60", "-Z", "480"]
    pages = fake_world.page_changes()
    assert pages[0] == "color" and len(pages) == 2


def test_at_and_items_pick_the_frames(
    run_cli: Run, parse_toon_keys: Keys, sips: _Sips, tmp_path: Path
) -> None:
    folder = tmp_path / "sb"
    code, out, _ = run_cli(
        ["timeline", "stills", str(folder), "--at", "10,50,90", "--items", "V1:1"]
    )
    assert code == 0 and parse_toon_keys(out)["stills"] == "3"
    assert [row["frame"] for row in _manifest(folder)] == [86408, 86440, 86472]
    assert sum(p.suffix == ".jpg" for p in folder.iterdir()) == 3
    assert len(sips.calls) == 3


def test_size_and_quality_flags_reach_sips(run_cli: Run, sips: _Sips, tmp_path: Path) -> None:
    folder = str(tmp_path / "s")
    code, _, _ = run_cli(["timeline", "stills", folder, "--max-size", "200", "--quality", "33"])
    assert code == 0 and sips.calls[0][5:9] == ["formatOptions", "33", "-Z", "200"]


def test_zero_and_hundred_percent_stay_inside_the_item(
    run_cli: Run, sips: _Sips, tmp_path: Path
) -> None:
    folder = tmp_path / "s"
    code, _, _ = run_cli(["timeline", "stills", str(folder), "--at", "0,100", "--items", "V1:1"])
    assert code == 0
    assert [row["frame"] for row in _manifest(folder)] == [86400, 86479]


def test_busy_folder_needs_overwrite(run_cli: Run, sips: _Sips, tmp_path: Path) -> None:
    folder = tmp_path / "sb"
    folder.mkdir()
    (folder / "old.txt").write_text("x", encoding="utf-8")
    code, out, _ = run_cli(["timeline", "stills", str(folder)])
    assert code == 1 and "FILE_EXISTS" in out and "already holds files" in out
    assert sips.calls == []
    code, _, _ = run_cli(["timeline", "stills", str(folder), "--overwrite"])
    assert code == 0 and (folder / "manifest.json").exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--quality", "0"],
        ["--quality", "101"],
        ["--max-size", "10"],
        ["--columns", "0"],
        ["--at", "150"],
        ["--track", "0"],
        ["--items", "V1:1,,V1:2"],
        ["--items", "V1:1", "--track", "1"],
    ],
)
def test_bad_flags_exit_2_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, extra: list[str]
) -> None:
    code, out, _ = run_cli(["timeline", "stills", str(tmp_path / "sb"), *extra])
    assert code == 2 and "VALIDATION_ERROR" in out and fake_world.calls == []


def test_audio_items_are_refused(run_cli: Run, sips: _Sips, tmp_path: Path) -> None:
    code, out, _ = run_cli(["timeline", "stills", str(tmp_path / "sb"), "--items", "A1:1"])
    assert code == 2 and "video items" in out and not (tmp_path / "sb").exists()


def test_export_failure_is_api_failed_and_leaves_no_temp(
    run_cli: Run, fake_world: FakeWorld, sips: _Sips, tmp_path: Path
) -> None:
    fake_world.inject(EXPORT, False)
    folder = tmp_path / "sb"
    code, out, _ = run_cli(["timeline", "stills", str(folder)])
    assert code == 1 and "API_FAILED" in out
    assert [p.name for p in folder.iterdir()] == []


def test_empty_track_reports_zero_stills(
    run_cli: Run, parse_toon_keys: Keys, sips: _Sips, tmp_path: Path
) -> None:
    folder = tmp_path / "sb"
    code, out, _ = run_cli(["timeline", "stills", str(folder), "--timeline", "Edit v2"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["stills"] == "0" and "0 stills" in keys["files"]
    assert not folder.exists() and sips.calls == []


def test_other_timeline_is_made_current_and_restored(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, sips: _Sips, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "stills", str(tmp_path / "sb"), "--timeline", "Edit v2"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["timeline_restored"] == "true"
    project = fake_world.resolve.pm.current_project
    assert isinstance(project, FakeProject) and project.current_timeline is not None
    assert project.current_timeline.name == "Edit v1"
    switches = [
        getattr(args[0], "name", None) for args in fake_world.calls_to("Project.SetCurrentTimeline")
    ]
    assert switches == ["Edit v2", "Edit v1"]


def test_missing_sips_keeps_png_and_says_so(
    run_cli: Run, parse_toon_keys: Keys, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stills_common.shutil, "which", lambda _: None)
    folder = tmp_path / "sb"
    code, out, _ = run_cli(["timeline", "stills", str(folder), "--items", "V1:1"])
    assert code == 0 and stills_common.SIPS_NOTE in parse_toon_keys(out)["note"]
    assert [row["file"] for row in _manifest(folder)] == ["01-V1-clip-1-50.png"]
    assert sorted(p.name for p in folder.iterdir()) == ["01-V1-clip-1-50.png", "manifest.json"]


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

    monkeypatch.setattr(timeline_stills, "compose_sheet", compose)
    sheet = tmp_path / "sheet.jpg"
    code, out, _ = run_cli(
        ["timeline", "stills", str(tmp_path / "sb"), "--sheet", str(sheet), "--columns", "2"]
    )
    assert code == 0 and seen == [(3, 2)] and "sheet" in parse_toon_keys(out)


def test_sheet_without_pillow_fails_before_connecting(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(sys.modules, "PIL", None)
    monkeypatch.setattr("davinci_axi.commands.stills_common.shutil.which", lambda _name: None)
    code, out, _ = run_cli(
        ["timeline", "stills", str(tmp_path / "sb"), "--sheet", str(tmp_path / "s.jpg")]
    )
    assert code == 1 and "NOT_AVAILABLE" in out and fake_world.calls == []
