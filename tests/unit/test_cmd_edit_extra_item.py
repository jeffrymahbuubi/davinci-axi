"""U25 item verbs: link, unlink, transition, sidecar, take, blanking, stereo and custom marker
data. Timeline verbs are in test_cmd_edit_extra.py."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeProject, FakeTimeline, FakeTimelineItem, FakeWorld, ext_edit_extra
from davinci_axi.fake.ext_edit_extra_item import sidecar_updates

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

SAVE = "ProjectManager.SaveProject"
EXPORT = "ProjectManager.ExportProject"


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_edit_extra.extend()
    ext_edit_extra.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "item", "davinci_axi.commands.item_extra:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")


def _edit(world: FakeWorld) -> FakeTimeline:
    project: FakeProject | None = world.resolve.pm.current_project
    assert project is not None
    return project.timelines[0]


def _clip(world: FakeWorld, position: int) -> FakeTimelineItem:
    return _edit(world).tracks["video"][0].items[position - 1]


def _audio(world: FakeWorld) -> FakeTimelineItem:
    return _edit(world).tracks["audio"][0].items[0]


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def test_unlink_then_link_roundtrip_and_no_op_when_already_there(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["item", "link", "V1:1,A1:1", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false"
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["item", "unlink", "V1:1,A1:1", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert _clip(fake_world, 1).linked == [] and _audio(fake_world).linked == []
    assert fake_world.write_calls() == [SAVE, EXPORT, "Timeline.SetClipsLinked"]
    assert fake_world.calls_to("Timeline.SetClipsLinked")[0][1] is False
    code, out, _ = run_cli(["item", "link", "V1:1,A1:1", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert _audio(fake_world) in _clip(fake_world, 1).linked
    assert _clip(fake_world, 1) in _audio(fake_world).linked
    assert "linked,unlinked,linked" in out


def test_link_dry_run_and_bad_input(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["item", "link", "V1:2,V1:3"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert "linked" in out and fake_world.write_calls() == []
    assert not _snapshots(tmp_path).exists()
    assert run_cli(["item", "link", "V1:1", "--force"])[0] == 2
    assert run_cli(["item", "link", "V1:1,,V1:2", "--force"])[0] == 2
    code, out, _ = run_cli(["item", "link", "V1:1,V1:9", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert fake_world.write_calls() == []


def test_link_two_unlinked_items_and_unlink_a_single_item(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    assert run_cli(["item", "link", "V1:2,V1:3", "--force"])[0] == 0
    assert _clip(fake_world, 3) in _clip(fake_world, 2).linked
    code, out, _ = run_cli(["item", "unlink", "V1:2", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert _clip(fake_world, 2).linked == [] and _clip(fake_world, 3).linked == []


def test_link_refused_is_api_failed_and_a_failed_read_back_is_verify_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("Timeline.SetClipsLinked", False)
    code, out, _ = run_cli(["item", "link", "V1:2,V1:3", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    fake_world.fail.clear()
    fake_world.inject("Timeline.SetClipsLinked", True)
    code, out, _ = run_cli(["item", "link", "V1:2,V1:3", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "VERIFY_FAILED"


def test_transition_adds_an_item_with_the_options(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = ["item", "transition", "V1:2", "--type", "Cross Dissolve", "--duration", "12"]
    code, out, _ = run_cli(args)
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    code, out, _ = run_cli([*args, "--position", "end", "--category", "simple", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.calls_to("TimelineItem.AddTransition") == [
        (
            {
                "type": "Cross Dissolve",
                "category": "simple",
                "position": "end",
                "alignment": "center",
                "duration": 12,
            },
        )
    ]
    middle = _clip(fake_world, 2)
    items = _edit(fake_world).tracks["video"][0].items
    added = items[items.index(middle) + 1]
    assert (added.name, added.kind, added.duration) == ("Cross Dissolve", "Transition", 12)
    assert added.start == middle.start + middle.duration - 6
    assert fake_world.write_calls() == [SAVE, EXPORT, "TimelineItem.AddTransition"]


def test_transition_sends_live_required_options_and_lists_in_timeline_order(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    # Live 21.1.1 returns None unless category, position and alignment are all present, and
    # lists the new transition among the clips, so the next clip moves to the next index.
    middle = _clip(fake_world, 2)
    assert middle.AddTransition({"type": "Cross Dissolve", "category": "simple"}) is None
    args = ["item", "transition", "V1:2", "--type", "Cross Dissolve", "--force"]
    code, out, _ = run_cli(args)
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    sent = fake_world.calls_to("TimelineItem.AddTransition")[-1][0]
    assert (sent["category"], sent["position"], sent["alignment"]) == ("simple", "start", "center")
    items = _edit(fake_world).tracks["video"][0].items
    assert [i.start for i in items] == sorted(i.start for i in items)
    assert items[1].kind == "Transition" and items[2] is middle


def test_transition_errors(run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld) -> None:
    base = ["item", "transition", "V1:2", "--type", "Cross Dissolve", "--force"]
    assert run_cli([*base, "--category", "weird"])[0] == 2
    assert run_cli([*base, "--duration", "0"])[0] == 2
    assert run_cli(["item", "transition", "V1:2", "--force"])[0] == 2
    assert run_cli(["item", "transition", "V1:9", "--type", "x", "--force"])[0] == 1
    assert fake_world.write_calls() == []
    fake_world.inject("TimelineItem.AddTransition", None)
    code, out, _ = run_cli(base)
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"


def test_sidecar_update_is_checked_by_the_return_value(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["item", "sidecar", "update", "V1:1"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert "not read back" in parse_toon_keys(out)["warning"]
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["item", "sidecar", "update", "V1:1", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert sidecar_updates(_clip(fake_world, 1)) == 1
    assert fake_world.write_calls() == [SAVE, EXPORT, "TimelineItem.UpdateSidecar"]
    fake_world.inject("TimelineItem.UpdateSidecar", False)
    code, out, _ = run_cli(["item", "sidecar", "update", "V1:1", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"


def test_take_add_list_select_delete_finalize(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    clip = _clip(fake_world, 1)
    code, out, _ = run_cli(["item", "take", "add", "V1:1", "A002_city", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true"
    assert keys["original_became_take_1"] == "true"
    assert "takes,0,2" in out and fake_world.write_calls()[-1] == "TimelineItem.AddTake"
    assert [t["endFrame"] for t in clip.takes] == [80, 192]
    code, out, _ = run_cli(["item", "take", "list", "V1:1"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["selected"] == "1"
    assert "1,true,A001_beach,0,80" in out and "2,false,A002_city,0,192" in out
    args = ["item", "take", "add", "V1:1", "A001_beach", "--in", "10", "--out", "60", "--force"]
    code, out, _ = run_cli(args)
    assert code == 0 and "takes,2,3" in out
    assert fake_world.calls_to("TimelineItem.AddTake")[-1][1:] == (10, 60)
    code, out, _ = run_cli(["item", "take", "select", "V1:1", "2", "--force"])
    assert code == 0 and "selected take,1,2" in out and clip.selected_take == 2
    code, out, _ = run_cli(["item", "take", "select", "V1:1", "2", "--force"])
    assert parse_toon_keys(out)["applied"] == "false"
    code, out, _ = run_cli(["item", "take", "delete", "V1:1", "1"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and keys["irreversible"] == "true"
    code, out, _ = run_cli(["item", "take", "delete", "V1:1", "1", "--force"])
    assert code == 0 and parse_toon_keys(out)["irreversible"] == "true"
    assert len(clip.takes) == 2 and clip.selected_take == 1
    code, out, _ = run_cli(["item", "take", "finalize", "V1:1", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["irreversible"] == "true"
    assert clip.takes == [] and clip.selected_take == 0
    assert "0 takes" in run_cli(["item", "take", "list", "V1:1"])[1]


def test_take_add_with_only_one_bound(run_cli: Run, fake_world: FakeWorld) -> None:
    assert run_cli(["item", "take", "add", "V1:1", "A002_city", "--in", "5", "--force"])[0] == 0
    assert run_cli(["item", "take", "add", "V1:1", "A002_city", "--out", "40", "--force"])[0] == 0
    calls = fake_world.calls_to("TimelineItem.AddTake")
    assert [c[1:] for c in calls] == [(5, None), (0, 40)]


@pytest.mark.parametrize(
    "argv",
    [
        ["item", "take", "add", "V1:1", "nosuchclip"],
        ["item", "take", "add", "V1:1", "A002_city", "--in", "50", "--out", "10"],
        ["item", "take", "add", "V1:1", "A002_city", "--in", "-1"],
        ["item", "take", "select", "V1:1", "1"],
        ["item", "take", "delete", "V1:1", "x"],
        ["item", "take", "finalize", "V1:1"],
    ],
)
def test_take_errors_happen_before_any_write(
    run_cli: Run, fake_world: FakeWorld, argv: list[str]
) -> None:
    code, _, _ = run_cli([*argv, "--force"])
    assert code in (1, 2)
    assert fake_world.write_calls() == []


def test_take_refused_writes_are_api_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    assert run_cli(["item", "take", "add", "V1:1", "A002_city", "--force"])[0] == 0
    for verb, extra, method in (
        ("add", ["A001_beach"], "AddTake"),
        ("select", ["1"], "SelectTakeByIndex"),
        ("delete", ["1"], "DeleteTakeByIndex"),
        ("finalize", [], "FinalizeTake"),
    ):
        fake_world.inject(f"TimelineItem.{method}", False)
        code, out, _ = run_cli(["item", "take", verb, "V1:1", *extra, "--force"])
        if verb == "select":  # already selected: a no-op, nothing is called
            assert code == 0
            continue
        assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED", verb


def test_item_blanking_follows_the_timeline_until_use_timeline_is_off(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    keys = parse_toon_keys(run_cli(["item", "blanking", "show", "V1:1"])[1])
    assert keys["uses_timeline"] == "true" and keys["Top"] == "0"
    code, out, _ = run_cli(["item", "blanking", "set", "V1:1", "Top=10", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "BLANKING_FOLLOWS_TIMELINE"
    assert "use-timeline V1:1 off" in out and fake_world.write_calls() == []
    code, out, _ = run_cli(["item", "blanking", "use-timeline", "V1:1", "off"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["item", "blanking", "use-timeline", "V1:1", "off", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.calls_to("TimelineItem.SetUseTimelineForOutputBlanking") == [(False,)]
    code, out, _ = run_cli(["item", "blanking", "set", "V1:1", "Top=10,Left=4", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.calls_to("TimelineItem.SetOutputBlanking") == [
        ({"Top": 10, "Bottom": 0, "Left": 4, "Right": 0},)
    ]
    keys = parse_toon_keys(run_cli(["item", "blanking", "show", "V1:1"])[1])
    assert (keys["uses_timeline"], keys["Top"], keys["Left"]) == ("false", "10", "4")
    code, out, _ = run_cli(["item", "blanking", "use-timeline", "V1:1", "on", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    code, out, _ = run_cli(["item", "blanking", "use-timeline", "V1:1", "on", "--force"])
    assert parse_toon_keys(out)["applied"] == "false"


@pytest.mark.parametrize(
    "argv",
    [
        ["item", "blanking", "use-timeline", "V1:1", "maybe"],
        ["item", "blanking", "set", "V1:1", "Middle=3"],
        ["item", "blanking", "set", "V1:1", "Top=-2"],
    ],
)
def test_item_blanking_input_errors_exit_2(
    run_cli: Run, fake_world: FakeWorld, argv: list[str]
) -> None:
    assert run_cli([*argv, "--force"])[0] == 2
    assert fake_world.write_calls() == []


def test_item_stereo_renders_convergence_and_windows(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["item", "stereo", "V1:1"])
    assert code == 0 and "convergence[2]{offset,value}:" in out
    assert "  24,1.5" in out
    assert "left[1]{offset,left,right,top,bottom}:" in out
    assert "right[1]{offset,left,right,top,bottom}:" in out
    assert fake_world.write_calls() == []


def test_item_custom_marker_data_roundtrip(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = ["item", "marker", "custom", "set", "V1:1", "review-1", "--frame", "10"]
    code, out, _ = run_cli(args)
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    assert fake_world.calls_to("TimelineItem.UpdateMarkerCustomData") == [(10, "review-1")]
    assert fake_world.write_calls() == [SAVE, EXPORT, "TimelineItem.UpdateMarkerCustomData"]
    keys = parse_toon_keys(run_cli(["item", "marker", "custom", "get", "V1:1", "--frame", "10"])[1])
    assert keys["custom_data"] == "review-1" and keys["name"] == "Cut"
    keys = parse_toon_keys(
        run_cli(["item", "marker", "custom", "get", "V1:1", "--find", "review-1"])[1]
    )
    assert keys["frame"] == "10" and keys["color"] == "Blue"
    assert parse_toon_keys(run_cli([*args, "--force"])[1])["applied"] == "false"


def test_item_custom_marker_data_errors(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(
        ["item", "marker", "custom", "set", "V1:1", "x", "--frame", "3", "--force"]
    )
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert run_cli(["item", "marker", "custom", "get", "V1:1"])[0] == 2
    assert run_cli(["item", "marker", "custom", "get", "V1:1", "--find", "nope"])[0] == 1
    fake_world.inject("TimelineItem.UpdateMarkerCustomData", False)
    code, out, _ = run_cli(
        ["item", "marker", "custom", "set", "V1:1", "x", "--frame", "10", "--force"]
    )
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"


MUTATIONS: list[tuple[list[str], str]] = [
    (["item", "unlink", "V1:1,A1:1"], "Timeline.SetClipsLinked"),
    (["item", "link", "V1:2,V1:3"], "Timeline.SetClipsLinked"),
    (["item", "transition", "V1:2", "--type", "Cross Dissolve"], "TimelineItem.AddTransition"),
    (["item", "sidecar", "update", "V1:1"], "TimelineItem.UpdateSidecar"),
    (["item", "take", "add", "V1:1", "A002_city"], "TimelineItem.AddTake"),
    (
        ["item", "blanking", "use-timeline", "V1:1", "off"],
        "TimelineItem.SetUseTimelineForOutputBlanking",
    ),
    (
        ["item", "marker", "custom", "set", "V1:1", "x", "--frame", "10"],
        "TimelineItem.UpdateMarkerCustomData",
    ),
]


@pytest.mark.parametrize(("argv", "method"), MUTATIONS)
def test_every_item_mutation_is_a_dry_run_without_force(
    run_cli: Run,
    parse_toon_keys: Keys,
    fake_world: FakeWorld,
    tmp_path: Path,
    argv: list[str],
    method: str,
) -> None:
    code, out, _ = run_cli(argv)
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert "`davinci-axi item" in out and "--force`" in out
    assert fake_world.write_calls() == [] and fake_world.calls_to(method) == []
    assert not _snapshots(tmp_path).exists()


@pytest.mark.parametrize(("argv", "method"), MUTATIONS)
@pytest.mark.parametrize("failing", [SAVE, EXPORT])
def test_item_snapshot_failure_aborts_before_the_write(
    run_cli: Run,
    parse_toon_keys: Keys,
    fake_world: FakeWorld,
    argv: list[str],
    method: str,
    failing: str,
) -> None:
    fake_world.inject(failing, False)
    code, out, _ = run_cli([*argv, "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert method not in fake_world.write_calls()


@pytest.mark.parametrize("verb", ["delete", "finalize"])
def test_take_delete_and_finalize_snapshot_failure(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, verb: str
) -> None:
    assert run_cli(["item", "take", "add", "V1:1", "A002_city", "--force"])[0] == 0
    fake_world.inject(EXPORT, False)
    extra = ["1"] if verb == "delete" else []
    code, out, _ = run_cli(["item", "take", verb, "V1:1", *extra, "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert len(_clip(fake_world, 1).takes) == 2


def test_no_snapshot_is_refused_before_any_resolve_call(
    monkeypatch: pytest.MonkeyPatch, run_cli: Run, fake_world: FakeWorld
) -> None:
    monkeypatch.delenv("DAVINCI_AXI_ALLOW_UNSNAPSHOTTED", raising=False)
    for argv in (
        ["item", "link", "V1:2,V1:3"],
        ["item", "transition", "V1:2", "--type", "x"],
        ["item", "take", "add", "V1:1", "A002_city"],
    ):
        code, out, _ = run_cli([*argv, "--no-snapshot", "--force"])
        assert code == 1 and "WRITE_REFUSED" in out
    assert fake_world.calls == []


def test_the_wrapped_parent_still_serves_the_phase_2_verbs(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    for argv in (
        ["item", "set", "--help"],
        ["item", "marker", "add", "--help"],
        ["item", "marker", "custom", "--help"],
        ["item", "take", "--help"],
        ["item", "blanking", "--help"],
        ["item", "sidecar", "--help"],
    ):
        code, out, err = run_cli(argv)
        assert code == 0 and err == "" and "examples[" in out
    code, out, _ = run_cli(["item", "V1:2"])
    assert code == 0 and parse_toon_keys(out)["id"] == _clip(fake_world, 2).uid
    code, out, _ = run_cli(["item", "rename", "V1:2", "--to", "Interview", "--force"])
    assert code == 0 and _clip(fake_world, 2).name == "Interview"
    code, out, _ = run_cli(
        ["item", "marker", "add", "V1:2", "--frame", "5", "--name", "x", "--force"]
    )
    assert code == 0 and 5 in _clip(fake_world, 2).markers


def test_spec_methods_are_declared_on_the_parent() -> None:
    from davinci_axi.commands import item_extra

    expected = {
        "Timeline.SetClipsLinked",
        "TimelineItem.GetLinkedItems",
        "TimelineItem.AddTransition",
        "TimelineItem.UpdateSidecar",
        "TimelineItem.AddTake",
        "TimelineItem.DeleteTakeByIndex",
        "TimelineItem.SelectTakeByIndex",
        "TimelineItem.FinalizeTake",
        "TimelineItem.GetTakesCount",
        "TimelineItem.GetTakeByIndex",
        "TimelineItem.GetSelectedTakeIndex",
        "TimelineItem.SetOutputBlanking",
        "TimelineItem.GetOutputBlanking",
        "TimelineItem.SetUseTimelineForOutputBlanking",
        "TimelineItem.GetUseTimelineForOutputBlanking",
        "TimelineItem.GetStereoConvergenceValues",
        "TimelineItem.GetStereoLeftFloatingWindowParams",
        "TimelineItem.GetStereoRightFloatingWindowParams",
        "TimelineItem.UpdateMarkerCustomData",
        "TimelineItem.GetMarkerCustomData",
        "TimelineItem.GetMarkerByCustomData",
        "TimelineItem.SetName",
        "TimelineItem.AddMarker",
    }
    assert expected <= set(item_extra.SPEC.methods)
