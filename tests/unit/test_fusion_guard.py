from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from davinci_axi import connect as connect_module
from davinci_axi import fusion_guard
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeWorld, ext_fusion
from davinci_axi.fake.timeline import FakeTimelineItem
from davinci_axi.fusion_guard import (
    FUSION_FLAGS,
    UNDO_PREFIX,
    comp_snapshot_hint,
    export_comp,
    find_tool,
    fusion_call,
    preflight,
    resolve_comp,
    tool_row,
    tools_of,
    undo_group,
    validate_target,
)
from davinci_axi.session import Session
from davinci_axi.spec import GlobalOpts, Invocation


@pytest.fixture(autouse=True)
def _extend() -> None:
    ext_fusion.extend()
    ext_fusion.extend()  # idempotent


def _session() -> Session:
    return connect_module.connect(GlobalOpts(timeout=2.0))


def _inv(session: Session, **args: object) -> Invocation:
    defaults: dict[str, object] = {"item": None, "comp": "1", "timeline": None, "active": False}
    defaults.update(args)
    return Invocation(GlobalOpts(), defaults, {}, lambda: session)


def _clip(world: FakeWorld, number: int) -> FakeTimelineItem:
    project = world.resolve.pm.current_project
    assert project is not None and project.current_timeline is not None
    return project.current_timeline.tracks["video"][0].items[number - 1]


def _methods(world: FakeWorld, cls: str) -> list[str]:
    return [method for c, method, _ in world.calls if c == cls]


def _raises(code: str, call: Any, *args: Any, **kwargs: Any) -> AxiError:
    with pytest.raises(AxiError) as caught:
        call(*args, **kwargs)
    assert caught.value.code == code
    return caught.value


# fusion_call


def test_fusion_call_returns_the_value_and_passes_the_arguments() -> None:
    assert fusion_call("add", lambda a, b: a + b, 2, 3) == 5


def test_fusion_call_re_raises_an_axi_error_and_wraps_anything_else() -> None:
    def boom() -> None:
        raise AxiError("NOT_FOUND", "gone")

    def odd() -> None:
        raise KeyError("x")

    assert _raises("NOT_FOUND", fusion_call, "boom", boom).message == "gone"
    assert "KeyError" in _raises("API_FAILED", fusion_call, "odd", odd).message


def test_a_hung_call_times_out_and_makes_no_further_call(fake_world: FakeWorld) -> None:
    comp = ext_fusion.comp_of(_clip(fake_world, 2))
    comp.hang("FindTool")
    started = time.monotonic()
    err = _raises("FUSION_TIMEOUT", fusion_call, "FindTool", comp.FindTool, "Blur1", timeout=0.2)
    assert time.monotonic() - started < 2.0
    assert err.detail["call"] == "FindTool" and "ResolvePython" in " ".join(err.help)
    assert fake_world.calls[-1][:2] == ("FusionComp", "FindTool")
    assert [m for c, m, _ in fake_world.calls if c == "FusionComp"] == ["FindTool"]


