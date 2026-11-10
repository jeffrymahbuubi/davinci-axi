from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import project as project_module
from davinci_axi.fake import FakeProject, FakeWorld, ext_project
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register_project(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_project.extend()  # the lead splices this into EXTENSIONS after merge
    monkeypatch.setitem(registry.COMMANDS, "project", "davinci_axi.commands.project:SPEC")
    monkeypatch.delitem(registry.COMMANDS, "snapshot", raising=False)


def _only_snapshot_dir(tmp_path: Path) -> Path:
    dirs = list((tmp_path / "axi-home" / "snapshots").iterdir())
    assert len(dirs) == 1
    return dirs[0]


def _exports(world: FakeWorld) -> list[tuple[object, ...]]:
    return [args for cls, method, args in world.calls if method == "ExportProject"]


def test_snapshot_writes_files_under_axi_home(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["project", "snapshot"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    directory = _only_snapshot_dir(tmp_path)
    assert directory.name.endswith("-davinci-axi-test")
    assert {p.name for p in directory.iterdir()} == {"meta.json", "state.json", "project.drp"}
    assert keys["snapshot"] == directory.name
    assert keys["project"] == "davinci-axi-test"
    assert keys["reason"] == "manual"
    assert keys["dir"].endswith(directory.name)
    assert keys["drp"].endswith("project.drp") and keys["state"].endswith("state.json")
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    assert meta["with_stills_luts"] is True


def test_snapshot_counts_match_state_json(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _, out, _ = run_cli(["project", "snapshot"])
    state = json.loads((_only_snapshot_dir(tmp_path) / "state.json").read_text(encoding="utf-8"))
    items = sum(len(t["items"]) for tl in state["timelines"] for t in tl["tracks"])
    keys = parse_toon_keys(out)
    assert keys["timelines"] == str(len(state["timelines"])) == "2"
    assert keys["items"] == str(items)
    assert items > 0


def test_snapshot_out_is_used_verbatim(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "chosen"
    code, out, _ = run_cli(["project", "snapshot", "--out", str(target)])
    assert code == 0
    assert (target / "project.drp").exists() and (target / "state.json").exists()
    assert not (tmp_path / "axi-home" / "snapshots").exists()
    assert parse_toon_keys(out)["dir"] == str(target)


def test_snapshot_reason_is_stored(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _, out, _ = run_cli(["project", "snapshot", "--reason", "before regrade"])
    assert parse_toon_keys(out)["reason"] == "before regrade"
    meta = json.loads((_only_snapshot_dir(tmp_path) / "meta.json").read_text(encoding="utf-8"))
    assert meta["reason"] == "before regrade"
    assert meta["manual"] is True  # F34(g): prune keeps it unless --all


def test_only_export_project_is_written_and_page_unchanged(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, _, _ = run_cli(["project", "snapshot"])
    assert code == 0
    assert fake_world.write_calls() == ["ProjectManager.ExportProject"]
    assert fake_world.page_changes() == []


def test_snapshot_force_is_accepted_and_ignored(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["project", "snapshot", "--force"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["force"].startswith("ignored")
    assert (_only_snapshot_dir(tmp_path) / "project.drp").exists()
    assert len(_exports(fake_world)) == 1


def test_snapshot_without_force_has_no_force_key(run_cli: Run, parse_toon_keys: Keys) -> None:
    _, out, _ = run_cli(["project", "snapshot"])
    assert "force" not in parse_toon_keys(out)


def test_snapshot_help_mentions_disk_only(run_cli: Run) -> None:
    code, out, _ = run_cli(["project", "snapshot", "--help"])
    assert code == 0
    assert "disk-only" in out


def test_snapshot_includes_stills_and_luts_by_default(run_cli: Run, fake_world: FakeWorld) -> None:
    run_cli(["project", "snapshot"])
    exports = _exports(fake_world)
    assert len(exports) == 1 and exports[0][2] is True


def test_no_stills_luts_passes_false(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, _, _ = run_cli(["project", "snapshot", "--no-stills-luts"])
    assert code == 0
    exports = _exports(fake_world)
    assert len(exports) == 1 and exports[0][2] is False
    meta = json.loads((_only_snapshot_dir(tmp_path) / "meta.json").read_text(encoding="utf-8"))
    assert meta["with_stills_luts"] is False


def test_export_failure_exits_1_and_keeps_state(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("ProjectManager.ExportProject", False)
    code, out, _ = run_cli(["project", "snapshot", "--json"])
    assert code == 1
    assert "SNAPSHOT_FAILED" in out
    directory = _only_snapshot_dir(tmp_path)
    assert (directory / "state.json").exists()
    assert str(directory / "state.json") in out.replace("\\/", "/")


def test_no_project_is_an_error(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    fake_world.resolve.pm.current_project = None
    code, out, _ = run_cli(["project", "snapshot"])
    assert code == 1
    assert "NO_PROJECT" in out
    assert not (tmp_path / "axi-home" / "snapshots").exists()
    assert fake_world.write_calls() == []


def test_bare_project_exits_2_listing_subcommands(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["project"])
    assert code == 2
    assert "snapshot" in out


def test_unknown_flag_exits_2(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _, _ = run_cli(["project", "snapshot", "--bogus"])
    assert code == 2
    assert fake_world.write_calls() == []


def test_hints_only_name_registered_commands(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(registry.COMMANDS, "items", raising=False)
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")
    _, out, _ = run_cli(["project", "snapshot"])
    assert "`davinci-axi timelines`" in out
    assert "`davinci-axi items`" not in out


def test_items_hint_appears_when_registered(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")
    _, out, _ = run_cli(["project", "snapshot"])
    assert "`davinci-axi items`" in out


def test_third_help_line_points_at_snapshot_list_only_when_registered(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, out, _ = run_cli(["project", "snapshot"])
    assert "Restore by importing the .drp" in out
    assert "Run `davinci-axi snapshot list`" not in out
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")
    _, out, _ = run_cli(["project", "snapshot", "--reason", "again"])
    assert "Run `davinci-axi snapshot list` to list snapshots and restore one" in out
    assert "Restore by importing the .drp" not in out


def test_project_lists_the_lifecycle_subcommands(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["project"])
    assert code == 2
    for word in ("create", "open", "rename", "close", "delete", "import", "folder", "settings"):
        assert word in out


def test_help_exits_zero(run_cli: Run) -> None:
    for argv in (["project", "--help"], ["project", "snapshot", "--help"]):
        code, out, err = run_cli(argv)
        assert (code, err) == (0, "")
        assert "examples[" in out


# --- folders, settings and shared checks of the U10 lifecycle commands -----------------------

SAFETY = ["ProjectManager.SaveProject", "ProjectManager.ExportProject"]
NAME = "davinci-axi-test"


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _json(out: str) -> dict[str, object]:
    body = json.loads(out)
    assert isinstance(body, dict)
    return body


# --- folders --------------------------------------------------------------------------------


def test_folder_create_and_delete_round_trip(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["project", "folder", "create", "clients"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["project", "folder", "create", "clients", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [*SAFETY, "ProjectManager.CreateFolder"]
    assert list(fake_world.resolve.pm.folder.children) == ["clients"]
    code, out, _ = run_cli(["project", "folder", "create", "clients", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "ALREADY_EXISTS"
    code, out, _ = run_cli(["project", "folder", "delete", "clients"])
    assert code == 0 and parse_toon_keys(out)["irreversible"] == "true"
    assert "every project in it" in out
    code, out, _ = run_cli(["project", "folder", "delete", "clients", "--force"])
    assert code == 0 and parse_toon_keys(out)["irreversible"] == "true"
    assert fake_world.resolve.pm.folder.children == {}


def test_folder_delete_that_resolve_refuses_leaves_the_folder(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    busy = fake_world.resolve.pm.add_folder("busy")
    busy.projects["inside"] = FakeProject(fake_world, "inside")
    code, out, _ = run_cli(["project", "folder", "delete", "busy", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert "busy" in fake_world.resolve.pm.folder.children
    code, out, _ = run_cli(["project", "folder", "delete", "nope", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"


def test_folder_navigation_round_trip_takes_no_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.resolve.pm.add_folder("clients")
    code, out, _ = run_cli(["project", "folder", "open", "clients", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] == "none"
    assert fake_world.resolve.pm.GetCurrentFolder() == "clients"
    assert fake_world.write_calls() == ["ProjectManager.OpenFolder"]
    code, out, _ = run_cli(["project", "folder", "up", "--force"])
    assert code == 0 and parse_toon_keys(out)["now_in"] == "Root"
    assert fake_world.resolve.pm.GetCurrentFolder() == "Root"
    run_cli(["project", "folder", "open", "clients", "--force"])
    code, _, _ = run_cli(["project", "folder", "root", "--force"])
    assert code == 0 and fake_world.resolve.pm.GetCurrentFolder() == "Root"
    assert fake_world.write_calls()[-1] == "ProjectManager.GotoRootFolder"
    assert "ProjectManager.SaveProject" not in fake_world.write_calls()
    assert not _snapshots(tmp_path).exists()


def test_folder_navigation_at_the_root_is_a_no_op(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    for verb in ("up", "root"):
        code, out, _ = run_cli(["project", "folder", verb, "--force"])
        assert code == 0 and parse_toon_keys(out)["applied"] == "false" and "changes: []" in out
    assert fake_world.write_calls() == []


def test_folder_open_unknown_lists_folders(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.resolve.pm.add_folder("clients")
    code, out, _ = run_cli(["project", "folder", "open", "nope", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND" and "clients" in out


# --- settings -------------------------------------------------------------------------------


def test_settings_set_dry_run_shows_the_live_value(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["project", "settings", "set", "timelineFrameRate=25", "--json"])
    body = _json(out)
    assert code == 0 and body["applied"] is False
    assert body["changes"] == [{"field": "timelineFrameRate", "before": 24.0, "after": 25.0}]
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    assert fake_world.resolve.pm.current_project is not None
    assert fake_world.resolve.pm.current_project.settings["timelineFrameRate"] == 24.0


def test_settings_set_force_applies_and_reads_back(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    arg = "timelineFrameRate=25,colorScienceMode=davinciACES"
    code, out, _ = run_cli(["project", "settings", "set", arg, "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [*SAFETY, "Project.SetSettings"]
    assert fake_world.calls_to("Project.SetSettings") == [
        ({"timelineFrameRate": "25", "colorScienceMode": "davinciACES"},)
    ]
    project = fake_world.resolve.pm.current_project
    assert project is not None
    assert project.settings["timelineFrameRate"] == 25.0
    assert project.settings["colorScienceMode"] == "davinciACES"


def test_settings_set_validates_before_writing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["project", "settings", "set", "bogusKey=1", "--force"])
    assert code == 2 and "bogusKey" in out and "timelineFrameRate" in out
    assert run_cli(["project", "settings", "set", "timelineFrameRate=fast", "--force"])[0] == 2
    assert run_cli(["project", "settings", "set", "novalue", "--force"])[0] == 2
    assert fake_world.write_calls() == []


def test_settings_set_with_unchanged_values_is_a_no_op(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["project", "settings", "set", "timelineFrameRate=24", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == []


def test_settings_set_rejected_by_resolve_names_the_snapshot(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("Project.SetSettings", False)
    code, out, _ = run_cli(
        ["project", "settings", "set", "timelineFrameRate=25", "--force", "--json"]
    )
    body = _json(out)
    assert code == 1 and body["code"] == "API_FAILED"
    detail = body["detail"]
    assert isinstance(detail, dict)
    assert detail["snapshot"] == next(_snapshots(tmp_path).iterdir()).name


# --- shared -------------------------------------------------------------------------------


def _walk(prefix: tuple[str, ...], spec: CommandSpec) -> Iterator[tuple[str, ...]]:
    yield prefix
    for name, child in spec.subcommands.items():
        yield from _walk((*prefix, name), child)


def test_every_project_path_has_help(run_cli: Run) -> None:
    paths = list(_walk(("project",), project_module.SPEC))
    assert len(paths) >= 19
    for path in paths:
        code, out, err = run_cli([*path, "--help"])
        assert (code, err) == (0, ""), path
        assert "examples[" in out, path


def test_bare_parents_exit_2_and_list_subcommands(run_cli: Run, fake_world: FakeWorld) -> None:
    for argv, word in ((["project", "folder"], "create"), (["project", "settings"], "set")):
        code, out, _ = run_cli(argv)
        assert code == 2 and word in out
    assert fake_world.calls == []


def test_every_mutating_verb_has_the_mutation_flags(run_cli: Run, fake_world: FakeWorld) -> None:
    disk_only = {"snapshot", "export", "archive"}
    for path in _walk(("project",), project_module.SPEC):
        spec = project_module.SPEC
        for word in path[1:]:
            spec = spec.subcommands[word]
        if spec.run is None or path[-1] in disk_only:
            continue
        names = {flag.name for flag in spec.flags}
        assert {"force", "no-snapshot", "snapshot-out"} <= names, path


def test_spec_methods_cover_every_resolve_call() -> None:
    expected = {
        "ProjectManager.CreateProject",
        "ProjectManager.LoadProject",
        "ProjectManager.SaveProject",
        "Project.SetName",
        "ProjectManager.CloseProject",
        "ProjectManager.DeleteProject",
        "ProjectManager.ImportProject",
        "ProjectManager.ExportProject",
        "ProjectManager.ArchiveProject",
        "ProjectManager.CreateFolder",
        "ProjectManager.DeleteFolder",
        "ProjectManager.OpenFolder",
        "ProjectManager.GotoParentFolder",
        "ProjectManager.GotoRootFolder",
        "Project.SetSettings",
    }
    assert expected <= set(project_module.SPEC.methods)
    for name, spec in project_module.SPEC.subcommands.items():
        assert spec.methods, name
