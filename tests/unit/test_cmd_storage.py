"""`storage` browse, add, mattes and reveal verbs (U21) against the fake."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import storage
from davinci_axi.fake import FakeProject, FakeWorld, ext_media_edit, ext_storage, ext_writes_core
from davinci_axi.fake.media import FakeFolder, FakeMediaPoolItem
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

SAVE, EXPORT = "ProjectManager.SaveProject", "ProjectManager.ExportProject"
SET_FOLDER = "MediaPool.SetCurrentFolder"
ADD = "MediaStorage.AddItemListToMediaPool"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Neither the registry line nor the EXTENSIONS entry exists in the worktree."""
    ext_writes_core.extend()
    ext_media_edit.extend()
    ext_storage.extend()
    ext_storage.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "storage", "davinci_axi.commands.storage:SPEC")
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


def _footage(world: FakeWorld) -> FakeFolder:
    return _root(world).subfolders[0]


def _clip(world: FakeWorld, name: str) -> FakeMediaPoolItem:
    for folder in [_root(world), *_root(world).subfolders]:
        for clip in folder.clips:
            if clip.name == name:
                return clip
    raise LookupError(name)


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _untouched(world: FakeWorld, tmp_path: Path) -> None:
    assert world.write_calls() == [] and not _snapshots(tmp_path).exists()


def _file(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"matte")
    return str(path)


# ---- reads --------------------------------------------------------------------------------


def test_volumes_and_ls_read_without_writing(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["storage", "volumes"])
    assert code == 0 and "volumes[1]{path}:" in out and "/Volumes/Media" in out
    code, out, _ = run_cli(["storage", "ls", "/Volumes/Media"])
    assert code == 0 and "dirs[1]{path}:" in out and "files[2]{path}:" in out
    assert "/Volumes/Media/a.mov" in out and "Shoot1" in out
    code, out, _ = run_cli(["storage", "ls", "/Volumes/Media", "--files"])
    assert "files[2]" in out and "dirs" not in out
    code, out, _ = run_cli(["storage", "ls", "/Volumes/Media", "--dirs", "--json"])
    assert '"dirs"' in out and '"files"' not in out
    code, out, _ = run_cli(["storage", "ls", "/Volumes/Nowhere"])
    assert code == 0 and "0 folders in /Volumes/Nowhere" in out and "0 files" in out
    code, out, _ = run_cli(["storage", "ls", "/Volumes/Media", "--limit", "1", "--files"])
    assert "files[1]" in out and "--limit 2" in out
    _untouched(fake_world, tmp_path)


def test_volumes_empty_state(run_cli: Run, fake_world: FakeWorld) -> None:
    ext_storage.storage_of(fake_world).volumes.clear()
    code, out, _ = run_cli(["storage", "volumes"])
    assert code == 0 and "0 volumes in Media Storage" in out


# ---- add ----------------------------------------------------------------------------------


