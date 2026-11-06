from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import timeline, timeline_io
from davinci_axi.fake import (
    FakeProject,
    FakeTimeline,
    FakeWorld,
    ext_timeline_edit,
    ext_timeline_io,
)
from davinci_axi.fake.media import FakeMediaPool

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_timeline_edit.extend()
    ext_timeline_io.extend()
    ext_timeline_io.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "timeline", "davinci_axi.commands.timeline_io:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _names(world: FakeWorld) -> list[str]:
    return [t.name for t in _project(world).timelines]


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _source(tmp_path: Path, name: str = "edit.otio") -> Path:
    path = tmp_path / name
    path.write_text("fake timeline file\n", encoding="utf-8")
    return path


def test_export_writes_the_file_and_logs_the_export_call(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "out" / "edit.otio"
    code, out, err = run_cli(["timeline", "export", str(target)])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert target.read_text(encoding="utf-8").startswith("fake timeline export: Edit v1")
    assert keys["timeline"] == "Edit v1" and keys["format"] == "otio"
    assert keys["bytes"] == str(target.stat().st_size)
    assert fake_world.calls_to("Timeline.Export") == [
        (str(target), fake_world.resolve.EXPORT_OTIO, None)
    ]
    assert fake_world.write_calls() == ["Timeline.Export"]
    assert not _snapshots(tmp_path).exists()
    assert "timeline import" in out


def test_export_edl_passes_the_none_subtype_and_aaf_the_new_subtype(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    resolve = fake_world.resolve
    code, out, _ = run_cli(["timeline", "export", str(tmp_path / "cut.edl")])
    assert code == 0 and "one video track" in out
    code, out, _ = run_cli(["timeline", "export", str(tmp_path / "cut.mov"), "--format", "aaf"])
    assert code == 0 and "drop effects" in out
    assert [args[1:] for args in fake_world.calls_to("Timeline.Export")] == [
        (resolve.EXPORT_EDL, resolve.EXPORT_NONE),
        (resolve.EXPORT_AAF, resolve.EXPORT_AAF_NEW),
    ]


@pytest.mark.parametrize(
    ("name", "fmt", "enum"),
    [
        ("a.xml", "fcpxml", "EXPORT_FCPXML_1_10"),
        ("a.fcpxml", "fcpxml", "EXPORT_FCPXML_1_10"),
        ("a.drt", "drt", "EXPORT_DRT"),
        ("a.csv", "csv", "EXPORT_TEXT_CSV"),
        ("a.tab", "tab", "EXPORT_TEXT_TAB"),
        ("a.ale", "ale", "EXPORT_ALE"),
    ],
)
def test_export_picks_the_format_from_the_extension(
    run_cli: Run,
    parse_toon_keys: Keys,
    fake_world: FakeWorld,
    tmp_path: Path,
    name: str,
    fmt: str,
    enum: str,
) -> None:
    code, out, _ = run_cli(["timeline", "export", str(tmp_path / name)])
    assert code == 0 and parse_toon_keys(out)["format"] == fmt
    assert fake_world.calls_to("Timeline.Export")[0][1] == getattr(fake_world.resolve, enum)


def test_export_of_another_timeline_by_name(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "v2.otio"
    code, out, _ = run_cli(["timeline", "export", str(target), "--timeline", "Edit v2"])
    assert code == 0 and parse_toon_keys(out)["timeline"] == "Edit v2"
    assert "Edit v2" in target.read_text(encoding="utf-8")
    code, out, _ = run_cli(["timeline", "export", str(tmp_path / "x.otio"), "--timeline", "nope"])
    assert code == 1 and "NOT_FOUND" in out


def test_export_refuses_to_overwrite_unless_asked(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = _source(tmp_path)
    code, out, _ = run_cli(["timeline", "export", str(target)])
    assert code == 1 and parse_toon_keys(out)["code"] == "FILE_EXISTS"
    assert fake_world.calls == []
    assert target.read_text(encoding="utf-8") == "fake timeline file\n"
    code, _, _ = run_cli(["timeline", "export", str(target), "--overwrite"])
    assert code == 0
    assert target.read_text(encoding="utf-8").startswith("fake timeline export")


def test_export_unknown_extension_without_format_exits_2(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "export", str(tmp_path / "edit.mov")])
    assert code == 2 and "Valid formats: otio, fcpxml, drt, edl, aaf, csv, tab, ale" in out
    code, out, _ = run_cli(["timeline", "export", str(tmp_path / "edit")])
    assert code == 2 and "(none)" in out
    code, out, _ = run_cli(["timeline", "export", str(tmp_path / "e.otio"), "--format", "mp4"])
    assert code == 2 and "Valid values for --format" in out
    assert fake_world.calls == []


def test_export_into_a_directory_is_a_validation_error(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "export", str(tmp_path), "--format", "otio"])
    assert code == 2 and "is a directory" in out
    assert fake_world.calls == []


def test_export_returning_false_is_api_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("Timeline.Export", False)
    code, out, _ = run_cli(["timeline", "export", str(tmp_path / "edit.otio")])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert "Timeline.Export" in out and not (tmp_path / "edit.otio").exists()


def test_export_that_wrote_no_file_is_api_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("Timeline.Export", True)
    code, out, _ = run_cli(["timeline", "export", str(tmp_path / "edit.otio")])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert "wrote no" in out


def test_export_names_a_missing_enum_attribute(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    del fake_world.resolve.EXPORT_ALE
    code, out, _ = run_cli(["timeline", "export", str(tmp_path / "a.ale")])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert "this Resolve has no EXPORT_ALE" in out
    assert fake_world.write_calls() == []


def test_formats_table_names_attributes_the_resolve_object_exposes(fake_world: FakeWorld) -> None:
    for fmt, (type_name, subtype_name) in timeline_io.FORMATS.items():
        assert hasattr(fake_world.resolve, type_name), fmt
        if subtype_name:
            assert hasattr(fake_world.resolve, subtype_name), fmt


def test_import_dry_run_changes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _source(tmp_path)
    code, out, err = run_cli(["timeline", "import", str(source)])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["applied"] == "false" and keys["command"] == "timeline import"
    assert "changes[1]{field,before,after}:" in out and "  timeline,null,edit" in out
    assert "names the timeline from the file" in out
    assert f"timeline import {source} --force" in out
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    assert _names(fake_world) == ["Edit v1", "Edit v2"]


def test_import_force_snapshots_then_imports_and_reads_back(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _source(tmp_path)
    code, out, _ = run_cli(["timeline", "import", str(source), "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] != "none"
    assert fake_world.write_calls() == [
        "ProjectManager.SaveProject",
        "ProjectManager.ExportProject",
        "MediaPool.ImportTimelineFromFile",
    ]
    assert fake_world.calls_to("MediaPool.ImportTimelineFromFile") == [(str(source.resolve()), {})]
    assert any(p.is_dir() for p in _snapshots(tmp_path).iterdir())
    assert _names(fake_world) == ["Edit v1", "Edit v2", "edit"]
    created = _project(fake_world).timelines[-1]
    assert [len(t.items) for t in created.tracks["video"]] == [1]
    _, listing, _ = run_cli(["timelines"])
    assert "total: 3" in listing and "edit" in listing


def test_import_with_name_passes_the_import_option_and_checks_the_name(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _source(tmp_path)
    code, out, _ = run_cli(["timeline", "import", str(source), "--name", "Edit v3", "--force"])
    assert code == 0 and "  timeline,null,Edit v3" in out
    assert "names the timeline from the file" not in out
    options = fake_world.calls_to("MediaPool.ImportTimelineFromFile")[0][1]
    assert options == {"timelineName": "Edit v3"}
    assert _names(fake_world)[-1] == "Edit v3"


def test_import_reports_the_name_resolve_chose_when_no_name_is_given(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    original = FakeMediaPool.ImportTimelineFromFile  # type: ignore[attr-defined]

    def renamed(self: FakeMediaPool, filePath: str, importOptions: object = None) -> FakeTimeline:
        created: FakeTimeline = original(self, filePath, importOptions)
        created.name = "Name inside the file"
        return created

    monkeypatch.setattr(FakeMediaPool, "ImportTimelineFromFile", renamed, raising=False)
    code, out, _ = run_cli(["timeline", "import", str(_source(tmp_path)), "--force"])
    assert code == 0 and "  timeline,null,Name inside the file" in out


def test_import_refuses_an_existing_name(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _source(tmp_path)
    code, out, _ = run_cli(["timeline", "import", str(source), "--name", "Edit v1", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "TIMELINE_EXISTS"
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()


def test_import_name_is_not_valid_for_drt(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _source(tmp_path, "edit.drt")
    code, out, _ = run_cli(["timeline", "import", str(source), "--name", "x", "--force"])
    assert code == 2 and "not valid for a .drt import" in out
    assert fake_world.calls == []
    code, out, _ = run_cli(["timeline", "import", str(source), "--force"])
    assert code == 0 and _names(fake_world)[-1] == "edit"


def test_import_missing_file_exits_1_before_connecting(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "import", str(tmp_path / "gone.otio"), "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert fake_world.calls == [] and not _snapshots(tmp_path).exists()


def test_import_unsupported_extension_exits_2(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "import", str(_source(tmp_path, "edit.mov"))])
    assert code == 2 and "Supported extensions" in out
    assert fake_world.calls == []


def test_import_otioz_warns_that_media_is_unpacked(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "import", str(_source(tmp_path, "edit.otioz"))])
    assert code == 0 and ".otioz unpacks media next to the file" in out


def test_import_returning_none_is_api_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("MediaPool.ImportTimelineFromFile", None)
    code, out, _ = run_cli(["timeline", "import", str(_source(tmp_path)), "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert "snapshot" in out  # the snapshot id is reported with the failure
    assert _names(fake_world) == ["Edit v1", "Edit v2"]


def test_import_that_does_not_show_up_is_verify_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("MediaPool.ImportTimelineFromFile", FakeTimeline(fake_world, "ghost"))
    args = ["timeline", "import", str(_source(tmp_path)), "--name", "ghost", "--force"]
    code, out, _ = run_cli(args)
    assert code == 1 and parse_toon_keys(out)["code"] == "VERIFY_FAILED"


@pytest.mark.parametrize("failing", ["ProjectManager.ExportProject", "ProjectManager.SaveProject"])
def test_snapshot_failure_aborts_before_the_import(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path, failing: str
) -> None:
    fake_world.inject(failing, False)
    code, out, _ = run_cli(["timeline", "import", str(_source(tmp_path)), "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert "MediaPool.ImportTimelineFromFile" not in fake_world.write_calls()
    assert _names(fake_world) == ["Edit v1", "Edit v2"]


def test_no_snapshot_is_refused_before_any_resolve_call(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    monkeypatch.delenv("DAVINCI_AXI_ALLOW_UNSNAPSHOTTED", raising=False)
    code, out, _ = run_cli(["timeline", "import", str(_source(tmp_path)), "--no-snapshot"])
    assert code == 1 and "WRITE_REFUSED" in out
    assert fake_world.calls == []


def test_fake_import_rejects_missing_files_unknown_extensions_and_duplicates(
    fake_world: FakeWorld, tmp_path: Path
) -> None:
    pool = fake_world.resolve.GetMediaPool()
    assert pool is not None
    assert pool.ImportTimelineFromFile(str(tmp_path / "none.otio"), {}) is None
    assert pool.ImportTimelineFromFile(str(_source(tmp_path, "a.mov")), {}) is None
    assert pool.ImportTimelineFromFile(str(_source(tmp_path)), {"timelineName": "Edit v1"}) is None
    assert pool.ImportTimelineFromFile(str(tmp_path / "edit.otio"), None) is not None


def test_u11_subcommands_still_dispatch_through_the_wrapped_spec(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["timeline", "rename", "--help"])
    assert (code, err) == (0, "") and "examples[" in out
    code, out, _ = run_cli(["timeline", "rename", "Edit v2", "--to", "Edit v3", "--force"])
    assert code == 0 and "applied: true" in out
    assert _names(fake_world) == ["Edit v1", "Edit v3"]


def test_bare_timeline_lists_export_and_import(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timeline"])
    assert code == 2 and "needs a subcommand" in out
    assert "export" in out and "import" in out and "rename" in out
    assert fake_world.calls == []


@pytest.mark.parametrize("path", [["export"], ["import"]])
def test_help_exits_0_with_examples(run_cli: Run, fake_world: FakeWorld, path: list[str]) -> None:
    code, out, err = run_cli(["timeline", *path, "--help"])
    assert (code, err) == (0, "") and "examples[" in out
    assert fake_world.calls == []


def test_spec_is_the_wrapped_timeline_spec_with_both_methods() -> None:
    assert set(timeline_io.SPEC.subcommands) == {*timeline.SPEC.subcommands, "export", "import"}
    assert {"Timeline.Export", "MediaPool.ImportTimelineFromFile"} <= set(timeline_io.SPEC.methods)
    assert set(timeline.SPEC.methods) <= set(timeline_io.SPEC.methods)
    assert "export" not in timeline.SPEC.subcommands
    for leaf in (timeline_io.EXPORT_SPEC, timeline_io.IMPORT_SPEC):
        assert leaf.reads_only is False and leaf.methods
    flags = {f.name for f in timeline_io.IMPORT_SPEC.flags}
    assert {"force", "no-snapshot", "snapshot-out", "name"} <= flags
    assert "force" not in {f.name for f in timeline_io.EXPORT_SPEC.flags}
