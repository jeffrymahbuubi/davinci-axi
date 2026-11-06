"""`color still|album|lut|thumbnail` (U19) on the fake, plus the whole `color` help tree."""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import color_extras
from davinci_axi.fake import FakeWorld, ext_color
from davinci_axi.fake.ext_color_gallery import PNG_1X1, FakeGallery, set_thumbnail_settle
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]
SAVE, EXPORT = "ProjectManager.SaveProject", "ProjectManager.ExportProject"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_color.extend()
    ext_color.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "color", "davinci_axi.commands.color:SPEC")
    monkeypatch.setattr(color_extras, "THUMBNAIL_SETTLE_INTERVAL", 0)  # never sleep in tests
    yield


def _gallery(world: FakeWorld) -> FakeGallery:
    project = world.resolve.pm.current_project
    assert project is not None
    gallery = project.GetGallery()  # type: ignore[attr-defined]
    assert isinstance(gallery, FakeGallery)
    world.calls.clear()
    return gallery


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _grab(run_cli: Run, world: FakeWorld, *extra: str) -> None:
    code, out, _ = run_cli(["color", "still", "grab", *extra, "--force"])
    assert code == 0, out
    world.calls.clear()


def test_still_grab_opens_the_color_page_and_restores_it(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    gallery = _gallery(fake_world)
    code, out, err = run_cli(["color", "still", "grab"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "") and keys["applied"] == "false"
    assert fake_world.write_calls() == [] and fake_world.page_changes() == []
    assert not _snapshots(tmp_path).exists()
    code, out, _ = run_cli(["color", "still", "grab", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["page_restored"] == "true"
    assert fake_world.page_changes() == ["color", "edit"]
    assert fake_world.write_calls() == [
        SAVE,
        EXPORT,
        "Resolve.OpenPage",
        "Timeline.GrabStill",
        "Resolve.OpenPage",
    ]
    assert fake_world.resolve.page == "edit"
    assert [s.label for s in gallery.current.stills] == ["clip-1"]


def test_still_grab_all_counts_one_still_per_video_clip(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    gallery = _gallery(fake_world)
    code, out, _ = run_cli(["color", "still", "grab", "--all", "middle"])
    assert code == 0 and "one per video clip" in out
    code, out, _ = run_cli(["color", "still", "grab", "--all", "middle", "--force"])
    assert code == 0 and fake_world.calls_to("Timeline.GrabAllStills") == [(2,)]
    assert len(gallery.current.stills) == 4  # three video clips on V1 plus the title on V2
    assert "stills,0,4" in out
    assert run_cli(["color", "still", "grab", "--all", "last"])[0] == 2


def test_still_grab_on_the_color_page_changes_no_page(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.resolve.page = "color"
    code, out, _ = run_cli(["color", "still", "grab", "--force"])
    assert code == 0 and "page_restored" not in out and fake_world.page_changes() == []


def test_a_failed_snapshot_or_grab_leaves_the_stills_alone(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    gallery = _gallery(fake_world)
    fake_world.inject(EXPORT, False)
    code, out, _ = run_cli(["color", "still", "grab", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert "Timeline.GrabStill" not in fake_world.write_calls() and fake_world.page_changes() == []
    fake_world.fail.clear()
    fake_world.inject("Timeline.GrabStill", None)
    code, out, _ = run_cli(["color", "still", "grab", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert fake_world.resolve.page == "edit" and gallery.current.stills == []


def test_still_list_label_and_export(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    gallery = _gallery(fake_world)
    code, out, _ = run_cli(["color", "still", "list"])
    assert code == 0 and "0 stills in album Stills 1" in out
    _grab(run_cli, fake_world, "--all", "first")
    code, out, _ = run_cli(["color", "still", "label", "2", "hero", "--force"])
    assert code == 0 and gallery.current.stills[1].label == "hero"
    code, out, _ = run_cli(["color", "still", "list"])
    assert "2,hero" in out and "stills[4]{position,label}" in out
    fake_world.calls.clear()
    out_dir = tmp_path / "stills"
    code, out, err = run_cli(
        ["color", "still", "export", "1,2", str(out_dir), "--prefix", "s", "--format", "png"]
    )
    assert (code, err) == (0, ""), out
    assert sorted(p.name for p in out_dir.iterdir()) == ["s_1.png", "s_2.png"]
    assert fake_world.write_calls() == ["GalleryStillAlbum.ExportStills"]  # no save, no snapshot
    code, out, _ = run_cli(["color", "still", "export", "1", str(out_dir), "--prefix", "s"])
    assert code == 1 and "FILE_EXISTS" in out
    argv = ["color", "still", "export", "1", str(out_dir), "--prefix", "s", "--overwrite"]
    assert run_cli(argv)[0] == 0
    for argv in (
        ["color", "still", "export", "9", str(out_dir)],
        ["color", "still", "export", "1,1", str(out_dir)],
        ["color", "still", "export", "x", str(out_dir)],
        ["color", "still", "export", "1", str(out_dir), "--format", "gif"],
        ["color", "still", "label", "1,2", "x", "--force"],
    ):
        assert run_cli(argv)[0] in (1, 2), argv


def test_still_delete_is_irreversible_and_import_adds(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    gallery = _gallery(fake_world)
    _grab(run_cli, fake_world, "--all", "first")
    code, out, _ = run_cli(["color", "still", "delete", "2,3"])
    assert code == 0 and "irreversible: true" in out and fake_world.write_calls() == []
    code, out, _ = run_cli(["color", "still", "delete", "2,3", "--force"])
    assert code == 0 and "stills,4,2" in out and len(gallery.current.stills) == 2
    still_file = tmp_path / "grade.dpx"
    still_file.write_text("x\n", encoding="utf-8")
    code, out, _ = run_cli(["color", "still", "import", str(still_file), "--force"])
    assert code == 0 and "stills,2,3" in out and gallery.current.stills[-1].label == "grade"
    code, out, _ = run_cli(["color", "still", "import", str(tmp_path / "gone.dpx"), "--force"])
    assert code == 1 and "NOT_FOUND" in out


def test_album_lifecycle(run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld) -> None:
    gallery = _gallery(fake_world)
    code, out, _ = run_cli(["color", "album", "list"])
    assert code == 0 and "Stills 1,still,0,true" in out and "PowerGrade 1,powergrade,0,false" in out
    code, out, _ = run_cli(["color", "album", "create", "--powergrade"])
    assert code == 0 and fake_world.write_calls() == []
    code, out, _ = run_cli(["color", "album", "create", "--powergrade", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["new_album"] == "PowerGrade 2" and len(gallery.power_albums) == 2
    code, out, _ = run_cli(["color", "album", "create", "--force"])
    assert code == 0 and "Stills 2" in out
    code, out, _ = run_cli(["color", "album", "rename", "Stills 2", "--to", "Hero", "--force"])
    assert code == 0 and gallery.still_albums[1].name == "Hero"
    fake_world.calls.clear()
    code, out, _ = run_cli(["color", "album", "use", "Hero", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["snapshot"] == "none" and gallery.current.name == "Hero"
    assert fake_world.write_calls() == ["Gallery.SetCurrentStillAlbum"]
    code, out, _ = run_cli(["color", "album", "use", "Ghost", "--force"])
    assert code == 1 and "NOT_FOUND" in out and "Hero" in out
    assert run_cli(["color", "album", "rename", "Hero"])[0] == 2


def test_an_album_flag_picks_the_album(run_cli: Run, fake_world: FakeWorld) -> None:
    gallery = _gallery(fake_world)
    _grab(run_cli, fake_world)
    power = gallery.power_albums[0]
    assert run_cli(["color", "still", "list", "--album", "PowerGrade 1"])[0] == 0
    assert power.stills == []
    code, out, _ = run_cli(["color", "still", "list", "--album", "Nope"])
    assert code == 1 and "NOT_FOUND" in out


def test_thumbnail_writes_the_decoded_bytes_and_restores_the_page(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "t.png"
    code, out, err = run_cli(["color", "thumbnail", str(target)])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, ""), out
    assert target.read_bytes() == base64.b64decode(PNG_1X1)
    assert keys["width"] == "1" and keys["page_restored"] == "true"
    assert fake_world.page_changes() == ["color", "edit"]
    assert fake_world.write_calls() == ["Resolve.OpenPage", "Resolve.OpenPage"]
    code, out, _ = run_cli(["color", "thumbnail", str(target)])
    assert code == 1 and "FILE_EXISTS" in out
    assert run_cli(["color", "thumbnail", str(target), "--overwrite"])[0] == 0
    fake_world.inject("Timeline.GetCurrentClipThumbnailImage", {})
    code, out, _ = run_cli(["color", "thumbnail", str(tmp_path / "u.png")])
    assert code == 1 and "API_FAILED" in out and fake_world.resolve.page == "edit"
    fake_world.inject("Timeline.GetCurrentClipThumbnailImage", {"data": "***"})
    assert run_cli(["color", "thumbnail", str(tmp_path / "v.png")])[0] == 1


READ = "Timeline.GetCurrentClipThumbnailImage"


def test_thumbnail_waits_for_the_color_page_to_settle(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    set_thumbnail_settle(fake_world, 2)
    target = tmp_path / "t.png"
    code, out, err = run_cli(["color", "thumbnail", str(target)])
    assert (code, err) == (0, ""), out
    assert target.read_bytes() == base64.b64decode(PNG_1X1)
    assert len(fake_world.calls_to(READ)) == 3
    assert parse_toon_keys(out)["settle_reads"] == "2"
    assert fake_world.page_changes() == ["color", "edit"]


def test_thumbnail_gives_up_after_the_settle_budget(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    set_thumbnail_settle(fake_world, 50)
    code, out, _ = run_cli(["color", "thumbnail", str(tmp_path / "t.png")])
    assert code == 1 and "API_FAILED" in out and "no image" in out
    assert len(fake_world.calls_to(READ)) == 1 + color_extras.THUMBNAIL_SETTLE_READS
    assert fake_world.page_changes() == ["color", "edit"]


def test_thumbnail_on_the_color_page_reads_once(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.resolve.page = "color"
    set_thumbnail_settle(fake_world, 1)
    code, out, _ = run_cli(["color", "thumbnail", str(tmp_path / "t.png")])
    assert code == 1 and "API_FAILED" in out
    assert len(fake_world.calls_to(READ)) == 1


def test_lut_export_passes_the_size_enum_and_is_disk_only(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "a.cube"
    code, out, err = run_cli(["color", "lut", "export", "V1:1", str(target), "--size", "33"])
    assert (code, err) == (0, ""), out
    assert target.is_file()
    assert fake_world.calls_to("TimelineItem.ExportLUT") == [
        (fake_world.resolve.EXPORT_LUT_33PTCUBE, str(target))  # type: ignore[attr-defined]
    ]
    assert SAVE not in fake_world.write_calls() and not _snapshots(tmp_path).exists()
    code, out, _ = run_cli(["color", "lut", "export", "V1:1", str(target), "--size", "33"])
    assert code == 1 and "FILE_EXISTS" in out
    for argv in (
        ["color", "lut", "export", "V1:1", str(tmp_path / "b.cube")],
        ["color", "lut", "export", "V1:1", str(tmp_path / "b.cube"), "--size", "99"],
    ):
        assert run_cli(argv)[0] == 2
    code, out, _ = run_cli(
        ["color", "lut", "export", "V1:1", str(tmp_path / "p.vlut"), "--size", "panasonic"]
    )
    assert code == 0 and "size: panasonic" in out


def test_lut_refresh_is_a_force_call_without_a_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["color", "lut", "refresh"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["color", "lut", "refresh", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] == "none"
    assert fake_world.write_calls() == ["Project.RefreshLUTList"]
    assert not _snapshots(tmp_path).exists()
    fake_world.inject("Project.RefreshLUTList", False)
    assert run_cli(["color", "lut", "refresh", "--force"])[0] == 1


WANTED = (
    "Graph.GetNumNodes Graph.SetLUT Graph.GetLUT Graph.SetNodeCacheMode Graph.GetNodeCacheMode "
    "Graph.GetNodeLabel Graph.GetToolsInNode Graph.SetNodeEnabled Graph.ApplyArriCdlLut "
    "Graph.ApplyGradeFromDRX Graph.ResetAllGrades ColorGroup.SetName ColorGroup.GetName "
    "ColorGroup.GetClipsInTimeline ColorGroup.GetPreClipNodeGraph "
    "ColorGroup.GetPostClipNodeGraph "
    "Gallery.GetAlbumName Gallery.SetAlbumName Gallery.GetCurrentStillAlbum "
    "Gallery.SetCurrentStillAlbum Gallery.CreateGalleryStillAlbum "
    "Gallery.CreateGalleryPowerGradeAlbum Gallery.GetGalleryStillAlbums "
    "Gallery.GetGalleryPowerGradeAlbums GalleryStillAlbum.GetStills GalleryStillAlbum.GetLabel "
    "GalleryStillAlbum.SetLabel GalleryStillAlbum.ImportStills GalleryStillAlbum.ExportStills "
    "GalleryStillAlbum.DeleteStills Project.GetGallery Project.RefreshLUTList "
    "Project.AddColorGroup Project.DeleteColorGroup Project.GetColorGroupsList "
    "Timeline.GrabStill Timeline.GrabAllStills Timeline.GetNodeGraph "
    "Timeline.GetCurrentClipThumbnailImage TimelineItem.GetNodeGraph TimelineItem.SetCDL "
    "TimelineItem.CopyGrades TimelineItem.AddVersion TimelineItem.LoadVersionByName "
    "TimelineItem.RenameVersionByName TimelineItem.DeleteVersionByName "
    "TimelineItem.GetVersionNameList TimelineItem.GetCurrentVersion "
    "TimelineItem.AssignToColorGroup TimelineItem.RemoveFromColorGroup "
    "TimelineItem.GetColorGroup TimelineItem.ExportLUT TimelineItem.SetColorOutputCache "
    "TimelineItem.GetIsColorOutputCacheEnabled TimelineItem.ResetAllNodeColors"
)


def _walk(
    path: tuple[str, ...], spec: CommandSpec
) -> Iterator[tuple[tuple[str, ...], CommandSpec]]:
    yield path, spec
    for name, child in spec.subcommands.items():
        yield from _walk((*path, name), child)


def test_every_color_path_has_help_and_the_spec_declares_every_colour_method(run_cli: Run) -> None:
    spec = registry.lookup("color")
    assert spec is not None
    paths = list(_walk(("color",), spec))
    assert len(paths) > 40
    methods = set(spec.methods)
    wanted = WANTED.split()
    assert [m for m in wanted if m not in methods] == []
    for path, leaf in paths:
        code, out, err = run_cli([*path, "--help"])
        assert (code, err) == (0, "") and "examples[" in out, path
        if leaf.run is not None:
            assert leaf.methods, path
            assert all(m.count(".") == 1 for m in leaf.methods), path
        assert leaf.examples, path
        assert all(e.startswith("davinci-axi ") for e in leaf.examples), path
