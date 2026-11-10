"""F22: `fusion tool connect` proves its link, `--via-page` edits the comp the Fusion page shows."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.fake import (
    FakeWorld,
    ext_fusion,
    ext_fusion_edit,
    ext_fusion_page,
    ext_writes_core,
)
from davinci_axi.fake.ext_fusion import FakeComp
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem

Run = Callable[[list[str]], tuple[int, str, str]]

ITEM = ["--item", "V1:2"]
ITEM_START_TC = "01:00:03:08"  # clip-2 starts 80 frames (3s 8f at 24 fps) into the timeline
PAGE_CALLS = (
    "Timeline.SetCurrentTimecode",
    "Resolve.OpenPage",
    "Fusion.GetCurrentComp",
    "FusionTool.SetAttrs",
)


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext_fusion.extend()
    ext_fusion_edit.extend()
    ext_fusion_page.extend()
    ext_fusion_page.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "fusion", "davinci_axi.commands.fusion_comp:SPEC")
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    yield


def _timeline(world: FakeWorld) -> FakeTimeline:
    project = world.resolve.pm.current_project
    assert project is not None and project.current_timeline is not None
    return project.current_timeline


def _clip(world: FakeWorld, number: int) -> FakeTimelineItem:
    return _timeline(world).tracks["video"][0].items[number - 1]


def _comp(world: FakeWorld) -> FakeComp:
    return ext_fusion.comp_of(_clip(world, 2))


def _json(run_cli: Run, argv: list[str]) -> tuple[int, dict[str, Any]]:
    code, out, _ = run_cli([*argv, "--json"])
    return code, json.loads(out)


def _add_blur(world: FakeWorld) -> None:
    comp = _comp(world)
    assert comp.AddTool("Blur", 1.0, 1.0) is not None
    world.calls.clear()
    comp.undo_stack.clear()


def _names(world: FakeWorld, wanted: tuple[str, ...]) -> list[str]:
    return [n for cls, method, _ in world.calls if (n := f"{cls}.{method}") in wanted]


def _pages(world: FakeWorld) -> list[object]:
    return [
        args[0] for cls, method, args in world.calls if (cls, method) == ("Resolve", "OpenPage")
    ]


def test_connect_reports_the_link_and_reads_it_back(run_cli: Run, fake_world: FakeWorld) -> None:
    _add_blur(fake_world)
    argv = ["fusion", "tool", "connect", "MediaIn1", "Blur1.Input", *ITEM]
    code, data = _json(run_cli, argv)
    assert code == 0 and data["applied"] is False
    assert data["link"] == "MediaIn1.Output -> Blur1.Input"
    code, data = _json(run_cli, [*argv, "--force"])
    assert code == 0 and data["applied"] is True
    assert data["link"] == "MediaIn1.Output -> Blur1.Input"
    blur = _comp(fake_world).tools[-1]
    assert blur.inputs[0].connected is _comp(fake_world).tools[0].outputs[0]


def test_connect_fails_when_fusion_says_true_but_did_not_link(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _add_blur(fake_world)
    fake_world.inject("FusionTool.ConnectInput", True)
    code, out, _ = run_cli(
        ["fusion", "tool", "connect", "MediaIn1", "Blur1.Input", *ITEM, "--force", "--json"]
    )
    assert code == 1
    error = json.loads(out)
    assert error["code"] == "VERIFY_FAILED" and "Blur1.Input" in error["error"]
    assert error["detail"]["comp_snapshot"]
    assert any("comp_snapshot" in h or ".comp" in h for h in error["help"])


def test_disconnect_has_no_link_key(run_cli: Run, fake_world: FakeWorld) -> None:
    code, data = _json(run_cli, ["fusion", "tool", "disconnect", "MediaOut1.Input", *ITEM])
    assert code == 0 and "link" not in data


def test_via_page_goes_through_the_page_comp_and_restores_the_page(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, data = _json(
        run_cli, ["fusion", "tool", "bypass", "MediaOut1", *ITEM, "--via-page", "--force"]
    )
    assert code == 0 and data["applied"] is True
    assert data["via_page"] is True and data["page_restored"] is True
    assert data["playhead_moved"] is True
    assert _names(fake_world, PAGE_CALLS)[:3] == list(PAGE_CALLS[:3])
    assert fake_world.calls_to("Timeline.SetCurrentTimecode") == [(ITEM_START_TC,)]
    assert _pages(fake_world) == ["fusion", "edit"]
    page_comp = _comp(fake_world)
    out = next(t for t in page_comp.tools if t.name == "MediaOut1")
    assert out.GetAttrs()["TOOLB_PassThrough"] is True
    # the SetAttrs call falls between opening the page and leaving it
    kinds = [(c, m) for c, m, _ in fake_world.calls]
    assert kinds.index(("Fusion", "GetCurrentComp")) < kinds.index(("FusionTool", "SetAttrs"))
    assert kinds.index(("FusionTool", "SetAttrs")) < len(kinds) - 1 - kinds[::-1].index(
        ("Resolve", "OpenPage")
    )
    assert fake_world.resolve.page == "edit"


def test_via_page_dry_run_shows_the_route_and_switches_nothing(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, data = _json(run_cli, ["fusion", "tool", "bypass", "MediaOut1", *ITEM, "--via-page"])
    assert code == 0 and data["applied"] is False and data["via_page"] is True
    assert "page_restored" not in data
    assert _pages(fake_world) == []
    assert fake_world.calls_to("Timeline.SetCurrentTimecode") == []
    assert fake_world.calls_to("Fusion.GetCurrentComp") == []
    assert fake_world.resolve.page == "edit"


def test_force_line_keeps_via_page(run_cli: Run, fake_world: FakeWorld) -> None:
    _, out, _ = run_cli(["fusion", "tool", "bypass", "MediaOut1", *ITEM, "--via-page"])
    assert "--via-page" in out and "--force" in out


def test_without_the_flag_nothing_moves(run_cli: Run, fake_world: FakeWorld) -> None:
    code, data = _json(run_cli, ["fusion", "tool", "bypass", "MediaOut1", *ITEM, "--force"])
    assert code == 0 and "via_page" not in data and "page_restored" not in data
    assert _pages(fake_world) == []
    assert fake_world.calls_to("Fusion.GetCurrentComp") == []


def test_already_on_the_fusion_page_makes_no_switch(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.resolve.page = "fusion"
    _timeline(fake_world).current_tc = ITEM_START_TC
    code, data = _json(
        run_cli, ["fusion", "tool", "bypass", "MediaOut1", *ITEM, "--via-page", "--force"]
    )
    assert code == 0 and data["via_page"] is True
    assert "page_restored" not in data and "playhead_moved" not in data
    assert _pages(fake_world) == []
    assert fake_world.resolve.page == "fusion"


def test_no_page_comp_fails_and_the_page_is_restored(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.inject("Fusion.GetCurrentComp", None)
    code, out, _ = run_cli(
        ["fusion", "tool", "bypass", "MediaOut1", *ITEM, "--via-page", "--force", "--json"]
    )
    assert code == 1 and "API_FAILED" in out
    assert _pages(fake_world) == ["fusion", "edit"]
    assert fake_world.resolve.page == "edit"
    assert fake_world.calls_to("FusionTool.SetAttrs") == []


def test_a_different_comp_on_the_page_is_refused(run_cli: Run, fake_world: FakeWorld) -> None:
    _clip(fake_world, 1).comps = ["Other"]
    fake_world.inject("Fusion.GetCurrentComp", ext_fusion.comp_of(_clip(fake_world, 1)))
    code, out, _ = run_cli(
        ["fusion", "tool", "bypass", "MediaOut1", *ITEM, "--via-page", "--force"]
    )
    assert code == 1 and "API_FAILED" in out and "not Composition 1" in out
    assert fake_world.resolve.page == "edit"


def test_an_error_after_the_switch_still_restores_the_page(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["fusion", "tool", "bypass", "Nope9", *ITEM, "--via-page", "--force"])
    assert code == 1 and "NOT_FOUND" in out
    assert _pages(fake_world) == ["fusion", "edit"]
    assert fake_world.resolve.page == "edit"


def test_drop_frame_timeline_is_refused_before_any_change(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _timeline(fake_world).start_tc = "01:00:00;00"
    code, out, _ = run_cli(
        ["fusion", "tool", "bypass", "MediaOut1", *ITEM, "--via-page", "--force"]
    )
    assert code == 2 and "drop-frame" in out
    assert _pages(fake_world) == []
    assert fake_world.calls_to("Timeline.SetCurrentTimecode") == []


def test_active_stays_read_only_with_via_page(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["fusion", "tool", "bypass", "MediaOut1", "--active", "--via-page"])
    assert code == 2 and "read-only" in out


def test_help_carries_the_render_ladder_and_the_flag(run_cli: Run) -> None:
    code, out, _ = run_cli(["fusion", "--help"])
    assert code == 0 and "fusion comp export" in out and "--via-page" in out
    code, out, _ = run_cli(["fusion", "tool", "bypass", "--help"])
    assert code == 0 and "via-page" in out


def test_fake_page_comp_follows_the_playhead(fake_world: FakeWorld) -> None:
    fusion = ext_fusion.fusion_of(fake_world)
    assert fusion.GetCurrentComp() is None  # edit page
    fake_world.resolve.page = "fusion"
    timeline = _timeline(fake_world)
    assert fusion.GetCurrentComp() is None  # clip-1 has no comp
    timeline.current_tc = ITEM_START_TC
    assert fusion.GetCurrentComp() is _comp(fake_world)
    timeline.current_tc = "01:10:00:00"
    assert fusion.GetCurrentComp() is None
    fusion.current = _comp(fake_world)
    assert fusion.GetCurrentComp() is _comp(fake_world)
