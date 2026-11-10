"""F1: a relative `--snapshot-out`/`--out` reaches Resolve as an absolute path (fake-backed).

The fake opens `filePath` relative to the CLI cwd, so it cannot reproduce Resolve's own cwd;
these tests assert the argument handed to `ExportProject` instead of the file on disk.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi.fake import FakeWorld
from davinci_axi.snapshot import NEVER_SAVED_HINT, resolve_out

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]
EXPORT = "ProjectManager.ExportProject"


def _export_path(world: FakeWorld) -> str:
    calls = world.calls_to(EXPORT)
    assert len(calls) == 1
    return str(calls[0][1])


def test_resolve_out_is_absolute_expanded_and_does_not_follow_symlinks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    assert resolve_out("snaps/../snapshots") == Path(os.path.abspath(tmp_path / "snapshots"))
    assert resolve_out("~/s") == tmp_path / "home" / "s"
    assert resolve_out("link/x") == tmp_path / "link" / "x"


def test_relative_snapshot_out_is_made_absolute_before_export(
    run_cli: Run,
    parse_toon_keys: Keys,
    fake_world: FakeWorld,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    code, out, err = run_cli(
        ["media", "rename", "A001_beach", "--to", "x", "--force", "--snapshot-out", "snapshots"]
    )
    assert (code, err) == (0, "")
    exported = _export_path(fake_world)
    assert os.path.isabs(exported)
    assert exported == str(tmp_path / "snapshots" / "project.drp")
    assert parse_toon_keys(out)["snapshot_dir"] == str(tmp_path / "snapshots")


def test_relative_project_snapshot_out_is_made_absolute(
    run_cli: Run,
    parse_toon_keys: Keys,
    fake_world: FakeWorld,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    code, out, err = run_cli(["project", "snapshot", "--out", "snaps"])
    assert (code, err) == (0, "")
    exported = _export_path(fake_world)
    assert os.path.isabs(exported)
    assert exported == str(tmp_path / "snaps" / "project.drp")
    assert parse_toon_keys(out)["dir"] == str(tmp_path / "snaps")


def test_tilde_out_expands(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    code, _, _ = run_cli(["project", "snapshot", "--out", "~/s"])
    assert code == 0
    assert _export_path(fake_world) == str(tmp_path / "s" / "project.drp")


def test_export_failure_help_names_the_drp_path(
    run_cli: Run,
    parse_toon_keys: Keys,
    fake_world: FakeWorld,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    fake_world.inject(EXPORT, False)
    drp = str(tmp_path / "snapshots" / "project.drp")
    code, out, err = run_cli(
        ["media", "rename", "A001_beach", "--to", "x", "--force", "--snapshot-out", "snapshots"]
    )
    assert (code, err) == (1, "")
    assert parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert drp in out
    assert "writable by Resolve" in out
    assert NEVER_SAVED_HINT in out
    assert "MediaPoolItem.SetName" not in fake_world.write_calls()


def test_export_failure_detail_carries_the_drp_path(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    fake_world.inject(EXPORT, False)
    code, out, _ = run_cli(["--json", "project", "snapshot", "--out", "snaps"])
    assert code == 1
    assert str(tmp_path / "snaps" / "project.drp") in out
    assert '"drp"' in out
