from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands.snapshot import parse_duration, prune_candidates
from davinci_axi.commands.snapshot_prune import is_manual
from davinci_axi.fake import FakeWorld
from davinci_axi.snapshot_index import SnapshotMeta, list_snapshots

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")


CUR = "davinci-axi-test"  # the fake world's current project


def _root(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _snap(
    root: Path,
    snap_id: str,
    project: str,
    created: datetime | str,
    reason: str = "render add",
    manual: bool | None = None,
) -> Path:
    directory = root / snap_id
    directory.mkdir(parents=True)
    stamp = created if isinstance(created, str) else created.isoformat(timespec="seconds")
    meta: dict[str, object] = {
        "id": snap_id,
        "project": project,
        "created": stamp,
        "reason": reason,
    }
    if manual is not None:
        meta["manual"] = manual
    (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (directory / "project.drp").mkdir()
    return directory


def _ago(days: float) -> datetime:
    return datetime.now().astimezone() - timedelta(days=days)


def _names(root: Path) -> set[str]:
    return {p.name for p in root.iterdir()}


def _five(root: Path) -> None:
    for n in range(1, 6):  # s1 oldest ... s5 newest
        _snap(root, f"s{n}", CUR, _ago(10 - n))


def test_keep_dry_run_lists_the_oldest_and_changes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    root = _root(tmp_path)
    _five(root)
    code, out, _ = run_cli(["snapshot", "prune", "--keep", "2"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and keys["irreversible"] == "true"
    assert (
        "3 of 5 snapshots" in keys["target"]
        and f"snapshot prune --keep 2 --project {CUR} --force" in out
    )
    assert "snapshots,5,2" in out
    assert _names(root) == {"s1", "s2", "s3", "s4", "s5"} and fake_world.write_calls() == []


def test_keep_force_removes_exactly_the_three_oldest(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    root = _root(tmp_path)
    _five(root)
    code, out, _ = run_cli(["snapshot", "prune", "--keep", "2", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] == "none"
    assert _names(root) == {"s4", "s5"} and fake_world.write_calls() == []


def test_older_than_keeps_each_projects_newest(run_cli: Run, tmp_path: Path) -> None:
    root = _root(tmp_path)
    _snap(root, "a-new", CUR, _ago(1))
    _snap(root, "a-old", CUR, _ago(8))
    _snap(root, "b-only", "beta", _ago(30))
    _snap(root, "b-older", "beta", _ago(40))
    argv = ["snapshot", "prune", "--older-than", "7d", "--all-projects", "--force"]
    code, _, _ = run_cli(argv)
    assert code == 0
    assert _names(root) == {"a-new", "b-only"}


def test_project_filter_scopes_the_prune(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    root = _root(tmp_path)
    _snap(root, "a1", "alpha", _ago(20))
    _snap(root, "a2", "alpha", _ago(10))
    _snap(root, "b1", "beta", _ago(30))
    _snap(root, "b2", "beta", _ago(25))
    argv = ["snapshot", "prune", "--older-than", "1d", "--project", "alpha", "--force"]
    code, _, _ = run_cli(argv)
    assert code == 0 and _names(root) == {"a2", "b1", "b2"} and fake_world.calls == []


def _two_projects(root: Path) -> None:
    _snap(root, "t-old", CUR, _ago(20))
    _snap(root, "t-new", CUR, _ago(10))
    _snap(root, "a-old", "alpha", _ago(30))
    _snap(root, "a-new", "alpha", _ago(25))


def test_default_scope_is_the_current_project(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    root = _root(tmp_path)
    _two_projects(root)
    code, out, _ = run_cli(["snapshot", "prune", "--older-than", "1d"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["scope"] == CUR and f"of project {CUR}" in keys["target"]
    assert f"--project {CUR}" in out and "--all-projects" not in out
    code, _, _ = run_cli(["snapshot", "prune", "--older-than", "1d", "--force"])
    assert code == 0 and _names(root) == {"a-old", "a-new", "t-new"}


def test_all_projects_restores_the_old_scope(
    run_cli: Run, parse_toon_keys: Keys, tmp_path: Path
) -> None:
    root = _root(tmp_path)
    _two_projects(root)
    argv = ["snapshot", "prune", "--older-than", "1d", "--all-projects"]
    code, out, _ = run_cli(argv)
    keys = parse_toon_keys(out)
    assert code == 0 and keys["scope"] == "all projects" and "across all projects" in keys["target"]
    assert "--all-projects --force" in out
    code, _, _ = run_cli([*argv, "--force"])
    assert code == 0 and _names(root) == {"a-new", "t-new"}


def test_project_and_all_projects_together_exit_2(run_cli: Run, tmp_path: Path) -> None:
    root = _root(tmp_path)
    _two_projects(root)
    argv = ["snapshot", "prune", "--older-than", "1d", "--project", "alpha", "--all-projects"]
    code, out, _ = run_cli([*argv, "--force"])
    assert code == 2 and "VALIDATION_ERROR" in out and len(_names(root)) == 4


def test_offline_default_scope_refuses_with_help(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    root = _root(tmp_path)
    _two_projects(root)
    fake_world.connection = "not_running"
    code, out, _ = run_cli(["snapshot", "prune", "--keep", "1", "--force"])
    assert code == 1 and "RESOLVE_NOT_RUNNING" in out
    assert "--project <name>" in out and "--all-projects" in out
    assert len(_names(root)) == 4


def test_project_flag_works_offline(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    root = _root(tmp_path)
    _two_projects(root)
    fake_world.connection = "not_running"
    argv = ["snapshot", "prune", "--older-than", "1d", "--project", "alpha", "--force"]
    code, _, _ = run_cli(argv)
    assert code == 0 and _names(root) == {"t-old", "t-new", "a-new"} and fake_world.calls == []


def test_no_open_project_refuses_with_help(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    root = _root(tmp_path)
    _two_projects(root)
    fake_world.resolve.pm.current_project = None
    code, out, _ = run_cli(["snapshot", "prune", "--keep", "1", "--force"])
    assert code == 1 and "NO_PROJECT" in out
    assert "--project <name>" in out and "--all-projects" in out
    assert len(_names(root)) == 4


@pytest.mark.parametrize(
    "argv",
    [["--older-than", "7x"], ["--older-than", "0d"], ["--keep", "0"], ["--keep", "-3"], []],
)
def test_bad_flags_exit_2_and_delete_nothing(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, argv: list[str]
) -> None:
    root = _root(tmp_path)
    _five(root)
    code, _, _ = run_cli(["snapshot", "prune", *argv, "--force"])
    assert code == 2 and len(_names(root)) == 5 and fake_world.write_calls() == []


def test_nothing_to_prune_is_a_no_op(run_cli: Run, parse_toon_keys: Keys, tmp_path: Path) -> None:
    root = _root(tmp_path)
    _snap(root, "only", CUR, _ago(100))
    code, out, _ = run_cli(["snapshot", "prune", "--older-than", "7d", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and keys["note"] == "nothing to prune"
    assert _names(root) == {"only"}


def test_empty_root_is_a_no_op(run_cli: Run, parse_toon_keys: Keys) -> None:
    code, out, _ = run_cli(["snapshot", "prune", "--keep", "5", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"


def test_symlinked_snapshot_is_skipped_with_a_warning(run_cli: Run, tmp_path: Path) -> None:
    root = _root(tmp_path)
    outside = _snap(tmp_path / "elsewhere", "linked", CUR, _ago(50))
    _snap(root, "newest", CUR, _ago(1))
    (root / "linked").symlink_to(outside, target_is_directory=True)
    code, out, _ = run_cli(["snapshot", "prune", "--older-than", "7d", "--force", "--json"])
    data = json.loads(out)
    assert code == 0 and data["applied"] is False
    assert any("linked" in w for w in data["warnings"])
    assert (outside / "meta.json").is_file() and (root / "linked").is_symlink()


def test_json_lists_every_id_and_toon_truncates(run_cli: Run, tmp_path: Path) -> None:
    root = _root(tmp_path)
    for n in range(14):
        _snap(root, f"s{n:02d}", CUR, _ago(30 - n))
    code, out, _ = run_cli(["snapshot", "prune", "--keep", "1", "--json"])
    data = json.loads(out)
    assert code == 0 and data["delete"] == [f"s{n:02d}" for n in range(13)]
    assert data["kept_newest"] == ["s13"]
    _, toon, _ = run_cli(["snapshot", "prune", "--keep", "1"])
    assert "+3 more" in toon and "s12" not in toon


def test_failed_delete_reports_what_was_removed(
    run_cli: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    _five(root)
    real = shutil.rmtree

    def flaky(path: Path, *args: object, **kwargs: object) -> None:
        if Path(path).name == "s2":
            raise PermissionError("denied")
        real(path)

    monkeypatch.setattr("davinci_axi.commands.snapshot.shutil.rmtree", flaky)
    code, out, _ = run_cli(["snapshot", "prune", "--keep", "1", "--force", "--json"])
    err = json.loads(out)
    assert code == 1 and "DELETE_FAILED" in out
    assert "s1" in out and "s2" in out
    assert (root / "s2").is_dir() and not (root / "s1").exists() and err


def test_parse_duration() -> None:
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("12h") == timedelta(hours=12)
    assert parse_duration("7d") == timedelta(days=7)
    assert parse_duration("2w") == timedelta(weeks=2)


def _meta(snap_id: str, project: str, created: str) -> SnapshotMeta:
    return SnapshotMeta(snap_id, Path(snap_id), project, created, "render add", "", None, None)


def test_candidates_union_and_unparsable_created_never_aged() -> None:
    now = datetime.now().astimezone()
    metas = [
        _meta("a3", "a", (now - timedelta(days=1)).isoformat()),
        _meta("b2", "b", (now - timedelta(days=2)).isoformat()),
        _meta("a2", "a", (now - timedelta(days=9)).isoformat()),
        _meta("junk", "a", "not-a-date"),
        _meta("a1", "a", (now - timedelta(days=20)).isoformat()),
    ]
    aged = prune_candidates(metas, keep=None, older_than=timedelta(days=7), now=now)
    assert [m.id for m in aged] == ["a1", "a2"]
    both = prune_candidates(metas, keep=2, older_than=timedelta(days=7), now=now)
    assert [m.id for m in both] == ["a1", "junk", "a2"]
    assert prune_candidates(metas, keep=5, older_than=None, now=now) == []


def test_listing_survives_a_prune(run_cli: Run, tmp_path: Path) -> None:
    root = _root(tmp_path)
    _five(root)
    run_cli(["snapshot", "prune", "--keep", "3", "--force"])
    assert [m.id for m in list_snapshots(root)] == ["s5", "s4", "s3"]


def _legacy_root(tmp_path: Path) -> Path:
    """Six old legacy snapshots (no `manual` key) plus a newest one that protects the project."""
    root = _root(tmp_path)
    for n, reason in enumerate(
        ["manual", "verify-batch-start", "u3-before-grade", "render add", "fusion tool add"]
    ):
        _snap(root, f"old{n}", CUR, _ago(30 - n), reason=reason)
    _snap(root, "newest", CUR, _ago(1), reason="render add")
    return root


def test_legacy_manual_reasons_are_kept_and_command_reasons_pruned(
    run_cli: Run, parse_toon_keys: Keys, tmp_path: Path
) -> None:
    root = _legacy_root(tmp_path)
    code, out, _ = run_cli(["snapshot", "prune", "--keep", "1"])
    keys = parse_toon_keys(out)
    assert code == 0 and "2 of 6 snapshots" in keys["target"]
    assert "old3" in out and "old4" in out
    for kept in ("old0", "old1", "old2"):
        assert kept in out  # listed under kept_manual
    code, out, _ = run_cli(["snapshot", "prune", "--keep", "1", "--json"])
    extra = json.loads(out)
    assert extra["delete"] == ["old3", "old4"]
    assert sorted(extra["kept_manual"]) == ["old0", "old1", "old2"]
    run_cli(["snapshot", "prune", "--keep", "1", "--force"])
    assert _names(root) == {"old0", "old1", "old2", "newest"}


def test_new_format_manual_flag_wins_over_the_reason(run_cli: Run, tmp_path: Path) -> None:
    root = _root(tmp_path)
    _snap(root, "kept", CUR, _ago(9), reason="render add", manual=True)
    _snap(root, "gone", CUR, _ago(8), reason="before-grade", manual=False)
    _snap(root, "newest", CUR, _ago(1), reason="render add", manual=False)
    run_cli(["snapshot", "prune", "--keep", "1", "--force"])
    assert _names(root) == {"kept", "newest"}


def test_all_prunes_manual_snapshots_too(
    run_cli: Run, parse_toon_keys: Keys, tmp_path: Path
) -> None:
    root = _legacy_root(tmp_path)
    code, out, _ = run_cli(["snapshot", "prune", "--keep", "1", "--all"])
    keys = parse_toon_keys(out)
    assert code == 0 and "5 of 6 snapshots" in keys["target"]
    assert "kept_manual" not in out and f"--keep 1 --project {CUR} --all --force" in out
    run_cli(["snapshot", "prune", "--keep", "1", "--all", "--force"])
    assert _names(root) == {"newest"}


def test_manual_snapshots_still_count_toward_keep(run_cli: Run, tmp_path: Path) -> None:
    root = _root(tmp_path)
    _snap(root, "m1", CUR, _ago(5), manual=True)
    _snap(root, "a2", CUR, _ago(4), manual=False)
    _snap(root, "a3", CUR, _ago(3), manual=False)
    _snap(root, "a4", CUR, _ago(2), manual=False)
    run_cli(["snapshot", "prune", "--keep", "3", "--force"])
    assert _names(root) == {"m1", "a2", "a3", "a4"}  # m1 is beyond keep but manual
    run_cli(["snapshot", "prune", "--keep", "2", "--force"])
    assert _names(root) == {"m1", "a3", "a4"}


def test_is_manual() -> None:
    legacy = _meta("x", "p", "2026-01-01T00:00:00+00:00")
    assert not is_manual(legacy)
    assert is_manual(SnapshotMeta("x", Path("x"), "p", "", "", "", None, None))  # empty reason
    assert is_manual(SnapshotMeta("x", Path("x"), "p", "", "render add", "", None, None, True))
    assert not is_manual(SnapshotMeta("x", Path("x"), "p", "", "keep me", "", None, None, False))


def test_candidates_skip_manual_unless_included() -> None:
    now = datetime.now().astimezone()
    metas = [
        _meta("a3", "a", (now - timedelta(days=1)).isoformat()),
        SnapshotMeta(
            "m", Path("m"), "a", (now - timedelta(days=9)).isoformat(), "x", "", None, None
        ),
        _meta("a1", "a", (now - timedelta(days=20)).isoformat()),
    ]
    older = timedelta(days=7)
    assert [m.id for m in prune_candidates(metas, keep=None, older_than=older, now=now)] == ["a1"]
    got = prune_candidates(metas, keep=None, older_than=older, now=now, include_manual=True)
    assert [m.id for m in got] == ["a1", "m"]


def test_list_snapshots_reads_the_manual_key(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _snap(root, "yes", "p", _ago(3), manual=True)
    _snap(root, "no", "p", _ago(2), manual=False)
    _snap(root, "old", "p", _ago(1))
    found = {m.id: m.manual for m in list_snapshots(root)}
    assert found == {"yes": True, "no": False, "old": None}
