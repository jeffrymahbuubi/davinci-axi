"""`color_targets` (F8 core): target parsing, resolution and the one-plan multi-target write."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from davinci_axi import connect as connect_module
from davinci_axi import registry
from davinci_axi.commands.color_targets import (
    GraphTarget,
    Step,
    Targets,
    TargetSpec,
    called_step,
    check_node_each,
    field_for,
    open_targets,
    parse_target,
    targets_write,
)
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeWorld, ext_color
from davinci_axi.spec import GlobalOpts, Invocation

Run = Callable[[list[str]], tuple[int, str, str]]
SAVE, EXPORT = "ProjectManager.SaveProject", "ProjectManager.ExportProject"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_color.extend()
    monkeypatch.setitem(registry.COMMANDS, "color", "davinci_axi.commands.color:SPEC")
    yield


def _inv(target: str, **args: object) -> Invocation:
    return Invocation(
        opts=GlobalOpts(),
        args={"force": False, "no_snapshot": False, "snapshot_out": None, **args},
        positionals={"target": target},
        connect=lambda: connect_module.connect(GlobalOpts()),
    )


def _hero(world: FakeWorld) -> None:
    project = world.resolve.pm.current_project
    assert project is not None
    project.AddColorGroup("Hero")
    world.calls.clear()


def _code(err: pytest.ExceptionInfo[AxiError]) -> str:
    return err.value.code


def test_parse_target_forms() -> None:
    assert parse_target("V1:1", graphs=True) == TargetSpec("items", refs=("V1:1",))
    assert parse_target("V1:1, V1:3,V1:1", graphs=True).refs == ("V1:1", "V1:3")
    assert parse_target("timeline", graphs=True).kind == "timeline"
    group = parse_target("group:Hero:pre", graphs=True)
    assert (group.kind, group.group, group.side) == ("group", "Hero", "pre")
    assert parse_target("group:A:B:post", graphs=True).group == "A:B"
    for bad in ("group:Hero", "group::pre", "group:Hero:mid", "", ","):
        with pytest.raises(AxiError) as err:
            parse_target(bad, graphs=True)
        assert _code(err) == "VALIDATION_ERROR", bad


def test_parse_target_refuses_graphs_when_item_only() -> None:
    for text in ("timeline", "group:Hero:pre"):
        with pytest.raises(AxiError) as err:
            parse_target(text, graphs=False)
        assert _code(err) == "VALIDATION_ERROR" and "SetCDL" in " ".join(err.value.help)
    assert parse_target("V1:1,V1:2", graphs=False).kind == "items"


def test_open_targets_resolves_items_timeline_and_group(fake_world: FakeWorld) -> None:
    items = open_targets(_inv("V1:1,V1:2"), graphs=True)
    assert [e.ref for e in items.entries] == ["V1:1", "V1:2"]
    assert all(e.label.startswith("item V1:") and e.target for e in items.entries)
    assert items.label.startswith("2 items (V1:1, V1:2) in timeline Edit v1")
    fake_world.calls.clear()
    shared = open_targets(_inv("timeline"), graphs=True)
    assert [e.label for e in shared.entries] == ["timeline Edit v1"]
    assert shared.entries[0].target is None and shared.label == "timeline Edit v1"
    assert len(fake_world.calls_to("Timeline.GetNodeGraph")) == 1
    with pytest.raises(AxiError) as err:
        open_targets(_inv("group:Hero:pre"), graphs=True)
    assert _code(err) == "NOT_FOUND" and "(none)" in " ".join(err.value.help)
    _hero(fake_world)
    group = open_targets(_inv("group:Hero:pre"), graphs=True)
    assert group.entries[0].label == "color group Hero (pre-clip)"
    assert group.entries[0].ref == "group:Hero:pre"
    for text in ("timeline", "group:Hero:post"):
        with pytest.raises(AxiError) as err:
            open_targets(_inv(text), graphs=True, layer=2)
        assert _code(err) == "VALIDATION_ERROR"
    with pytest.raises(AxiError) as err:
        open_targets(_inv("timeline"), graphs=False)
    assert "SetCDL" in " ".join(err.value.help)
    assert open_targets(_inv("V1:1"), graphs=True, layer=1).entries[0].target is not None


def test_graph_hint_per_kind(fake_world: FakeWorld) -> None:
    _hero(fake_world)
    assert "color graph --item V1:1`" in open_targets(_inv("V1:1,V1:2"), graphs=True).graph_hint()
    assert "--timeline-graph" in open_targets(_inv("timeline"), graphs=True).graph_hint()
    hint = open_targets(_inv("group:Hero:post"), graphs=True).graph_hint()
    assert "--group Hero --post" in hint


def test_field_for_and_check_node_each(fake_world: FakeWorld) -> None:
    one = open_targets(_inv("V1:1"), graphs=True)
    many = open_targets(_inv("V1:1,V1:3"), graphs=True)
    assert field_for(one, "lut", one.entries[0]) == "lut"
    assert field_for(many, "lut", many.entries[1]) == "lut[V1:3]"
    check_node_each(many, 1)
    shared = open_targets(_inv("timeline"), graphs=True)
    assert shared.entries[0].graph.GetNumNodes() == 1
    with pytest.raises(AxiError) as err:
        check_node_each(shared, 2)
    assert _code(err) == "NOT_FOUND"
    assert "timeline" in err.value.message and "1 nodes" in err.value.message


def _three(fake_world: FakeWorld) -> Targets:
    targets = open_targets(_inv("V1:1,V1:2,V1:3", force=True), graphs=True)
    assert len(targets.entries) == 3
    fake_world.calls.clear()
    return targets


def test_targets_write_stops_at_the_first_refusal(fake_world: FakeWorld) -> None:
    targets = _three(fake_world)
    ran: list[str] = []

    def step(entry: GraphTarget) -> Step:
        def call() -> bool:
            ran.append(entry.ref)
            return entry.ref != "V1:2"

        return called_step(targets, entry, call)

    with pytest.raises(AxiError) as err:
        targets_write(
            _inv("V1:1,V1:2,V1:3", force=True),
            targets,
            command="color node enable",
            words=["color", "node", "enable", "V1:1,V1:2,V1:3", "1"],
            step=step,
            fail="Graph.SetNodeEnabled",
        )
    assert err.value.code == "API_FAILED"
    snapshot = err.value.detail.pop("snapshot")
    assert snapshot
    assert err.value.detail == {"done": ["V1:1"], "failed": "V1:2", "not_done": ["V1:3"]}
    assert ran == ["V1:1", "V1:2"]
    assert fake_world.write_calls()[:2] == [SAVE, EXPORT]


def test_targets_write_dry_run_lists_every_target_and_skips_no_ops(
    fake_world: FakeWorld,
) -> None:
    targets = _three(fake_world)

    def step(entry: GraphTarget) -> Step:
        if entry.ref == "V1:2":
            return [], lambda: True, lambda: {}
        return called_step(targets, entry, lambda: True)

    result = targets_write(
        _inv("V1:1,V1:2,V1:3"),
        targets,
        command="color node enable",
        words=["color", "node", "enable", "V1:1,V1:2,V1:3", "1"],
        step=step,
        fail="Graph.SetNodeEnabled",
    )
    data = result.data
    assert data["applied"] is False and data["count"] == 3
    assert data["targets"] == "V1:1, V1:2, V1:3"
    assert [c["field"] for c in data["changes"]] == ["called[V1:1]", "called[V1:3]"]  # type: ignore[attr-defined]
    assert "color node enable V1:1,V1:2,V1:3 1 --force" in result.help[0]
    assert fake_world.write_calls() == []


def test_targets_write_applies_every_target_and_reads_back(fake_world: FakeWorld) -> None:
    targets = _three(fake_world)
    result = targets_write(
        _inv("V1:1,V1:2,V1:3", force=True),
        targets,
        command="color node enable",
        words=["color", "node", "enable", "V1:1,V1:2,V1:3", "1"],
        step=lambda e: called_step(targets, e, lambda: e.graph.SetNodeEnabled(1, True)),
        fail="Graph.SetNodeEnabled",
    )
    assert result.data["applied"] is True and result.data["count"] == 3
    assert fake_world.write_calls() == [SAVE, EXPORT] + ["Graph.SetNodeEnabled"] * 3


def test_color_graph_still_reads_shared_graphs(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, err = run_cli(["color", "graph", "--timeline-graph"])
    assert (code, err) == (0, "") and "timeline Edit v1" in out
    code, out, _ = run_cli(["color", "graph", "--group", "Hero", "--pre"])
    assert code == 1 and "NOT_FOUND" in out and "(none)" in out
    _hero(fake_world)
    code, out, _ = run_cli(["color", "graph", "--group", "Hero", "--post"])
    assert code == 0 and "Hero (post-clip)" in out and "Post-clip" in out
    code, out, _ = run_cli(["color", "graph", "--group", "Hero", "--pre"])
    assert code == 0 and "Hero (pre-clip)" in out
