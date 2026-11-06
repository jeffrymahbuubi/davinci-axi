"""The extra `media` verbs (U21) that act on clips, through the repointed `media_extra:SPEC`."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import media_extra
from davinci_axi.fake import FakeProject, FakeWorld, ext_media_edit, ext_storage, ext_writes_core
from davinci_axi.fake.media import FakeFolder, FakeMediaPoolItem
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

SAVE, EXPORT = "ProjectManager.SaveProject", "ProjectManager.ExportProject"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Neither the registry line nor the EXTENSIONS entry exists in the worktree."""
    ext_writes_core.extend()
    ext_media_edit.extend()
    ext_storage.extend()
    ext_storage.extend()  # idempotent
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


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _untouched(world: FakeWorld, tmp_path: Path) -> None:
    assert world.write_calls() == [] and not _snapshots(tmp_path).exists()


def _file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"media")
    return path


# ---- refresh and selection ----------------------------------------------------------------


def test_refresh_is_not_read_back_and_takes_no_snapshot(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["media", "refresh"])
    assert code == 0 and _keys(out)["applied"] == "false"
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli(["media", "refresh", "--force"])
    keys = _keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] == "none"
    assert "not read back" in keys["note"]
    assert fake_world.write_calls() == ["MediaPool.RefreshFolders"]
    fake_world.inject("MediaPool.RefreshFolders", False)
    code, out, _ = run_cli(["media", "refresh", "--force"])
    assert code == 1 and "API_FAILED" in out and "collaboration" in out


def test_selection_show_and_set(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(["media", "selection", "show"])
    assert code == 0 and "0 clips selected" in out
    code, out, _ = run_cli(["media", "selection", "set", "A001_beach"])
    assert code == 0 and _keys(out)["applied"] == "false"
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli(["media", "selection", "set", "A001_beach", "--force"])
    keys = _keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] == "none"
    assert fake_world.write_calls() == ["MediaPool.SetSelectedClip"]
    code, out, _ = run_cli(["media", "selection", "show"])
    assert "selected[1]{id,name,folder}:" in out and "A001_beach,Master/Footage" in out
    code, out, _ = run_cli(["media", "selection", "set", "A001_beach", "--force"])
    assert _keys(out)["applied"] == "false"  # already the whole selection
    fake_world.inject("MediaPool.SetSelectedClip", False)
    code, out, _ = run_cli(["media", "selection", "set", "A002_city", "--force"])
    assert code == 1 and "API_FAILED" in out


# ---- metadata and properties --------------------------------------------------------------


