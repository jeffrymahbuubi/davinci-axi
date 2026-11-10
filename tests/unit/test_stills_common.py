from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

import pytest

from davinci_axi.commands import stills_common
from davinci_axi.commands.stills_common import (
    StillRow,
    check_sheet_available,
    compose_sheet,
    image_size,
    parse_at,
    sheet_cell,
    still_path,
    stills_result,
    write_manifest,
)
from davinci_axi.errors import AxiError


def test_parse_at_reads_percentages_in_order() -> None:
    assert parse_at("50") == [50]
    assert parse_at("10, 50,90") == [10, 50, 90]
    assert parse_at("0,100") == [0, 100]


@pytest.mark.parametrize("text", ["", "101", "-1", "10,,20", "ten", "10,10", "5.5"])
def test_parse_at_rejects_bad_text(text: str) -> None:
    with pytest.raises(AxiError) as caught:
        parse_at(text)
    assert caught.value.code == "VALIDATION_ERROR"


def test_still_path_slugs_the_name() -> None:
    folder = Path("/x")
    assert still_path(folder, 1, "V1", "clip-1", 50) == folder / "01-V1-clip-1-50.jpg"
    assert still_path(folder, 12, "V2", "My Clip/é.mov", 5).name == "12-V2-My_Clip__.mov-5.jpg"
    assert still_path(folder, 3, "V1", "", 0).name == "03-V1-clip-0.jpg"


def test_check_sheet_available_without_sheet_never_looks(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_pillow(monkeypatch, None)
    check_sheet_available(None)


def _no_pillow(monkeypatch: pytest.MonkeyPatch, ffmpeg: str | None) -> None:
    monkeypatch.setitem(sys.modules, "PIL", None)
    monkeypatch.setattr(stills_common.shutil, "which", lambda _name: ffmpeg)


def test_check_sheet_available_accepts_ffmpeg_without_pillow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_pillow(monkeypatch, "/usr/bin/ffmpeg")
    check_sheet_available("sheet.jpg")


def test_check_sheet_available_without_either_is_not_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_pillow(monkeypatch, None)
    with pytest.raises(AxiError) as caught:
        check_sheet_available("sheet.jpg")
    assert caught.value.code == "NOT_AVAILABLE"
    assert "Pillow" in str(caught.value) and "ffmpeg" in str(caught.value)


def _rows(count: int) -> list[StillRow]:
    return [
        StillRow(f"{n}.png", f"V1:{n}", f"id-{n}", f"c{n}", 86400 + n, "01:00:00:01", 50)
        for n in range(1, count + 1)
    ]


def test_compose_sheet_lays_out_a_grid(tmp_path: Path) -> None:
    image = pytest.importorskip("PIL.Image")
    rows = _rows(3)
    for row in rows:
        image.new("RGB", (64, 64), "red").save(tmp_path / row.file)
    sheet = tmp_path / "out" / "sheet.jpg"
    compose_sheet(rows, tmp_path, sheet, 2)
    with image.open(sheet) as made:
        assert made.size == (128, 2 * (64 + stills_common.LABEL_HEIGHT))


def test_compose_sheet_with_fewer_stills_than_columns_is_narrow(tmp_path: Path) -> None:
    image = pytest.importorskip("PIL.Image")
    rows = _rows(1)
    image.new("RGB", (64, 32), "blue").save(tmp_path / rows[0].file)
    compose_sheet(rows, tmp_path, tmp_path / "s.png", 6)
    with image.open(tmp_path / "s.png") as made:
        assert made.size == (64, 32 + stills_common.LABEL_HEIGHT)


def test_manifest_and_result_summary(tmp_path: Path) -> None:
    rows = _rows(8)
    for row in rows:
        (tmp_path / row.file).write_text("x", encoding="utf-8")
    manifest = write_manifest(tmp_path, rows)
    assert manifest.name == "manifest.json"
    assert '"item": "V1:1"' in manifest.read_text(encoding="utf-8")
    result = stills_result(tmp_path, rows, None, {"timeline": "T"})
    assert result.data["stills"] == 8 and result.data["timeline"] == "T"
    assert result.data["files"] == ["1.png", "...", "8.png"]
    assert result.data["note"] == stills_common.SIPS_NOTE and "sheet" not in result.data
    assert result.help and "manifest.json" in result.help[0]


def test_result_lists_few_files_in_full_and_handles_none(tmp_path: Path) -> None:
    rows = _rows(2)
    assert stills_result(tmp_path, rows, None, {}).data["files"] == ["1.png", "2.png"]
    empty = stills_result(tmp_path, [], None, {})
    assert empty.data["stills"] == 0 and "0 stills" in str(empty.data["files"])
    assert "manifest" not in empty.data and empty.help == []


def test_shrink_with_sips_reports_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 13, "", "bad image")

    monkeypatch.setattr(stills_common.subprocess, "run", failing)
    with pytest.raises(AxiError) as caught:
        stills_common.shrink_with_sips(
            tmp_path / "a.png", tmp_path / "a.jpg", max_size=64, quality=9
        )
    assert caught.value.code == "API_FAILED" and "bad image" in str(caught.value)


