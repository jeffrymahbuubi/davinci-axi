"""`project` lifecycle, folder and settings mutations (U10), against the fake."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_project

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

NAME = "davinci-axi-test"
SAFETY = ["ProjectManager.SaveProject", "ProjectManager.ExportProject"]


@pytest.fixture(autouse=True)
def _registered(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_project.extend()
    ext_project.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "project", "davinci_axi.commands.project:SPEC")
    monkeypatch.delitem(registry.COMMANDS, "snapshot", raising=False)
    yield


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _current(world: FakeWorld) -> str | None:
    project = world.resolve.pm.current_project
    return project.name if project else None


def _projects(world: FakeWorld) -> list[str]:
    return list(world.resolve.pm.folder.projects)


def _json(out: str) -> dict[str, object]:
    body = json.loads(out)
    assert isinstance(body, dict)
    return body


# --- rename (AE5 shape) ---------------------------------------------------------------------


def test_rename_dry_run_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["project", "rename", "--to", "promo"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["applied"] == "false" and keys["command"] == "project rename"
    assert f"name,{NAME},promo" in out
    assert "Run `davinci-axi project rename --to promo --force` to apply this change" in out
    assert fake_world.write_calls() == []
    assert not _snapshots(tmp_path).exists()
    assert _current(fake_world) == NAME


def test_rename_force_saves_snapshots_writes_and_reads_back(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["project", "rename", "--to", "promo", "--force"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert fake_world.write_calls() == [*SAFETY, "Project.SetName"]
    assert keys["applied"] == "true"
    directory = _snapshots(tmp_path) / keys["snapshot"]
    assert {p.name for p in directory.iterdir()} == {"meta.json", "state.json", "project.drp"}
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    assert meta["project"] == NAME and meta["reason"] == "project rename"
    assert _current(fake_world) == "promo"
    assert "promo" in _projects(fake_world) and NAME not in _projects(fake_world)


def test_rename_to_a_taken_name_exits_1_without_writes(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["project", "rename", "--to", "sample-project", "--force"])
    assert code == 1
    assert parse_toon_keys(out)["code"] == "ALREADY_EXISTS"
    assert fake_world.write_calls() == []


def test_rename_to_the_same_name_is_a_no_op(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["project", "rename", "--to", NAME, "--force"])
    assert code == 0
    assert parse_toon_keys(out)["applied"] == "false" and "changes: []" in out
    assert fake_world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


def test_rename_needs_to_and_a_non_empty_name(run_cli: Run, fake_world: FakeWorld) -> None:
    assert run_cli(["project", "rename"])[0] == 2
    assert run_cli(["project", "rename", "--to", "  "])[0] == 2
    assert fake_world.calls == []


def test_rename_aborts_before_the_write_when_the_snapshot_fails(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("ProjectManager.ExportProject", False)
    code, out, _ = run_cli(["project", "rename", "--to", "promo", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert "Project.SetName" not in fake_world.write_calls()
    assert _current(fake_world) == NAME


def test_rename_verify_mismatch_is_reported_with_the_snapshot(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("Project.GetName", NAME)
    code, out, _ = run_cli(["project", "rename", "--to", "promo", "--force", "--json"])
    body = _json(out)
    assert code == 1 and body["code"] == "VERIFY_FAILED"
    detail = body["detail"]
    assert isinstance(detail, dict) and detail["field"] == "name" and detail["expected"] == "promo"
    assert detail["snapshot"] == next(_snapshots(tmp_path).iterdir()).name


def test_no_snapshot_is_refused_before_any_resolve_call(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DAVINCI_AXI_ALLOW_UNSNAPSHOTTED", raising=False)
    code, out, _ = run_cli(["project", "rename", "--to", "promo", "--no-snapshot"])
    assert code == 1 and parse_toon_keys(out)["code"] == "WRITE_REFUSED"
    assert fake_world.calls == []


def test_restore_hint_appears_only_when_the_snapshot_command_is_registered(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, out, _ = run_cli(["project", "rename", "--to", "a", "--force"])
    assert "snapshot restore" not in out
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")
    _, out, _ = run_cli(["project", "rename", "--to", "b", "--force"])
    assert "davinci-axi snapshot restore " in out


# --- create / open / save / close / delete --------------------------------------------------


def test_create_with_an_existing_name_exits_1_in_the_dry_run(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["project", "create", "sample-project"])
    assert code == 1 and parse_toon_keys(out)["code"] == "ALREADY_EXISTS"
    assert fake_world.write_calls() == []


def test_create_dry_run_then_force(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["project", "create", "promo"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert "opens the new project" in out
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    code, out, _ = run_cli(["project", "create", "promo", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true"
    assert fake_world.write_calls() == [*SAFETY, "ProjectManager.CreateProject"]
    assert keys["snapshot"].endswith(NAME)
    assert _current(fake_world) == "promo"


def test_create_passes_the_media_path(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _, _ = run_cli(["project", "create", "promo", "--media-path", "/media/x", "--force"])
    assert code == 0
    assert fake_world.calls_to("ProjectManager.CreateProject") == [("promo", "/media/x")]


def test_create_with_no_project_open_skips_the_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.resolve.pm.current_project = None
    code, out, _ = run_cli(["project", "create", "promo", "--force"])
    assert code == 0 and parse_toon_keys(out)["snapshot"] == "none"
    assert fake_world.write_calls() == ["ProjectManager.CreateProject"]
    assert not _snapshots(tmp_path).exists()


def test_open_snapshots_the_current_project_first(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["project", "open", "sample-project", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["snapshot"].endswith(NAME)
    assert fake_world.write_calls() == [*SAFETY, "ProjectManager.LoadProject"]
    assert _current(fake_world) == "sample-project"
    assert "changes the current project" in out


def test_open_dry_run_writes_nothing(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(["project", "open", "sample-project"])
    assert code == 0 and "davinci-axi project open sample-project --force" in out
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    assert _current(fake_world) == NAME


def test_open_with_no_project_open_has_no_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.resolve.pm.current_project = None
    code, out, _ = run_cli(["project", "open", "sample-project", "--force"])
    assert code == 0 and parse_toon_keys(out)["snapshot"] == "none"
    assert fake_world.write_calls() == ["ProjectManager.LoadProject"]


def test_open_unknown_project_lists_the_folder(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["project", "open", "nope", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert "sample-project" in out and fake_world.write_calls() == []


def test_open_the_current_project_is_a_no_op(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["project", "open", NAME, "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == []


def test_open_failure_is_api_failed_with_the_snapshot_id(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("ProjectManager.LoadProject", None)
    code, out, _ = run_cli(["project", "open", "sample-project", "--force", "--json"])
    body = _json(out)
    assert code == 1 and body["code"] == "API_FAILED"
    detail = body["detail"]
    assert isinstance(detail, dict)
    assert detail["snapshot"] == next(_snapshots(tmp_path).iterdir()).name


def test_save_writes_only_save_project_and_takes_no_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["project", "save"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["project", "save", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] == "none"
    assert fake_world.write_calls() == ["ProjectManager.SaveProject"]
    assert not _snapshots(tmp_path).exists()


def test_save_failure_exits_1(run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld) -> None:
    fake_world.inject("ProjectManager.SaveProject", False)
    code, out, _ = run_cli(["project", "save", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"


def test_close_is_irreversible_and_leaves_no_current_project(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["project", "close"])
    assert code == 0 and parse_toon_keys(out)["irreversible"] == "true"
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["project", "close", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["irreversible"] == "true" and keys["applied"] == "true"
    assert fake_world.write_calls() == [*SAFETY, "ProjectManager.CloseProject"]
    assert fake_world.resolve.pm.GetCurrentProject() is None
    assert "without a further save" in out


def test_close_with_no_project_open_exits_1(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.resolve.pm.current_project = None
    code, out, _ = run_cli(["project", "close", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NO_PROJECT"


def test_delete_refuses_the_current_project(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["project", "delete", NAME, "--force"])
    assert code == 2 and parse_toon_keys(out)["code"] == "VALIDATION_ERROR"
    assert "project close" in out and fake_world.write_calls() == []
    assert NAME in _projects(fake_world)


def test_delete_removes_another_project_and_says_irreversible(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["project", "delete", "sample-project"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and keys["irreversible"] == "true"
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["project", "delete", "sample-project", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["irreversible"] == "true"
    assert fake_world.write_calls() == [*SAFETY, "ProjectManager.DeleteProject"]
    assert _projects(fake_world) == [NAME]
    assert keys["snapshot"].endswith(NAME)


def test_delete_unknown_project_is_not_found(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["project", "delete", "nope", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert fake_world.write_calls() == []


# --- import / export / archive --------------------------------------------------------------


def test_export_writes_the_file_without_save_or_force(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "out" / "promo.drp"
    code, out, _ = run_cli(["project", "export", str(target)])
    keys = parse_toon_keys(out)
    assert code == 0 and target.exists()
    assert keys["project"] == NAME and keys["path"] == str(target)
    assert int(keys["bytes"]) == target.stat().st_size > 0
    assert fake_world.write_calls() == ["ProjectManager.ExportProject"]
    assert fake_world.calls_to("ProjectManager.ExportProject") == [(NAME, str(target), True)]
    assert not _snapshots(tmp_path).exists()


def test_export_refuses_to_overwrite_without_the_flag(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "promo.drp"
    assert run_cli(["project", "export", str(target)])[0] == 0
    code, out, _ = run_cli(["project", "export", str(target)])
    assert code == 1 and parse_toon_keys(out)["code"] == "FILE_EXISTS"
    code, _, _ = run_cli(["project", "export", str(target), "--overwrite", "--no-stills-luts"])
    assert code == 0
    assert fake_world.calls_to("ProjectManager.ExportProject")[-1] == (NAME, str(target), False)
    assert "ProjectManager.SaveProject" not in fake_world.write_calls()


def test_export_failure_and_no_project(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("ProjectManager.ExportProject", False)
    code, out, _ = run_cli(["project", "export", str(tmp_path / "a.drp")])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    fake_world.resolve.pm.current_project = None
    code, out, _ = run_cli(["project", "export", str(tmp_path / "b.drp")])
    assert code == 1 and parse_toon_keys(out)["code"] == "NO_PROJECT"


def test_archive_writes_a_directory_and_passes_the_flags(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "promo.dra"
    code, out, _ = run_cli(["project", "archive", str(target), "--media", "--proxy"])
    keys = parse_toon_keys(out)
    assert code == 0 and (target / "project.drp").exists()
    assert keys["media"] == "true" and keys["render_cache"] == "false" and keys["proxy"] == "true"
    assert int(keys["bytes"]) > 0
    assert fake_world.calls_to("ProjectManager.ArchiveProject") == [
        (NAME, str(target), True, False, True)
    ]
    assert fake_world.write_calls() == ["ProjectManager.ArchiveProject"]
    code, out, _ = run_cli(["project", "archive", str(target)])
    assert code == 1 and parse_toon_keys(out)["code"] == "FILE_EXISTS"
    assert run_cli(["project", "archive", str(target), "--overwrite"])[0] == 0


def test_import_of_an_exported_file_with_the_default_name_is_a_duplicate(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    exported = tmp_path / f"{NAME}.drp"
    assert run_cli(["project", "export", str(exported)])[0] == 0
    fake_world.calls.clear()
    code, out, _ = run_cli(["project", "import", str(exported)])
    assert code == 1 and parse_toon_keys(out)["code"] == "ALREADY_EXISTS"
    assert fake_world.write_calls() == []


def test_import_with_a_name_adds_a_project_and_never_loads_it(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    exported = tmp_path / "backup.drp"
    assert run_cli(["project", "export", str(exported)])[0] == 0
    fake_world.calls.clear()
    code, out, _ = run_cli(["project", "import", str(exported), "--name", "copy"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert "copy" not in _projects(fake_world) and fake_world.write_calls() == []
    code, out, _ = run_cli(["project", "import", str(exported), "--name", "copy", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [*SAFETY, "ProjectManager.ImportProject"]
    assert fake_world.calls_to("ProjectManager.ImportProject") == [(str(exported), "copy")]
    assert "copy" in _projects(fake_world) and _current(fake_world) == NAME


def test_import_without_a_name_reports_the_name_from_the_file(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = tmp_path / "x.drp"
    source.write_text("fake drp: from-file\n", encoding="utf-8")
    code, out, _ = run_cli(["project", "import", str(source), "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["imported_as"] == "from-file"
    assert "from-file" in _projects(fake_world)
    assert fake_world.calls_to("ProjectManager.ImportProject") == [(str(source), None)]


def test_import_that_resolve_refuses_is_api_failed_and_changes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = tmp_path / "x.drp"
    source.write_text("fake drp: sample-project\n", encoding="utf-8")
    before = _projects(fake_world)
    code, out, _ = run_cli(["project", "import", str(source), "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert _projects(fake_world) == before


def test_import_of_a_missing_file_fails_before_connecting(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["project", "import", str(tmp_path / "missing.drp"), "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert fake_world.calls == []
