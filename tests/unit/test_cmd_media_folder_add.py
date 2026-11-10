"""`media folder add` puts the previous current folder back after Resolve switches away."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_writes_core
from davinci_axi.fake import ext_media_edit as ext

Run = Callable[[list[str]], tuple[int, str, str]]

SET_CURRENT = "MediaPool.SetCurrentFolder"
ADD = "MediaPool.AddSubFolder"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext.extend()
    monkeypatch.setitem(registry.COMMANDS, "media", "davinci_axi.commands.media:SPEC")
    yield


def _keys(out: str) -> dict[str, str]:
    found = (re.match(r"^([A-Za-z_][\w.-]*): (.*)$", line) for line in out.splitlines())
    return {m.group(1): m.group(2) for m in found if m}


def _pool(world: FakeWorld):  # type: ignore[no-untyped-def]
    project = world.resolve.pm.current_project
    assert project is not None
    return project.media_pool


def test_dry_run_makes_no_current_folder_call(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _, _ = run_cli(["media", "folder", "add", "Dailies"])
    assert code == 0
    assert fake_world.calls_to(SET_CURRENT) == []
    assert fake_world.calls_to(ADD) == []


def test_force_restores_the_root_as_current(run_cli: Run, fake_world: FakeWorld) -> None:
    pool = _pool(fake_world)
    root = pool.root
    code, out, _ = run_cli(["media", "folder", "add", "Dailies", "--force"])
    assert code == 0 and _keys(out)["current_folder_restored"] == "true"
    assert pool.current is root
    assert fake_world.calls_to(SET_CURRENT) == [(root,)]


def test_in_parent_with_root_current_stays_on_root(run_cli: Run, fake_world: FakeWorld) -> None:
    pool = _pool(fake_world)
    assert pool.current is pool.root
    code, out, _ = run_cli(
        ["media", "folder", "add", "Selects", "--in", "Master/Footage", "--force"]
    )
    assert code == 0 and _keys(out)["current_folder_restored"] == "true"
    assert pool.current is pool.root
    assert [f.name for f in pool.root.subfolders[0].subfolders] == ["Selects"]
    assert "--in is now --parent; the old name still works" in out


def test_parent_is_the_current_name_and_prints_no_rename_note(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    pool = _pool(fake_world)
    code, out, _ = run_cli(
        ["media", "folder", "add", "Selects", "--parent", "Master/Footage", "--force"]
    )
    assert code == 0
    assert [f.name for f in pool.root.subfolders[0].subfolders] == ["Selects"]
    assert "still works" not in out


def test_a_failed_restore_still_succeeds_but_says_so(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject(SET_CURRENT, False)
    code, out, _ = run_cli(["media", "folder", "add", "Dailies", "--force"])
    assert code == 0 and _keys(out)["current_folder_restored"] == "false"
    assert [f.name for f in _pool(fake_world).root.subfolders] == ["Footage", "Dailies"]


def test_a_failed_add_never_touches_the_current_folder(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject(ADD, None)
    code, out, _ = run_cli(["media", "folder", "add", "Dailies", "--force"])
    assert code == 1 and _keys(out)["code"] == "API_FAILED"
    assert fake_world.calls_to(SET_CURRENT) == []


def test_help_names_the_restore(run_cli: Run) -> None:
    code, out, _ = run_cli(["media", "folder", "add", "--help"])
    assert code == 0 and "current_folder_restored" in out
