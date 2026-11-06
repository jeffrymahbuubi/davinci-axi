"""Media file verbs (metadata export, folder import/export) and clip marks (U21)."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeProject, FakeWorld, ext_media_edit, ext_storage, ext_writes_core
from davinci_axi.fake.media import FakeFolder, FakeMediaPoolItem

Run = Callable[[list[str]], tuple[int, str, str]]

SAVE, EXPORT = "ProjectManager.SaveProject", "ProjectManager.ExportProject"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext_media_edit.extend()
    ext_storage.extend()
    monkeypatch.setitem(registry.COMMANDS, "media", "davinci_axi.commands.media_extra:SPEC")
    yield


def _keys(out: str) -> dict[str, str]:
    found = (re.match(r"^([A-Za-z_][\w.-]*): (.*)$", line) for line in out.splitlines())
    return {m.group(1): m.group(2) for m in found if m}


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _root(world: FakeWorld) -> FakeFolder:
    return _project(world).media_pool.root


def _clip(world: FakeWorld, name: str) -> FakeMediaPoolItem:
    for folder in [_root(world), *_root(world).subfolders]:
        for clip in folder.clips:
            if clip.name == name:
                return clip
    raise LookupError(name)


def _untouched(world: FakeWorld, tmp_path: Path) -> None:
    assert world.write_calls() == [] and not (tmp_path / "axi-home" / "snapshots").exists()


# ---- metadata export and folder export/import ---------------------------------------------


def test_metadata_export_is_disk_only_and_never_overwrites(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "meta.csv"
    code, out, _ = run_cli(["media", "metadata", "export", str(target)])
    keys = _keys(out)
    assert code == 0 and keys["clips"] == "4" and target.read_text().startswith("Clip Name")
    assert fake_world.write_calls() == ["MediaPool.ExportMetadata"]
    assert not (tmp_path / "axi-home" / "snapshots").exists()
    code, out, _ = run_cli(["media", "metadata", "export", str(target)])
    assert code == 1 and "FILE_EXISTS" in out
    args = ["media", "metadata", "export", str(target), "--overwrite", "--clips", "A001_beach"]
    code, out, _ = run_cli(args)
    assert code == 0 and _keys(out)["clips"] == "1"
    assert "A001_beach" in target.read_text() and "A002_city" not in target.read_text()
    assert fake_world.calls_to("MediaPool.ExportMetadata")[-1][1] == [
        _clip(fake_world, "A001_beach")
    ]


def test_metadata_export_errors(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(["media", "metadata", "export", str(tmp_path)])
    assert code == 2 and "directory" in out
    code, out, _ = run_cli(
        ["media", "metadata", "export", str(tmp_path / "a.csv"), "--clips", "no"]
    )
    assert code == 1 and "NOT_FOUND" in out and not (tmp_path / "a.csv").exists()
    fake_world.inject("MediaPool.ExportMetadata", False)
    code, out, _ = run_cli(["media", "metadata", "export", str(tmp_path / "b.csv")])
    assert code == 1 and "API_FAILED" in out
    fake_world.inject("MediaPool.ExportMetadata", True)
    code, out, _ = run_cli(["media", "metadata", "export", str(tmp_path / "c.csv")])
    assert code == 1 and "wrote no c.csv" in out


def test_folder_export_then_import_adds_a_folder(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "f.drb"
    args = ["media", "folder", "export", "Master/Footage", str(target)]
    code, out, _ = run_cli(args)
    assert code == 0 and _keys(out)["folder"] == "Master/Footage" and target.exists()
    assert fake_world.write_calls() == ["Folder.Export"]
    code, out, _ = run_cli(args)
    assert code == 1 and "FILE_EXISTS" in out
    assert run_cli([*args, "--overwrite"])[0] == 0
    fake_world.calls.clear()
    imp = ["media", "folder", "import", str(target)]
    code, out, _ = run_cli(imp)
    assert code == 0 and _keys(out)["applied"] == "false"
    assert fake_world.write_calls() == []
    code, out, _ = run_cli([*imp, "--source-path", "/Volumes/Media", "--force"])
    keys = _keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["new_folders"] == "Master/Footage 1"
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPool.ImportFolderFromFile"]
    assert fake_world.calls_to("MediaPool.ImportFolderFromFile")[0] == (
        str(target.resolve()),
        "/Volumes/Media",
    )
    assert [f.name for f in _root(fake_world).subfolders] == ["Footage", "Footage 1"]


def test_folder_import_errors(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(["media", "folder", "import", str(tmp_path / "no.drb"), "--force"])
    assert code == 1 and "NOT_FOUND" in out and fake_world.calls == []
    bad = tmp_path / "bad.drb"
    bad.write_text("nothing\n")
    code, out, _ = run_cli(["media", "folder", "import", str(bad), "--force"])
    assert code == 1 and "API_FAILED" in out
    code, out, _ = run_cli(["media", "folder", "export", "Master/Nope", str(tmp_path / "x.drb")])
    assert code == 1 and "NOT_FOUND" in out
    fake_world.inject("Folder.Export", False)
    code, out, _ = run_cli(["media", "folder", "export", "Master", str(tmp_path / "y.drb")])
    assert code == 1 and "API_FAILED" in out


def test_folder_import_snapshot_failure_makes_no_import_call(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    good = tmp_path / "g.drb"
    good.write_text("fake folder export: Dailies\n")
    fake_world.inject(SAVE, False)
    code, out, _ = run_cli(["media", "folder", "import", str(good), "--force"])
    assert code == 1 and "SNAPSHOT_FAILED" in out
    assert fake_world.calls_to("MediaPool.ImportFolderFromFile") == []


# ---- mark in/out --------------------------------------------------------------------------


def test_mark_set_and_clear_read_back_without_a_snapshot(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = ["media", "mark", "set", "A001_beach", "--in", "10", "--out", "50"]
    code, out, _ = run_cli(args)
    assert code == 0 and _keys(out)["applied"] == "false" and "all in" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    keys = _keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] == "none"
    assert fake_world.write_calls() == ["MediaPoolItem.SetMarkInOut"]
    assert fake_world.calls_to("MediaPoolItem.SetMarkInOut")[0] == (10, 50, "all")
    marks = _clip(fake_world, "A001_beach").mark_in_out
    assert marks == {"video": {"in": 10, "out": 50}, "audio": {"in": 10, "out": 50}}
    assert _keys(run_cli([*args, "--force"])[1])["applied"] == "false"
    code, out, _ = run_cli(["media", "mark", "clear", "A001_beach", "--type", "audio", "--force"])
    assert code == 0 and "audio marks,10-50,cleared" in out
    assert list(_clip(fake_world, "A001_beach").mark_in_out) == ["video"]
    code, out, _ = run_cli(["media", "mark", "clear", "A001_beach", "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    assert _clip(fake_world, "A001_beach").mark_in_out == {}
    assert (
        _keys(run_cli(["media", "mark", "clear", "A001_beach", "--force"])[1])["applied"] == "false"
    )


def test_mark_set_by_type_and_mixed_ranges(run_cli: Run, fake_world: FakeWorld) -> None:
    args = ["media", "mark", "set", "A001_beach", "--in", "1", "--out", "9", "--type", "video"]
    assert run_cli([*args, "--force"])[0] == 0
    assert _clip(fake_world, "A001_beach").mark_in_out == {"video": {"in": 1, "out": 9}}
    code, out, _ = run_cli(
        [
            "media",
            "mark",
            "set",
            "music_bed",
            "--in",
            "1",
            "--out",
            "9",
            "--type",
            "video",
            "--force",
        ]
    )
    assert code == 1 and "API_FAILED" in out  # an audio clip has no video marks
    clip = _clip(fake_world, "A001_beach")
    clip.mark_in_out["audio"] = {"in": 2, "out": 3}
    code, out, _ = run_cli(["media", "mark", "clear", "A001_beach"])
    assert code == 0 and "mixed-mixed" in out


@pytest.mark.parametrize(
    "extra",
    [
        ["--in", "5"],
        ["--out", "5"],
        ["--in", "9", "--out", "5"],
        ["--in", "-1", "--out", "5"],
        ["--in", "1", "--out", "5", "--type", "stereo"],
    ],
)
def test_mark_set_bad_arguments_are_exit_2(
    run_cli: Run, fake_world: FakeWorld, extra: list[str]
) -> None:
    code, _, _ = run_cli(["media", "mark", "set", "A001_beach", *extra, "--force"])
    assert code == 2 and fake_world.write_calls() == []


# ---- custom marker data -------------------------------------------------------------------


def _mark(world: FakeWorld) -> FakeMediaPoolItem:
    clip = _clip(world, "A001_beach")
    clip.markers[24] = {
        "color": "Green",
        "duration": 1,
        "note": "",
        "name": "Pick",
        "customData": "",
    }
    return clip


def test_custom_marker_set_then_get_by_frame_and_by_data(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    clip = _mark(fake_world)
    args = ["media", "marker", "custom", "set", "A001_beach", "--frame", "24", "note1"]
    code, out, _ = run_cli(args)
    assert code == 0 and _keys(out)["applied"] == "false"
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPoolItem.UpdateMarkerCustomData"]
    assert clip.markers[24]["customData"] == "note1"
    code, out, _ = run_cli(["media", "marker", "custom", "get", "A001_beach", "--frame", "24"])
    keys = _keys(out)
    assert code == 0 and keys["custom_data"] == "note1" and keys["name"] == "Pick"
    code, out, _ = run_cli(["media", "marker", "custom", "get", "A001_beach", "--data", "note1"])
    keys = _keys(out)
    assert code == 0 and keys["frame"] == "24" and keys["custom_data"] == "note1"
    assert _keys(run_cli([*args, "--force"])[1])["applied"] == "false"


def test_custom_marker_errors(run_cli: Run, fake_world: FakeWorld) -> None:
    _mark(fake_world)
    base = ["media", "marker", "custom"]
    code, out, _ = run_cli([*base, "get", "A001_beach"])
    assert code == 2 and "exactly one" in out
    code, _, _ = run_cli([*base, "get", "A001_beach", "--frame", "24", "--data", "x"])
    assert code == 2
    code, out, _ = run_cli([*base, "get", "A001_beach", "--frame", "99"])
    assert code == 1 and "NOT_FOUND" in out and "24" in out
    code, out, _ = run_cli([*base, "get", "A001_beach", "--data", "nope"])
    assert code == 1 and "NOT_FOUND" in out
    code, out, _ = run_cli([*base, "set", "A001_beach", "--frame", "99", "x", "--force"])
    assert code == 1 and "NOT_FOUND" in out and fake_world.write_calls() == []
    code, _, _ = run_cli([*base, "set", "A001_beach", "x", "--force"])
    assert code == 2  # --frame is required
    fake_world.inject("MediaPoolItem.UpdateMarkerCustomData", False)
    code, out, _ = run_cli([*base, "set", "A001_beach", "--frame", "24", "x", "--force"])
    assert code == 1 and "API_FAILED" in out
