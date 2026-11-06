"""`color graph|node|grade|cdl|version|group|cache|node-colors` (U19) on the fake."""

from __future__ import annotations

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
    """Neither the registry line nor the EXTENSIONS entry exists in the worktree."""
    ext_color.extend()
    ext_color.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "color", "davinci_axi.commands.color:SPEC")
    yield


def _item(world: FakeWorld, number: int, track: int = 1) -> FakeTimelineItem:
    project = world.resolve.pm.current_project
    assert project is not None
    return project.timelines[0].tracks["video"][track - 1].items[number - 1]


def _graph(world: FakeWorld, number: int = 1) -> Any:
    return _item(world, number).__dict__["graphs"][1]


def _hero(world: FakeWorld) -> None:
    """A colour group `Hero`, built without leaving calls in the log."""
    project = world.resolve.pm.current_project
    assert project is not None
    project.AddColorGroup("Hero")
    world.calls.clear()


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _apply(
    run_cli: Run, keys: Keys, world: FakeWorld, argv: list[str]
) -> tuple[dict[str, str], str]:
    """Run `argv --force`; it must succeed, apply, and snapshot before the first write."""
    code, out, err = run_cli([*argv, "--force"])
    assert (code, err) == (0, ""), out
    assert keys(out)["applied"] == "true", out
    assert world.write_calls()[:2] == [SAVE, EXPORT]
    return keys(out), out


def test_graph_lists_the_nodes_and_changes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["color", "graph", "--item", "V1:1"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "") and keys["count"] == "2"
    assert (
        "nodes[2]{index,label,tools,lut,cache}" in out and "1,Primary,Color Wheels,null,auto" in out
    )
    assert fake_world.write_calls() == [] and fake_world.page_changes() == []
    code, out, _ = run_cli(["color", "graph", "--timeline-graph"])
    assert code == 0 and "timeline Edit v1" in out
    code, out, _ = run_cli(["color", "graph", "--item", "V1:1", "--layer", "99"])
    assert code == 1 and "NOT_FOUND" in out


def test_graph_by_group_and_flag_validation(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["color", "graph", "--group", "Hero", "--pre"])
    assert code == 1 and "NOT_FOUND" in out and "(none)" in out
    _hero(fake_world)
    code, out, _ = run_cli(["color", "graph", "--group", "Hero", "--post"])
    assert code == 0 and "Hero (post-clip)" in out and "Post-clip" in out
    for argv in (
        ["color", "graph"],
        ["color", "graph", "--item", "V1:1", "--timeline-graph"],
        ["color", "graph", "--group", "Hero"],
        ["color", "graph", "--timeline-graph", "--layer", "2"],
        ["color", "graph", "--item", "V1:1", "--pre"],
    ):
        assert run_cli(argv)[0] == 2, argv


