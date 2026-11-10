from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import snapshot as snapshot_cmd
from davinci_axi.fake import FakeWorld
from davinci_axi.snapshot_index import counts, find_snapshot, list_snapshots
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")


def _root(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _take(run_cli: Run, reason: str = "manual") -> None:
    code, _, _ = run_cli(["project", "snapshot", "--reason", reason])
    assert code == 0


def _two_snapshots(run_cli: Run, tmp_path: Path) -> list[str]:
    _take(run_cli, "first")
    _take(run_cli, "second")
    ids = sorted(p.name for p in _root(tmp_path).iterdir())
    assert len(ids) == 2
    return ids


def _json(run_cli: Run, argv: list[str]) -> dict[str, object]:
    code, out, _ = run_cli([*argv, "--json"])
    assert code == 0
    data = json.loads(out)
    assert isinstance(data, dict)
    return data


def _project_names(world: FakeWorld) -> list[str]:
    return list(world.resolve.pm.folder.projects)


def test_list_shows_two_rows_newest_first_without_touching_resolve(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    older, newer = _two_snapshots(run_cli, tmp_path)
    before = len(fake_world.calls)
    data = _json(run_cli, ["snapshot", "list"])
    assert len(fake_world.calls) == before
    rows = data["snapshots"]
    assert isinstance(rows, list) and data["total"] == 2
    assert [r["id"] for r in rows] == [newer, older]
    assert [r["reason"] for r in rows] == ["second", "first"]
    assert all(r["drp"] is True and r["project"] == "davinci-axi-test" for r in rows)
    assert set(rows[0]) == {"id", "project", "created", "reason", "drp"}


def test_list_toon_output_and_hints(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    _, newer = _two_snapshots(run_cli, tmp_path)
    code, out, err = run_cli(["snapshot", "list"])
    assert (code, err) == (0, "")
    assert "snapshots[2]{id,project,created,reason,drp}:" in out
    assert f"Run `davinci-axi snapshot show {newer}` to see what the newest snapshot holds" in out


def test_list_fields_and_limit(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    _two_snapshots(run_cli, tmp_path)
    data = _json(run_cli, ["snapshot", "list", "--fields", "id,dir", "--limit", "1"])
    rows = data["snapshots"]
    assert isinstance(rows, list) and len(rows) == 1 and data["total"] == 2
    assert set(rows[0]) == {"id", "dir"}
    code, out, _ = run_cli(["snapshot", "list", "--fields", "bogus"])
    assert code == 2 and "Valid fields" in out
    code, out, _ = run_cli(["snapshot", "list", "--limit", "0"])
    assert code == 2


def test_list_project_filter_and_empty_state(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _two_snapshots(run_cli, tmp_path)
    code, out, _ = run_cli(["snapshot", "list", "--project", "other"])
    assert code == 0
    assert "0 snapshots under" in out and "(filters: project=other)" in out
    assert _json(run_cli, ["snapshot", "list", "--project", "davinci-axi-test"])["total"] == 2


def test_list_with_no_snapshot_root_is_an_empty_state(run_cli: Run, tmp_path: Path) -> None:
    code, out, _ = run_cli(["snapshot", "list"])
    assert code == 0 and "0 snapshots under" in out and "total: 0" in out
    assert "project snapshot" in out
    assert not _root(tmp_path).exists()


def test_directory_without_meta_json_is_ignored(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _take(run_cli)
    stray = _root(tmp_path) / "20990101-000000-stray"
    stray.mkdir()
    (stray / "project.drp").write_text("fake drp: stray\n", encoding="utf-8")
    (_root(tmp_path) / "notes.txt").write_text("not a snapshot", encoding="utf-8")
    data = _json(run_cli, ["snapshot", "list"])
    assert data["total"] == 1 and "warnings" not in data


def test_unreadable_meta_json_warns_and_keeps_the_rest(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _take(run_cli)
    bad = _root(tmp_path) / "20990101-000000-broken"
    bad.mkdir()
    (bad / "meta.json").write_text("{not json", encoding="utf-8")
    listed = _json(run_cli, ["snapshot", "list"])
    assert listed["total"] == 1
    warnings = listed["warnings"]
    assert isinstance(warnings, list) and "20990101-000000-broken" in warnings[0]
    seen: list[str] = []
    assert len(list_snapshots(_root(tmp_path), seen)) == 1 and len(seen) == 1


def test_show_prints_location_counts_and_reason(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _take(run_cli, "before-regrade")
    snapshot_id = next(_root(tmp_path).iterdir()).name
    before = len(fake_world.calls)
    code, out, err = run_cli(["snapshot", "show", snapshot_id])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "") and len(fake_world.calls) == before
    assert keys["id"] == snapshot_id and keys["reason"] == "before-regrade"
    assert keys["project"] == "davinci-axi-test" and keys["timelines"] == "2"
    assert keys["items"] == "6"
    assert keys["dir"].endswith(snapshot_id)
    assert keys["drp"].endswith("project.drp") and keys["state"].endswith("state.json")
    assert f"davinci-axi snapshot restore {snapshot_id}" in out


def test_show_unknown_id_is_not_found_and_lists_ids(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    older, newer = _two_snapshots(run_cli, tmp_path)
    code, out, _ = run_cli(["snapshot", "show", "nope"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert older in out and newer in out


def test_show_does_not_match_by_prefix(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    _take(run_cli)
    snapshot_id = next(_root(tmp_path).iterdir()).name
    code, out, _ = run_cli(["snapshot", "show", snapshot_id[:12]])
    assert code == 1 and "NOT_FOUND" in out


def test_show_with_nothing_saved_points_at_project_snapshot(run_cli: Run) -> None:
    code, out, _ = run_cli(["snapshot", "show", "nope"])
    assert code == 1 and "NOT_FOUND" in out and "No snapshots under" in out


def test_restore_dry_run_shows_the_new_name_and_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _take(run_cli)
    snapshot_id = next(_root(tmp_path).iterdir()).name
    writes = len(fake_world.write_calls())
    code, out, _ = run_cli(["snapshot", "restore", snapshot_id])
    keys = parse_toon_keys(out)
    expected = f"davinci-axi-test (restored {snapshot_id[:15]})"
    assert code == 0 and keys["applied"] == "false"
    assert keys["restored_as"] == expected
    assert keys["command"] == "snapshot restore" and keys["from_project"] == "davinci-axi-test"
    assert "changes[1]{field,before,after}:" in out
    assert f"davinci-axi snapshot restore {snapshot_id} --force" in out
    assert len(fake_world.write_calls()) == writes
    assert expected not in _project_names(fake_world)
    assert len(list(_root(tmp_path).iterdir())) == 1


def test_restore_force_snapshots_first_then_imports_as_a_new_project(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _take(run_cli)
    snapshot_id = next(_root(tmp_path).iterdir()).name
    current = fake_world.resolve.pm.current_project
    mark = len(fake_world.write_calls())
    code, out, _ = run_cli(["snapshot", "restore", snapshot_id, "--force"])
    keys = parse_toon_keys(out)
    expected = f"davinci-axi-test (restored {snapshot_id[:15]})"
    assert code == 0 and keys["applied"] == "true" and keys["restored_as"] == expected
    assert fake_world.write_calls()[mark:] == [
        "ProjectManager.SaveProject",
        "ProjectManager.ExportProject",
        "ProjectManager.ImportProject",
    ]
    drp = _root(tmp_path) / snapshot_id / "project.drp"
    assert fake_world.calls_to("ProjectManager.ImportProject")[-1] == (str(drp), expected)
    assert expected in _project_names(fake_world)
    assert fake_world.resolve.pm.current_project is current  # F4: the open project is untouched
    assert len(list(_root(tmp_path).iterdir())) == 2  # the pre-restore safety snapshot
    assert keys["snapshot"] != "none"
    assert "davinci-axi project open" in out and "--force" in out


def test_restore_with_a_custom_name(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _take(run_cli)
    snapshot_id = next(_root(tmp_path).iterdir()).name
    code, out, _ = run_cli(["snapshot", "restore", snapshot_id, "--name", "Back Again", "--force"])
    assert code == 0 and parse_toon_keys(out)["restored_as"] == "Back Again"
    assert "Back Again" in _project_names(fake_world)
    code, out, _ = run_cli(["snapshot", "restore", snapshot_id, "--name", "Back Again"])
    assert code == 1 and "ALREADY_EXISTS" in out


@pytest.mark.parametrize("taken", ["davinci-axi-test", "sample-project"])
def test_restore_never_overwrites_an_existing_project(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path, taken: str
) -> None:
    _take(run_cli)
    snapshot_id = next(_root(tmp_path).iterdir()).name
    mark = len(fake_world.write_calls())
    code, out, _ = run_cli(["snapshot", "restore", snapshot_id, "--name", taken, "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "ALREADY_EXISTS"
    assert len(fake_world.write_calls()) == mark


def test_restore_rejects_an_empty_name_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _take(run_cli)
    snapshot_id = next(_root(tmp_path).iterdir()).name
    before = len(fake_world.calls)
    code, _, _ = run_cli(["snapshot", "restore", snapshot_id, "--name", " "])
    assert code == 2 and len(fake_world.calls) == before


def test_restore_unknown_id_is_not_found_without_connecting(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["snapshot", "restore", "nope", "--force"])
    assert code == 1 and "NOT_FOUND" in out and fake_world.calls == []


def test_restore_without_a_drp_is_incomplete(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _take(run_cli)
    directory = next(_root(tmp_path).iterdir())
    (directory / "project.drp").unlink()
    mark = len(fake_world.calls)
    code, out, _ = run_cli(["snapshot", "restore", directory.name, "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_INCOMPLETE"
    assert "state.json" in out and len(fake_world.calls) == mark
    assert _json(run_cli, ["snapshot", "list"])["snapshots"][0]["drp"] is False  # type: ignore[index]


@pytest.mark.parametrize("failing", ["ProjectManager.ExportProject", "ProjectManager.SaveProject"])
def test_restore_aborts_before_the_import_when_the_safety_snapshot_fails(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path, failing: str
) -> None:
    _take(run_cli)
    snapshot_id = next(_root(tmp_path).iterdir()).name
    fake_world.inject(failing, False)
    code, out, _ = run_cli(["snapshot", "restore", snapshot_id, "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert "ProjectManager.ImportProject" not in fake_world.write_calls()


def test_restore_import_failure_is_api_failed_with_the_snapshot_named(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _take(run_cli)
    snapshot_id = next(_root(tmp_path).iterdir()).name
    fake_world.inject("ProjectManager.ImportProject", False)
    code, out, _ = run_cli(["snapshot", "restore", snapshot_id, "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"


def test_restore_import_that_did_not_take_is_verify_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _take(run_cli)
    snapshot_id = next(_root(tmp_path).iterdir()).name
    fake_world.inject("ProjectManager.ImportProject", True)
    code, out, _ = run_cli(["snapshot", "restore", snapshot_id, "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "VERIFY_FAILED"


def test_restore_no_snapshot_is_refused_before_any_resolve_call(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _take(run_cli)
    snapshot_id = next(_root(tmp_path).iterdir()).name
    monkeypatch.delenv("DAVINCI_AXI_ALLOW_UNSNAPSHOTTED", raising=False)
    before = len(fake_world.calls)
    code, out, _ = run_cli(["snapshot", "restore", snapshot_id, "--no-snapshot"])
    assert code == 1 and "WRITE_REFUSED" in out and len(fake_world.calls) == before


def test_restore_with_no_open_project_skips_the_safety_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _take(run_cli)
    snapshot_id = next(_root(tmp_path).iterdir()).name
    fake_world.resolve.pm.current_project = None
    mark = len(fake_world.write_calls())
    code, out, _ = run_cli(["snapshot", "restore", snapshot_id, "--force"])
    assert code == 0 and parse_toon_keys(out)["snapshot"] == "none"
    assert fake_world.write_calls()[mark:] == ["ProjectManager.ImportProject"]


def test_delete_dry_run_keeps_the_directory(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _take(run_cli)
    directory = next(_root(tmp_path).iterdir())
    before = len(fake_world.calls)
    code, out, _ = run_cli(["snapshot", "delete", directory.name])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and keys["irreversible"] == "true"
    assert "warning" in keys and f"snapshot delete {directory.name} --force" in out
    assert directory.is_dir() and len(fake_world.calls) == before


def test_delete_force_removes_only_that_directory_and_never_connects(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    older, newer = _two_snapshots(run_cli, tmp_path)
    before = len(fake_world.calls)
    code, out, _ = run_cli(["snapshot", "delete", older, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["irreversible"] == "true"
    assert keys["snapshot"] == "none"
    assert not (_root(tmp_path) / older).exists() and (_root(tmp_path) / newer).is_dir()
    assert len(fake_world.calls) == before


def test_delete_refuses_a_directory_that_resolves_outside_the_root(
    run_cli: Run, parse_toon_keys: Keys, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere" / "20990101-000000-outside"
    outside.mkdir(parents=True)
    (outside / "meta.json").write_text('{"project": "x"}', encoding="utf-8")
    _root(tmp_path).mkdir(parents=True)
    (_root(tmp_path) / outside.name).symlink_to(outside, target_is_directory=True)
    for argv in (
        ["snapshot", "delete", outside.name],
        ["snapshot", "delete", outside.name, "--force"],
    ):
        code, out, _ = run_cli(argv)
        assert code == 1 and parse_toon_keys(out)["code"] == "WRITE_REFUSED"
    assert (outside / "meta.json").is_file()


def test_delete_rejects_snapshot_flags_it_does_not_take(run_cli: Run, tmp_path: Path) -> None:
    code, _, _ = run_cli(["snapshot", "delete", "x", "--no-snapshot"])
    assert code == 2


def test_bare_snapshot_exits_2_and_lists_subcommands(run_cli: Run) -> None:
    code, out, _ = run_cli(["snapshot"])
    assert code == 2
    for word in ("list", "show", "restore", "delete"):
        assert word in out


def _walk(path: tuple[str, ...], spec: CommandSpec) -> list[tuple[tuple[str, ...], CommandSpec]]:
    found = [(path, spec)]
    for name, child in spec.subcommands.items():
        found.extend(_walk((*path, name), child))
    return found


def test_every_help_exits_zero_with_examples(run_cli: Run) -> None:
    paths = _walk(("snapshot",), snapshot_cmd.SPEC)
    assert len(paths) == 7
    for path, _ in paths:
        code, out, err = run_cli([*path, "--help"])
        assert code == 0 and err == "" and "examples[" in out, path


def test_restore_help_lists_the_mutation_flags(run_cli: Run) -> None:
    _, out, _ = run_cli(["snapshot", "restore", "--help"])
    for flag in ("--force", "--no-snapshot", "--snapshot-out", "--name"):
        assert flag in out


def test_spec_methods_cover_every_write_the_verbs_make() -> None:
    assert {
        "ProjectManager.SaveProject",
        "ProjectManager.ExportProject",
        "ProjectManager.ImportProject",
    } <= set(snapshot_cmd.SPEC.methods)
    assert snapshot_cmd.RESTORE_SPEC.reads_only is False
    assert snapshot_cmd.LIST_SPEC.methods == () and snapshot_cmd.SHOW_SPEC.methods == ()


def test_index_find_and_counts(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    _take(run_cli)
    root = _root(tmp_path)
    meta = find_snapshot(root, next(root.iterdir()).name)
    assert meta.drp is not None and meta.state is not None
    assert counts(meta) == (2, 6)
    meta.state.write_text("[1, 2]", encoding="utf-8")
    assert counts(meta) == (0, 0)
    meta.state.write_text("{broken", encoding="utf-8")
    assert counts(meta) == (0, 0)
    meta.state.unlink()
    assert find_snapshot(root, meta.id).state is None
    assert counts(find_snapshot(root, meta.id)) == (0, 0)
