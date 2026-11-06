"""`media` read view and every guarded `media` verb (U13) on the fake."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import media as media_cmd
from davinci_axi.fake import FakeProject, FakeWorld, ext_writes_core
from davinci_axi.fake import ext_media_edit as ext
from davinci_axi.fake.media import FakeFolder, FakeMediaPoolItem
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

SAVE, EXPORT = "ProjectManager.SaveProject", "ProjectManager.ExportProject"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Neither the registry line nor the EXTENSIONS entry exists in the worktree."""
    ext_writes_core.extend()
    ext.extend()
    ext.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "media", "davinci_axi.commands.media:SPEC")
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


def _assert_untouched(world: FakeWorld, tmp_path: Path) -> None:
    assert world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


def _file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text("x", encoding="utf-8")
    return path


def _noop(run_cli: Run, argv: list[str]) -> bool:
    """`--force` on a verb with nothing to change: exit 0, `applied: false`, no changes."""
    code, out, _ = run_cli([*argv, "--force"])
    return code == 0 and _keys(out)["applied"] == "false" and "changes: []" in out


def _force_ok(run_cli: Run, argv: list[str]) -> str:
    code, out, err = run_cli([*argv, "--force"])
    assert (code, err) == (0, ""), out
    return out


def test_media_lists_every_clip_without_writing_or_switching_pages(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["media"])
    keys = _keys(out)
    assert (code, err) == (0, "")
    assert keys["folder"] == "Master" and keys["total"] == "4"
    assert "folders[1]{path,clips,subfolders}:" in out and "  Master/Footage,3,0" in out
    assert "clips[4]{id,name,type,duration,folder}:" in out
    assert '  clip-1,A001_beach,Video + Audio,"00:00:05:00",Master/Footage' in out
    assert '  clip-4,music_bed,Audio,"00:01:00:00",Master' in out
    assert fake_world.write_calls() == [] and fake_world.page_changes() == []


def test_media_folder_and_recursion_flags(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["media", "--folder", "Master/Footage"])
    assert code == 0 and _keys(out)["total"] == "3"
    assert "folders: []" in out
    code, out, _ = run_cli(["media", "--no-recursive"])
    assert code == 0 and _keys(out)["total"] == "1" and "music_bed" in out
    assert "A001_beach" not in out
    code, out, _ = run_cli(["media", "--folder", "Nope"])
    assert code == 1 and _keys(out)["code"] == "NOT_FOUND" and "Master, Master/Footage" in out


def test_media_fields_limit_and_empty_state(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["media", "--fields", "name,fps,path,start_tc,color", "--limit", "2"])
    assert code == 0 and "clips[2]{name,fps,path,start_tc,color}:" in out
    assert "/media/davinci-axi-test/A001_beach.mov" in out
    assert "`davinci-axi media --limit 4`" in out
    assert run_cli(["media", "--fields", "bogus"])[0] == 2
    assert run_cli(["media", "--limit", "0"])[0] == 2
    _footage(fake_world).clips.clear()
    code, out, _ = run_cli(["media", "--folder", "Master/Footage", "--no-recursive"])
    assert code == 0 and "0 clips in folder Master/Footage (filters: recursive=no)" in out