def test_node_cache_passes_the_enum_and_reads_it_back(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    keys, _ = _apply(
        run_cli, parse_toon_keys, fake_world, ["color", "node", "cache", "V1:1", "2", "enabled"]
    )
    enabled = fake_world.resolve.CACHE_ENABLED  # type: ignore[attr-defined]
    assert fake_world.calls_to("Graph.SetNodeCacheMode") == [(2, enabled)]
    assert _graph(fake_world).nodes[1]["cache"] == int(enabled)
    assert keys["mode"] == "enabled"
    code, out, _ = run_cli(["color", "node", "cache", "V1:1", "2", "enabled", "--force"])
    assert code == 0 and "applied: false" in out  # already enabled: a no-op


def test_node_enable_disable_and_lut_are_checked_by_the_call(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    keys, out = _apply(
        run_cli, parse_toon_keys, fake_world, ["color", "node", "disable", "V1:1", "2"]
    )
    assert "not read back" in out and keys["node"] == "2"
    assert _graph(fake_world).nodes[1]["enabled"] is False
    _apply(run_cli, parse_toon_keys, fake_world, ["color", "node", "enable", "V1:1", "2"])
    assert _graph(fake_world).nodes[1]["enabled"] is True
    lut = tmp_path / "film.cube"
    lut.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")
    fake_world.calls.clear()
    _apply(
        run_cli, parse_toon_keys, fake_world, ["color", "node", "lut", "set", "V1:1", "1", str(lut)]
    )
    code, out, _ = run_cli(["color", "node", "lut", "show", "V1:1", "1"])
    assert code == 0 and str(lut) in out


def test_node_arguments_are_validated_before_resolve_is_touched(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    missing = str(tmp_path / "nope.cube")
    code, out, _ = run_cli(["color", "node", "lut", "set", "V1:1", "1", missing])
    assert code == 1 and "NOT_FOUND" in out and fake_world.calls == []
    for argv in (
        ["color", "node", "enable", "V1:1", "zero"],
        ["color", "node", "cache", "V1:1", "1", "sometimes"],
        ["color", "node", "disable", "V1:1", "1", "--layer", "0"],
    ):
        assert run_cli(argv)[0] == 2, argv
        assert fake_world.calls == []
    code, out, _ = run_cli(["color", "node", "enable", "V1:1", "9", "--force"])
    assert code == 1 and "2 nodes" in out and fake_world.write_calls() == []


def test_grade_reset_is_irreversible_and_copy_is_one_call(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["color", "grade", "reset", "V1:1"])
    assert code == 0 and "irreversible: true" in out and fake_world.write_calls() == []
    _, out = _apply(run_cli, parse_toon_keys, fake_world, ["color", "grade", "reset", "V1:1"])
    assert "irreversible: true" in out and _graph(fake_world).resets == 1
    fake_world.calls.clear()
    _apply(run_cli, parse_toon_keys, fake_world, ["color", "grade", "copy", "V1:1", "V1:2,V1:3"])
    assert len(fake_world.calls_to("TimelineItem.CopyGrades")) == 1
    assert _item(fake_world, 3).__dict__["grade_copied_from"] == _item(fake_world, 1).uid
    code, out, _ = run_cli(["color", "grade", "copy", "V1:1", "V1:1,V1:2", "--force"])
    assert code == 2 and "source item" in out
    drx = tmp_path / "look.drx"
    drx.write_text("drx\n", encoding="utf-8")
    fake_world.calls.clear()
    argv = [
        "color",
        "grade",
        "apply-drx",
        "V1:1",
        str(drx),
        "--mode",
        "start-frames",
        "--all-layers",
    ]
    _apply(run_cli, parse_toon_keys, fake_world, argv)
    assert _graph(fake_world).drx == [(str(drx), 2, True)]
    fake_world.calls.clear()
    _apply(run_cli, parse_toon_keys, fake_world, ["color", "grade", "arri-cdl", "V1:1"])
    assert _graph(fake_world).arri_applied
    assert run_cli(["color", "grade", "apply-drx", "V1:1", str(tmp_path / "x.drx")])[0] == 1
    assert run_cli(["color", "grade", "apply-drx", "V1:1", str(drx), "--mode", "bogus"])[0] == 2


def test_cdl_set_validates_before_connect_and_passes_the_dict(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    for text in (
        "NodeIndex=1,Slope=1 1",
        "NodeIndex=1,Offset=a b c",
        "NodeIndex=1,Gamma=1 1 1",
        "Slope=1 1 1",
        "NodeIndex=1",
        "NodeIndex=0,Saturation=1",
        "NodeIndex=1,Saturation=high",
    ):
        assert run_cli(["color", "cdl", "set", "V1:1", text])[0] == 2, text
    assert fake_world.calls == []
    code, out, _ = run_cli(["color", "cdl", "set", "V1:1", "NodeIndex=3,Saturation=0.8"])
    assert code == 1 and "NOT_FOUND" in out
    text = "NodeIndex=1,Slope=1.1  1 0.9,Saturation=0.8"
    keys, _ = _apply(run_cli, parse_toon_keys, fake_world, ["color", "cdl", "set", "V1:1", text])
    assert fake_world.calls_to("TimelineItem.SetCDL") == [
        (
            {
                "NodeIndex": 1,
                "Slope": "1.1 1 0.9",
                "Saturation": 0.8,
                "Offset": "0 0 0",
                "Power": "1 1 1",
            },
        )
    ]
    assert "Slope=1.1 1 0.9" in keys["cdl"]
    assert "Offset, Power sent as neutral values" in keys["neutral"]


def test_resolve_refuses_a_partial_cdl_so_omitted_keys_go_neutral(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    # Live 21.1.1: SetCDL({NodeIndex, Slope, Saturation}) returns False; all four keys succeed
    assert (
        _item(fake_world, 1).SetCDL({"NodeIndex": 1, "Slope": "1 1 1", "Saturation": 1.0}) is False
    )
    fake_world.calls.clear()
    keys, _ = _apply(
        run_cli,
        parse_toon_keys,
        fake_world,
        ["color", "cdl", "set", "V1:1", "NodeIndex=1,Slope=1.6 1 0.5"],
    )
    assert keys["applied"] == "true"
    assert "Offset=0 0 0 Power=1 1 1 Saturation=1.0" in keys["cdl"]


def test_version_lifecycle(run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["color", "version", "list", "V1:2"])
    assert code == 0 and "Version 1,true" in out
    _apply(run_cli, parse_toon_keys, fake_world, ["color", "version", "add", "V1:2", "alt"])
    assert _item(fake_world, 2).versions[0] == ["Version 1", "alt"]
    _apply(run_cli, parse_toon_keys, fake_world, ["color", "version", "load", "V1:2", "alt"])
    assert _item(fake_world, 2).GetCurrentVersion()["versionName"] == "alt"
    _apply(
        run_cli,
        parse_toon_keys,
        fake_world,
        ["color", "version", "rename", "V1:2", "alt", "--to", "final"],
    )
    assert _item(fake_world, 2).versions[0] == ["Version 1", "final"]
    _, out = _apply(
        run_cli, parse_toon_keys, fake_world, ["color", "version", "delete", "V1:2", "final"]
    )
    assert "irreversible: true" in out and _item(fake_world, 2).versions[0] == ["Version 1"]
    _apply(
        run_cli,
        parse_toon_keys,
        fake_world,
        ["color", "version", "add", "V1:2", "cloud", "--remote"],
    )
    assert _item(fake_world, 2).versions[1] == ["cloud"]
    code, out, _ = run_cli(["color", "version", "add", "V1:2", "Version 1", "--force"])
    assert code == 1 and "VERSION_EXISTS" in out
    code, out, _ = run_cli(["color", "version", "load", "V1:2", "ghost", "--force"])
    assert code == 1 and "NOT_FOUND" in out and "Version 1" in out
    assert run_cli(["color", "version", "rename", "V1:2", "Version 1"])[0] == 2  # --to is required


def test_group_lifecycle(run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["color", "group", "list"])
    assert code == 0 and "0 color groups in this project" in out
    _apply(run_cli, parse_toon_keys, fake_world, ["color", "group", "add", "Hero"])
    code, out, _ = run_cli(["color", "group", "add", "Hero", "--force"])
    assert code == 1 and "GROUP_EXISTS" in out
    fake_world.calls.clear()
    _apply(run_cli, parse_toon_keys, fake_world, ["color", "group", "assign", "V1:1", "Hero"])
    assert _item(fake_world, 1).GetColorGroup().GetName() == "Hero"
    code, out, _ = run_cli(["color", "group", "list"])
    assert "Hero,1" in out
    fake_world.calls.clear()
    _apply(
        run_cli, parse_toon_keys, fake_world, ["color", "group", "rename", "Hero", "--to", "Lead"]
    )
    fake_world.calls.clear()
    _apply(run_cli, parse_toon_keys, fake_world, ["color", "group", "unassign", "V1:1"])
    assert _item(fake_world, 1).GetColorGroup() is None
    code, out, _ = run_cli(["color", "group", "unassign", "V1:1", "--force"])
    assert code == 0 and "applied: false" in out
    fake_world.calls.clear()
    _, out = _apply(run_cli, parse_toon_keys, fake_world, ["color", "group", "delete", "Lead"])
    assert "irreversible: true" in out
    code, out, _ = run_cli(["color", "group", "assign", "V1:1", "Lead", "--force"])
    assert code == 1 and "NOT_FOUND" in out


def test_cache_and_node_colors(run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld) -> None:
    _apply(run_cli, parse_toon_keys, fake_world, ["color", "cache", "set", "V1:1", "on"])
    assert _item(fake_world, 1).GetIsColorOutputCacheEnabled() is True
    code, out, _ = run_cli(["color", "cache", "set", "V1:1", "on", "--force"])
    assert code == 0 and "applied: false" in out
    assert run_cli(["color", "cache", "set", "V1:1", "maybe"])[0] == 2
    fake_world.calls.clear()
    _apply(run_cli, parse_toon_keys, fake_world, ["color", "node-colors", "reset", "V1:1"])
    assert _item(fake_world, 1).__dict__["node_colors_reset"] == 1


def test_a_target_timeline_is_opened_and_restored(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    project.timelines[1].add_item("video", 1, "other", 86400, 10)
    argv = ["color", "cache", "set", "V1:1", "on", "--timeline", "Edit v2"]
    keys, _ = _apply(run_cli, parse_toon_keys, fake_world, argv)
    assert keys["timeline_restored"] == "true"
    assert project.current_timeline is project.timelines[0]


@pytest.mark.parametrize(
    "argv",
    [
        ["node", "enable", "V1:1", "1"],
        ["node", "cache", "V1:1", "2", "enabled"],
        ["node", "lut", "set", "V1:1", "1", "{lut}"],
        ["grade", "apply-drx", "V1:1", "{lut}"],
        ["grade", "reset", "V1:1"],
        ["grade", "copy", "V1:1", "V1:2"],
        ["grade", "arri-cdl", "V1:1"],
        ["cdl", "set", "V1:1", "NodeIndex=1,Slope=1 1 1"],
        ["version", "add", "V1:1", "alt"],
        ["version", "rename", "V1:2", "Version 1", "--to", "Final"],
        ["version", "delete", "V1:2", "Version 1"],
        ["group", "add", "Alpha"],
        ["group", "rename", "Hero", "--to", "Lead"],
        ["group", "delete", "Hero"],
        ["group", "assign", "V1:1", "Hero"],
        ["cache", "set", "V1:1", "on"],
        ["node-colors", "reset", "V1:1"],
    ],
)
def test_every_dry_run_writes_nothing_and_names_the_force_command(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path, argv: list[str]
) -> None:
    lut = tmp_path / "x.file"
    lut.write_text("x\n", encoding="utf-8")
    _hero(fake_world)
    code, out, err = run_cli(["color", *(a.replace("{lut}", str(lut)) for a in argv)])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "") and keys["applied"] == "false", out
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    assert "--force" in out


@pytest.mark.parametrize(
    ("argv", "write"),
    [
        (["node", "cache", "V1:1", "2", "enabled"], "Graph.SetNodeCacheMode"),
        (["cdl", "set", "V1:1", "NodeIndex=1,Saturation=0.5"], "TimelineItem.SetCDL"),
        (["version", "add", "V1:1", "alt"], "TimelineItem.AddVersion"),
        (["group", "add", "Alpha"], "Project.AddColorGroup"),
        (["cache", "set", "V1:1", "on"], "TimelineItem.SetColorOutputCache"),
    ],
)
def test_a_failed_snapshot_aborts_before_the_write(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, argv: list[str], write: str
) -> None:
    fake_world.inject(EXPORT, False)
    code, out, _ = run_cli(["color", *argv, "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert write not in fake_world.write_calls()


def test_a_refused_write_is_api_failed_and_names_the_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("TimelineItem.SetCDL", False)
    code, out, _ = run_cli(["color", "cdl", "set", "V1:1", "NodeIndex=1,Saturation=0.5", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert "snapshot restore" in out
