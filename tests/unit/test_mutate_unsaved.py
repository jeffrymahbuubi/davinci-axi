"""Live findings about unsaved projects (Resolve 21.1.1).

Resolve starts on an unsaved `Untitled Project` that is not in the project list. Saving it can
raise a modal dialog that hangs the scripting call, so the safety step skips the snapshot for
`if_open` mutations and refuses `required` ones before any write. And ExportProject fails on a
project never saved since it was created, so export errors say how to fix that.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeProject, FakeWorld, ext_project
from davinci_axi.snapshot import NEVER_SAVED_HINT

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _registered(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_project.extend()
    monkeypatch.setitem(registry.COMMANDS, "project", "davinci_axi.commands.project:SPEC")
    yield


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _open_unsaved_default(world: FakeWorld) -> None:
    world.resolve.pm.current_project = FakeProject(world, "Untitled Project")


def test_create_from_the_unsaved_default_project_never_saves_it(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _open_unsaved_default(fake_world)
    code, out, _ = run_cli(["project", "create", "scratch", "--force"])
    assert code == 0, out
    assert parse_toon_keys(out)["snapshot"] == "none"
    assert fake_world.write_calls() == ["ProjectManager.CreateProject"]
    assert not _snapshots(tmp_path).exists()


def test_required_snapshot_on_the_unsaved_default_project_refuses_before_any_write(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _open_unsaved_default(fake_world)
    code, out, _ = run_cli(["project", "rename", "--to", "promo", "--force"])
    assert code == 1
    assert "SNAPSHOT_FAILED" in out and "unsaved default project" in out
    assert fake_world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


def test_a_saved_project_named_untitled_is_still_snapshotted(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.resolve.pm.add_project("Untitled Project 1", current=True)
    code, out, _ = run_cli(["project", "create", "scratch", "--force"])
    assert code == 0, out
    assert fake_world.write_calls()[:2] == [
        "ProjectManager.SaveProject",
        "ProjectManager.ExportProject",
    ]


def test_export_failure_says_to_save_a_never_saved_project_first(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("ProjectManager.ExportProject", False)
    code, out, _ = run_cli(["project", "export", str(tmp_path / "x.drp")])
    assert code == 1
    assert "API_FAILED" in out and "project save --force" in out
    assert NEVER_SAVED_HINT.split(":")[0] in out
