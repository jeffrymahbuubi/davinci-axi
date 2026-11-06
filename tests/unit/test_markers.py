"""Marker add/delete plans, run end to end on the fake timeline (marker writes are patched in)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from davinci_axi import markers, registry
from davinci_axi.fake import FakeTimeline, FakeWorld, ext_writes_core
from davinci_axi.mutate import MUTATION_FLAGS, force_cmd, run_mutation
from davinci_axi.resolve_types_edit import TimelineEdit
from davinci_axi.spec import CommandSpec, GlobalOpts, Invocation, Result

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


def _add_marker(
    self: FakeTimeline,
    frameId: int,
    color: str,
    name: str,
    note: str,
    duration: int,
    customData: str | None = None,
) -> bool:
    injected, value = self._rec("AddMarker", frameId, color, name, note, duration, customData)
    if injected:
        return bool(value)
    if frameId in self.markers:
        return False
    self.markers[frameId] = {
        "color": color,
        "duration": duration,
        "note": note,
        "name": name,
        "customData": customData or "",
    }
    return True


def _delete_at(self: FakeTimeline, frameNum: int) -> bool:
    injected, value = self._rec("DeleteMarkerAtFrame", frameNum)
    return bool(value) if injected else self.markers.pop(frameNum, None) is not None


def _delete_color(self: FakeTimeline, color: str) -> bool:
    injected, value = self._rec("DeleteMarkersByColor", color)
    if injected:
        return bool(value)
    doomed = [f for f, m in self.markers.items() if color == "All" or m["color"] == color]
    for frame in doomed:
        del self.markers[frame]
    return bool(doomed)


def _delete_custom(self: FakeTimeline, customData: str) -> bool:
    injected, value = self._rec("DeleteMarkerByCustomData", customData)
    if injected:
        return bool(value)
    doomed = [f for f, m in self.markers.items() if m["customData"] == customData]
    for frame in doomed:
        del self.markers[frame]
    return bool(doomed)


def _run(inv: Invocation, build: Callable[..., Any], words: list[str]) -> Result:
    session = inv.connect()
    timeline = cast(TimelineEdit, session.require_timeline())
    plan, apply, verify = build(inv, timeline, f"timeline {timeline.GetName()}", words)
    return run_mutation(inv, session, plan, apply, verify)


def run_add(inv: Invocation) -> Result:
    markers.check_add_flags(inv)
    return _run(inv, markers.add_plan, ["mark", "add"])


def run_delete(inv: Invocation) -> Result:
    markers.check_delete_flags(inv)
    return _run(inv, markers.delete_plan, ["mark", "delete"])


ADD_SPEC = CommandSpec(
    "mark-add", "add", run=run_add, flags=(*markers.MARKER_ADD_FLAGS, *MUTATION_FLAGS)
)
DELETE_SPEC = CommandSpec(
    "mark-delete", "delete", run=run_delete, flags=(*markers.MARKER_DELETE_FLAGS, *MUTATION_FLAGS)
)


@pytest.fixture(autouse=True)
def _patched(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    for name, func in (
        ("AddMarker", _add_marker),
        ("DeleteMarkerAtFrame", _delete_at),
        ("DeleteMarkersByColor", _delete_color),
        ("DeleteMarkerByCustomData", _delete_custom),
    ):
        monkeypatch.setattr(FakeTimeline, name, func, raising=False)
    monkeypatch.setitem(registry.COMMANDS, "mark-add", f"{__name__}:ADD_SPEC")
    monkeypatch.setitem(registry.COMMANDS, "mark-delete", f"{__name__}:DELETE_SPEC")
    yield


def _markers(world: FakeWorld) -> dict[int, dict[str, Any]]:
    project = world.resolve.pm.current_project
    assert project is not None and project.current_timeline is not None
    return project.current_timeline.markers


def test_constants_match_the_pyi_palettes() -> None:
    assert len(markers.MARKER_COLORS) == 16 and markers.MARKER_COLORS[0] == "Blue"
    assert len(markers.CLIP_COLORS) == 16 and "Teal" in markers.CLIP_COLORS
    assert set(markers.MARKER_COLORS) & set(markers.CLIP_COLORS) == {
        "Yellow",
        "Green",
        "Blue",
        "Purple",
        "Pink",
    }


def test_add_dry_run_then_force_reads_back(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    argv = ["mark-add", "--frame", "10", "--color", "Green", "--name", "Pick"]
    code, out, _ = run_cli(argv)
    assert code == 0
    assert "marker@10,null,Green Pick" in out
    assert "changes[1]{field,before,after}:" in out
    assert "davinci-axi mark add --frame 10 --color Green --name Pick --force" in out
    assert fake_world.write_calls() == []
    assert 10 not in _markers(fake_world)
    assert not (tmp_path / "axi-home" / "snapshots").exists()

    code, out, _ = run_cli([*argv, "--note", "n", "--duration", "3", "--force"])
    assert code == 0
    assert parse_toon_keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [
        "ProjectManager.SaveProject",
        "ProjectManager.ExportProject",
        "Timeline.AddMarker",
    ]
    assert fake_world.calls_to("Timeline.AddMarker") == [(10, "Green", "Pick", "n", 3, "")]
    assert _markers(fake_world)[10]["name"] == "Pick"


def test_add_defaults_and_custom_data(run_cli: Run, fake_world: FakeWorld) -> None:
    code, _, _ = run_cli(["mark-add", "--frame", "0", "--custom-data", "t2", "--force"])
    assert code == 0
    assert fake_world.calls_to("Timeline.AddMarker") == [(0, "Blue", "", "", 1, "t2")]
    assert _markers(fake_world)[0]["customData"] == "t2"


def test_add_at_an_occupied_frame_is_refused_in_the_dry_run(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["mark-add", "--frame", "100"])
    assert code == 1
    assert parse_toon_keys(out)["code"] == "MARKER_EXISTS"
    assert fake_world.write_calls() == []


def test_add_failure_is_api_failed_after_the_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("Timeline.AddMarker", False)
    code, out, _ = run_cli(["mark-add", "--frame", "5", "--force"])
    assert code == 1
    assert parse_toon_keys(out)["code"] == "API_FAILED"


def test_add_verify_mismatch(run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld) -> None:
    fake_world.inject("Timeline.AddMarker", True)  # claims success, writes nothing
    code, out, _ = run_cli(["mark-add", "--frame", "5", "--force"])
    assert code == 1
    assert parse_toon_keys(out)["code"] == "VERIFY_FAILED"


@pytest.mark.parametrize(
    "argv",
    [
        ["mark-add"],
        ["mark-add", "--frame", "-1"],
        ["mark-add", "--frame", "5", "--duration", "0"],
        ["mark-add", "--frame", "5", "--color", "Teal"],
    ],
)
def test_add_usage_errors_exit_2_before_connecting(
    run_cli: Run, fake_world: FakeWorld, argv: list[str]
) -> None:
    code, _, _ = run_cli(argv)
    assert code == 2
    assert fake_world.calls == []


def test_delete_by_frame(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["mark-delete", "--frame", "100"])
    assert code == 0
    assert "marker@100,1,0" in out
    assert fake_world.write_calls() == []
    code, _, _ = run_cli(["mark-delete", "--frame", "100", "--force"])
    assert code == 0
    assert _markers(fake_world) == {}
    assert fake_world.calls_to("Timeline.DeleteMarkerAtFrame") == [(100,)]


def test_delete_by_color_counts_matches(run_cli: Run, fake_world: FakeWorld) -> None:
    timeline = fake_world.resolve.pm.current_project.current_timeline  # type: ignore[union-attr]
    assert timeline is not None
    timeline.markers[5] = {"color": "Red", "name": "b", "note": "", "customData": "", "duration": 1}
    timeline.markers[6] = {
        "color": "Blue",
        "name": "c",
        "note": "",
        "customData": "",
        "duration": 1,
    }
    code, out, _ = run_cli(["mark-delete", "--color", "Red"])
    assert code == 0 and '"markers:Red",2,0' in out
    code, _, _ = run_cli(["mark-delete", "--color", "Red", "--force"])
    assert code == 0
    assert list(_markers(fake_world)) == [6]
    code, _, _ = run_cli(["mark-delete", "--color", "All", "--force"])
    assert code == 0 and _markers(fake_world) == {}


def test_delete_by_custom_data(run_cli: Run, fake_world: FakeWorld) -> None:
    _markers(fake_world)[7] = {
        "color": "Pink",
        "name": "d",
        "note": "",
        "customData": "take-2",
        "duration": 1,
    }
    code, out, _ = run_cli(["mark-delete", "--custom-data", "take-2", "--force"])
    assert code == 0 and '"markers:take-2",1,0' in out
    assert 7 not in _markers(fake_world) and 100 in _markers(fake_world)


def test_delete_with_nothing_to_delete_is_a_no_op(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["mark-delete", "--frame", "3", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and "changes: []" in out
    assert fake_world.write_calls() == []
    assert not (tmp_path / "axi-home" / "snapshots").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["mark-delete"],
        ["mark-delete", "--frame", "1", "--color", "Red"],
        ["mark-delete", "--color", "Red", "--custom-data", "x"],
        ["mark-delete", "--color", "Teal"],
    ],
)
def test_delete_needs_exactly_one_selector(
    run_cli: Run, fake_world: FakeWorld, argv: list[str]
) -> None:
    code, _, _ = run_cli(argv)
    assert code == 2
    assert fake_world.calls == []


def test_force_cmd_quotes_each_word() -> None:
    inv = Invocation(
        opts=GlobalOpts(),
        args={},
        positionals={},
        connect=lambda: pytest.fail("no connect"),
    )
    assert force_cmd(inv, "a b", "c") == "davinci-axi 'a b' c --force"