def test_find_tool_does_not_list_tools_after_a_timeout(
    fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(fusion_guard.FUSION_TIMEOUT_ENV, "0.2")
    comp = ext_fusion.comp_of(_clip(fake_world, 2))
    comp.hang("FindTool")
    _raises("FUSION_TIMEOUT", find_tool, comp, "Blur1")
    assert "GetToolList" not in _methods(fake_world, "FusionComp")


def test_the_timeout_env_must_be_a_positive_number(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(fusion_guard.FUSION_TIMEOUT_ENV, "soon")
    assert _raises("VALIDATION_ERROR", fusion_call, "x", lambda: 1).exit_code == 2


# preflight


def test_preflight_accepts_the_current_timeline(fake_world: FakeWorld) -> None:
    session = _session()
    for name in (None, "Edit v1"):
        pre = preflight(session, _inv(session, timeline=name))
        assert (pre.timeline, pre.resolvepython_processes, pre.warnings) == ("Edit v1", 0, [])


def test_preflight_refuses_a_timeline_that_is_not_current(fake_world: FakeWorld) -> None:
    session = _session()
    err = _raises(
        "FUSION_TIMELINE_NOT_CURRENT", preflight, session, _inv(session, timeline="Edit v2")
    )
    assert any("timeline open 'Edit v2' --force" in line for line in err.help)
    assert _methods(fake_world, "FusionComp") == []
    assert _raises("NOT_FOUND", preflight, session, _inv(session, timeline="nope")).exit_code == 1


def test_two_resolvepython_processes_add_one_warning(
    fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fusion_guard, "count_resolve_python", lambda: 2)
    session = _session()
    pre = preflight(session, _inv(session))
    assert pre.resolvepython_processes == 2 and len(pre.warnings) == 1
    assert "2 ResolvePython processes" in pre.warnings[0]


def test_count_resolve_python_is_zero_in_fake_mode_and_counts_pgrep_lines_on_darwin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert fusion_guard.count_resolve_python() == 0
    seen: list[list[str]] = []

    def pgrep(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "101\n202\n", "")

    monkeypatch.setattr(fusion_guard, "is_fake", lambda: False)
    monkeypatch.setattr(fusion_guard.sys, "platform", "darwin")
    monkeypatch.setattr(fusion_guard.subprocess, "run", pgrep)
    assert fusion_guard.count_resolve_python() == 2
    assert seen == [["pgrep", "-f", "ResolvePython"]]
    monkeypatch.setattr(fusion_guard.sys, "platform", "linux")
    assert fusion_guard.count_resolve_python() == 0


# resolve_comp


def test_resolve_comp_by_item_comp_index_and_name(fake_world: FakeWorld) -> None:
    session = _session()
    ref = resolve_comp(session, _inv(session, item="V1:2"), mutating=False)
    assert (ref.comp_name, ref.comp_index, ref.item_ref) == ("Composition 1", 1, "V1:2")
    assert ref.item is not None and ref.item.track == "V1" and ref.timeline is not None
    named = resolve_comp(session, _inv(session, item="V1:2", comp="Composition 1"), mutating=True)
    assert named.comp is ref.comp
    assert _methods(fake_world, "Fusion") == []


def test_comp_out_of_range_is_not_found_and_lists_the_names(fake_world: FakeWorld) -> None:
    session = _session()
    for wanted in ("2", "0", "Nope"):
        err = _raises(
            "NOT_FOUND",
            resolve_comp,
            session,
            _inv(session, item="V1:2", comp=wanted),
            mutating=False,
        )
        assert "Composition 1" in " ".join(err.help)


def test_an_item_without_comps_is_not_found(fake_world: FakeWorld) -> None:
    session = _session()
    err = _raises("NOT_FOUND", resolve_comp, session, _inv(session, item="V1:1"), mutating=False)
    assert "no Fusion comp" in err.message


def test_active_is_refused_for_a_mutation_with_exit_2(fake_world: FakeWorld) -> None:
    session = _session()
    err = _raises(
        "VALIDATION_ERROR", resolve_comp, session, _inv(session, active=True), mutating=True
    )
    assert err.exit_code == 2 and "read-only" in err.message
    assert fake_world.calls_to("Resolve.Fusion") == []


def test_target_flags_are_validated_without_a_connection() -> None:
    session = None  # never used: validate_target makes no call
    for args in ({}, {"active": True, "item": "V1:1"}):
        inv = Invocation(GlobalOpts(), {"item": None, "active": False, **args}, {}, lambda: session)  # type: ignore[arg-type,return-value]
        assert _raises("VALIDATION_ERROR", validate_target, inv, mutating=False).exit_code == 2


def test_active_uses_the_comp_open_on_the_fusion_page(fake_world: FakeWorld) -> None:
    session = _session()
    err = _raises("NOT_FOUND", resolve_comp, session, _inv(session, active=True), mutating=False)
    assert "no comp is open on the Fusion page" in err.message
    comp = ext_fusion.comp_of(_clip(fake_world, 2))
    ext_fusion.fusion_of(fake_world).current = comp
    ref = resolve_comp(session, _inv(session, active=True), mutating=False)
    assert ref.comp is comp and ref.item is None and ref.comp_name == "Composition 1"


# export_comp


def test_export_comp_writes_under_the_fusion_snapshot_folder(
    fake_world: FakeWorld, tmp_path: Path
) -> None:
    session = _session()
    ref = resolve_comp(session, _inv(session, item="V1:2"), mutating=True)
    path = export_comp(ref, "fusion tool add")
    assert path.parent == tmp_path / "axi-home" / "snapshots" / "fusion"
    assert path.name.endswith("-item-2-Composition-1.comp")
    assert path.read_text(encoding="utf-8").startswith("fake fusion comp: Composition 1")
    assert fake_world.calls_to("TimelineItem.ExportFusionComp") == [(str(path), 1)]
    again = export_comp(ref, "fusion tool add")
    assert again != path and again.is_file()


def test_a_failed_comp_export_is_fusion_snapshot_failed(
    fake_world: FakeWorld, tmp_path: Path
) -> None:
    session = _session()
    ref = resolve_comp(session, _inv(session, item="V1:2"), mutating=True)
    fake_world.inject("TimelineItem.ExportFusionComp", False)
    err = _raises("FUSION_SNAPSHOT_FAILED", export_comp, ref, "fusion tool add")
    assert "No change was made" in err.help
    assert not list((tmp_path / "axi-home" / "snapshots" / "fusion").glob("*.comp"))


def test_the_active_comp_cannot_be_exported(fake_world: FakeWorld) -> None:
    comp = ext_fusion.comp_of(_clip(fake_world, 2))
    ref = fusion_guard.CompRef(None, "", comp, comp.name, 0, None)
    _raises("FUSION_SNAPSHOT_FAILED", export_comp, ref, "fusion tool add")


# undo_group


def test_undo_group_orders_lock_start_end_unlock(fake_world: FakeWorld) -> None:
    comp = ext_fusion.comp_of(_clip(fake_world, 2))
    with undo_group(comp, UNDO_PREFIX + "fusion tool add"):
        comp.push_undo("fusion tool add")
    calls = [(m, a) for c, m, a in fake_world.calls if c == "FusionComp"]
    assert calls == [
        ("Lock", ()),
        ("StartUndo", ("davinci-axi: fusion tool add",)),
        ("EndUndo", (True,)),
        ("Unlock", ()),
    ]
    assert comp.undo_stack == ["davinci-axi: fusion tool add"] and not comp.IsLocked()


def test_undo_group_discards_the_event_and_unlocks_on_an_error(fake_world: FakeWorld) -> None:
    comp = ext_fusion.comp_of(_clip(fake_world, 2))
    with pytest.raises(RuntimeError), undo_group(comp, UNDO_PREFIX + "x"):
        raise RuntimeError("boom")
    calls = [(m, a) for c, m, a in fake_world.calls if c == "FusionComp"]
    assert calls == [
        ("Lock", ()),
        ("StartUndo", ("davinci-axi: x",)),
        ("EndUndo", (False,)),
        ("Unlock", ()),
    ]
    assert comp.undo_stack == []


def test_undo_group_without_lock_makes_no_lock_call(fake_world: FakeWorld) -> None:
    comp = ext_fusion.comp_of(_clip(fake_world, 2))
    with undo_group(comp, UNDO_PREFIX + "keyframe", lock=False):
        pass
    assert _methods(fake_world, "FusionComp") == ["StartUndo", "EndUndo"]


def test_undo_group_sends_nothing_more_after_a_timeout(fake_world: FakeWorld) -> None:
    comp = ext_fusion.comp_of(_clip(fake_world, 2))
    with pytest.raises(AxiError) as caught, undo_group(comp, UNDO_PREFIX + "x"):
        raise AxiError("FUSION_TIMEOUT", "hung")
    assert caught.value.code == "FUSION_TIMEOUT"
    assert _methods(fake_world, "FusionComp") == ["Lock", "StartUndo"]


# tools


def test_tools_find_tool_and_tool_row(fake_world: FakeWorld) -> None:
    comp = ext_fusion.comp_of(_clip(fake_world, 2))
    assert [tool_row(comp, t)["name"] for t in tools_of(comp)] == ["MediaIn1", "MediaOut1"]
    row = tool_row(comp, find_tool(comp, "MediaOut1"))
    assert row == {"name": "MediaOut1", "id": "MediaOut", "passthrough": False, "pos": (3.0, 0.0)}


def test_find_tool_not_found_lists_the_tool_names(fake_world: FakeWorld) -> None:
    comp = ext_fusion.comp_of(_clip(fake_world, 2))
    err = _raises("NOT_FOUND", find_tool, comp, "Blur1")
    assert "MediaIn1, MediaOut1" in " ".join(err.help)


def test_tool_position_is_none_without_a_flow_view(fake_world: FakeWorld) -> None:
    comp = ext_fusion.comp_of(_clip(fake_world, 2))
    comp.CurrentFrame = None
    assert tool_row(comp, find_tool(comp, "MediaIn1"))["pos"] is None


def test_comp_snapshot_hint_names_the_import_command() -> None:
    text = comp_snapshot_hint(Path("/tmp/x y.comp"))
    assert text == "Re-import with `fusion comp import '/tmp/x y.comp' --item <id> --force`"


def test_fusion_flags_are_the_four_target_flags() -> None:
    assert [f.name for f in FUSION_FLAGS] == ["item", "comp", "timeline", "active"]
    assert next(f for f in FUSION_FLAGS if f.name == "comp").default == "1"


# the fake itself


def test_the_fake_reproduces_the_import_no_op_on_an_item_without_comps(
    fake_world: FakeWorld, tmp_path: Path
) -> None:
    bare, with_comp = _clip(fake_world, 1), _clip(fake_world, 2)
    source = tmp_path / "x.comp"
    source.write_text("{}\n", encoding="utf-8")
    assert bare.ImportFusionComp(str(source)) is None and bare.GetFusionCompNameList() == []
    assert with_comp.ImportFusionComp(str(tmp_path / "missing.comp")) is None
    assert with_comp.ImportFusionComp(str(source)) is not None
    assert with_comp.GetFusionCompNameList() == ["Composition 1", "Composition 2"]


def test_the_fake_comp_lifecycle_and_output_cache(fake_world: FakeWorld, tmp_path: Path) -> None:
    item = _clip(fake_world, 2)
    added = item.AddFusionComp()
    assert added is not None and item.GetFusionCompCount() == 2
    assert item.RenameFusionCompByName("Composition 2", "Soft") and not item.RenameFusionCompByName(
        "Composition 2", "Again"
    )
    assert item.GetFusionCompByName("Soft") is added and item.GetFusionCompByIndex(3) is None
    target = tmp_path / "out.comp"
    assert item.ExportFusionComp(str(target), 2) and "Soft" in target.read_text(encoding="utf-8")
    assert not item.ExportFusionComp(str(target), 9)
    assert item.LoadFusionCompByName("Soft") is added and item.LoadFusionCompByName("zz") is None
    assert item.DeleteFusionCompByName("Soft") and not item.DeleteFusionCompByName("Soft")
    assert item.GetIsFusionOutputCacheEnabled() == "auto"
    assert item.SetFusionOutputCache("enabled") and not item.SetFusionOutputCache("sometimes")
    assert item.GetIsFusionOutputCacheEnabled() == "enabled"


def test_the_fake_graph_registry_and_undo_stack(fake_world: FakeWorld) -> None:
    comp = ext_fusion.comp_of(_clip(fake_world, 2))
    media_in = comp.FindTool("MediaIn1")
    media_out = comp.FindToolByID("MediaOut")
    assert media_in is not None and media_out is not None
    source = media_out.FindMainInput(1).GetConnectedOutput()
    assert source.GetTool() is media_in and len(source.GetConnectedInputs()) == 1
    assert comp.GetToolList(True) == {} and len(comp.GetToolList(False, "Blur")) == 0
    assert media_out.GetInput("Index") == 0.0 and media_out.GetInput("Nope") is None
    fusion = fake_world.resolve.Fusion()
    assert fusion.GetCurrentComp() is None
    assert len(fusion.GetRegList("CT_Modifier")) == 1 and len(fusion.GetRegList()) == 7
    assert fusion.GetRegAttrs("Blur")["REGS_ID"] == "Blur" and fusion.GetRegAttrs("zz") is None
    assert comp.GetUndoStack() == [] and comp.Undo(1) is False
    comp.StartUndo("a")
    comp.push_undo("a")
    comp.EndUndo(True)
    assert comp.GetUndoStack() == ["a"] and comp.Undo(1) and comp.GetUndoStack() == []
    assert ext_fusion.fusion_write_calls(fake_world) == [
        "FusionComp.Undo",
        "FusionComp.StartUndo",
        "FusionComp.EndUndo",
        "FusionComp.Undo",
    ]
