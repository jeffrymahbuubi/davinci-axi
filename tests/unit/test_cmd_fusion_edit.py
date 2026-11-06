"""`fusion tool add|set|connect|...`, `fusion keyframe set` and `fusion undo` (U17b) on the fake."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_fusion, ext_fusion_edit, ext_writes_core
from davinci_axi.fake.ext_fusion import FakeComp
from davinci_axi.fake.timeline import FakeTimelineItem

Run = Callable[[list[str]], tuple[int, str, str]]

SAVE, EXPORT_PROJECT = "ProjectManager.SaveProject", "ProjectManager.ExportProject"
ITEM = ["--item", "V1:2"]
FLOW = (
    SAVE,
    EXPORT_PROJECT,
    "TimelineItem.ExportFusionComp",
    "FusionComp.Lock",
    "FusionComp.StartUndo",
    "FusionComp.AddTool",
    "FusionComp.EndUndo",
    "FusionComp.Unlock",
)


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext_fusion.extend()
    ext_fusion_edit.extend()
    ext_fusion_edit.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "fusion", "davinci_axi.commands.fusion_comp:SPEC")
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    yield


def _clip(world: FakeWorld, number: int) -> FakeTimelineItem:
    project = world.resolve.pm.current_project
    assert project is not None and project.current_timeline is not None
    return project.current_timeline.tracks["video"][0].items[number - 1]


def _comp(world: FakeWorld) -> FakeComp:
    return ext_fusion.comp_of(_clip(world, 2))


def _json(run_cli: Run, argv: list[str]) -> tuple[int, dict[str, Any]]:
    code, out, _ = run_cli([*argv, "--json"])
    return code, json.loads(out)


def _calls(world: FakeWorld, names: tuple[str, ...]) -> list[str]:
    return [n for cls, method, _ in world.calls if (n := f"{cls}.{method}") in names]


def _add_blur(world: FakeWorld, pos: tuple[float, float] = (1.0, 1.0)) -> None:
    """Put a `Blur1` in the comp without the CLI, then forget the calls and undo entries."""
    comp = _comp(world)
    assert comp.AddTool("Blur", *pos) is not None
    world.calls.clear()
    comp.undo_stack.clear()


def _untouched(world: FakeWorld, tmp_path: Path) -> None:
    assert world.write_calls() == []
    assert ext_fusion.fusion_write_calls(world) == []
    assert not (tmp_path / "axi-home" / "snapshots").exists()


def _rows(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = data[key]
    assert isinstance(rows, list)
    return rows


def test_tool_add_dry_run_writes_nothing(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, data = _json(run_cli, ["fusion", "tool", "add", "Blur", *ITEM])
    assert code == 0 and data["applied"] is False
    assert _rows(data, "changes") == [{"field": "tool", "before": None, "after": "Blur1"}]
    _untouched(fake_world, tmp_path)
    assert not (tmp_path / "axi-home" / "snapshots" / "fusion").exists()


def test_tool_add_force_runs_the_whole_flow_in_order(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, data = _json(run_cli, ["fusion", "tool", "add", "Blur", *ITEM, "--force"])
    assert code == 0 and data["applied"] is True
    assert _calls(fake_world, FLOW) == list(FLOW)
    assert fake_world.calls_to("FusionComp.StartUndo") == [("davinci-axi: fusion tool add",)]
    assert fake_world.calls_to("FusionComp.EndUndo") == [(True,)]
    snapshot = Path(str(data["comp_snapshot"]).replace("~", str(Path.home()), 1))
    assert snapshot.is_file() and snapshot.parent.name == "fusion"
    assert [t.name for t in _comp(fake_world).tools] == ["MediaIn1", "MediaOut1", "Blur1"]
    code, out, _ = run_cli(["fusion", "undo-stack", *ITEM])
    assert code == 0 and "davinci-axi: fusion tool add" in out and "true" in out
    assert data["resolvepython_processes"] == 0


def test_tool_add_auto_places_before_media_out_and_name_and_pos_are_honoured(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    run_cli(["fusion", "tool", "add", "Blur", *ITEM, "--name", "Soft", "--force"])
    run_cli(["fusion", "tool", "add", "Blur", *ITEM, "--pos", "4,2", "--force"])
    comp = _comp(fake_world)
    assert [t.name for t in comp.tools] == ["MediaIn1", "MediaOut1", "Soft", "Blur1"]
    assert comp.flow_view.positions["Blur1"] == (4.0, 2.0)
    _, out, _ = run_cli(["fusion", "graph", *ITEM, "--json"])
    edges = {(e["from"], e["to"]) for e in json.loads(out)["edges"]}
    assert edges == {("MediaIn1", "Soft"), ("Soft", "MediaOut1")}


def test_tool_add_after_connects_to_the_named_tool_and_second_add_gets_the_next_name(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    run_cli(["fusion", "tool", "add", "Blur", *ITEM, "--force"])
    code, _ = _json(
        run_cli, ["fusion", "tool", "add", "Blur", *ITEM, "--after", "MediaIn1", "--force"]
    )
    assert code == 0
    comp = _comp(fake_world)
    assert comp.tools[-1].name == "Blur2"
    assert comp.tools[-1].inputs[0].connected is comp.tools[0].outputs[0]


def test_tool_add_refuses_bad_input_before_any_write(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["fusion", "tool", "add", "Blur", *ITEM, "--pos", "x", "--force"])
    assert code == 2 and "flow position" in out and fake_world.calls == []
    code, _, _ = run_cli(
        ["fusion", "tool", "add", "Blur", *ITEM, "--pos", "1,1", "--after", "MediaIn1"]
    )
    assert code == 2
    code, out, _ = run_cli(
        ["fusion", "tool", "add", "Blur", *ITEM, "--name", "MediaIn1", "--force"]
    )
    assert code == 2 and "already exists" in out
    code, out, _ = run_cli(["fusion", "tool", "add", "Blur", *ITEM, "--after", "Nope", "--force"])
    assert code == 1 and "NOT_FOUND" in out and "MediaIn1" in out
    assert ext_fusion.fusion_write_calls(fake_world) == []


def test_tool_add_unknown_registry_id_is_api_failed_and_discards_the_undo_event(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["fusion", "tool", "add", "Nope", *ITEM, "--force"])
    assert code == 1 and "API_FAILED" in out
    assert fake_world.calls_to("FusionComp.EndUndo") == [(False,)]
    assert _calls(fake_world, ("FusionComp.Unlock",)) == ["FusionComp.Unlock"]
    assert "comp_snapshot" in out and "Re-import" in out


def test_tool_add_refused_by_a_locked_comp_is_api_failed(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    ext_fusion_edit.edit_state(_comp(fake_world)).locked_by_other = True
    code, out, _ = run_cli(["fusion", "tool", "add", "Blur", *ITEM, "--force"])
    assert code == 1 and "API_FAILED" in out and "Blur1" not in str(_comp(fake_world).tools)


def test_tool_set_reads_back_and_dry_run_writes_nothing(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _add_blur(fake_world)
    code, data = _json(run_cli, ["fusion", "tool", "set", "Blur1", "XBlurSize=2", *ITEM])
    assert code == 0 and data["applied"] is False
    assert _rows(data, "changes") == [{"field": "XBlurSize", "before": 1.0, "after": 2.0}]
    _untouched(fake_world, tmp_path)
    code, data = _json(run_cli, ["fusion", "tool", "set", "Blur1", "XBlurSize=2", *ITEM, "--force"])
    assert code == 0 and data["applied"] is True
    assert _comp(fake_world).FindTool("Blur1").GetInput("XBlurSize") == 2.0
    assert fake_world.calls_to("FusionComp.StartUndo") == [("davinci-axi: fusion tool set",)]


def test_tool_set_validates_before_any_write(run_cli: Run, fake_world: FakeWorld) -> None:
    _add_blur(fake_world)
    code, out, _ = run_cli(["fusion", "tool", "set", "Blur1", "Nope=1", *ITEM, "--force"])
    assert code == 1 and "NOT_FOUND" in out and "XBlurSize" in out
    code, out, _ = run_cli(["fusion", "tool", "set", "Blur1", "XBlurSize=abc", *ITEM, "--force"])
    assert code == 2 and "cannot read abc" in out
    code, out, _ = run_cli(["fusion", "tool", "set", "Blur1", "Input=1", *ITEM, "--force"])
    assert code == 2 and "cannot be set" in out
    code, out, _ = run_cli(["fusion", "tool", "set", "Nope1", "XBlurSize=1", *ITEM, "--force"])
    assert code == 1 and "Blur1" in out
    assert ext_fusion.fusion_write_calls(fake_world) == []


def test_tool_set_unchanged_value_is_a_noop(run_cli: Run, fake_world: FakeWorld) -> None:
    _add_blur(fake_world)
    code, data = _json(run_cli, ["fusion", "tool", "set", "Blur1", "XBlurSize=1", *ITEM, "--force"])
    assert code == 0 and data["applied"] is False and data["changes"] == []
    assert ext_fusion.fusion_write_calls(fake_world) == []


def test_tool_connect_and_disconnect_change_the_graph(run_cli: Run, fake_world: FakeWorld) -> None:
    _add_blur(fake_world)
    code, data = _json(run_cli, ["fusion", "tool", "connect", "Blur1", "MediaOut1.Input", *ITEM])
    assert code == 0 and _rows(data, "changes")[0]["before"] == "MediaIn1.Output"
    assert ext_fusion.fusion_write_calls(fake_world) == []
    code, data = _json(
        run_cli, ["fusion", "tool", "connect", "Blur1", "MediaOut1", *ITEM, "--force"]
    )
    assert code == 0 and data["applied"] is True
    _, graph = _json(run_cli, ["fusion", "graph", *ITEM])
    assert {"from": "Blur1", "output": "Output", "to": "MediaOut1", "input": "Input"} in graph[
        "edges"
    ]
    code, data = _json(
        run_cli, ["fusion", "tool", "disconnect", "MediaOut1.Input", *ITEM, "--force"]
    )
    assert code == 0 and data["applied"] is True
    _, graph = _json(run_cli, ["fusion", "graph", *ITEM])
    assert graph["edges"] == "0 edges in comp Composition 1"


def test_tool_connect_to_a_connected_input_is_a_noop_and_bad_ports_fail(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, data = _json(
        run_cli, ["fusion", "tool", "connect", "MediaIn1", "MediaOut1.Input", *ITEM, "--force"]
    )
    assert code == 0 and data["applied"] is False
    code, out, _ = run_cli(["fusion", "tool", "disconnect", "MediaOut1", *ITEM, "--force"])
    assert code == 2 and "needs an input" in out
    code, out, _ = run_cli(["fusion", "tool", "connect", "MediaIn1.Nope", "MediaOut1.Input", *ITEM])
    assert code == 1 and "NOT_FOUND" in out
    code, out, _ = run_cli(["fusion", "tool", "connect", "MediaIn1", "MediaOut1.Nope", *ITEM])
    assert code == 1 and "NOT_FOUND" in out and "Input" in out
    assert ext_fusion.fusion_write_calls(fake_world) == []


def test_tool_rename_bypass_move(run_cli: Run, fake_world: FakeWorld) -> None:
    _add_blur(fake_world)
    code, _ = _json(
        run_cli, ["fusion", "tool", "rename", "Blur1", "--to", "Soft", *ITEM, "--force"]
    )
    comp = _comp(fake_world)
    assert code == 0 and comp.FindTool("Soft") is not None and comp.FindTool("Blur1") is None
    code, data = _json(run_cli, ["fusion", "tool", "bypass", "Soft", *ITEM, "--force"])
    assert code == 0 and data["applied"] is True and comp.FindTool("Soft").passthrough is True
    code, data = _json(run_cli, ["fusion", "tool", "bypass", "Soft", "--on", *ITEM, "--force"])
    assert data["applied"] is False
    code, data = _json(run_cli, ["fusion", "tool", "bypass", "Soft", "--off", *ITEM, "--force"])
    assert comp.FindTool("Soft").passthrough is False
    code, _ = _json(run_cli, ["fusion", "tool", "move", "Soft", "--pos", "5,2", *ITEM, "--force"])
    assert code == 0 and comp.flow_view.positions["Soft"] == (5.0, 2.0)


def test_tool_rename_and_bypass_and_move_reject_bad_arguments(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _add_blur(fake_world)
    code, out, _ = run_cli(
        ["fusion", "tool", "rename", "Blur1", "--to", "MediaIn1", *ITEM, "--force"]
    )
    assert code == 2 and "already exists" in out
    code, out, _ = run_cli(["fusion", "tool", "rename", "Blur1", "--to", "a b", *ITEM, "--force"])
    assert code == 2
    code, _, _ = run_cli(["fusion", "tool", "bypass", "Blur1", "--on", "--off", *ITEM, "--force"])
    assert code == 2
    code, _, _ = run_cli(["fusion", "tool", "move", "Blur1", "--pos", "1", *ITEM, "--force"])
    assert code == 2
    assert ext_fusion.fusion_write_calls(fake_world) == []


def test_tool_move_without_a_flow_view_is_api_failed(run_cli: Run, fake_world: FakeWorld) -> None:
    _add_blur(fake_world)
    _comp(fake_world).CurrentFrame = None
    code, out, _ = run_cli(["fusion", "tool", "move", "Blur1", "--pos", "5,2", *ITEM, "--force"])
    assert code == 1 and "no FlowView" in out
    assert ext_fusion.fusion_write_calls(fake_world) == []


def test_tool_delete_is_irreversible_and_exports_the_comp_first(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _add_blur(fake_world)
    code, data = _json(run_cli, ["fusion", "tool", "delete", "Blur1", *ITEM, "--force"])
    assert code == 0 and data["irreversible"] is True and "warning" in data
    assert _comp(fake_world).FindTool("Blur1") is None
    assert _calls(fake_world, ("TimelineItem.ExportFusionComp", "FusionTool.Delete")) == [
        "TimelineItem.ExportFusionComp",
        "FusionTool.Delete",
    ]


def test_tool_save_and_load_settings_use_files(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _add_blur(fake_world)
    target = tmp_path / "blur.setting"
    code, data = _json(run_cli, ["fusion", "tool", "save-settings", "Blur1", str(target), *ITEM])
    assert code == 0 and target.is_file() and data["bytes"] > 0
    assert fake_world.write_calls() == []
    assert not (tmp_path / "axi-home" / "snapshots").exists()
    code, out, _ = run_cli(["fusion", "tool", "save-settings", "Blur1", str(target), *ITEM])
    assert code == 1 and "FILE_EXISTS" in out
    code, _, _ = run_cli(
        ["fusion", "tool", "save-settings", "Blur1", str(target), *ITEM, "--overwrite"]
    )
    assert code == 0
    code, data = _json(
        run_cli, ["fusion", "tool", "load-settings", "Blur1", str(target), *ITEM, "--force"]
    )
    assert code == 0 and data["applied"] is True and "not read back" in str(data["warning"])
    assert fake_world.calls_to("FusionTool.LoadSettings") == [(str(target),)]
    code, out, _ = run_cli(
        ["fusion", "tool", "load-settings", "Blur1", str(tmp_path / "x"), *ITEM, "--force"]
    )
    assert code == 1 and "NOT_FOUND" in out


def test_keyframe_set_is_two_steps_without_lock_and_reads_back(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    pauses: list[float] = []
    monkeypatch.setattr(time, "sleep", pauses.append)
    _add_blur(fake_world)
    argv = ["fusion", "keyframe", "set", "Blur1", "XBlurSize", "0=0,24=5", *ITEM]
    code, data = _json(run_cli, argv)
    assert code == 0 and ext_fusion.fusion_write_calls(fake_world) == []
    code, data = _json(run_cli, [*argv, "--force"])
    assert code == 0 and data["applied"] is True
    executed = fake_world.calls_to("FusionComp.Execute")
    assert len(executed) == 1 and "BezierSpline" in str(executed[0][0])
    sets = fake_world.calls_to("FusionTool.SetInput")
    assert sets == [("XBlurSize", 0.0, 0.0), ("XBlurSize", 5.0, 24.0)]
    assert _calls(fake_world, ("FusionComp.Lock", "FusionComp.Unlock")) == []
    assert pauses == [0.12, 0.12]
    tool = _comp(fake_world).FindTool("Blur1")
    assert tool.GetInput("XBlurSize", 24) == 5.0 and tool.GetInput("XBlurSize", 0) == 0.0
    assert fake_world.calls_to("FusionComp.StartUndo") == [("davinci-axi: fusion keyframe set",)]


def test_fake_keeps_only_the_last_value_without_the_spline_step() -> None:
    from davinci_axi.fake import default_world

    world = default_world()
    tool = _comp(world).AddTool("Blur", 1.0, 1.0)
    tool.SetInput("XBlurSize", 0.0, 0.0)
    tool.SetInput("XBlurSize", 5.0, 24.0)
    assert tool.GetInput("XBlurSize", 0) == 5.0 and tool.GetInput("XBlurSize", 24) == 5.0


def test_keyframe_set_rejects_bad_keys_and_unsafe_names(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _add_blur(fake_world)
    code, out, _ = run_cli(
        ["fusion", "keyframe", "set", "Blur1", "XBlurSize", "a=b", *ITEM, "--force"]
    )
    assert code == 2 and "time=value" in out
    code, _, _ = run_cli(
        ["fusion", "keyframe", "set", "Blur1", "XBlurSize", "oops", *ITEM, "--force"]
    )
    assert code == 2
    code, _, _ = run_cli(["fusion", "keyframe", "set", 'B"x', "XBlurSize", "0=1", *ITEM, "--force"])
    assert code == 2
    code, out, _ = run_cli(["fusion", "keyframe", "set", "Blur1", "Input", "0=1", *ITEM, "--force"])
    assert code == 2 and "cannot be animated" in out
    code, out, _ = run_cli(["fusion", "keyframe", "set", "Blur1", "Nope", "0=1", *ITEM, "--force"])
    assert code == 1 and "NOT_FOUND" in out
    assert ext_fusion.fusion_write_calls(fake_world) == []


def test_undo_refuses_foreign_entries_unless_any(run_cli: Run, fake_world: FakeWorld) -> None:
    comp = _comp(fake_world)
    comp.undo_stack.extend(["davinci-axi: fusion tool add", "user moved a tool"])
    code, out, _ = run_cli(["fusion", "undo", *ITEM, "--force"])
    assert code == 1 and "UNDO_REFUSED" in out and "user moved a tool" in out
    assert fake_world.write_calls() == [] and ext_fusion.fusion_write_calls(fake_world) == []
    code, data = _json(run_cli, ["fusion", "undo", *ITEM, "--any", "--force"])
    assert (
        code == 0
        and data["applied"] is True
        and comp.undo_stack == ["davinci-axi: fusion tool add"]
    )
    assert data["undone"] == "user moved a tool"


def test_undo_undoes_our_own_entries_outside_an_undo_group(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    run_cli(["fusion", "tool", "add", "Blur", *ITEM, "--force"])
    fake_world.calls.clear()
    code, data = _json(run_cli, ["fusion", "undo", *ITEM])
    assert (
        code == 0 and data["applied"] is False and ext_fusion.fusion_write_calls(fake_world) == []
    )
    code, data = _json(run_cli, ["fusion", "undo", *ITEM, "--force"])
    assert code == 0 and data["applied"] is True and _comp(fake_world).undo_stack == []
    assert _calls(fake_world, ("FusionComp.StartUndo", "FusionComp.Lock")) == []
    assert fake_world.calls_to("FusionComp.Undo") == [(1,)]


def test_undo_count_beyond_the_stack_is_refused(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["fusion", "undo", *ITEM, "--force"])
    assert code == 1 and "UNDO_REFUSED" in out and "0 entries" in out
    code, _, _ = run_cli(["fusion", "undo", *ITEM, "--count", "0"])
    assert code == 2


def test_active_is_refused_on_every_mutation_before_any_call(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    for argv in (
        ["tool", "add", "Blur"],
        ["tool", "set", "Blur1", "XBlurSize=1"],
        ["tool", "delete", "Blur1"],
        ["keyframe", "set", "Blur1", "XBlurSize", "0=1"],
        ["undo"],
    ):
        code, out, _ = run_cli(["fusion", *argv, "--active", "--force"])
        assert code == 2 and "read-only" in out, argv
    assert fake_world.calls == []


def test_a_non_current_timeline_is_refused_without_a_write(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    argv = ["fusion", "tool", "add", "Blur", *ITEM, "--timeline", "Edit v2", "--force"]
    code, out, _ = run_cli(argv)
    assert code == 1 and "FUSION_TIMELINE_NOT_CURRENT" in out and "timeline open" in out
    assert ext_fusion.fusion_write_calls(fake_world) == [] and fake_world.write_calls() == []


def test_a_failed_project_snapshot_leaves_no_comp_export(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject(SAVE, False)
    code, out, _ = run_cli(["fusion", "tool", "add", "Blur", *ITEM, "--force"])
    assert code == 1 and "SNAPSHOT_FAILED" in out
    assert _calls(fake_world, ("TimelineItem.ExportFusionComp", "FusionComp.AddTool")) == []
    assert not (tmp_path / "axi-home" / "snapshots" / "fusion").exists()


def test_a_failed_comp_export_aborts_before_the_undo_group(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject("TimelineItem.ExportFusionComp", False)
    code, out, _ = run_cli(["fusion", "tool", "add", "Blur", *ITEM, "--force"])
    assert code == 1 and "FUSION_SNAPSHOT_FAILED" in out
    assert _calls(fake_world, ("FusionComp.Lock", "FusionComp.AddTool")) == []


def test_a_hung_fusion_call_times_out_and_sends_nothing_more(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DAVINCI_AXI_FUSION_TIMEOUT", "0.3")
    _comp(fake_world).hang("AddTool")
    code, out, _ = run_cli(["fusion", "tool", "add", "Blur", *ITEM, "--force"])
    assert code == 1 and "FUSION_TIMEOUT" in out
    assert _calls(fake_world, ("FusionComp.EndUndo", "FusionComp.Unlock")) == []


def test_reads_survive_the_wrapped_spec(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, err = run_cli(["fusion", "tools", *ITEM])
    assert (code, err) == (0, "") and "MediaIn1" in out
    code, out, _ = run_cli(["fusion", "comp", "show", *ITEM])
    assert code == 0 and "undo_depth" in out
    code, out, _ = run_cli(["fusion", "tool", "show", "MediaOut1", *ITEM])
    assert code == 0 and "inputs[" in out