def _fake_ffmpeg(calls: list[list[str]], code: int = 0, stderr: str = "") -> object:
    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if code == 0:
            Path(command[-1]).write_bytes(b"jpg")
        return subprocess.CompletedProcess(command, code, "", stderr)

    return run


def _png(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
    )


def _jpeg(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 4) + b"\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHH", 17, 8, height, width)
    return b"\xff\xd8" + app0 + sof


def test_image_size_reads_png_and_jpeg_headers(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(_png(1080, 1920))
    (tmp_path / "b.jpg").write_bytes(_jpeg(1080, 1920))
    (tmp_path / "c.bin").write_bytes(b"not an image at all, really")
    assert image_size(tmp_path / "a.png") == (1080, 1920)
    assert image_size(tmp_path / "b.jpg") == (1080, 1920)
    assert image_size(tmp_path / "c.bin") is None
    assert image_size(tmp_path / "missing.png") is None


def test_sheet_cell_is_portrait_for_portrait_stills(tmp_path: Path) -> None:
    rows = _rows(2)
    assert sheet_cell(rows, tmp_path) == (320, 320)
    (tmp_path / rows[1].file).write_bytes(_png(1080, 1920))
    assert sheet_cell(rows, tmp_path) == (320, 570)
    (tmp_path / rows[0].file).write_bytes(_png(1920, 1080))
    assert sheet_cell(rows, tmp_path) == (320, 180)


def test_compose_sheet_ffmpeg_builds_the_tile_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for row in _rows(3):
        (tmp_path / row.file).write_bytes(_png(1080, 1920))
    calls: list[list[str]] = []
    monkeypatch.setattr(stills_common.subprocess, "run", _fake_ffmpeg(calls))
    sheet = tmp_path / "out" / "sheet.jpg"
    stills_common.compose_sheet_ffmpeg(_rows(3), tmp_path, sheet, 2, ffmpeg="ff")
    (command,) = calls
    graph = command[command.index("-filter_complex") + 1]
    assert command[0] == "ff" and command.count("-i") == 3 and command[-1] == str(sheet)
    assert command[command.index("-i") + 1] == str(tmp_path / "1.png")
    assert graph.endswith("[v0][v1][v2]concat=n=3:v=1:a=0,tile=2x2[out]")
    assert "[2:v]scale=320:570:force_original_aspect_ratio=decrease,pad=320:570:-1:-1" in graph
    assert sheet.exists()


def test_compose_sheet_ffmpeg_narrows_to_the_still_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(stills_common.subprocess, "run", _fake_ffmpeg(calls))
    stills_common.compose_sheet_ffmpeg(_rows(2), tmp_path, tmp_path / "s.jpg", 6)
    assert "tile=2x1[out]" in calls[0][calls[0].index("-filter_complex") + 1]


def test_compose_sheet_ffmpeg_refuses_no_rows(tmp_path: Path) -> None:
    with pytest.raises(AxiError) as caught:
        stills_common.compose_sheet_ffmpeg([], tmp_path, tmp_path / "s.jpg", 2)
    assert caught.value.code == "VALIDATION_ERROR"


def test_compose_sheet_dispatches_to_ffmpeg_without_pillow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_pillow(monkeypatch, "/opt/bin/ffmpeg")
    calls: list[list[str]] = []
    monkeypatch.setattr(stills_common.subprocess, "run", _fake_ffmpeg(calls))
    compose_sheet(_rows(2), tmp_path, tmp_path / "s.jpg", 2)
    assert calls[0][0] == "/opt/bin/ffmpeg"


def test_ffmpeg_failure_is_api_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(stills_common.subprocess, "run", _fake_ffmpeg(calls, 1, "bad filter"))
    with pytest.raises(AxiError) as caught:
        stills_common.compose_sheet_ffmpeg(_rows(1), tmp_path, tmp_path / "s.jpg", 2)
    assert caught.value.code == "API_FAILED" and "bad filter" in str(caught.value)


def test_stills_result_notes_the_unlabelled_sheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _rows(1)
    (tmp_path / rows[0].file).write_text("x", encoding="utf-8")
    write_manifest(tmp_path, rows)
    sheet = tmp_path / "sheet.jpg"
    sheet.write_bytes(b"jpg")
    _no_pillow(monkeypatch, "/usr/bin/ffmpeg")
    data = stills_result(tmp_path, rows, sheet, {}).data
    assert data["sheet_note"] == stills_common.FFMPEG_SHEET_NOTE
    monkeypatch.undo()
    pytest.importorskip("PIL")
    assert "sheet_note" not in stills_result(tmp_path, rows, sheet, {}).data
