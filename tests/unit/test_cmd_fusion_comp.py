"""`fusion comp add|import|export|load|rename|delete`, `clip`, `insert` and `cache` (U17b)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.commands import fusion_comp
from davinci_axi.fake import FakeWorld, ext_fusion, ext_fusion_edit, ext_writes_core
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

SAVE = "ProjectManager.SaveProject"
ITEM = ["--item", "V1:2"]
BARE = ["--item", "V1:1"]


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext_fusion.extend()
    ext_fusion_edit.extend()
    monkeypatch.setitem(registry.COMMANDS, "fusion", "davinci_axi.commands.fusion_comp:SPEC")
    yield


def _timeline(world: FakeWorld) -> FakeTimeline:
    project = world.resolve.pm.current_project
    assert project is not None and project.current_timeline is not None
    return project.current_timeline


def _clip(world: FakeWorld, number: int) -> FakeTimelineItem:
    return _timeline(world).tracks["video"][0].items[number - 1]


def _json(run_cli: Run, argv: list[str]) -> tuple[int, dict[str, Any]]:
    code, out, _ = run_cli([*argv, "--json"])
    return code, json.loads(out)


def _calls(world: FakeWorld, names: tuple[str, ...]) -> list[str]:
    return [n for cls, method, _ in world.calls if (n := f"{cls}.{method}") in names]


def _untouched(world: FakeWorld, tmp_path: Path) -> None:
    assert world.write_calls() == []
    assert ext_fusion.fusion_write_calls(world) == []
    assert not (tmp_path / "axi-home" / "snapshots").exists()


def test_comp_add_dry_run_then_force(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, data = _json(run_cli, ["fusion", "comp", "add", *BARE])
    assert code == 0 and data["applied"] is False
    assert data["changes"] == [{"field": "comps", "before": 0, "after": 1}]
    _untouched(fake_world, tmp_path)
    code, data = _json(run_cli, ["fusion", "comp", "add", *BARE, "--force"])
    assert code == 0 and data["applied"] is True and _clip(fake_world, 1).comps == ["Composition 1"]
    assert _calls(fake_world, (SAVE, "TimelineItem.AddFusionComp"))[:2] == [
        SAVE,
        "TimelineItem.AddFusionComp",
    ]
    assert _calls(fake_world, ("FusionComp.Lock", "FusionComp.StartUndo")) == []


def test_comp_import_uses_the_dummy_comp_workaround(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = tmp_path / "x.comp"
    source.write_text("fake comp\n", encoding="utf-8")
    assert _clip(fake_world, 1).ImportFusionComp(str(source)) is None  # quirk 2 on the fake
    fake_world.calls.clear()
    code, data = _json(run_cli, ["fusion", "comp", "import", str(source), *BARE])
    assert code == 0 and data["applied"] is False
    _untouched(fake_world, tmp_path)
    code, data = _json(run_cli, ["fusion", "comp", "import", str(source), *BARE, "--force"])
    assert code == 0 and data["applied"] is True
    steps = (
        "TimelineItem.AddFusionComp",
        "TimelineItem.LoadFusionCompByName",
        "TimelineItem.ImportFusionComp",
        "TimelineItem.DeleteFusionCompByName",
    )
    assert _calls(fake_world, steps) == list(steps)
    assert _clip(fake_world, 1).comps == ["Composition 2"]


def test_comp_import_into_an_item_with_a_comp_adds_one_and_cleans_up_on_failure(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = tmp_path / "y.comp"
    source.write_text("fake comp\n", encoding="utf-8")
    code, _ = _json(run_cli, ["fusion", "comp", "import", str(source), *ITEM, "--force"])
    assert code == 0 and len(_clip(fake_world, 2).comps) == 2
    fake_world.inject("TimelineItem.ImportFusionComp", None)
    code, out, _ = run_cli(["fusion", "comp", "import", str(source), *ITEM, "--force"])
    assert code == 1 and "API_FAILED" in out and len(_clip(fake_world, 2).comps) == 2
    code, out, _ = run_cli(["fusion", "comp", "import", str(tmp_path / "no.comp"), *ITEM])
    assert code == 1 and "NOT_FOUND" in out


def test_comp_export_writes_a_file_and_never_overwrites_by_default(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "out" / "c.comp"
    code, data = _json(run_cli, ["fusion", "comp", "export", str(target), *ITEM])
    assert code == 0 and target.is_file() and data["bytes"] > 0
    assert fake_world.write_calls() == ["TimelineItem.ExportFusionComp"]
    assert not (tmp_path / "axi-home" / "snapshots").exists()
    code, out, _ = run_cli(["fusion", "comp", "export", str(target), *ITEM])
    assert code == 1 and "FILE_EXISTS" in out
    code, _, _ = run_cli(["fusion", "comp", "export", str(target), *ITEM, "--overwrite"])
    assert code == 0
    code, out, _ = run_cli(["fusion", "comp", "export", str(tmp_path / "z.comp"), "--active"])
    assert code == 2 and "read-only" in out


def test_comp_load_needs_no_snapshot_and_rejects_unknown_names(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _clip(fake_world, 2).comps.append("Composition 2")
    code, data = _json(run_cli, ["fusion", "comp", "load", "Composition 2", *ITEM, "--force"])
    assert code == 0 and data["applied"] is True
    assert _calls(fake_world, (SAVE, "ProjectManager.ExportProject")) == []
    assert fake_world.calls_to("TimelineItem.LoadFusionCompByName") == [("Composition 2",)]
    code, out, _ = run_cli(["fusion", "comp", "load", "Nope", *ITEM, "--force"])
    assert code == 1 and "NOT_FOUND" in out and "Composition 1" in out


def test_comp_rename_reads_back_and_refuses_a_taken_name(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, data = _json(
        run_cli, ["fusion", "comp", "rename", "Composition 1", "--to", "Hero", *ITEM]
    )
    assert (
        code == 0 and data["applied"] is False and _clip(fake_world, 2).comps == ["Composition 1"]
    )
    code, data = _json(
        run_cli, ["fusion", "comp", "rename", "Composition 1", "--to", "Hero", *ITEM, "--force"]
    )
    assert code == 0 and data["applied"] is True and _clip(fake_world, 2).comps == ["Hero"]
    _clip(fake_world, 2).comps.append("Other")
    code, _, _ = run_cli(["fusion", "comp", "rename", "Hero", "--to", "Other", *ITEM, "--force"])
    assert code == 2


def test_comp_delete_exports_first_and_is_irreversible(run_cli: Run, fake_world: FakeWorld) -> None:
    code, data = _json(run_cli, ["fusion", "comp", "delete", "Composition 1", *ITEM])
    assert code == 0 and data["applied"] is False and data["irreversible"] is True
    assert fake_world.write_calls() == []
    code, data = _json(run_cli, ["fusion", "comp", "delete", "Composition 1", *ITEM, "--force"])
    assert code == 0 and data["applied"] is True and _clip(fake_world, 2).comps == []
    order = _calls(
        fake_world, ("TimelineItem.ExportFusionComp", "TimelineItem.DeleteFusionCompByName")
    )
    assert order == ["TimelineItem.ExportFusionComp", "TimelineItem.DeleteFusionCompByName"]
    snapshot = Path(str(data["comp_snapshot"]).replace("~", str(Path.home()), 1))
    assert snapshot.is_file()
    code, out, _ = run_cli(["fusion", "comp", "delete", "Nope", *ITEM, "--force"])
    assert code == 1 and "NOT_FOUND" in out


def test_a_failed_project_snapshot_blocks_every_comp_verb(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject(SAVE, False)
    for argv in (["comp", "add", *BARE], ["comp", "delete", "Composition 1", *ITEM]):
        code, out, _ = run_cli(["fusion", *argv, "--force"])
        assert code == 1 and "SNAPSHOT_FAILED" in out
    assert (
        _calls(
            fake_world,
            (
                "TimelineItem.AddFusionComp",
                "TimelineItem.ExportFusionComp",
                "TimelineItem.DeleteFusionCompByName",
            ),
        )
        == []
    )


def test_clip_create_replaces_the_items_and_is_irreversible(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, data = _json(run_cli, ["fusion", "clip", "create", "V1:1,V1:2"])
    assert code == 0 and data["applied"] is False and data["irreversible"] is True
    _untouched(fake_world, tmp_path)
    code, data = _json(run_cli, ["fusion", "clip", "create", "V1:1,V1:2", "--force"])
    assert code == 0 and data["applied"] is True
    names = [i.name for i in _timeline(fake_world).tracks["video"][0].items]
    assert names == ["clip-3", "Fusion Clip"]
    assert fake_world.calls_to("Timeline.CreateFusionClip")[0][0][0].name == "clip-1"


def test_clip_create_validates_its_items(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["fusion", "clip", "create", " , ", "--force"])
    assert code == 2 and "no items" in out
    code, out, _ = run_cli(["fusion", "clip", "create", "V1:1,V9:1", "--force"])
    assert code == 1 and "NOT_FOUND" in out
    assert fake_world.write_calls() == []
    fake_world.inject("Timeline.CreateFusionClip", None)
    code, out, _ = run_cli(["fusion", "clip", "create", "V1:1", "--force"])
    assert code == 1 and "API_FAILED" in out


@pytest.mark.parametrize(
    ("argv", "call", "name"),
    [
        (["composition"], "InsertFusionCompositionIntoTimeline", "Fusion Composition"),
        (["generator", "Solid Color"], "InsertFusionGeneratorIntoTimeline", "Solid Color"),
        (["title", "Text+"], "InsertFusionTitleIntoTimeline", "Text+"),
    ],
)
def test_insert_adds_one_item_and_warns_about_the_track_selector(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, argv: list[str], call: str, name: str
) -> None:
    code, data = _json(run_cli, ["fusion", "insert", *argv, "--allow-ripple"])
    assert (
        code == 0 and data["applied"] is False and "Source Track Selector" in str(data["warning"])
    )
    _untouched(fake_world, tmp_path)
    code, data = _json(run_cli, ["fusion", "insert", *argv, "--allow-ripple", "--force"])
    assert code == 0 and data["applied"] is True
    v1 = _timeline(fake_world).tracks["video"][0].items
    inserted = next(i for i in v1 if i.name == name)
    position = v1.index(inserted) + 1
    assert data["item"] == f"V1:{position}" and data["item_id"] == inserted.GetUniqueId()
    assert data["changes"] == [{"field": argv[0], "before": None, "after": f"V1@{inserted.start}"}]
    assert fake_world.calls_to(f"Timeline.{call}") == [tuple(argv[1:])]


def test_insert_on_a_locked_track_is_api_failed(run_cli: Run, fake_world: FakeWorld) -> None:
    _timeline(fake_world).tracks["video"][0].locked = True
    code, out, _ = run_cli(["fusion", "insert", "title", "Text+", "--allow-ripple", "--force"])
    assert code == 1 and "API_FAILED" in out and "locked" in out
    assert len(_timeline(fake_world).tracks["video"][0].items) == 3


def test_cache_set_reads_back_and_validates_the_mode(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, data = _json(run_cli, ["fusion", "cache", "set", "enabled", *ITEM])
    assert code == 0 and data["changes"] == [
        {"field": "cache", "before": "auto", "after": "enabled"}
    ]
    _untouched(fake_world, tmp_path)
    code, data = _json(run_cli, ["fusion", "cache", "set", "enabled", *ITEM, "--force"])
    assert code == 0 and data["applied"] is True
    assert _clip(fake_world, 2).GetIsFusionOutputCacheEnabled() == "enabled"
    code, data = _json(run_cli, ["fusion", "cache", "set", "enabled", *ITEM, "--force"])
    assert data["applied"] is False and data["changes"] == []
    code, out, _ = run_cli(["fusion", "cache", "set", "sometimes", *ITEM, "--force"])
    assert code == 2 and "auto, enabled, disabled" in out


def test_item_level_mutations_refuse_active_and_a_non_current_timeline(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["fusion", "comp", "add", "--active", "--force"])
    assert code == 2 and "read-only" in out
    code, out, _ = run_cli(
        ["fusion", "cache", "set", "auto", *ITEM, "--timeline", "Edit v2", "--force"]
    )
    assert code == 1 and "FUSION_TIMELINE_NOT_CURRENT" in out
    code, out, _ = run_cli(["fusion", "clip", "create", "V1:1", "--timeline", "Edit v2", "--force"])
    assert code == 1 and "FUSION_TIMELINE_NOT_CURRENT" in out
    assert fake_world.write_calls() == []


def test_the_parent_keeps_its_read_verbs_and_declares_every_new_one(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    spec = fusion_comp.SPEC
    assert spec.reads_only is False
    assert set(spec.subcommands) >= {
        "comps",
        "comp",
        "tools",
        "tool",
        "graph",
        "registry",
        "undo-stack",
        "keyframe",
        "undo",
        "clip",
        "insert",
        "cache",
    }
    assert {"show", "add", "import", "export", "load", "rename", "delete"} == set(
        spec.subcommands["comp"].subcommands
    )
    assert {"Timeline.CreateFusionClip", "TimelineItem.ImportFusionComp"} <= set(spec.methods)
    code, out, _ = run_cli(["fusion", "comps", *ITEM])
    assert code == 0 and "Composition 1" in out


def _walk(path: tuple[str, ...], spec: CommandSpec) -> Iterator[tuple[str, ...]]:
    yield path
    for name, child in spec.subcommands.items():
        yield from _walk((*path, name), child)


def test_every_fusion_path_has_help_and_examples(run_cli: Run, fake_world: FakeWorld) -> None:
    paths = list(_walk(("fusion",), fusion_comp.SPEC))
    assert len(paths) > 30
    for path in paths:
        code, out, err = run_cli([*path, "--help"])
        assert (code, err) == (0, ""), path
        assert "examples[" in out, path
    assert fake_world.calls == []


def test_comp_delete_of_the_only_comp_warns_ahead_and_explains_a_refusal(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, data = _json(run_cli, ["fusion", "comp", "delete", "Composition 1", *ITEM])
    assert code == 0 and data["applied"] is False and data["only_comp"] is True
    assert "only comp" in str(data["warning"])
    fake_world.inject("TimelineItem.DeleteFusionCompByName", False)
    code, out, _ = run_cli(["fusion", "comp", "delete", "Composition 1", *ITEM, "--force"])
    assert code == 1 and "API_FAILED" in out and "pass-through" in out and "snapshot" in out
    assert _calls(fake_world, ("TimelineItem.ExportFusionComp",)) != []
    assert _clip(fake_world, 2).comps == ["Composition 1"]


def test_comp_delete_loads_another_comp_first_only_when_the_target_is_active(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    clip = _clip(fake_world, 2)
    clip.comps.append("Composition 2")
    ext_fusion.fusion_of(fake_world).current = ext_fusion.comp_of(clip, "Composition 1")
    fake_world.calls.clear()
    code, data = _json(run_cli, ["fusion", "comp", "delete", "Composition 1", *ITEM, "--force"])
    assert code == 0 and data["applied"] is True and data["only_comp"] is False
    assert data["loaded_first"] == "Composition 2"
    assert fake_world.calls_to("TimelineItem.LoadFusionCompByName") == [("Composition 2",)]
    order = _calls(
        fake_world,
        ("TimelineItem.LoadFusionCompByName", "TimelineItem.DeleteFusionCompByName"),
    )
    assert order == ["TimelineItem.LoadFusionCompByName", "TimelineItem.DeleteFusionCompByName"]
    clip.comps.append("Composition 3")
    ext_fusion.fusion_of(fake_world).current = ext_fusion.comp_of(clip, "Composition 2")
    fake_world.calls.clear()
    code, data = _json(run_cli, ["fusion", "comp", "delete", "Composition 3", *ITEM, "--force"])
    assert code == 0 and "loaded_first" not in data
    assert fake_world.calls_to("TimelineItem.LoadFusionCompByName") == []


def test_comp_add_warns_about_21_1_1_and_only_the_first_comp_is_permanent(run_cli: Run) -> None:
    code, data = _json(run_cli, ["fusion", "comp", "add", *BARE])
    assert code == 0 and "permanent" in str(data["warning"]) and "21.1.1" in str(data["warning"])
    code, data = _json(run_cli, ["fusion", "comp", "add", *ITEM])
    assert code == 0 and "permanent" not in str(data["warning"])
    assert fusion_comp.ITEM_COMP_NOTE in str(data["warning"])


def test_comp_add_reads_back_adoption_and_the_count(run_cli: Run, fake_world: FakeWorld) -> None:
    code, data = _json(run_cli, ["fusion", "comp", "add", *ITEM, "--force"])
    assert code == 0 and data["applied"] is True and data["adopted"] is True
    assert data["count"] == len(_clip(fake_world, 2).comps) == 2


def test_comp_add_reports_an_unadopted_comp_without_failing(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    fake_world.inject("TimelineItem.GetFusionCompCount", 0)
    code, out, _ = run_cli(["fusion", "comp", "add", *ITEM, "--force", "--json"])
    data = json.loads(out)
    assert code == 0 and data["adopted"] is False and data["count"] == 0
    assert "does not list the new comp" in "\n".join(data.get("help", []))


def test_comp_add_help_names_adopted_and_the_observed_limits(run_cli: Run) -> None:
    code, out, _ = run_cli(["fusion", "comp", "add", "--help"])
    assert code == 0 and "adopted" in out and "--via-page" in out and "21.1.1" in out
    assert " ".join(fusion_comp.ITEM_COMP_NOTE.split()[:6]) in " ".join(out.split())


def test_fusion_notes_state_the_observed_ladder_result_without_cache_off(run_cli: Run) -> None:
    code, out, _ = run_cli(["fusion", "--help"])
    assert code == 0 and "no ladder step changed the render" in " ".join(out.split())
    assert "auto|enabled|disabled" in out and "cache set off" not in out
