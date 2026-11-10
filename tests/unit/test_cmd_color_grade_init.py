"""F20: `color grade init` gives an empty timeline or group graph its first node from a DRX.

Fake-backed; the lead splices `init` into `color.GRADE_SPEC`, so the fixture does it here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.commands import color, color_graph
from davinci_axi.fake import FakeWorld, ext_color

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]
DRX = Path(__file__).resolve().parents[1] / "data" / "one-node.drx"
APPLY = "Graph.ApplyGradeFromDRX"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_color.extend()
    monkeypatch.setitem(registry.COMMANDS, "color", "davinci_axi.commands.color:SPEC")
    monkeypatch.setitem(color.GRADE_SPEC.subcommands, "init", color_graph.GRADE_INIT_SPEC)
    yield


def _timeline(world: FakeWorld) -> Any:
    project = world.resolve.pm.current_project
    assert project is not None
    return project.timelines[0]


def _empty_timeline_graph(world: FakeWorld) -> Any:
    graph = ext_color.empty_graph(_timeline(world), "timeline")
    world.calls.clear()
    return graph


def _lut(tmp_path: Path) -> Path:
    lut = tmp_path / "film.cube"
    lut.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")
    return lut


def test_init_on_an_empty_timeline_graph(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    graph = _empty_timeline_graph(fake_world)
    argv = ["color", "grade", "init", "timeline", "--drx", str(DRX), "--try"]
    code, out, err = run_cli(argv)
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "") and keys["applied"] == "false"
    assert "nodes" in out and "1+" in out and keys["tried"] == "true"
    assert fake_world.write_calls() == [] and graph.nodes == []
    code, out, err = run_cli([*argv, "--force"])
    assert (code, err) == (0, ""), out
    assert parse_toon_keys(out)["applied"] == "true"
    assert fake_world.calls_to(APPLY) == [(str(DRX), 0, False)]
    assert len(graph.nodes) == 1
    lut = _lut(tmp_path)
    code, out, err = run_cli(["color", "node", "lut", "set", "timeline", "1", str(lut), "--force"])
    assert (code, err) == (0, ""), out
    assert graph.nodes[0]["lut"] == str(lut)


def test_init_refuses_without_try_and_never_connects(run_cli: Run, fake_world: FakeWorld) -> None:
    graph = _empty_timeline_graph(fake_world)
    for extra in ([], ["--force"], ["--drx", str(DRX), "--force"]):
        code, out, _ = run_cli(["color", "grade", "init", "timeline", *extra])
        assert code == 1 and "NOT_AVAILABLE" in out and "21.1.1" in out
        assert "--try" in out and "color node lut set" in out
    assert fake_world.calls_to("Project.GetCurrentTimeline") == []
    assert fake_world.calls_to(APPLY) == [] and fake_world.write_calls() == []
    assert graph.nodes == []


def test_init_on_an_empty_group_pre_graph(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    group = project.AddColorGroup("Hero")
    ext_color.empty_graph(group, "pre")
    fake_world.calls.clear()
    code, out, err = run_cli(
        ["color", "grade", "init", "group:Hero:pre", "--drx", str(DRX), "--try", "--force"]
    )
    assert (code, err) == (0, ""), out
    assert parse_toon_keys(out)["applied"] == "true"
    assert len(group.pre.nodes) == 1 and len(group.post.nodes) == 1


def test_init_refuses_a_graph_with_nodes(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(
        ["color", "grade", "init", "timeline", "--drx", str(DRX), "--try", "--force"]
    )
    assert code == 2 and "already has 1 nodes" in out
    assert fake_world.write_calls() == []


def test_init_refuses_items(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(
        ["color", "grade", "init", "V1:1", "--drx", str(DRX), "--try", "--force"]
    )
    assert code == 2 and "items always have a node" in out
    assert fake_world.write_calls() == []


def test_init_without_a_template_says_how_to_capture_one(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(color_graph, "template_path", lambda: None)
    _empty_timeline_graph(fake_world)
    code, out, _ = run_cli(["color", "grade", "init", "timeline", "--try", "--force"])
    assert code == 1 and "NOT_FOUND" in out and "no DRX template" in out
    assert "color still export" in out and "--format drx" in out
    assert "only useful with --try" in out
    assert fake_world.write_calls() == []


def test_init_with_a_missing_drx_file(run_cli: Run, tmp_path: Path) -> None:
    code, out, _ = run_cli(
        [
            "color",
            "grade",
            "init",
            "timeline",
            "--drx",
            str(tmp_path / "nope.drx"),
            "--try",
            "--force",
        ]
    )
    assert code == 1 and "NOT_FOUND" in out


def test_init_uses_the_shipped_template(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(color_graph, "template_path", lambda: DRX)
    graph = _empty_timeline_graph(fake_world)
    code, out, err = run_cli(["color", "grade", "init", "timeline", "--try", "--force"])
    assert (code, err) == (0, ""), out
    assert "template: true" in out and len(graph.nodes) == 1


def test_init_that_changes_nothing_fails_verification(run_cli: Run, fake_world: FakeWorld) -> None:
    graph = _empty_timeline_graph(fake_world)
    fake_world.inject(APPLY, True)
    code, out, _ = run_cli(
        ["color", "grade", "init", "timeline", "--drx", str(DRX), "--try", "--force"]
    )
    assert code == 1 and "VERIFY_FAILED" in out
    assert graph.nodes == []


def test_init_try_that_leaves_zero_nodes_fails_verification(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    """F29 probe B: ApplyGradeFromDRX reports True and the count stays 0 on 21.1.1."""
    graph = _empty_timeline_graph(fake_world)
    fake_world.inject(APPLY, True)
    code, out, _ = run_cli(
        ["color", "grade", "init", "timeline", "--drx", str(DRX), "--try", "--force"]
    )
    assert code == 1 and "VERIFY_FAILED" in out
    assert graph.nodes == []
