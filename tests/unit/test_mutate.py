"""The mutation framework, tested once against a sample `Project.SetName` mutation (KTD12)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest

from davinci_axi import connect as connect_module
from davinci_axi import registry
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.fake import FakeWorld, ext_writes_core
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    SnapshotPolicy,
    check_snapshot_flags,
    coerce_like,
    force_cmd,
    parse_assignments,
    refuse_existing,
    run_mutation,
    with_current_timeline,
    with_page,
)
from davinci_axi.resolve_types_edit import ProjectEdit, ProjectManagerEdit, TimelineEdit
from davinci_axi.spec import CommandSpec, Flag, GlobalOpts, Invocation, Positional, Result

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

NAME = "davinci-axi-test"


def run_rename(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    session = inv.connect()
    project = cast(ProjectEdit, session.require_project())
    old, new = project.GetName(), inv.positionals["name"]
    policy = cast(SnapshotPolicy, inv.args["policy"])
    plan = MutationPlan(
        command="sample rename",
        target=f"project {old}",
        changes=[Change("name", old, new)],
        force_cmd=force_cmd(inv, "sample", "rename", new),
        irreversible=bool(inv.args["irreversible"]),
        warning="the old name is gone" if inv.args["irreversible"] else "",
        snapshot=policy,
        next=["Run `davinci-axi projects` to see the project list"],
    )

    def apply() -> None:
        if not project.SetName(new):
            raise api_failed("SetName")

    def verify() -> dict[str, object]:
        return {"name": project.GetName()}

    return run_mutation(inv, None if policy == "none" else session, plan, apply, verify)


def run_open(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    session = inv.connect()
    manager = cast(ProjectManagerEdit, session.project_manager())
    current = session.current_project()
    plan = MutationPlan(
        command="sample open",
        target="project sample-project",
        changes=[Change("current", current.GetName() if current else None, "sample-project")],
        force_cmd=force_cmd(inv, "sample-open"),
        snapshot="if_open",
    )

    def apply() -> None:
        if manager.LoadProject("sample-project") is None:
            raise api_failed("LoadProject")

    def verify() -> dict[str, object]:
        now = session.current_project()
        return {"current": now.GetName() if now else None}

    return run_mutation(inv, session, plan, apply, verify)


SPEC = CommandSpec(
    name="sample",
    summary="Sample mutation used by the framework tests",
    run=run_rename,
    positionals=(Positional("name"),),
    flags=(
        *MUTATION_FLAGS,
        Flag("policy", "str", "required", choices=("required", "if_open", "none")),
        Flag("irreversible", "bool", False),
    ),
    reads_only=False,
)
OPEN_SPEC = CommandSpec(
    name="sample-open",
    summary="Sample if_open mutation",
    run=run_open,
    flags=MUTATION_FLAGS,
    reads_only=False,
)


@pytest.fixture(autouse=True)
def _extended(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext_writes_core.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "sample", f"{__name__}:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "sample-open", f"{__name__}:OPEN_SPEC")
    yield


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _inv(*, force: bool) -> Invocation:
    return Invocation(
        opts=GlobalOpts(),
        args={"force": force, "no_snapshot": False, "snapshot_out": None},
        positionals={},
        connect=lambda: connect_module.connect(GlobalOpts()),
    )


def _log(world: FakeWorld) -> list[str]:
    return [f"{cls}.{method}" for cls, method, _ in world.calls]


def test_dry_run_writes_nothing_and_prints_the_force_command(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["sample", "renamed"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["applied"] == "false"
    assert keys["command"] == "sample rename"
    assert keys["target"] == f"project {NAME}"
    assert "changes[1]{field,before,after}:" in out
    assert f"name,{NAME},renamed" in out
    assert "Run `davinci-axi sample rename renamed --force` to apply this change" in out
    assert "`davinci-axi sample rename 'two words' --force`" in run_cli(["sample", "two words"])[1]
    assert fake_world.write_calls() == []
    assert not _snapshots(tmp_path).exists()
    assert fake_world.resolve.pm.current_project is not None
    assert fake_world.resolve.pm.current_project.name == NAME


def test_force_saves_snapshots_writes_then_reads_back(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["sample", "renamed", "--force"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert fake_world.write_calls() == [
        "ProjectManager.SaveProject",
        "ProjectManager.ExportProject",
        "Project.SetName",
    ]
    log = _log(fake_world)
    assert log.index("Project.SetName") < len(log) - 1 - log[::-1].index("Project.GetName")
    assert keys["applied"] == "true"
    directories = list(_snapshots(tmp_path).iterdir())
    assert [d.name for d in directories] == [keys["snapshot"]]
    assert {p.name for p in directories[0].iterdir()} == {"meta.json", "state.json", "project.drp"}
    meta = json.loads((directories[0] / "meta.json").read_text(encoding="utf-8"))
    assert meta["reason"] == "sample rename" and meta["project"] == NAME
    assert fake_world.resolve.pm.current_project is not None
    assert fake_world.resolve.pm.current_project.name == "renamed"
    assert "Run `davinci-axi projects` to see the project list" in out


def test_restore_hint_only_when_snapshot_command_is_registered(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(registry.COMMANDS, "snapshot", raising=False)
    _, out, _ = run_cli(["sample", "a", "--force"])
    assert "snapshot restore" not in out
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")
    _, out, _ = run_cli(["sample", "b", "--force"])
    assert "davinci-axi snapshot restore " in out


def test_snapshot_out_is_used_and_reported(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "chosen"
    code, out, _ = run_cli(["sample", "x", "--force", "--snapshot-out", str(target)])
    keys = parse_toon_keys(out)
    assert code == 0
    assert (target / "project.drp").exists()
    assert keys["snapshot_dir"] == str(target)
    assert not _snapshots(tmp_path).exists()
    assert "snapshot restore" not in out


def test_export_failure_aborts_before_the_write(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("ProjectManager.ExportProject", False)
    code, out, err = run_cli(["sample", "renamed", "--force"])
    assert (code, err) == (1, "")
    assert parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert "Project.SetName" not in fake_world.write_calls()
    assert fake_world.resolve.pm.current_project is not None
    assert fake_world.resolve.pm.current_project.name == NAME


def test_save_failure_aborts_before_snapshot_and_write(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("ProjectManager.SaveProject", False)
    code, out, _ = run_cli(["sample", "renamed", "--force"])
    assert code == 1
    assert parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert fake_world.write_calls() == ["ProjectManager.SaveProject"]


def test_verify_mismatch_names_field_values_and_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("Project.GetName", NAME)
    code, out, err = run_cli(["sample", "renamed", "--force", "--json"])
    assert (code, err) == (1, "")
    body = json.loads(out)
    assert body["code"] == "VERIFY_FAILED"
    snapshot_id = next(_snapshots(tmp_path).iterdir()).name
    assert body["detail"] == {
        "field": "name",
        "expected": "renamed",
        "actual": NAME,
        "snapshot": snapshot_id,
    }
    assert "name is" in body["error"]


def test_apply_error_carries_the_snapshot_id(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("Project.SetName", False)
    code, out, _ = run_cli(["sample", "renamed", "--force", "--json"])
    body = json.loads(out)
    assert code == 1 and body["code"] == "API_FAILED"
    assert body["detail"]["snapshot"] == next(_snapshots(tmp_path).iterdir()).name


def test_no_snapshot_is_refused_before_any_resolve_call(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DAVINCI_AXI_ALLOW_UNSNAPSHOTTED", raising=False)
    for argv in (["sample", "x", "--no-snapshot"], ["sample", "x", "--force", "--no-snapshot"]):
        code, out, err = run_cli(argv)
        assert (code, err) == (1, "")
        assert parse_toon_keys(out)["code"] == "WRITE_REFUSED"
    assert fake_world.calls == []


def test_no_snapshot_with_the_env_var_skips_save_and_export(
    run_cli: Run,
    parse_toon_keys: Keys,
    fake_world: FakeWorld,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DAVINCI_AXI_ALLOW_UNSNAPSHOTTED", "1")
    code, out, _ = run_cli(["sample", "renamed", "--force", "--no-snapshot"])
    assert code == 0
    assert parse_toon_keys(out)["snapshot"] == "skipped"
    assert fake_world.write_calls() == ["Project.SetName"]
    assert not _snapshots(tmp_path).exists()
    code, _, _ = run_cli(["sample", "x", "--no-snapshot", "--snapshot-out", str(tmp_path)])
    assert code == 2


def test_if_open_snapshots_the_current_project_first(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["sample-open", "--force"])
    assert code == 0
    assert fake_world.write_calls() == [
        "ProjectManager.SaveProject",
        "ProjectManager.ExportProject",
        "ProjectManager.LoadProject",
    ]
    assert next(_snapshots(tmp_path).iterdir()).name == parse_toon_keys(out)["snapshot"]


def test_if_open_with_no_project_open_skips_the_snapshot_but_still_applies(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.resolve.pm.current_project = None
    code, out, _ = run_cli(["sample-open", "--force"])
    assert code == 0
    assert parse_toon_keys(out)["snapshot"] == "none"
    assert fake_world.write_calls() == ["ProjectManager.LoadProject"]
    assert not _snapshots(tmp_path).exists()


def test_policy_none_never_saves(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["sample", "renamed", "--force", "--policy", "none"])
    assert code == 0
    assert parse_toon_keys(out)["snapshot"] == "none"
    assert fake_world.write_calls() == ["Project.SetName"]


@pytest.mark.parametrize("force", [False, True])
def test_irreversible_is_printed_in_both_modes(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, force: bool
) -> None:
    argv = ["sample", "renamed", "--irreversible", *(["--force"] if force else [])]
    code, out, _ = run_cli(argv)
    keys = parse_toon_keys(out)
    assert code == 0
    assert keys["irreversible"] == "true"
    assert keys["warning"] == "the old name is gone"
    assert keys["applied"] == str(force).lower()


def test_a_plan_without_changes_is_a_no_op(fake_world: FakeWorld, tmp_path: Path) -> None:
    inv = _inv(force=True)
    plan = MutationPlan("sample noop", "nothing", [], "davinci-axi sample noop --force")
    result = run_mutation(inv, inv.connect(), plan, lambda: pytest.fail("applied"), dict)
    assert result.data["applied"] is False and result.data["changes"] == []
    assert fake_world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


def test_verify_is_numeric_tolerant_and_a_missing_field_fails(fake_world: FakeWorld) -> None:
    inv = _inv(force=True)
    plan = MutationPlan(
        "sample num",
        "x",
        [Change("opacity", 100.0, 50.0)],
        "davinci-axi sample num --force",
        snapshot="none",
    )
    result = run_mutation(inv, None, plan, lambda: None, lambda: {"opacity": 50.0000001})
    assert result.data["applied"] is True
    with pytest.raises(AxiError) as missing:
        run_mutation(inv, None, plan, lambda: None, dict)
    assert missing.value.code == "VERIFY_FAILED"


def test_changes_are_reduced_to_primitives(fake_world: FakeWorld) -> None:
    inv = _inv(force=False)
    plan = MutationPlan(
        "sample prim", "x", [Change("tracks", ["V1", "V2"], {"a": 1})], "davinci-axi x --force"
    )
    result = run_mutation(inv, None, plan, lambda: None, dict)
    assert result.data["changes"] == [{"field": "tracks", "before": "V1, V2", "after": "{'a': 1}"}]


def test_with_page_switches_and_restores(fake_world: FakeWorld) -> None:
    session = connect_module.connect(GlobalOpts())
    with with_page(session, "color") as switched:
        assert switched is True
        assert fake_world.resolve.page == "color"
    assert fake_world.resolve.page == "edit"
    assert fake_world.page_changes() == ["color", "edit"]


def test_with_page_is_a_no_op_on_the_current_page(fake_world: FakeWorld) -> None:
    session = connect_module.connect(GlobalOpts())
    with with_page(session, "edit") as switched:
        assert switched is False
    assert fake_world.page_changes() == []


def test_with_page_restores_after_an_error(fake_world: FakeWorld) -> None:
    session = connect_module.connect(GlobalOpts())
    with pytest.raises(RuntimeError), with_page(session, "color"):
        raise RuntimeError("boom")
    assert fake_world.resolve.page == "edit"


def test_with_page_failure_is_api_failed(fake_world: FakeWorld) -> None:
    fake_world.inject("Resolve.OpenPage", False)
    session = connect_module.connect(GlobalOpts())
    with pytest.raises(AxiError) as caught, with_page(session, "color"):
        pytest.fail("must not enter")
    assert caught.value.code == "API_FAILED"


def test_with_current_timeline_switches_and_restores(fake_world: FakeWorld) -> None:
    session = connect_module.connect(GlobalOpts())
    other = cast(TimelineEdit, session.require_timeline("Edit v2"))
    with with_current_timeline(session, other) as switched:
        assert switched is True
        assert session.require_timeline().GetName() == "Edit v2"
    assert session.require_timeline().GetName() == "Edit v1"
    same = cast(TimelineEdit, session.require_timeline("Edit v1"))
    fake_world.calls.clear()
    with with_current_timeline(session, same) as switched:
        assert switched is False
    assert fake_world.write_calls() == []


def test_parse_assignments() -> None:
    assert parse_assignments("a=1,b=x y") == {"a": "1", "b": "x y"}
    assert parse_assignments(" k = v ") == {"k": "v"}
    assert parse_assignments("Comments=") == {"Comments": ""}


@pytest.mark.parametrize("text", ["a", "=1", "", "a=1,", "a=1,a=2", "a=1,,b=2"])
def test_parse_assignments_rejects_malformed_text(text: str) -> None:
    with pytest.raises(AxiError) as caught:
        parse_assignments(text)
    assert caught.value.code == "VALIDATION_ERROR" and caught.value.exit_code == 2


def test_coerce_like() -> None:
    assert coerce_like("50", 100.0) == 50.0
    assert isinstance(coerce_like("50", 100.0), float)
    assert coerce_like("7", 1) == 7 and isinstance(coerce_like("7", 1), int)
    assert coerce_like("true", False) is True
    assert coerce_like("0", True) is False
    assert coerce_like("x", "old") == "x"
    assert coerce_like("1920", None) == "1920"


@pytest.mark.parametrize(("value", "like"), [("x", 1), ("x", 1.5), ("1.5", 1), ("maybe", False)])
def test_coerce_like_rejects_bad_values(value: str, like: object) -> None:
    with pytest.raises(AxiError) as caught:
        coerce_like(value, like)
    assert caught.value.code == "VALIDATION_ERROR" and caught.value.exit_code == 2


def test_refuse_existing(tmp_path: Path) -> None:
    target = tmp_path / "out.drp"
    refuse_existing(target, False)
    target.write_text("x", encoding="utf-8")
    with pytest.raises(AxiError) as caught:
        refuse_existing(target, False)
    assert caught.value.code == "FILE_EXISTS" and caught.value.exit_code == 1
    refuse_existing(target, True)


def test_ext_writes_core_project_methods(fake_world: FakeWorld, tmp_path: Path) -> None:
    session = connect_module.connect(GlobalOpts())
    manager = cast(ProjectManagerEdit, session.project_manager())
    project = cast(ProjectEdit, session.require_project())
    assert project.SetName("sample-project") is False  # duplicate name in the folder
    exported = tmp_path / "a.drp"
    assert manager.ExportProject(NAME, str(exported)) is True
    assert manager.ImportProject(str(exported)) is False  # never overwrites
    assert manager.ImportProject(str(exported), "copy") is True
    assert "copy" in manager.GetProjectListInCurrentFolder()
    assert session.require_project().GetName() == NAME  # import does not open the project
    assert manager.ImportProject(str(tmp_path / "missing.drp"), "z") is False
    assert manager.CloseProject(project) is True
    assert session.current_project() is None
    assert manager.CloseProject(project) is False
    assert fake_world.calls_to("ProjectManager.ImportProject")[1] == (str(exported), "copy")


def test_owner_project_walks_the_project_tree(fake_world: FakeWorld) -> None:
    sample = fake_world.resolve.pm.folder.projects["sample-project"]
    current = fake_world.resolve.pm.current_project
    assert current is not None
    assert ext_writes_core.owner_project(sample.media_pool) is sample
    assert ext_writes_core.owner_project(current.media_pool) is current
    nested = fake_world.resolve.pm.add_folder("nested")
    inner = type(sample)(fake_world, "inner")
    nested.projects["inner"] = inner
    assert ext_writes_core.owner_project(inner.media_pool) is inner


def test_rename_keeps_project_order(fake_world: FakeWorld) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    order = list(fake_world.resolve.pm.folder.projects)
    assert cast(ProjectEdit, project).SetName("renamed") is True
    renamed = [("renamed" if n == NAME else n) for n in order]
    assert list(fake_world.resolve.pm.folder.projects) == renamed
