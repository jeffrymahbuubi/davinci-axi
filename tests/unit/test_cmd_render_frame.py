from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import render_frame
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeWorld, ext_render
from davinci_axi.fake.core import FakeProject

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register_render(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_render.extend()
    monkeypatch.setitem(registry.COMMANDS, "render", "davinci_axi.commands.render:SPEC")


class _Sips:
    """A stand-in for `subprocess.run` that records the call and writes the --out file."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if self.returncode == 0:
            Path(command[command.index("--out") + 1]).write_text("fake jpeg\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, self.returncode, "", self.stderr)


@pytest.fixture
def sips(monkeypatch: pytest.MonkeyPatch) -> _Sips:
    fake = _Sips()
    monkeypatch.setattr(render_frame.shutil, "which", lambda _: "/usr/bin/sips")
    monkeypatch.setattr(render_frame.subprocess, "run", fake)
    return fake


@pytest.fixture
def exports(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every path `ExportCurrentFrameAsStill` is asked for."""
    seen: list[str] = []
    original = FakeProject.ExportCurrentFrameAsStill  # type: ignore[attr-defined]

    def export(self: FakeProject, filePath: str) -> bool:
        seen.append(filePath)
        return bool(original(self, filePath))

    monkeypatch.setattr(FakeProject, "ExportCurrentFrameAsStill", export, raising=False)
    return seen


def test_jpg_target_exports_directly(
    run_cli: Run, parse_toon_keys: Keys, exports: list[str], tmp_path: Path
) -> None:
    target = tmp_path / "f.jpg"
    code, out, _ = run_cli(["render", "frame", str(target)])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["format"] == "jpg"
    assert exports == [str(target)]
    assert "converted_with" not in out and "quality" not in out


def test_quality_exports_a_temp_png_and_converts_with_sips(
    run_cli: Run, parse_toon_keys: Keys, exports: list[str], sips: _Sips, tmp_path: Path
) -> None:
    target = tmp_path / "f.jpg"
    code, out, _ = run_cli(["render", "frame", str(target), "--quality", "70"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["converted_with"] == "sips" and keys["quality"] == "70"
    assert exports == [str(tmp_path / ".f.axi-tmp.png")]
    assert len(sips.calls) == 1
    call = sips.calls[0]
    assert call[:5] == ["sips", "-s", "format", "jpeg", "-s"]
    assert call[5:7] == ["formatOptions", "70"]
    assert call[-1] == str(target)
    assert target.read_text(encoding="utf-8") == "fake jpeg\n"
    assert not (tmp_path / ".f.axi-tmp.png").exists()


def test_a_resolve_refusal_of_jpg_falls_back_to_png_and_sips(
    run_cli: Run,
    parse_toon_keys: Keys,
    monkeypatch: pytest.MonkeyPatch,
    sips: _Sips,
    tmp_path: Path,
) -> None:
    original = FakeProject.ExportCurrentFrameAsStill  # type: ignore[attr-defined]
    seen: list[str] = []

    def export(self: FakeProject, filePath: str) -> bool:
        seen.append(filePath)
        return False if filePath.endswith(".jpg") else bool(original(self, filePath))

    monkeypatch.setattr(FakeProject, "ExportCurrentFrameAsStill", export, raising=False)
    target = tmp_path / "f.jpg"
    code, out, _ = run_cli(["render", "frame", str(target)])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["converted_with"] == "sips"
    assert "Resolve refused .jpg" in keys["note"]
    assert seen == [str(target), str(tmp_path / ".f.axi-tmp.png")]
    assert sips.calls[0][5:7] == ["formatOptions", "80"]
    assert target.exists() and not (tmp_path / ".f.axi-tmp.png").exists()


def test_missing_sips_is_api_failed_and_leaves_no_temp_file(
    run_cli: Run, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(render_frame.shutil, "which", lambda _: None)
    code, out, _ = run_cli(["render", "frame", str(tmp_path / "f.jpg"), "--quality", "70"])
    assert code == 1 and "API_FAILED" in out and "sips is not available" in out
    assert list(tmp_path.iterdir()) == []


def test_sips_failure_reports_its_stderr(
    run_cli: Run, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(render_frame.shutil, "which", lambda _: "/usr/bin/sips")
    monkeypatch.setattr(render_frame.subprocess, "run", _Sips(returncode=13, stderr="bad image"))
    code, out, _ = run_cli(["render", "frame", str(tmp_path / "f.jpg"), "--quality", "70"])
    assert code == 1 and "API_FAILED" in out and "bad image" in out
    assert list(tmp_path.iterdir()) == []


def test_png_refused_by_resolve_is_still_api_failed(
    run_cli: Run, fake_world: FakeWorld, sips: _Sips, tmp_path: Path
) -> None:
    fake_world.inject("Project.ExportCurrentFrameAsStill", False)
    code, out, _ = run_cli(["render", "frame", str(tmp_path / "f.jpg")])
    assert code == 1 and "API_FAILED" in out
    assert sips.calls == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("name", "extra", "needle"),
    [
        ("f.png", ["--format", "tif"], "contradicts"),
        ("f.jpg", ["--quality", "101"], "1-100"),
        ("f.jpg", ["--quality", "0"], "1-100"),
        ("f.png", ["--quality", "80"], "jpg only"),
        ("f.bmp", [], "png, jpg, tif"),
        ("f", [], "png, jpg, tif"),
    ],
)
def test_bad_format_or_quality_exits_2_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, name: str, extra: list[str], needle: str
) -> None:
    code, out, _ = run_cli(["render", "frame", str(tmp_path / name), *extra])
    assert code == 2 and needle in out and fake_world.calls == []


def test_format_names_the_format_of_an_unknown_suffix(
    run_cli: Run, parse_toon_keys: Keys, exports: list[str], tmp_path: Path
) -> None:
    target = tmp_path / "f.frame"
    code, out, _ = run_cli(["render", "frame", str(target), "--format", "tif"])
    assert code == 0 and parse_toon_keys(out)["format"] == "tif"
    assert exports == [str(target)]


def test_suffix_aliases_map_to_a_format(
    run_cli: Run, parse_toon_keys: Keys, tmp_path: Path
) -> None:
    for name, fmt in (("a.jpeg", "jpg"), ("b.tiff", "tif"), ("c.TIF", "tif")):
        code, out, _ = run_cli(["render", "frame", str(tmp_path / name)])
        assert code == 0 and parse_toon_keys(out)["format"] == fmt


def test_png_behaviour_is_unchanged(
    run_cli: Run, parse_toon_keys: Keys, exports: list[str], tmp_path: Path
) -> None:
    target = tmp_path / "f.png"
    code, out, _ = run_cli(["render", "frame", str(target)])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["format"] == "png" and exports == [str(target)]
    assert "converted_with" not in out and "note" not in out


def test_convert_with_sips_maps_the_format_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _Sips()
    monkeypatch.setattr(render_frame.shutil, "which", lambda _: "/usr/bin/sips")
    monkeypatch.setattr(render_frame.subprocess, "run", fake)
    src = tmp_path / "a.png"
    render_frame.convert_with_sips(src, tmp_path / "a.jpg", "jpg", None)
    assert fake.calls[0][:7] == ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80"]
    monkeypatch.setattr(render_frame.shutil, "which", lambda _: None)
    with pytest.raises(AxiError, match="sips is not available"):
        render_frame.convert_with_sips(src, tmp_path / "a.jpg", "jpg", 50)


def test_timecode_is_always_reported(
    run_cli: Run, parse_toon_keys: Keys, exports: list[str], tmp_path: Path
) -> None:
    code, out, _ = run_cli(["render", "frame", str(tmp_path / "f.png")])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["timecode"].strip('"') == "01:00:00:00"
    assert "playhead_moved" not in out


def _deliver_stale(fake_world: FakeWorld, tc: str) -> None:
    """On the Deliver page, move the playhead; the fake viewer keeps the old frame (F30)."""
    fake_world.resolve.page = "deliver"
    timeline = fake_world.resolve.pm.current_project.current_timeline  # type: ignore[union-attr]
    assert timeline is not None and timeline.SetCurrentTimecode(tc)


def test_deliver_page_is_left_before_positioning(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _deliver_stale(fake_world, "01:00:02:00")
    target = tmp_path / "a.png"
    code, out, _ = run_cli(["render", "frame", str(target)])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["left_page"] == "deliver" and keys["page_restored"] == "true"
    assert keys["timecode"].strip('"') == "01:00:02:00"
    assert target.read_text(encoding="utf-8").endswith("@01:00:02:00\n")
    assert fake_world.page_changes() == ["edit", "color", "edit", "deliver"]
    assert fake_world.resolve.page == "deliver"


def test_two_stills_on_deliver_differ(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    for name, tc in (("a.png", "01:00:02:00"), ("b.png", "01:02:47:06")):
        _deliver_stale(fake_world, tc)
        assert run_cli(["render", "frame", str(tmp_path / name)])[0] == 0
    assert (tmp_path / "a.png").read_bytes() != (tmp_path / "b.png").read_bytes()


def test_the_fake_viewer_is_stale_on_deliver_without_the_fix(fake_world: FakeWorld) -> None:
    _deliver_stale(fake_world, "01:00:02:00")
    project = fake_world.resolve.pm.current_project
    assert project is not None and project.current_timeline is not None
    assert project.current_timeline.current_tc == "01:00:02:00"
    assert project.current_timeline.__dict__["viewer_tc"] == "01:00:00:00"


def test_other_pages_do_not_leave_the_page(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["render", "frame", str(tmp_path / "f.png")])
    assert code == 0 and "left_page" not in out
    assert fake_world.page_changes() == ["color", "edit"]
    assert parse_toon_keys(out)["page_restored"] == "true"


def test_tc_on_deliver_moves_the_playhead_and_the_still(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _deliver_stale(fake_world, "01:00:02:00")
    target = tmp_path / "f.png"
    code, out, _ = run_cli(["render", "frame", str(target), "--tc", "01:00:05:00"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["playhead_moved"] == "true" and keys["left_page"] == "deliver"
    assert target.read_text(encoding="utf-8").endswith("@01:00:05:00\n")


def test_open_page_failure_on_deliver_is_api_failed_and_writes_nothing(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _deliver_stale(fake_world, "01:00:02:00")
    fake_world.inject("Resolve.OpenPage", False)
    code, out, _ = run_cli(["render", "frame", str(tmp_path / "f.png")])
    assert code == 1 and "API_FAILED" in out and "OpenPage" in out
    assert list(tmp_path.iterdir()) == []
