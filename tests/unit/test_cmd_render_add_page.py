"""`render add`, `start` and `settings set` restore the page Resolve opens (Deliver, F40b/F44)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import connect as connect_module
from davinci_axi import registry
from davinci_axi.commands.render_jobs import ADD_SPEC
from davinci_axi.fake import FakeWorld, ext_render
from davinci_axi.fake.core import FakeProject
from davinci_axi.mutate import keep_page
from davinci_axi.spec import GlobalOpts

Run = Callable[[list[str]], tuple[int, str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_render.extend()
    monkeypatch.setitem(registry.COMMANDS, "render", "davinci_axi.commands.render:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")


@pytest.fixture(autouse=True)
def _deliver_opens(fake_world: FakeWorld) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    ext_render.add_opens_deliver(project)


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    path = (tmp_path / "out").resolve()
    path.mkdir()
    return path


def _add(out_dir: Path, *extra: str) -> list[str]:
    return ["render", "add", "--target-dir", str(out_dir), "--json", *extra]


def test_render_add_puts_the_page_back(run_cli: Run, fake_world: FakeWorld, out_dir: Path) -> None:
    assert fake_world.resolve.page == "edit"
    code, out, _ = run_cli(_add(out_dir, "--force"))
    assert code == 0
    assert fake_world.resolve.page == "edit"
    assert fake_world.page_changes() == ["edit"]
    assert json.loads(out)["page_restored"] is True


def test_render_add_from_the_deliver_page_does_not_switch(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    fake_world.resolve.page = "deliver"
    code, out, _ = run_cli(_add(out_dir, "--force"))
    assert code == 0
    assert fake_world.page_changes() == []
    assert "page_restored" not in json.loads(out)


def test_dry_run_does_not_touch_the_page(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, out, _ = run_cli(_add(out_dir))
    assert code == 0
    assert fake_world.page_changes() == []
    assert "page_restored" not in json.loads(out)


def test_open_page_refusal_is_reported_not_fatal(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    fake_world.inject("Resolve.OpenPage", False)
    code, out, _ = run_cli(_add(out_dir, "--force"))
    assert code == 0
    assert json.loads(out)["page_restored"] is False


def test_target_dir_is_still_required_and_help_points_at_settings_set(run_cli: Run) -> None:
    code, _, _ = run_cli(["render", "add", "--force"])
    assert code == 2
    flag = next(f for f in ADD_SPEC.flags if f.name == "target-dir")
    assert flag.required and "render settings set" in flag.help
    assert any("page_restored" in note for note in ADD_SPEC.notes)


def test_keep_page_unit(fake_world: FakeWorld) -> None:
    session = connect_module.connect(GlobalOpts(timeout=2.0))
    with keep_page(session) as state:
        fake_world.resolve.OpenPage("deliver")
    assert state == {"page_restored": True}
    assert fake_world.resolve.page == "edit"
    with keep_page(session) as state:
        pass
    assert state == {}
    with pytest.raises(RuntimeError), keep_page(session):
        fake_world.resolve.OpenPage("color")
        raise RuntimeError("boom")
    assert fake_world.resolve.page == "edit"


def _start(*extra: str) -> list[str]:
    return ["render", "start", "job-2", "--json", "--poll-interval", "0", *extra]


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def test_render_start_no_wait_reports_the_page_it_could_not_restore(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(_start("--force", "--no-wait"))
    assert code == 0
    data = json.loads(out)
    assert data["page_restored"] is False
    assert data["page_before"] == "edit"
    assert fake_world.page_changes() == []
    assert fake_world.resolve.page == "deliver"
    assert any("page set edit --force" in line for line in data["help"])


def test_render_start_with_wait_restores_after_completion(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(_start("--force"))
    assert code == 0
    data = json.loads(out)
    assert data["page_restored"] is True
    assert "page_before" not in data
    assert fake_world.resolve.page == "edit"
    assert _project(fake_world).render_status["job-2"]["JobStatus"] == "Complete"


def test_render_start_dry_run_does_not_touch_the_page(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(_start())
    assert code == 0
    assert fake_world.page_changes() == []
    assert "page_restored" not in json.loads(out)


def test_render_start_from_deliver_does_not_switch(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.resolve.page = "deliver"
    code, out, _ = run_cli(_start("--force", "--no-wait"))
    assert code == 0
    assert fake_world.page_changes() == []
    data = json.loads(out)
    assert data["page_restored"] is False
    assert "page_before" not in data


def test_fake_opt_in_moves_the_page_on_all_three_calls(fake_world: FakeWorld) -> None:
    project = _project(fake_world)
    fake_world.resolve.page = "edit"
    assert project.SetRenderSettings({"TargetDir": "/tmp", "CustomName": "x"})
    assert fake_world.resolve.page == "deliver"
    fake_world.resolve.page = "edit"
    assert project.AddRenderJob()
    assert fake_world.resolve.page == "deliver"
    fake_world.resolve.page = "edit"
    assert project.StartRendering(["job-2"])
    assert fake_world.resolve.page == "deliver"
    ext_render.add_opens_deliver(project, False)
    fake_world.resolve.page = "edit"
    assert project.SetRenderSettings({"CustomName": "y"})
    assert fake_world.resolve.page == "edit"


def test_fake_page_reads_empty_while_rendering(fake_world: FakeWorld) -> None:
    project = _project(fake_world)
    resolve = fake_world.resolve
    assert project.StartRendering(["job-2"])
    assert resolve.GetCurrentPage() == ""
    assert resolve.OpenPage("edit") is False
    assert resolve.page == "deliver"
    project.GetRenderJobStatus("job-2")
    project.GetRenderJobStatus("job-2")
    assert resolve.GetCurrentPage() == "deliver"
    assert resolve.OpenPage("edit") is True
    ext_render.add_opens_deliver(project, False)
    resolve.page = "edit"
    assert project.StartRendering(["job-2"], False)
    assert resolve.GetCurrentPage() == "edit"