def test_third_party_set_and_show(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(["media", "third-party", "show", "A001_beach"])
    assert code == 0 and "0 third party metadata on clip A001_beach" in out
    args = ["media", "third-party", "set", "A001_beach", "Reel=A12,Take=3"]
    code, out, _ = run_cli(args)
    assert code == 0 and _keys(out)["applied"] == "false"
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPoolItem.SetThirdPartyMetadata"]
    assert _clip(fake_world, "A001_beach").third_party == {"Reel": "A12", "Take": "3"}
    code, out, _ = run_cli(["media", "third-party", "show", "A001_beach"])
    assert "Reel: A12" in out and 'Take: "3"' in out
    assert run_cli([*args, "--force"])[1].count("applied: false") == 1  # nothing left to change
    code, _, _ = run_cli(["media", "third-party", "set", "A001_beach", "Reel"])
    assert code == 2


def test_property_set_reads_back_each_key(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = ["media", "property", "set", "A001_beach", "Alpha mode=Straight,Comments=ok"]
    code, out, _ = run_cli(args)
    assert code == 0 and "Alpha mode" in out and _keys(out)["applied"] == "false"
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [SAVE, EXPORT] + ["MediaPoolItem.SetClipProperty"] * 2
    assert _clip(fake_world, "A001_beach").properties["Alpha mode"] == "Straight"


def test_property_set_treats_equal_numbers_as_equal(run_cli: Run, fake_world: FakeWorld) -> None:
    clip = _clip(fake_world, "A001_beach")
    clip.properties["Gain"] = "1"
    original = type(clip).SetClipProperty

    def normalising(self: FakeMediaPoolItem, name: str, value: str) -> bool:
        ok = original(self, name, value)
        self.properties[name] = f"{float(value)}"
        return ok

    type(clip).SetClipProperty = normalising  # type: ignore[method-assign]
    try:
        code, out, _ = run_cli(["media", "property", "set", "A001_beach", "Gain=2", "--force"])
    finally:
        type(clip).SetClipProperty = original  # type: ignore[method-assign]
    assert code == 0 and _keys(out)["applied"] == "true"


def test_property_set_failure_names_the_key_and_what_was_applied(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    clip = _clip(fake_world, "A001_beach")
    original = type(clip).SetClipProperty

    def refuse_second(self: FakeMediaPoolItem, name: str, value: str) -> bool:
        return False if name == "Two" else original(self, name, value)

    type(clip).SetClipProperty = refuse_second  # type: ignore[method-assign]
    try:
        code, out, _ = run_cli(["media", "property", "set", "A001_beach", "One=1,Two=2", "--force"])
    finally:
        type(clip).SetClipProperty = original  # type: ignore[method-assign]
    assert code == 1 and "could not set Two (already applied: One)" in out


# ---- replace, fullres, monitor ------------------------------------------------------------


def test_replace_changes_the_path_and_reads_it_back(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    new = _file(tmp_path, "new.mov")
    args = ["media", "replace", "A001_beach", str(new)]
    code, out, _ = run_cli(args)
    assert code == 0 and _keys(out)["applied"] == "false" and "path" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPoolItem.ReplaceClip"]
    assert _clip(fake_world, "A001_beach").properties["File Path"] == str(new)
    code, out, _ = run_cli([*args, "--force"])
    assert _keys(out)["applied"] == "false"  # already the file behind the clip


def test_replace_preserve_subclip_uses_the_other_call(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    new = _file(tmp_path, "new.mov")
    args = ["media", "replace", "A002_city", str(new), "--preserve-subclip", "--force"]
    assert run_cli(args)[0] == 0
    assert fake_world.write_calls()[-1] == "MediaPoolItem.ReplaceClipPreserveSubClip"
    assert _clip(fake_world, "A002_city").__dict__["subclip_preserved"] is True


def test_replace_missing_file_is_not_found_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["media", "replace", "A001_beach", str(tmp_path / "no.mov"), "--force"])
    assert code == 1 and "NOT_FOUND" in out and fake_world.calls == []


def test_replace_snapshot_failure_makes_no_replace_call(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject(SAVE, False)
    new = _file(tmp_path, "new.mov")
    code, out, _ = run_cli(["media", "replace", "A001_beach", str(new), "--force"])
    assert code == 1 and "SNAPSHOT_FAILED" in out
    assert fake_world.calls_to("MediaPoolItem.ReplaceClip") == []


def test_fullres_link_and_monitor_say_not_read_back(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    full = _file(tmp_path, "full.mov")
    args = ["media", "fullres", "link", "A001_beach", str(full)]
    code, out, _ = run_cli(args)
    assert code == 0 and "not read back" in out and _keys(out)["applied"] == "false"
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPoolItem.LinkFullResolutionMedia"]
    assert _clip(fake_world, "A001_beach").__dict__["full_resolution"] == str(full)
    code, out, _ = run_cli(["media", "monitor", "A002_city", "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    assert fake_world.write_calls()[-1] == "MediaPoolItem.MonitorGrowingFile"
    code, out, _ = run_cli(
        ["media", "fullres", "link", "A001_beach", str(tmp_path / "no"), "--force"]
    )
    assert code == 1 and "NOT_FOUND" in out
    fake_world.inject("MediaPoolItem.MonitorGrowingFile", False)
    code, out, _ = run_cli(["media", "monitor", "A002_city", "--force"])
    assert code == 1 and "API_FAILED" in out


# ---- stereo -------------------------------------------------------------------------------


def test_stereo_create_replaces_the_inputs_with_one_clip(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = ["media", "stereo", "create", "A001_beach", "A002_city"]
    code, out, _ = run_cli(args)
    assert code == 0 and _keys(out)["applied"] == "false" and "warning" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and _keys(out)["created"] == "Stereo 1"
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPool.CreateStereoClip"]
    names = [c.name for c in _root(fake_world).subfolders[0].clips]
    assert "Stereo 1" in names and "A001_beach" not in names and "A002_city" not in names
    code, out, _ = run_cli(["media", "stereo", "create", "A003_dusk", "A003_dusk"])
    assert code == 2


def test_stereo_create_refused_by_resolve_is_api_failed(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject("MediaPool.CreateStereoClip", None)
    code, out, _ = run_cli(["media", "stereo", "create", "A001_beach", "A002_city", "--force"])
    assert code == 1 and "API_FAILED" in out


# ---- spec, dispatch and fake --------------------------------------------------------------


def _walk(
    path: tuple[str, ...], spec: CommandSpec
) -> Iterator[tuple[tuple[str, ...], CommandSpec]]:
    yield path, spec
    for name, child in spec.subcommands.items():
        yield from _walk((*path, name), child)


def test_every_media_path_has_help_with_examples(run_cli: Run) -> None:
    paths = [path for path, _ in _walk(("media",), media_extra.SPEC)]
    for expected in (
        ("media", "folder", "move"),
        ("media", "proxy", "unlink"),
        ("media", "folder", "import"),
        ("media", "marker", "custom", "set"),
        ("media", "third-party", "show"),
        ("media", "stereo", "create"),
    ):
        assert expected in paths
    assert len(paths) == 26 + 24
    for path in paths:
        code, out, err = run_cli([*path, "--help"])
        assert code == 0 and err == "", path
        assert "examples[" in out, path
    for group, listed in (
        ("folder", "add, delete, move, import, export"),
        ("marker", "add, delete, custom"),
        ("metadata", "set, export"),
    ):
        code, out, _ = run_cli(["media", group])
        assert code == 2 and listed in out


def test_the_repointed_media_still_dispatches_the_phase_2_verbs(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["media"])
    assert code == 0 and "A001_beach" in out
    code, out, _ = run_cli(["media", "show", "A001_beach"])
    assert code == 0 and "id: clip-1" in out
    code, out, _ = run_cli(["media", "rename", "A001_beach", "--to", "beach_wide", "--force"])
    assert code == 0 and _clip(fake_world, "beach_wide").name == "beach_wide"
    code, out, _ = run_cli(["media", "folder", "add", "Dailies", "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    code, out, _ = run_cli(["media", "marker", "add", "A002_city", "--frame", "5", "--force"])
    assert code == 0 and 5 in _clip(fake_world, "A002_city").markers
    code, out, _ = run_cli(["media", "metadata", "set", "A002_city", "Comments=ok", "--force"])
    assert code == 0 and _clip(fake_world, "A002_city").metadata["Comments"] == "ok"


def test_spec_methods_cover_every_call_the_verbs_make() -> None:
    declared = {m for _, spec in _walk(("media",), media_extra.SPEC) for m in spec.methods}
    pool = (
        "RefreshFolders GetSelectedClips SetSelectedClip ExportMetadata ImportFolderFromFile "
        "CreateStereoClip ImportMedia MoveFolders"
    )
    item = (
        "GetThirdPartyMetadata SetThirdPartyMetadata SetClipProperty ReplaceClip "
        "ReplaceClipPreserveSubClip LinkFullResolutionMedia MonitorGrowingFile GetMarkInOut "
        "SetMarkInOut ClearMarkInOut UpdateMarkerCustomData GetMarkerCustomData "
        "GetMarkerByCustomData AddMarker"
    )
    assert {f"MediaPool.{n}" for n in pool.split()} <= declared
    assert {f"MediaPoolItem.{n}" for n in item.split()} <= declared
    assert "Folder.Export" in declared
    for path, spec in _walk(("media",), media_extra.SPEC):
        if spec.run is not None and path not in (("media",), ("media", "show")):
            assert spec.methods, path


def test_fake_extra_methods_honour_injection_and_validation(
    fake_world: FakeWorld, tmp_path: Path
) -> None:
    pool = _project(fake_world).media_pool
    clip = _clip(fake_world, "A001_beach")
    assert clip.SetThirdPartyMetadata({}) is False and clip.ReplaceClip("/no/file") is False
    assert clip.LinkFullResolutionMedia("/no/file") is False
    assert pool.SetSelectedClip(_clip(fake_world, "music_bed")) is True
    assert pool.ExportMetadata(str(tmp_path / "x.csv"), []) is False
    assert pool.ImportFolderFromFile("/no/file.drb") is False
    assert pool.DeleteClipMattes(clip, []) is False and pool.GetTimelineMatteList(pool.root) == []
    other = _clip(fake_world, "A002_city")
    assert pool.CreateStereoClip(clip, clip) is None
    fake_world.inject("MediaPool.CreateStereoClip", other)
    assert pool.CreateStereoClip(clip, other) is other
    fake_world.inject("MediaPoolItem.GetMarkerCustomData", 7)
    assert clip.GetMarkerCustomData(1) == "7"