def test_media_show_has_properties_and_metadata_blocks(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, err = run_cli(["media", "show", "A001_beach"])
    keys = _keys(out)
    assert (code, err) == (0, "")
    assert keys["id"] == "clip-1" and keys["folder"] == "Master/Footage"
    assert "properties:" in out and '  "File Path": /media/davinci-axi-test/A001_beach.mov' in out
    assert "metadata:" in out and '  "Clip Name": A001_beach' in out
    code, out, _ = run_cli(["media", "show", "clip-4", "--json"])
    assert code == 0 and '"markers": [{"frame": 24' in out
    assert fake_world.write_calls() == []
    _clip(fake_world, "A001_beach").metadata["Description"] = "x" * 2000
    code, out, _ = run_cli(["media", "show", "A001_beach"])
    assert code == 0 and "metadata_note: truncated at 1500" in out
    code, out, _ = run_cli(["media", "show", "A001_beach", "--full"])
    assert code == 0 and "metadata_note" not in out and "x" * 2000 in out


def test_ambiguous_clip_name_exits_2_listing_ids(run_cli: Run, fake_world: FakeWorld) -> None:
    dup = _root(fake_world).add_clip("A001_beach", "/media/other/A001_beach.mov")
    code, out, _ = run_cli(["media", "show", "A001_beach"])
    assert code == 2 and _keys(out)["code"] == "VALIDATION_ERROR"
    assert "clip-1 (in Master/Footage)" in out and f"{dup.uid} (in Master)" in out
    code, out, _ = run_cli(["media", "show", dup.uid])
    assert code == 0 and "folder: Master" in out
    code, out, _ = run_cli(["media", "show", "nope"])
    assert code == 1 and _keys(out)["code"] == "NOT_FOUND"


def test_import_dry_run_then_force_adds_clips_named_by_stem(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    a, b = _file(tmp_path, "a.mov"), _file(tmp_path, "b.wav")
    text = f"{a},{b}"
    code, out, err = run_cli(["media", "import", text])
    assert (code, err) == (0, "") and _keys(out)["applied"] == "false"
    assert "  clips,0,2" in out and "--force" in out
    _assert_untouched(fake_world, tmp_path)
    out = _force_ok(run_cli, ["media", "import", text])
    keys = _keys(out)
    assert keys["applied"] == "true" and keys["imported"] == '"a, b"'
    assert "folder_restored" not in keys
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPool.ImportMedia"]
    assert fake_world.calls_to("MediaPool.ImportMedia") == [([str(a), str(b)],)]
    assert [c.name for c in _root(fake_world).clips] == ["music_bed", "a", "b"]
    assert _root(fake_world).clips[2].properties["Type"] == "Audio"


def test_import_into_another_folder_restores_the_current_folder(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    a = _file(tmp_path, "a.mov")
    out = _force_ok(run_cli, ["media", "import", str(a), "--folder", "Master/Footage"])
    keys = _keys(out)
    assert keys["folder"] == "Master/Footage" and keys["folder_restored"] == "true"
    assert [c.name for c in _footage(fake_world).clips][-1] == "a"
    pool = _project(fake_world).media_pool
    assert pool.current is pool.root
    assert fake_world.write_calls()[2:] == [
        "MediaPool.SetCurrentFolder",
        "MediaPool.ImportMedia",
        "MediaPool.SetCurrentFolder",
    ]


def test_import_missing_path_or_folder_fails_in_the_dry_run_before_any_write(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["media", "import", str(tmp_path / "nope.mov")])
    assert code == 1 and _keys(out)["code"] == "NOT_FOUND"
    code, out, _ = run_cli(["media", "import", str(tmp_path)])
    assert code == 2 and "is a folder" in out
    code, out, _ = run_cli(["media", "import", str(_file(tmp_path, "a.mov")), "--folder", "Nope"])
    assert code == 1 and "NOT_FOUND" in out
    _assert_untouched(fake_world, tmp_path)


def test_rename_dry_run_and_force(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(["media", "rename", "A001_beach", "--to", "beach_wide"])
    assert code == 0 and "  name,A001_beach,beach_wide" in out
    assert "`davinci-axi media rename A001_beach --to beach_wide --force`" in out
    _assert_untouched(fake_world, tmp_path)
    out = _force_ok(run_cli, ["media", "rename", "A001_beach", "--to", "beach_wide"])
    assert _keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPoolItem.SetName"]
    assert _footage(fake_world).clips[0].name == "beach_wide"
    assert run_cli(["media", "rename", "beach_wide", "--to", " "])[0] == 2


def test_move_clips_to_another_folder(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(["media", "move", "A001_beach,A002_city", "--to", "Master"])
    assert code == 0 and "  A001_beach,Master/Footage,Master" in out
    _assert_untouched(fake_world, tmp_path)
    _force_ok(run_cli, ["media", "move", "A001_beach,A002_city", "--to", "Master"])
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPool.MoveClips"]
    assert [c.name for c in _footage(fake_world).clips] == ["A003_dusk"]
    assert {"A001_beach", "A002_city"} <= {c.name for c in _root(fake_world).clips}
    assert _noop(run_cli, ["media", "move", "music_bed", "--to", "Master"])


def test_delete_clip_is_irreversible_and_listed_as_gone(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["media", "delete", "music_bed"])
    keys = _keys(out)
    assert code == 0 and keys["irreversible"] == "true" and "snapshot is the only way back" in out
    _assert_untouched(fake_world, tmp_path)
    out = _force_ok(run_cli, ["media", "delete", "music_bed"])
    keys = _keys(out)
    assert keys["irreversible"] == "true" and keys["applied"] == "true" and "snapshot" in keys
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPool.DeleteClips"]
    assert _root(fake_world).clips == []


def test_relink_rewrites_the_file_path(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    new = tmp_path / "moved"
    new.mkdir()
    code, out, _ = run_cli(["media", "relink", "A001_beach", "--path", str(new)])
    assert (
        code == 0
        and f"  A001_beach path,/media/davinci-axi-test/A001_beach.mov,{new}/A001_beach.mov" in out
    )
    assert fake_world.write_calls() == []
    _force_ok(run_cli, ["media", "relink", "A001_beach", "--path", str(new)])
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPool.RelinkClips"]
    assert _clip(fake_world, "A001_beach").properties["File Path"] == f"{new}/A001_beach.mov"
    code, out, _ = run_cli(["media", "relink", "A001_beach", "--path", str(tmp_path / "gone")])
    assert code == 1 and _keys(out)["code"] == "NOT_FOUND"


def test_unlink_warns_but_is_not_irreversible(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["media", "unlink", "A001_beach,A002_city"])
    keys = _keys(out)
    assert code == 0 and "irreversible" not in keys and "go offline" in keys["warning"]
    assert "  A001_beach status,Online,Offline" in out
    _assert_untouched(fake_world, tmp_path)
    _force_ok(run_cli, ["media", "unlink", "A001_beach,A002_city"])
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPool.UnlinkClips"]
    assert _clip(fake_world, "A002_city").properties["Online Status"] == "Offline"
    assert _noop(run_cli, ["media", "unlink", "A001_beach"])


def test_metadata_set_merges_and_reads_back(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["media", "metadata", "set", "A001_beach", "Comments=ok,Keywords=sea"])
    assert code == 0 and '  Comments,"",ok' in out and '  Keywords,"",sea' in out
    _assert_untouched(fake_world, tmp_path)
    _force_ok(run_cli, ["media", "metadata", "set", "A001_beach", "Comments=ok,Keywords=sea"])
    assert fake_world.calls_to("MediaPoolItem.SetMetadata") == [
        ({"Comments": "ok", "Keywords": "sea"},)
    ]
    assert _clip(fake_world, "A001_beach").metadata["Comments"] == "ok"
    assert _clip(fake_world, "A001_beach").metadata["Clip Name"] == "A001_beach"
    assert run_cli(["media", "metadata", "set", "A001_beach", "Comments"])[0] == 2
    assert _noop(run_cli, ["media", "metadata", "set", "A001_beach", "Comments=ok"])


def test_color_set_and_clear(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["media", "color", "set", "A001_beach", "Teal"])
    assert code == 0 and '  color,"",Teal' in out
    assert fake_world.write_calls() == []
    _force_ok(run_cli, ["media", "color", "set", "A001_beach", "Teal"])
    assert _clip(fake_world, "A001_beach").color == "Teal"
    code, out, _ = run_cli(["media", "color", "set", "A001_beach", "Magenta"])
    assert code == 2 and "Valid colours: Orange" in out
    _force_ok(run_cli, ["media", "color", "clear", "A001_beach"])
    assert _clip(fake_world, "A001_beach").color == ""
    assert _noop(run_cli, ["media", "color", "clear", "A001_beach"])


def test_flag_add_and_clear(run_cli: Run, fake_world: FakeWorld) -> None:
    _force_ok(run_cli, ["media", "flag", "add", "A001_beach", "Blue"])
    _force_ok(run_cli, ["media", "flag", "add", "A001_beach", "Red"])
    assert _clip(fake_world, "A001_beach").flags == ["Blue", "Red"]
    assert run_cli(["media", "flag", "add", "A001_beach", "Teal"])[0] == 2  # clip colour, not flag
    _force_ok(run_cli, ["media", "flag", "clear", "A001_beach", "--color", "Red"])
    assert _clip(fake_world, "A001_beach").flags == ["Blue"]
    _force_ok(run_cli, ["media", "flag", "clear", "A001_beach"])
    assert _clip(fake_world, "A001_beach").flags == []
    assert _noop(run_cli, ["media", "flag", "clear", "A001_beach"])


def test_marker_add_and_delete(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    flags = ["--frame", "12", "--color", "Green", "--name", "Pick"]
    argv = ["media", "marker", "add", "A002_city", *flags]
    code, out, _ = run_cli(argv)
    assert code == 0 and "  marker@12,null,Green Pick" in out
    assert f"`davinci-axi media marker add A002_city {' '.join(flags)} --force`" in out
    _assert_untouched(fake_world, tmp_path)
    _force_ok(run_cli, argv)
    assert _clip(fake_world, "A002_city").markers[12]["name"] == "Pick"
    code, out, _ = run_cli([*argv, "--force"])
    assert code == 1 and _keys(out)["code"] == "MARKER_EXISTS"
    assert run_cli(["media", "marker", "add", "A002_city", "--frame", "-1"])[0] == 2
    assert run_cli(["media", "marker", "delete", "A002_city"])[0] == 2  # no selector
    _force_ok(run_cli, ["media", "marker", "delete", "A001_beach", "--color", "Green"])
    assert _clip(fake_world, "A001_beach").markers == {}
    _force_ok(run_cli, ["media", "marker", "delete", "A002_city", "--frame", "12"])
    assert _clip(fake_world, "A002_city").markers == {}


def test_proxy_link_and_unlink(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    proxy = _file(tmp_path, "proxy.mov")
    code, out, _ = run_cli(["media", "proxy", "link", "A001_beach", "--path", str(proxy)])
    assert code == 0 and f'  proxy,"",{proxy}' in out
    _assert_untouched(fake_world, tmp_path)
    _force_ok(run_cli, ["media", "proxy", "link", "A001_beach", "--path", str(proxy)])
    assert _clip(fake_world, "A001_beach").properties["Proxy Media Path"] == str(proxy)
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPoolItem.LinkProxyMedia"]
    _force_ok(run_cli, ["media", "proxy", "unlink", "A001_beach"])
    assert "Proxy Media Path" not in _clip(fake_world, "A001_beach").properties
    assert _noop(run_cli, ["media", "proxy", "unlink", "A001_beach"])
    code, out, _ = run_cli(["media", "proxy", "link", "A001_beach", "--path", str(tmp_path / "no")])
    assert code == 1 and "NOT_FOUND" in out


def test_folder_add_then_delete(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(["media", "folder", "add", "Dailies"])
    assert code == 0 and "  folder,null,Master/Dailies" in out
    _assert_untouched(fake_world, tmp_path)
    _force_ok(run_cli, ["media", "folder", "add", "Dailies"])
    assert [f.name for f in _root(fake_world).subfolders] == ["Footage", "Dailies"]
    assert fake_world.write_calls() == [SAVE, EXPORT, "MediaPool.AddSubFolder"]
    code, out, _ = run_cli(["media", "folder", "add", "Dailies", "--force"])
    assert code == 1 and _keys(out)["code"] == "FOLDER_EXISTS"
    assert run_cli(["media", "folder", "add", "a/b"])[0] == 2
    _force_ok(run_cli, ["media", "folder", "add", "Selects", "--in", "Master/Footage"])
    assert [f.name for f in _footage(fake_world).subfolders] == ["Selects"]
    out = _force_ok(run_cli, ["media", "folder", "delete", "Master/Dailies"])
    assert _keys(out)["irreversible"] == "true"
    assert [f.name for f in _root(fake_world).subfolders] == ["Footage"]


def test_folder_delete_warns_with_counts_refuses_the_root_and_leaves_the_current_folder(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _footage(fake_world).add_folder("Selects").add_clip("x", "/m/x.mov")
    _, out, _ = run_cli(["media", "folder", "delete", "Master/Footage"])
    keys = _keys(out)
    assert (
        keys["irreversible"] == "true" and "with 4 clip(s) and 1 sub folder(s)" in keys["warning"]
    )
    code, out, _ = run_cli(["media", "folder", "delete", "Master", "--force"])
    assert code == 1 and _keys(out)["code"] == "WRITE_REFUSED"
    _assert_untouched(fake_world, tmp_path)
    pool = _project(fake_world).media_pool
    assert pool.DeleteFolders([pool.root]) is False
    pool.current = _footage(fake_world)
    _force_ok(run_cli, ["media", "folder", "delete", "Master/Footage"])
    assert pool.current is pool.root and _root(fake_world).subfolders == []


def test_folder_move_and_its_refusals(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    _root(fake_world).add_folder("Dailies")
    code, out, _ = run_cli(["media", "folder", "move", "Master/Dailies", "--to", "Master/Footage"])
    assert code == 0 and "  folder,Master,Master/Footage" in out
    _assert_untouched(fake_world, tmp_path)
    _force_ok(run_cli, ["media", "folder", "move", "Master/Dailies", "--to", "Master/Footage"])
    assert [f.name for f in _footage(fake_world).subfolders] == ["Dailies"]
    inside = "Master/Footage/Dailies"
    code, out, _ = run_cli(["media", "folder", "move", "Master/Footage", "--to", inside])
    assert code == 2 and "into itself" in out
    code, out, _ = run_cli(["media", "folder", "move", "Master", "--to", "Master/Footage"])
    assert code == 1 and _keys(out)["code"] == "WRITE_REFUSED"
    assert _noop(run_cli, ["media", "folder", "move", inside, "--to", "Master/Footage"])
    _root(fake_world).add_folder("Spare").add_folder("Footage")
    code, out, _ = run_cli(["media", "folder", "move", "Master/Spare/Footage", "--to", "Master"])
    assert code == 1 and _keys(out)["code"] == "FOLDER_EXISTS"


def test_snapshot_failure_aborts_before_any_media_write(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject(EXPORT, False)
    code, out, _ = run_cli(["media", "rename", "A001_beach", "--to", "x", "--force"])
    assert code == 1 and _keys(out)["code"] == "SNAPSHOT_FAILED"
    assert "MediaPoolItem.SetName" not in fake_world.write_calls()
    assert _footage(fake_world).clips[0].name == "A001_beach"
    fake_world.fail.clear()
    fake_world.inject(SAVE, False)
    code, out, _ = run_cli(["media", "delete", "music_bed", "--force"])
    assert code == 1 and "SNAPSHOT_FAILED" in out
    assert "MediaPool.DeleteClips" not in fake_world.write_calls()
    assert len(_root(fake_world).clips) == 1


def test_a_refused_write_is_api_failed_and_read_back_mismatch_is_verify_failed(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject("MediaPool.MoveClips", False)
    code, out, _ = run_cli(["media", "move", "A001_beach", "--to", "Master", "--force"])
    assert code == 1 and _keys(out)["code"] == "API_FAILED" and "snapshot" in out
    fake_world.fail.clear()
    fake_world.inject("MediaPoolItem.GetName", "A001_beach")
    code, out, _ = run_cli(["media", "rename", "clip-1", "--to", "X", "--force"])
    assert code == 1 and _keys(out)["code"] == "VERIFY_FAILED"
    assert "name is 'A001_beach', expected 'X'" in out


def test_no_snapshot_is_refused_before_any_resolve_call(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DAVINCI_AXI_ALLOW_UNSNAPSHOTTED", raising=False)
    code, out, _ = run_cli(["media", "delete", "music_bed", "--no-snapshot", "--force"])
    assert code == 1 and _keys(out)["code"] == "WRITE_REFUSED"
    assert fake_world.calls == []
    monkeypatch.setenv("DAVINCI_AXI_ALLOW_UNSNAPSHOTTED", "1")
    code, out, _ = run_cli(["media", "delete", "music_bed", "--no-snapshot", "--force"])
    assert code == 0 and _keys(out)["snapshot"] == "skipped"
    assert fake_world.write_calls() == ["MediaPool.DeleteClips"]


def _walk(
    path: tuple[str, ...], spec: CommandSpec
) -> Iterator[tuple[tuple[str, ...], CommandSpec]]:
    yield path, spec
    for name, child in spec.subcommands.items():
        yield from _walk((*path, name), child)


def test_every_media_path_has_help_with_examples(run_cli: Run) -> None:
    paths = [path for path, _ in _walk(("media",), media_cmd.SPEC)]
    assert ("media", "folder", "move") in paths and ("media", "proxy", "unlink") in paths
    assert len(paths) == 26
    for path in paths:
        code, out, err = run_cli([*path, "--help"])
        assert code == 0 and err == "", path
        assert "examples[" in out, path
    _, out, _ = run_cli(["media", "--help"])
    for name in media_cmd.SUBCOMMANDS:
        assert name in out
    for group, listed in (("folder", "add, delete, move"), ("marker", "add, delete")):
        code, out, _ = run_cli(["media", group])
        assert code == 2 and listed in out
    assert run_cli(["media", "rename", "A001_beach"])[0] == 2  # --to missing


def test_spec_methods_cover_every_write_the_verbs_make() -> None:
    declared = {m for _, spec in _walk(("media",), media_cmd.SPEC) for m in spec.methods}
    pool = "ImportMedia SetCurrentFolder MoveClips DeleteClips RelinkClips UnlinkClips"
    folder = "AddSubFolder DeleteFolders MoveFolders"
    item = (
        "SetName SetMetadata AddMarker DeleteMarkerAtFrame DeleteMarkersByColor "
        "DeleteMarkerByCustomData AddFlag ClearFlags SetClipColor ClearClipColor "
        "LinkProxyMedia UnlinkProxyMedia"
    )
    writes = {f"MediaPool.{n}" for n in f"{pool} {folder}".split()}
    writes |= {f"MediaPoolItem.{n}" for n in item.split()}
    assert writes <= declared
    for path, spec in _walk(("media",), media_cmd.SPEC):
        if spec.run is not None and path not in (("media",), ("media", "show")):
            assert spec.methods and spec.reads_only is False, path


def test_fake_media_write_methods_honour_injection_and_validation(fake_world: FakeWorld) -> None:
    pool = _project(fake_world).media_pool
    clip = _clip(fake_world, "A001_beach")
    footage = _footage(fake_world)
    assert pool.AddSubFolder(footage, "") is None and pool.MoveFolders([footage], footage) is False
    assert pool.SetCurrentFolder(footage) is True and pool.current is footage
    assert pool.MoveClips([], pool.root) is False and pool.RelinkClips([clip], "/nope") is False
    assert pool.UnlinkClips([]) is False and pool.DeleteClips([]) is False
    fake_world.inject("MediaPool.ImportMedia", [clip])
    assert pool.ImportMedia([]) == [clip]
    fake_world.fail.clear()
    assert pool.ImportMedia(["/nonexistent/a.mov"]) == []
    assert clip.SetName("") is False and clip.SetMetadata({}) is False
    assert clip.SetClipProperty("Comments", "x") is True and clip.properties["Comments"] == "x"
    assert clip.LinkProxyMedia("") is False and clip.UnlinkProxyMedia() is False
    assert clip.ClearFlags("Red") is False and clip.DeleteMarkerByCustomData("none") is False
    fake_world.inject("MediaPoolItem.AddFlag", False)
    assert clip.AddFlag("Blue") is False and clip.flags == []
