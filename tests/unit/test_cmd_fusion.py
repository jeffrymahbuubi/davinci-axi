from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import fusion_guard, registry
from davinci_axi.commands import fusion
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeWorld, ext_fusion
from davinci_axi.fake.ext_fusion import FakeComp
from davinci_axi.fake.timeline import FakeTimelineItem

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_fusion.extend()
    ext_fusion.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "fusion", "davinci_axi.commands.fusion:SPEC")


def _clip(world: FakeWorld, number: int) -> FakeTimelineItem:
    project = world.resolve.pm.current_project
    assert project is not None and project.current_timeline is not None
    return project.current_timeline.tracks["video"][0].items[number - 1]


def _comp(world: FakeWorld) -> FakeComp:
    return ext_fusion.comp_of(_clip(world, 2))


def _read_only(world: FakeWorld, tmp_path: Path) -> None:
    assert world.write_calls() == []
    assert ext_fusion.fusion_write_calls(world) == []
    assert world.page_changes() == []
    assert not (tmp_path / "axi-home" / "snapshots").exists()


def test_comps_lists_the_comps_of_an_item(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["fusion", "comps", "--item", "V1:2"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["timeline"] == "Edit v1" and keys["count"] == "1" and keys["cache"] == "auto"
    assert keys["resolvepython_processes"] == "0" and "warnings" not in keys
    assert "comps[1]{index,name,active}:" in out and "  1,Composition 1,false" in out
    assert "fusion tools --item V1:2" in out
    _read_only(fake_world, tmp_path)


def test_comps_marks_the_comp_open_on_the_fusion_page(run_cli: Run, fake_world: FakeWorld) -> None:
    ext_fusion.fusion_of(fake_world).current = _comp(fake_world)
    _, out, _ = run_cli(["fusion", "comps", "--item", "V1:2"])
    assert "  1,Composition 1,true" in out


def test_comps_on_an_item_without_comps_is_an_empty_state(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["fusion", "comps", "--item", "V1:1"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["count"] == "0"
    assert keys["comps"] == '"0 comps on item V1:1"'


def test_comps_needs_item_and_rejects_active_and_unknown_items(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["fusion", "comps"])
    assert code == 2 and "--item" in out
    assert fake_world.calls == []
    code, out, _ = run_cli(["fusion", "comps", "--active"])
    assert code == 2 and "unknown flag --active" in out
    code, out, _ = run_cli(["fusion", "comps", "--item", "V9:1"])
    assert code == 1 and "NOT_FOUND" in out


def test_tools_lists_tools_with_no_fusion_write_and_no_page_change(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(["fusion", "tools", "--item", "V1:2"])
    assert (code, err) == (0, "")
    assert "tools[2]{name,id,passthrough,x,y}:" in out
    assert "  MediaIn1,MediaIn,false,0,0" in out and "  MediaOut1,MediaOut,false,3,0" in out
    assert "fusion tool show MediaIn1 --item V1:2" in out
    _read_only(fake_world, tmp_path)


def test_tools_without_a_flow_view_have_null_positions(run_cli: Run, fake_world: FakeWorld) -> None:
    _comp(fake_world).CurrentFrame = None
    _, out, _ = run_cli(["fusion", "tools", "--item", "V1:2", "--json"])
    rows = json.loads(out)["tools"]
    assert [(r["x"], r["y"]) for r in rows] == [(None, None), (None, None)]


def test_tools_filters_by_type_and_selection(run_cli: Run, fake_world: FakeWorld) -> None:
    comp = _comp(fake_world)
    comp.tools[1].selected = True
    _, out, _ = run_cli(["fusion", "tools", "--item", "V1:2", "--type", "MediaIn"])
    assert "tools[1]" in out and "MediaIn1" in out and "MediaOut1" not in out
    _, out, _ = run_cli(["fusion", "tools", "--item", "V1:2", "--selected"])
    assert "tools[1]" in out and "MediaOut1" in out
    _, out, _ = run_cli(["fusion", "tools", "--item", "V1:2", "--type", "Blur"])
    assert "0 tools in comp Composition 1 (filters: type=Blur)" in out


def test_tools_limit_and_fields(run_cli: Run, fake_world: FakeWorld) -> None:
    _, out, _ = run_cli(["fusion", "tools", "--item", "V1:2", "--limit", "1", "--fields", "name,x"])
    assert "tools[1]{name,x}:" in out and "Showing 1 of 2 tools" in out
    fake_world.calls.clear()
    code, out, _ = run_cli(["fusion", "tools", "--item", "V1:2", "--fields", "nope"])
    assert code == 2 and "Valid fields" in out
    assert fake_world.calls == []


def test_comp_show_reports_range_file_lock_and_undo_depth(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    comp = _comp(fake_world)
    comp.undo_stack.append("user edit")
    comp.Lock()
    code, out, _ = run_cli(["fusion", "comp", "show", "--item", "V1:2"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["comp"] == "Composition 1" and keys["tools"] == "2"
    assert (keys["render_start"], keys["render_end"]) == ("0", "79")
    assert (keys["global_start"], keys["global_end"]) == ("0", "79")
    assert keys["file"] == '"Comp:/Composition 1.comp"'
    assert keys["locked"] == "true" and keys["undo_depth"] == "1"


def test_comp_flag_picks_a_comp_by_name_or_index(run_cli: Run, fake_world: FakeWorld) -> None:
    item = _clip(fake_world, 2)
    item.comps.append("Second")
    _, out, _ = run_cli(["fusion", "comp", "show", "--item", "V1:2", "--comp", "2"])
    assert "comp: Second" in out and "--comp Second" in out.replace("'", "")
    _, out, _ = run_cli(["fusion", "comp", "show", "--item", "V1:2", "--comp", "Composition 1"])
    assert "comp: Composition 1" in out
    code, out, _ = run_cli(["fusion", "comp", "show", "--item", "V1:2", "--comp", "7"])
    assert code == 1 and "NOT_FOUND" in out and "Composition 1, Second" in out


def test_tool_show_lists_inputs_and_outputs(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["fusion", "tool", "show", "MediaOut1", "--item", "V1:2"])
    assert code == 0
    assert "tool: MediaOut1" in out and "id: MediaOut" in out
    assert "inputs[2]{id,name,type,value,source}:" in out
    assert "  Input,Input,Image,null,MediaIn1.Output" in out
    assert "  Index,Index,Number,0,null" in out
    assert "outputs[1]{id,name,connected}:" in out
    # image inputs are not read: only the number input is asked for its value
    assert [a[0] for _, m, a in fake_world.calls if m == "GetInput"] == ["Index"]


def test_tool_show_source_is_null_when_unconnected_and_selectable(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _comp(fake_world).tools[1].inputs[0].connected = None
    _, out, _ = run_cli(["fusion", "tool", "show", "MediaOut1", "--item", "V1:2"])
    assert "  Input,Input,Image,null,null" in out
    _comp(fake_world).tools[1].inputs[0].connected = _comp(fake_world).tools[0].outputs[0]
    _, out, _ = run_cli(
        ["fusion", "tool", "show", "MediaOut1", "--item", "V1:2", "--fields", "id,source"]
    )
    assert "inputs[2]{id,source}:" in out and "  Input,MediaIn1.Output" in out


def test_tool_show_marks_a_connected_output_and_applies_the_limit(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _, out, _ = run_cli(["fusion", "tool", "show", "MediaIn1", "--item", "V1:2"])
    assert "  Output,Output,true" in out and "  Layer,Layer,Number,0" in out
    _, out, _ = run_cli(["fusion", "tool", "show", "MediaOut1", "--item", "V1:2", "--limit", "1"])
    assert "inputs[1]" in out and "inputs_total: 2" in out and "Showing 1 of 2 inputs" in out


def test_tool_get_reads_an_input_and_passes_the_time(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["fusion", "tool", "get", "MediaOut1", "Index", "--item", "V1:2"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["tool"] == "MediaOut1" and keys["input"] == "Index"
    assert keys["value"] == "0" and "time" not in keys
    code, out, _ = run_cli(
        ["fusion", "tool", "get", "MediaOut1", "Index", "--item", "V1:2", "--time", "24"]
    )
    assert code == 0 and parse_toon_keys(out)["time"] == "24"
    assert fake_world.calls_to("FusionTool.GetInput") == [("Index", None), ("Index", 24.0)]


def test_tool_get_of_an_empty_but_existing_input_is_null_not_an_error(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["fusion", "tool", "get", "MediaOut1", "Input", "--item", "V1:2"])
    assert code == 0 and parse_toon_keys(out)["value"] == "null"


def test_tool_get_not_found_lists_the_tools_or_the_inputs(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["fusion", "tool", "get", "Blur1", "XBlurSize", "--item", "V1:2"])
    assert code == 1 and "tool Blur1 not found" in out and "MediaIn1, MediaOut1" in out
    code, out, _ = run_cli(["fusion", "tool", "get", "MediaOut1", "Nope", "--item", "V1:2"])
    assert code == 1 and "input Nope not found" in out and "Input, Index" in out


def test_graph_lists_the_one_edge(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(["fusion", "graph", "--item", "V1:2"])
    assert code == 0 and "total: 1" in out
    assert "edges[1]{from,output,to,input}:" in out
    assert "  MediaIn1,Output,MediaOut1,Input" in out
    _read_only(fake_world, tmp_path)


def test_graph_with_no_connections_is_an_empty_state(run_cli: Run, fake_world: FakeWorld) -> None:
    comp = _comp(fake_world)
    comp.tools[1].inputs[0].connected = None
    _, out, _ = run_cli(["fusion", "graph", "--item", "V1:2"])
    assert "0 edges in comp Composition 1" in out


def test_active_without_a_current_comp_is_not_found(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["fusion", "tools", "--active"])
    assert code == 1 and "no comp is open on the Fusion page" in out and "NOT_FOUND" in out


def test_active_reads_the_comp_open_on_the_fusion_page(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    ext_fusion.fusion_of(fake_world).current = _comp(fake_world)
    code, out, _ = run_cli(["fusion", "comp", "show", "--active"])
    assert code == 0 and "comp: Composition 1" in out and "item:" not in out
    code, out, _ = run_cli(["fusion", "graph", "--active"])
    assert code == 0 and "MediaIn1,Output,MediaOut1,Input" in out
    _read_only(fake_world, tmp_path)


def test_active_and_item_together_exit_2(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["fusion", "tools", "--active", "--item", "V1:2"])
    assert code == 2 and "contradict" in out
    code, out, _ = run_cli(["fusion", "tools"])
    assert code == 2 and "--item" in out and fake_world.calls == []


def test_registry_lists_every_entry_without_a_type(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["fusion", "registry"])
    assert code == 0 and "total: 9" in out and "shown: 9" in out and "registry[9]{id,name}:" in out
    assert "  TextPlus,TextPlus" in out
    assert fake_world.calls_to("Fusion.GetRegList") == [(None,)]


def test_registry_type_sends_the_integer_mask(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["fusion", "registry", "--type", "CT_Modifier"])
    assert (
        code == 0 and "registry[1]" in out and "BezierSpline" in out and "type: CT_Modifier" in out
    )
    assert fake_world.calls_to("Fusion.GetRegList") == [(2,)]
    _, out, _ = run_cli(["fusion", "registry", "--type", "CT_Tool"])
    assert "registry[8]" in out and "BezierSpline" not in out


def test_registry_unknown_type_is_refused_before_any_resolve_call(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["fusion", "registry", "--type", "CT_Nothing"])
    assert code == 2 and "unknown registry type CT_Nothing" in out and "CT_Tool" in out
    assert fake_world.calls == []


def test_reg_mask_needs_an_integer_attribute() -> None:
    class App:
        CT_Tool = 1
        CT_Modifier = 2.0  # Fusion's Python bridge hands numbers over as floats
        CT_Bool = True
        CT_Text = "x"
        CT_Half = 1.5

    assert fusion.reg_mask(App(), "CT_Tool") == 1
    assert fusion.reg_mask(App(), "CT_Modifier") == 2
    for name in ("CT_Bool", "CT_Text", "CT_Missing", "CT_Half"):
        with pytest.raises(AxiError):
            fusion.reg_mask(App(), name)


def test_registry_search_matches_id_or_name_case_insensitively(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _, out, _ = run_cli(["fusion", "registry", "--search", "grain"])
    assert "registry[1]{id,name}:" in out and "FilmGrain" in out and "search: grain" in out
    assert "total: 1" in out
    _, out, _ = run_cli(["fusion", "registry", "--search", "REMOVAL"])
    assert "registry[1]" in out and "ObjectRemoval" in out
    _, out, _ = run_cli(["fusion", "registry", "--search", "zzz"])
    assert "0 registry entries in Fusion (filters: search=zzz)" in out and "search: zzz" in out


def test_registry_limit_reports_shown_and_total(run_cli: Run) -> None:
    _, out, _ = run_cli(["fusion", "registry", "--limit", "2"])
    assert "shown: 2" in out and "total: 9" in out and "registry[2]" in out
    assert "Showing 2 of 9 entries" in out


def test_undo_stack_empty_state_and_entries(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["fusion", "undo-stack", "--item", "V1:2"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["total"] == "0"
    assert keys["entries"] == "0 undo entries in comp Composition 1"
    comp = _comp(fake_world)
    comp.undo_stack.extend(["Add Blur", "davinci-axi: fusion tool add"])
    _, out, _ = run_cli(["fusion", "undo-stack", "--item", "V1:2"])
    assert "entries[2]{position,name,ours}:" in out
    assert "  1,Add Blur,false" in out and '  2,"davinci-axi: fusion tool add",true' in out


def test_a_non_current_timeline_is_refused_before_any_fusion_call(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["fusion", "tools", "--item", "V1:1", "--timeline", "Edit v2"])
    assert code == 1 and "FUSION_TIMELINE_NOT_CURRENT" in out
    assert "timeline open 'Edit v2' --force" in out
    assert [c for c, _, _ in fake_world.calls if c.startswith("Fusion")] == []
    assert fake_world.calls_to("TimelineItem.GetFusionCompNameList") == []


def test_two_resolvepython_processes_show_a_warning_on_every_result(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fusion_guard, "count_resolve_python", lambda: 2)
    for argv in (
        ["comps", "--item", "V1:2"],
        ["tools", "--item", "V1:2"],
        ["registry"],
        ["undo-stack", "--item", "V1:2"],
    ):
        code, out, _ = run_cli(["fusion", *argv])
        assert code == 0 and "resolvepython_processes: 2" in out, argv
        assert "warnings[1]:" in out and "2 ResolvePython processes" in out, argv


def test_a_hung_fusion_call_is_fusion_timeout(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(fusion_guard.FUSION_TIMEOUT_ENV, "0.2")
    _comp(fake_world).hang("GetAttrs")
    code, out, _ = run_cli(["fusion", "comp", "show", "--item", "V1:2"])
    assert code == 1 and "FUSION_TIMEOUT" in out and "GetAttrs" in out


def test_spec_methods_list_the_calls_the_reads_make() -> None:
    methods = set(fusion.SPEC.methods)
    assert {
        "Resolve.Fusion",
        "TimelineItem.GetFusionCompCount",
        "TimelineItem.GetFusionCompNameList",
        "TimelineItem.GetFusionCompByIndex",
        "TimelineItem.GetFusionCompByName",
        "TimelineItem.GetIsFusionOutputCacheEnabled",
    } <= methods
    assert fusion.SPEC.reads_only
    assert sorted(fusion.SPEC.subcommands) == [
        "comp",
        "comps",
        "graph",
        "registry",
        "tool",
        "tools",
        "undo-stack",
    ]


@pytest.mark.parametrize(
    "path",
    [
        ["fusion"],
        ["fusion", "comps"],
        ["fusion", "comp"],
        ["fusion", "comp", "show"],
        ["fusion", "tools"],
        ["fusion", "tool"],
        ["fusion", "tool", "show"],
        ["fusion", "tool", "get"],
        ["fusion", "graph"],
        ["fusion", "registry"],
        ["fusion", "undo-stack"],
    ],
)
def test_every_fusion_path_has_help_with_examples(run_cli: Run, path: list[str]) -> None:
    code, out, err = run_cli([*path, "--help"])
    assert (code, err) == (0, "") and "examples[" in out


def test_bare_parents_ask_for_a_subcommand_and_unknown_flags_exit_2(run_cli: Run) -> None:
    for path in (["fusion"], ["fusion", "comp"], ["fusion", "tool"]):
        code, out, _ = run_cli(path)
        assert code == 2 and "needs a subcommand" in out
    code, out, _ = run_cli(["fusion", "--axi-probe-unknown-flag"])
    assert code == 2 and "valid" in out.lower()
