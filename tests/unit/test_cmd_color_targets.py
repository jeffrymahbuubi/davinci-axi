"""F8 on the colour verbs: `color node ...` and `color grade ...` take a target (an item, an item
list, `timeline` or `group:<name>:pre|post`); F6/F7 notes in the help. Fake-backed."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_color
from davinci_axi.fake.timeline import FakeTimelineItem

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]
SAVE, EXPORT = "ProjectManager.SaveProject", "ProjectManager.ExportProject"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_color.extend()
    monkeypatch.setitem(registry.COMMANDS, "color", "davinci_axi.commands.color:SPEC")
    yield


def _item(world: FakeWorld, number: int) -> FakeTimelineItem:
    project = world.resolve.pm.current_project
    assert project is not None
    return project.timelines[0].tracks["video"][0].items[number - 1]


def _graph(world: FakeWorld, number: int = 1) -> Any:
    return _item(world, number).GetNodeGraph(1)  # type: ignore[attr-defined]


def _timeline_graph(world: FakeWorld) -> Any:
    project = world.resolve.pm.current_project
    assert project is not None
    return project.timelines[0].__dict__["graphs"]["timeline"]


def _group(world: FakeWorld) -> Any:
    """A colour group `Hero`, built without leaving calls in the log."""
    project = world.resolve.pm.current_project
    assert project is not None
    group = project.AddColorGroup("Hero")
    world.calls.clear()
    return group


def _lut(tmp_path: Path) -> Path:
    lut = tmp_path / "film.cube"
    lut.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")
    return lut


def _apply(
    run_cli: Run, keys: Keys, world: FakeWorld, argv: list[str]
) -> tuple[dict[str, str], str]:
    code, out, err = run_cli([*argv, "--force"])
    assert (code, err) == (0, ""), out
    assert keys(out)["applied"] == "true", out
    assert world.write_calls()[:2] == [SAVE, EXPORT]
    return keys(out), out


def test_lut_set_on_the_timeline_graph(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    lut = _lut(tmp_path)
    argv = ["color", "node", "lut", "set", "timeline", "1", str(lut)]
    code, out, err = run_cli(argv)
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "") and keys["target"] == "timeline Edit v1"
    assert keys["applied"] == "false" and "changes[1]" in out
    assert fake_world.write_calls() == []
    _, out = _apply(run_cli, parse_toon_keys, fake_world, argv)
    assert str(lut) in out
    assert _timeline_graph(fake_world).nodes[0]["lut"] == str(lut)
    assert fake_world.calls_to("Graph.SetLUT") == [(1, str(lut))]
    code, out, _ = run_cli(["color", "node", "lut", "show", "timeline", "1"])
    assert code == 0 and str(lut) in out and "target: timeline Edit v1" in out


def test_lut_set_on_a_group_graph_and_unknown_group(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    lut = _lut(tmp_path)
    code, out, _ = run_cli(
        ["color", "node", "lut", "set", "group:Nope:pre", "1", str(lut), "--force"]
    )
    assert code == 1 and "NOT_FOUND" in out and "(none)" in out
    assert fake_world.write_calls() == []
    group = _group(fake_world)
    _apply(
        run_cli,
        parse_toon_keys,
        fake_world,
        ["color", "node", "lut", "set", "group:Hero:post", "1", str(lut)],
    )
    assert group.post.nodes[0]["lut"] == str(lut) and group.pre.nodes[0]["lut"] == ""
    code, out, _ = run_cli(
        ["color", "node", "lut", "set", "group:Nope:pre", "1", str(lut), "--force"]
    )
    assert code == 1 and "NOT_FOUND" in out and "Hero" in out


def test_lut_set_on_an_item_list_takes_one_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    lut = _lut(tmp_path)
    argv = ["color", "node", "lut", "set", "V1:1,V1:3", "1", str(lut)]
    code, out, _ = run_cli(argv)
    keys = parse_toon_keys(out)
    assert code == 0 and keys["targets"].strip('"') == "V1:1, V1:3" and keys["count"] == "2"
    assert "lut[V1:1]" in out and "lut[V1:3]" in out and "changes[2]" in out
    assert fake_world.write_calls() == []
    _apply(run_cli, parse_toon_keys, fake_world, argv)
    assert len(fake_world.calls_to(EXPORT)) == 1
    assert _graph(fake_world, 1).nodes[0]["lut"] == str(lut)
    assert _graph(fake_world, 3).nodes[0]["lut"] == str(lut)
    for ref in ("V1:1", "V1:3"):
        code, out, _ = run_cli(["color", "node", "lut", "show", ref, "1"])
        assert code == 0 and str(lut) in out
    code, out, _ = run_cli(["color", "node", "lut", "show", "V1:1,V1:3", "1"])
    assert code == 0 and "luts[2]{target,label,lut}" in out


def test_list_partial_failure_reports_done_and_not_done(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    lut = _lut(tmp_path)
    _graph(fake_world, 2).SetLUT = lambda *a: False
    argv = ["color", "node", "lut", "set", "V1:1,V1:2,V1:3", "1", str(lut), "--force"]
    code, out, _ = run_cli(argv)
    assert code == 1 and "API_FAILED" in out
    assert "V1:1" in out and "V1:2" in out and "V1:3" in out
    assert "done" in out and "failed" in out and "not_done" in out
    assert "snapshot restore" in out
    assert _graph(fake_world, 1).nodes[0]["lut"] == str(lut)
    assert _graph(fake_world, 3).nodes[0]["lut"] == ""


def test_node_out_of_range_names_the_target(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["color", "node", "enable", "timeline", "2", "--force"])
    assert code == 1 and "NOT_FOUND" in out and "timeline" in out and "1 nodes" in out
    assert fake_world.write_calls() == []


def test_layer_is_refused_with_a_graph_target(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _, _ = run_cli(["color", "node", "disable", "timeline", "1", "--layer", "2"])
    assert code == 2
    assert fake_world.write_calls() == []


def test_cache_and_enable_on_a_group_graph(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    group = _group(fake_world)
    _apply(
        run_cli,
        parse_toon_keys,
        fake_world,
        ["color", "node", "cache", "group:Hero:pre", "1", "enabled"],
    )
    assert group.pre.nodes[0]["cache"] == int(fake_world.resolve.CACHE_ENABLED)  # type: ignore[attr-defined]
    fake_world.calls.clear()
    _, out = _apply(
        run_cli, parse_toon_keys, fake_world, ["color", "node", "disable", "group:Hero:pre", "1"]
    )
    assert group.pre.nodes[0]["enabled"] is False
    assert "not read back" in out and "label: Pre-clip" in out


def test_apply_drx_and_reset_on_shared_graphs(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    drx = tmp_path / "look.drx"
    drx.write_text("drx\n", encoding="utf-8")
    _apply(
        run_cli, parse_toon_keys, fake_world, ["color", "grade", "apply-drx", "timeline", str(drx)]
    )
    assert _timeline_graph(fake_world).drx == [(str(drx.resolve()), 0, False)]
    group = _group(fake_world)
    argv = ["color", "grade", "reset", "group:Hero:post"]
    _, out = _apply(run_cli, parse_toon_keys, fake_world, argv)
    assert group.post.resets == 1 and "irreversible: true" in out and "nodes: 1" in out


def test_grade_reset_on_a_list_reports_nodes_per_target(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _, out = _apply(run_cli, parse_toon_keys, fake_world, ["color", "grade", "reset", "V1:1,V1:2"])
    assert 'nodes: "V1:1: 2, V1:2: 2"' in out or "nodes: V1:1: 2, V1:2: 2" in out
    assert _graph(fake_world, 1).resets == 1 and _graph(fake_world, 2).resets == 1
    assert len(fake_world.calls_to(EXPORT)) == 1


def test_help_carries_the_api_limit_notes(run_cli: Run) -> None:
    code, out, _ = run_cli(["color", "--help"])
    assert code == 0 and "notes[" in out and "OFX" in out
    code, out, _ = run_cli(["color", "node", "--help"])
    assert code == 0 and "AddNode" in out
    code, out, _ = run_cli(["color", "grade", "--help"])
    assert code == 0 and ".drx" in out and "OFX" in out
    code, out, _ = run_cli(["color", "node", "lut", "set", "--help"])
    assert code == 0 and "group:<name>:pre|post" in out


def _six_items(world: FakeWorld) -> list[str]:
    """Add items so the first video track has six, and return their refs."""
    project = world.resolve.pm.current_project
    assert project is not None
    timeline = project.timelines[0]
    while len(timeline.tracks["video"][0].items) < 6:
        number = len(timeline.tracks["video"][0].items)
        timeline.add_item("video", 1, f"extra-{number}", 1000 + number * 10, 10)
    world.calls.clear()
    return [f"V1:{n}" for n in range(1, 7)]


def test_long_list_is_compact_in_toon(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    lut = _lut(tmp_path)
    refs = _six_items(fake_world)
    argv = ["color", "node", "lut", "set", ",".join(refs), "1", str(lut)]
    code, out, _ = run_cli(argv)
    keys = parse_toon_keys(out)
    assert code == 0 and keys["targets"].strip('"') == "V1:1 .. V1:6" and keys["count"] == "6"
    assert keys["target"] == "6 items in timeline Edit v1"
    assert "lut[6 items]" in out and "changes[1]" in out and "lut[V1:2]" not in out
    assert fake_world.write_calls() == []
    _, out = _apply(run_cli, parse_toon_keys, fake_world, argv)
    assert "lut[6 items]" in out
    for number in range(1, 7):
        assert _graph(fake_world, number).nodes[0]["lut"] == str(lut)


def test_long_list_json_keeps_the_full_data(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    lut = _lut(tmp_path)
    refs = _six_items(fake_world)
    code, out, _ = run_cli(["color", "node", "lut", "set", ",".join(refs), "1", str(lut), "--json"])
    data = json.loads(out)
    assert code == 0 and data["targets"] == refs and data["count"] == 6
    assert [c["field"] for c in data["changes"]] == [f"lut[{r}]" for r in refs]
    assert all(r in data["target"] for r in refs)


def test_long_list_verify_names_the_unchanged_item(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    lut = _lut(tmp_path)
    refs = _six_items(fake_world)
    _graph(fake_world, 4).SetLUT = lambda *a: True
    code, out, _ = run_cli(
        ["color", "node", "lut", "set", ",".join(refs), "1", str(lut), "--force"]
    )
    assert code == 1 and "VERIFY_FAILED" in out and "V1:4=" in out


def test_empty_graph_help_names_the_ways_out(run_cli: Run, fake_world: FakeWorld) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    project.timelines[0].GetNodeGraph().nodes.clear()  # type: ignore[attr-defined]
    code, out, _ = run_cli(["color", "node", "enable", "timeline", "2", "--force"])
    assert code == 1 and "NOT_FOUND" in out and "0 nodes" in out
    assert "Color page" in out and "21.1.1" in out and "V1:1,V1:3" in out
    assert "init --force" not in out
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["color", "node", "enable", "V1:1", "5", "--force"])
    assert code == 1 and "Color page" not in out
