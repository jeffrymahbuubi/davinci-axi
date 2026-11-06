"""`project` settings presets, restore, db and cloud (U22), against the fake."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import project, project_extra
from davinci_axi.fake import FakeProject, FakeWorld, ext_presets, ext_project, ext_writes_core
from davinci_axi.fake.core import FakeProjectManager
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

NAME = "davinci-axi-test"
SAFETY = ["ProjectManager.SaveProject", "ProjectManager.ExportProject"]
SAVE = "Project.SaveCurrentProjectSettingsAsNewPreset"
PRESET = ["project", "settings", "preset"]


@pytest.fixture(autouse=True)
def _registered(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext_project.extend()
    ext_presets.extend()
    ext_presets.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "project", "davinci_axi.commands.project_extra:SPEC")
    yield


def _project(world: FakeWorld) -> FakeProject:
    current = world.resolve.pm.current_project
    assert current is not None
    return current


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _projects(world: FakeWorld) -> list[str]:
    return list(world.resolve.pm.folder.projects)


def _drp(tmp_path: Path, name: str = "x.drp", project_name: str = "restored") -> Path:
    path = tmp_path / name
    path.write_text(f"fake drp: {project_name}\n", encoding="utf-8")
    return path


# --- settings presets -----------------------------------------------------------------------


def test_settings_preset_list_shows_name_width_and_height(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli([*PRESET, "list"])
    assert code == 0 and "0 project settings presets in Resolve" in out
    ext_presets.seed_settings_presets(_project(fake_world), "base")
    code, out, _ = run_cli([*PRESET, "list"])
    assert code == 0 and "presets[1]{name,width,height}:" in out and "base,1920,1080" in out
    assert fake_world.write_calls() == []


def test_settings_preset_save_apply_delete_round_trip(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli([*PRESET, "save", "base"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    code, out, _ = run_cli([*PRESET, "save", "base", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [*SAFETY, SAVE]
    saved = {"Name": "base", "Width": 1920, "Height": 1080}
    assert _project(fake_world).settings_presets == [saved]
    _project(fake_world).settings_presets[0]["Width"] = 3840
    fake_world.calls.clear()
    code, out, _ = run_cli([*PRESET, "apply", "base", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and "replaces the project's settings" in out
    assert fake_world.write_calls() == [*SAFETY, "Project.SetProjectSettingsPreset"]
    assert _project(fake_world).settings["timelineResolutionWidth"] == "3840"
    fake_world.calls.clear()
    code, out, _ = run_cli([*PRESET, "delete", "base", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["irreversible"] == "true" and keys["applied"] == "true"
    assert fake_world.write_calls() == [
        *SAFETY,
        "Project.ExportProjectSettingsPreset",
        "Project.DeleteProjectSettingsPreset",
    ]
    assert _project(fake_world).settings_presets == []
    backups = list((_snapshots(tmp_path) / "presets").glob("settings-base-*.preset"))
    assert len(backups) == 1


def test_settings_preset_save_of_an_existing_name_and_apply_of_an_unknown_one(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    ext_presets.seed_settings_presets(_project(fake_world), "base")
    code, out, _ = run_cli([*PRESET, "save", "base", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "ALREADY_EXISTS"
    code, out, _ = run_cli([*PRESET, "apply", "nope", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND" and "base" in out
    assert fake_world.write_calls() == []


def test_settings_preset_update_is_irreversible_and_keeps_a_copy(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    ext_presets.seed_settings_presets(_project(fake_world), "base")
    _project(fake_world).settings["timelineResolutionWidth"] = "3840"
    code, out, _ = run_cli([*PRESET, "update", "base", "--force"])
    assert code == 0 and parse_toon_keys(out)["irreversible"] == "true"
    assert _project(fake_world).settings_presets[0]["Width"] == 3840
    assert fake_world.write_calls()[-2:] == [
        "Project.ExportProjectSettingsPreset",
        "Project.UpdateProjectSettingsPreset",
    ]
    assert len(list((_snapshots(tmp_path) / "presets").iterdir())) == 1


def test_settings_preset_import_and_export(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = tmp_path / "wide.preset"
    source.write_text("fake preset: wide\n", encoding="utf-8")
    code, out, _ = run_cli([*PRESET, "import", str(source), "--name", "wide", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.calls_to("Project.ImportProjectSettingsPreset") == [(str(source), "wide")]
    target = tmp_path / "out.preset"
    code, out, _ = run_cli([*PRESET, "export", "wide", str(target)])
    assert code == 0 and target.is_file()
    code, out, _ = run_cli([*PRESET, "export", "wide", str(target)])
    assert code == 1 and parse_toon_keys(out)["code"] == "FILE_EXISTS"


def test_settings_preset_save_aborts_when_the_snapshot_fails(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("ProjectManager.ExportProject", False)
    code, out, _ = run_cli([*PRESET, "save", "base", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert SAVE not in fake_world.write_calls() and _project(fake_world).settings_presets == []


# --- restore --------------------------------------------------------------------------------


def test_restore_with_a_name_lists_the_new_project(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _drp(tmp_path)
    code, out, _ = run_cli(["project", "restore", str(source), "--name", "copy"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    code, out, _ = run_cli(["project", "restore", str(source), "--name", "copy", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"].endswith(NAME)
    assert fake_world.write_calls() == [*SAFETY, "ProjectManager.RestoreProject"]
    assert "copy" in _projects(fake_world)
    assert fake_world.calls_to("ProjectManager.RestoreProject") == [(str(source), "copy")]
    code, out, _ = run_cli(["project", "restore", str(source), "--name", "copy", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "ALREADY_EXISTS"


def test_restore_without_a_name_reports_the_name_resolve_chose(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _drp(tmp_path, project_name="from-file")
    code, out, _ = run_cli(["project", "restore", str(source), "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["restored_as"] == "from-file"
    assert "from-file" in _projects(fake_world)
    assert fake_world.calls_to("ProjectManager.RestoreProject") == [(str(source), None)]


def test_restore_of_a_missing_file_is_not_found_before_connecting(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["project", "restore", str(tmp_path / "nope.drp")])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND" and fake_world.calls == []


def test_a_failed_snapshot_blocks_restore_and_no_open_project_needs_none(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _drp(tmp_path)
    fake_world.inject("ProjectManager.ExportProject", False)
    code, out, _ = run_cli(["project", "restore", str(source), "--name", "copy", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert "ProjectManager.RestoreProject" not in fake_world.write_calls()
    fake_world.fail.clear()
    fake_world.resolve.pm.current_project = None
    shutil.rmtree(_snapshots(tmp_path), ignore_errors=True)
    code, out, _ = run_cli(["project", "restore", str(source), "--name", "copy", "--force"])
    assert code == 0 and parse_toon_keys(out)["snapshot"] == "none"
    assert not _snapshots(tmp_path).exists()


def test_restore_failure_is_api_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("ProjectManager.RestoreProject", False)
    argv = ["project", "restore", str(_drp(tmp_path)), "--name", "c", "--force"]
    code, out, _ = run_cli(argv)
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"


# --- databases ------------------------------------------------------------------------------


def test_db_list_marks_the_current_database(run_cli: Run, fake_world: FakeWorld) -> None:
    ext_presets.add_database(fake_world, "Other", "PostgreSQL")
    code, out, _ = run_cli(["project", "db", "list"])
    assert code == 0 and "current: Local Database" in out
    assert "databases[2]{name,type,ip,current}:" in out
    assert "Local Database,Disk,null,true" in out and "Other,PostgreSQL,null,false" in out
    assert fake_world.write_calls() == []


def test_db_use_closes_the_project_after_a_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    ext_presets.add_database(fake_world, "Other")
    code, out, _ = run_cli(["project", "db", "use", "Other"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and "closes the current project" in out
    assert f"closes_project: {NAME}" in out
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    code, out, _ = run_cli(["project", "db", "use", "Other", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"].endswith(NAME)
    assert fake_world.write_calls() == [*SAFETY, "ProjectManager.SetCurrentDatabase"]
    assert fake_world.resolve.pm.GetCurrentDatabase()["DbName"] == "Other"
    assert fake_world.resolve.pm.GetCurrentProject() is None
    code, out, _ = run_cli(["project", "db", "use", "Other", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"


def test_db_use_unknown_and_ambiguous_names(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["project", "db", "use", "nope", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND" and "Local Database" in out
    ext_presets.add_database(fake_world, "Dup", "Disk")
    ext_presets.add_database(fake_world, "Dup", "PostgreSQL")
    code, out, _ = run_cli(["project", "db", "use", "Dup", "--force"])
    assert code == 2 and "--type" in out and fake_world.write_calls() == []
    code, _, _ = run_cli(["project", "db", "use", "Dup", "--type", "PostgreSQL", "--force"])
    assert code == 0 and fake_world.resolve.pm.database["DbType"] == "PostgreSQL"


def test_db_use_aborts_when_the_snapshot_fails(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    ext_presets.add_database(fake_world, "Other")
    fake_world.inject("ProjectManager.ExportProject", False)
    code, out, _ = run_cli(["project", "db", "use", "Other", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert "ProjectManager.SetCurrentDatabase" not in fake_world.write_calls()
    assert fake_world.resolve.pm.current_project is not None


# --- cloud ----------------------------------------------------------------------------------


def test_cloud_create_dry_run_shows_the_settings_dict(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    settings = "projectName=promo,isCollab=true,syncMode=none"
    code, out, _ = run_cli(["project", "cloud", "create", "--set", settings])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and "untested live" in out
    assert "projectName: promo" in out and "isCollab: true" in out and "syncMode: none" in out
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()


def test_cloud_create_then_open_through_the_api(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    settings = "projectName=promo,isCollab=true"
    code, out, _ = run_cli(["project", "cloud", "create", "--set", settings, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"].endswith(NAME)
    assert fake_world.write_calls() == [*SAFETY, "ProjectManager.CreateCloudProject"]
    assert fake_world.calls_to("ProjectManager.CreateCloudProject") == [
        ({"projectName": "promo", "isCollab": True},)
    ]
    assert _project(fake_world).name == "promo"
    fake_world.resolve.pm.LoadProject("sample-project")
    fake_world.calls.clear()
    code, out, _ = run_cli(["project", "cloud", "open", "--set", "projectName=promo", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert "ProjectManager.LoadCloudProject" in fake_world.write_calls()
    assert _project(fake_world).name == "promo"


def test_cloud_sync_mode_is_passed_as_the_resolve_constant(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    settings = "projectName=p,syncMode=proxy-only"
    assert run_cli(["project", "cloud", "create", "--set", settings, "--force"])[0] == 0
    sent = fake_world.calls_to("ProjectManager.CreateCloudProject")[0][0]
    assert isinstance(sent, dict)
    assert sent["syncMode"] == getattr(fake_world.resolve, "CLOUD_SYNC_PROXY_ONLY")  # noqa: B009


def test_cloud_import_and_restore_use_a_path_and_a_called_change(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _drp(tmp_path, "cloud.drp")
    argv = ["project", "cloud", "import", str(source), "--set", "projectName=imp", "--force"]
    code, out, _ = run_cli(argv)
    assert code == 0 and "called,false,true" in out
    assert fake_world.calls_to("ProjectManager.ImportCloudProject") == [
        (str(source), {"projectName": "imp"})
    ]
    folder = tmp_path / "backup"
    folder.mkdir()
    argv = ["project", "cloud", "restore", str(folder), "--set", "projectName=res", "--force"]
    code, out, _ = run_cli(argv)
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    argv = ["project", "cloud", "restore", str(tmp_path / "gone"), "--set", "projectName=r"]
    code, out, _ = run_cli(argv)
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    code, out, _ = run_cli(["project", "cloud", "import", str(folder), "--set", "projectName=r"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "settings",
    [
        "isCollab=true",
        "projectName=p,colour=red",
        "projectName=p,syncMode=fast",
        "projectName=p,isCollab=maybe",
        "bad",
    ],
)
def test_cloud_settings_are_checked_before_connecting(
    run_cli: Run, fake_world: FakeWorld, settings: str
) -> None:
    code, out, _ = run_cli(["project", "cloud", "create", "--set", settings, "--force"])
    assert code == 2 and "VALIDATION_ERROR" in out and fake_world.calls == []


def test_cloud_verbs_need_set_and_report_api_failure(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    assert run_cli(["project", "cloud", "create"])[0] == 2
    argv = ["project", "cloud", "open", "--set", "projectName=missing", "--force"]
    code, out, _ = run_cli(argv)
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED" and "untested live" in out


# --- the repointed spec ---------------------------------------------------------------------


def test_the_repointed_project_spec_keeps_every_phase_2_verb(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    expected = [*project.SPEC.subcommands, "restore", "db", "cloud"]
    assert list(project_extra.SPEC.subcommands) == expected
    assert set(project.SETTINGS_SPEC.subcommands) <= set(project_extra.SETTINGS_SPEC.subcommands)
    for argv in (["project", "snapshot", "--help"], ["project", "rename", "--help"]):
        assert run_cli(argv)[0] == 0
    code, out, _ = run_cli(["project", "rename", "--to", "promo", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    code, out, _ = run_cli(["project", "settings", "set", "timelineFrameRate=25"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    for method in project.SPEC.methods:
        assert method in project_extra.SPEC.methods


def _walk(path: tuple[str, ...], spec: CommandSpec) -> Iterator[tuple[str, ...]]:
    yield path
    for name, child in spec.subcommands.items():
        yield from _walk((*path, name), child)


def test_every_project_path_has_help_and_examples(run_cli: Run) -> None:
    for path in _walk(("project",), project_extra.SPEC):
        code, out, err = run_cli([*path, "--help"])
        assert code == 0 and err == "" and "examples[" in out, path


def test_declared_methods_exist_on_the_fake() -> None:
    classes: dict[str, type] = {"Project": FakeProject, "ProjectManager": FakeProjectManager}
    for name in project_extra.SPEC.methods:
        cls, _, method = name.partition(".")
        assert hasattr(classes[cls], method), name
    for expected in (
        "ProjectManager.RestoreProject",
        "ProjectManager.SetCurrentDatabase",
        "ProjectManager.GetDatabaseList",
        "ProjectManager.CreateCloudProject",
        "ProjectManager.LoadCloudProject",
        "ProjectManager.ImportCloudProject",
        "ProjectManager.RestoreCloudProject",
        "Project.GetProjectSettingsPresetList",
        "Project.SetProjectSettingsPreset",
        "Project.ExportProjectSettingsPreset",
    ):
        assert expected in project_extra.SPEC.methods