def test_add_dry_run_then_force_restores_the_current_folder(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = ["storage", "add", "/Volumes/Media/a.mov", "--folder", "Master/Footage"]
    code, out, _ = run_cli(args)
    assert code == 0 and _keys(out)["applied"] == "false" and "--force" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    keys = _keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["added"] == "a"
    assert keys["folder_restored"] == "true" and keys["snapshot"] != "none"
    assert fake_world.write_calls() == [SAVE, EXPORT, SET_FOLDER, ADD, SET_FOLDER]
    assert [c.name for c in _footage(fake_world).clips][-1] == "a"
    assert _project(fake_world).media_pool.current is _root(fake_world)


def test_add_a_folder_adds_every_file_in_it(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["storage", "add", "/Volumes/Media", "--force"])
    assert code == 0 and _keys(out)["added"] == '"a, b, c"'
    assert "folder_restored" not in out
    assert [c.name for c in _root(fake_world).clips][-3:] == ["a", "b", "c"]
    assert _clip(fake_world, "b").properties["Type"] == "Audio"


def test_add_passes_the_frame_range(run_cli: Run, fake_world: FakeWorld) -> None:
    args = ["storage", "add", "/Volumes/Media/a.mov", "--in-frame", "5", "--out-frame", "20"]
    assert run_cli([*args, "--force"])[0] == 0
    sent = fake_world.calls_to(ADD)[0][0]
    assert sent == [{"media": "/Volumes/Media/a.mov", "startFrame": 5, "endFrame": 20}]


@pytest.mark.parametrize(
    "extra", [["--in-frame", "5"], ["--out-frame", "9"], ["--in-frame", "9", "--out-frame", "5"]]
)
def test_add_bad_frame_range_is_exit_2_before_any_write(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, extra: list[str]
) -> None:
    code, _, _ = run_cli(["storage", "add", "/Volumes/Media/a.mov", *extra, "--force"])
    assert code == 2
    _untouched(fake_world, tmp_path)


def test_add_unknown_path_fails_after_the_snapshot(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["storage", "add", "/Volumes/Nowhere/x.mov", "--force"])
    assert code == 1 and "API_FAILED" in out and "snapshot" in out


def test_add_snapshot_failure_makes_no_media_storage_call(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject(SAVE, False)
    code, out, _ = run_cli(["storage", "add", "/Volumes/Media/a.mov", "--force"])
    assert code == 1 and "SNAPSHOT_FAILED" in out
    assert fake_world.calls_to(ADD) == []


def test_add_unknown_folder_is_not_found(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["storage", "add", "/Volumes/Media/a.mov", "--folder", "Master/Nope"])
    assert code == 1 and "NOT_FOUND" in out and "Master/Footage" in out


# ---- reveal -------------------------------------------------------------------------------


def test_reveal_takes_no_snapshot_and_is_not_read_back(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = ["storage", "reveal", "/Volumes/Media/a.mov"]
    code, out, _ = run_cli(args)
    assert code == 0 and _keys(out)["applied"] == "false"
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    keys = _keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] == "none"
    assert "not read back" in keys["note"]
    assert fake_world.write_calls() == ["MediaStorage.RevealInStorage"]
    assert ext_storage.storage_of(fake_world).revealed == ["/Volumes/Media/a.mov"]
    code, out, _ = run_cli(["storage", "reveal", "/Volumes/Nowhere", "--force"])
    assert code == 1 and "API_FAILED" in out


# ---- mattes -------------------------------------------------------------------------------


def test_clip_mattes_add_list_delete(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    matte = _file(tmp_path, "m.png")
    code, out, _ = run_cli(["storage", "mattes", "list", "A001_beach"])
    assert code == 0 and "0 mattes on clip A001_beach" in out
    add = ["storage", "mattes", "add", "A001_beach", matte]
    code, out, _ = run_cli(add)
    assert code == 0 and _keys(out)["applied"] == "false"
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    code, out, _ = run_cli([*add, "--eye", "left", "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    assert fake_world.calls_to("MediaStorage.AddClipMattesToMediaPool")[0][1:] == (
        [matte],
        "left",
    )
    code, out, _ = run_cli(["storage", "mattes", "list", "A001_beach"])
    assert matte in out
    code, out, _ = run_cli([*add, "--force"])  # already there: nothing to do
    assert code == 0 and _keys(out)["applied"] == "false"
    delete = ["storage", "mattes", "delete", "A001_beach", matte]
    code, out, _ = run_cli([*delete, "--force"])
    keys = _keys(out)
    assert code == 0 and keys["irreversible"] == "true" and keys["applied"] == "true"
    assert _clip(fake_world, "A001_beach").__dict__["mattes"] == []


def test_matte_errors(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(["storage", "mattes", "delete", "A001_beach", "/nope.png"])
    assert code == 1 and "NOT_FOUND" in out and "(none)" in out
    code, out, _ = run_cli(["storage", "mattes", "add", "A001_beach", "/nope.png", "--force"])
    assert code == 1 and "API_FAILED" in out
    code, _, _ = run_cli(["storage", "mattes", "add", "A001_beach", "/a.png", "--eye", "up"])
    assert code == 2
    code, _, _ = run_cli(["storage", "mattes", "add", "A001_beach", "a.png,,b.png"])
    assert code == 2
    code, out, _ = run_cli(["storage", "mattes", "list", "nope"])
    assert code == 1 and "NOT_FOUND" in out


def test_timeline_mattes_add_and_list(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    matte = _file(tmp_path, "tm.mov")
    code, out, _ = run_cli(["storage", "timeline-mattes", "list"])
    assert code == 0 and "0 timeline mattes in folder Master" in out
    args = ["storage", "timeline-mattes", "add", matte, "--folder", "Master/Footage"]
    assert run_cli(args)[0] == 0 and fake_world.write_calls() == []
    code, out, _ = run_cli([*args, "--force"])
    keys = _keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["added"] == "tm"
    assert keys["folder_restored"] == "true"
    code, out, _ = run_cli(["storage", "timeline-mattes", "list", "--folder", "Master/Footage"])
    assert "mattes[1]{id,name}:" in out and "tm" in out


def test_timeline_mattes_add_nothing_is_api_failed(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["storage", "timeline-mattes", "add", "/Volumes/Nowhere/x", "--force"])
    assert code == 1 and "API_FAILED" in out


# ---- spec and fake ------------------------------------------------------------------------


def _walk(
    path: tuple[str, ...], spec: CommandSpec
) -> Iterator[tuple[tuple[str, ...], CommandSpec]]:
    yield path, spec
    for name, child in spec.subcommands.items():
        yield from _walk((*path, name), child)


def test_every_storage_path_has_help_with_examples(run_cli: Run) -> None:
    paths = [path for path, _ in _walk(("storage",), storage.SPEC)]
    assert ("storage", "clone", "start") in paths and ("storage", "mattes", "add") in paths
    for path in paths:
        code, out, err = run_cli([*path, "--help"])
        assert code == 0 and err == "", path
        assert "examples[" in out, path
    for group, listed in (("clone", "start, status, stop"), ("mattes", "list, add, delete")):
        code, out, _ = run_cli(["storage", group])
        assert code == 2 and listed in out
    code, out, _ = run_cli(["storage"])
    assert code == 2 and "timeline-mattes" in out


def test_spec_methods_cover_every_call_the_verbs_make() -> None:
    declared = {m for _, spec in _walk(("storage",), storage.SPEC) for m in spec.methods}
    names = (
        "GetMountedVolumeList GetSubFolderList GetFileList RevealInStorage AddItemListToMediaPool "
        "AddClipMattesToMediaPool AddTimelineMattesToMediaPool StartCloneMedia "
        "SetCloneToolSettings StopCloneMedia GetCloneStatus"
    )
    assert {f"MediaStorage.{n}" for n in names.split()} <= declared
    assert {"Resolve.GetMediaStorage", "MediaPool.GetClipMatteList"} <= declared
    assert {"MediaPool.GetTimelineMatteList", "MediaPool.DeleteClipMattes"} <= declared
    for path, spec in _walk(("storage",), storage.SPEC):
        if spec.run is not None:
            assert spec.methods, path


def test_fake_storage_methods_honour_injection_and_validation(
    fake_world: FakeWorld, tmp_path: Path
) -> None:
    store = ext_storage.storage_of(fake_world)
    clip = _clip(fake_world, "A001_beach")
    assert store.GetSubFolderList("/nope") == [] and store.GetFileList("/nope") == []
    assert store.AddClipMattesToMediaPool(clip, []) is False
    assert store.AddClipMattesToMediaPool(clip, ["/Volumes/Media/a.mov"], "up") is False
    assert store.SetCloneToolSettings({"Bogus": 1}) is False
    assert store.StartCloneMedia("", ["/x"]) is False and store.StopCloneMedia() is False
    fake_world.inject("MediaStorage.AddItemListToMediaPool", [clip])
    assert store.AddItemListToMediaPool([]) == [clip]
    fake_world.inject("MediaStorage.AddTimelineMattesToMediaPool", "no")
    assert store.AddTimelineMattesToMediaPool(["/x"]) == []
    fake_world.fail.clear()
    ext_storage.add_storage_file(fake_world, "/Volumes/Media/d.mov")
    assert "/Volumes/Media/d.mov" in store.GetFileList("/Volumes/Media")
    fake_world.inject("Resolve.GetMediaStorage", None)
    assert fake_world.resolve.GetMediaStorage() is None  # type: ignore[attr-defined]
