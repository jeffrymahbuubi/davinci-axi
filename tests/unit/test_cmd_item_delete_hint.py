"""F49 d: `item delete` names the `title: ` compound clip to remove from the pool."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from davinci_axi import registry
from davinci_axi.commands import item_batch, item_edit
from davinci_axi.fake import FakeProject, FakeTimeline, FakeWorld
from davinci_axi.fake.timeline import FakeTimelineItem

Run = Callable[[list[str]], tuple[int, str, str]]
HINT = "`davinci-axi media delete 'title: Probe' --force`"


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "item", "davinci_axi.commands.item_extra:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert isinstance(project, FakeProject)
    return project


def _timeline(world: FakeWorld) -> FakeTimeline:
    return _project(world).timelines[0]


def _title(world: FakeWorld, name: str = "title: Probe", clip_type: str = "Compound") -> None:
    """Append a V2 item (V2:1 is the world's own) backed by a pool clip of this name and type."""
    folder: Any = _project(world).media_pool.root
    clip = folder.add_clip(name, "", clip_type=clip_type)
    track = _timeline(world).tracks["video"][1].items
    _timeline(world).add_item("video", 2, name, 86400 + 100 * len(track), 48, media=clip)


def test_single_delete_names_the_compound_in_dry_run_and_applied(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _title(fake_world)
    code, out, _ = run_cli(["item", "delete", "V2:2"])
    assert code == 0 and "applied: false" in out and "media delete" not in out  # plan.next is
    code, out, _ = run_cli(["item", "delete", "V2:2", "--force"])  # printed only once applied
    assert code == 0 and HINT in out and "applied: true" in out


def test_list_form_gives_one_hint_per_distinct_compound(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _title(fake_world)
    _title(fake_world, "title: Other")
    code, out, _ = run_cli(["item", "delete", "V2:2,V2:3,V2:2,V1:1", "--force"])
    assert code == 0
    assert out.count("media delete") == 2 and HINT in out
    assert "`davinci-axi media delete 'title: Other' --force`" in out


def test_plain_clip_and_non_title_compounds_get_no_hint(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["item", "delete", "V1:1"])
    assert code == 0 and "media delete" not in out
    _title(fake_world, "nested edit")
    _title(fake_world, "title: Probe", clip_type="Video + Audio")
    for ref in ("V2:2", "V2:3"):
        code, out, _ = run_cli(["item", "delete", ref])
        assert code == 0 and "media delete" not in out


@pytest.mark.parametrize("ids", ["V2:2", "V2:2,V1:1"])
def test_a_missing_pool_item_gives_no_hint_and_no_error(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch, ids: str
) -> None:
    _title(fake_world)
    monkeypatch.setattr(FakeTimelineItem, "GetMediaPoolItem", lambda self: None)
    code, out, err = run_cli(["item", "delete", ids, "--force"])
    assert code == 0 and err == "" and "applied: true" in out and "media delete" not in out


def test_help_lists_the_methods_and_the_note(run_cli: Run) -> None:
    code, out, _ = run_cli(["item", "delete", "--help"])
    assert code == 0 and "leaves its compound clip" in out
    for spec in (item_batch.DELETE_SPEC, item_edit.DELETE_SPEC):
        assert "MediaPoolItem.GetClipProperty" in spec.methods and spec.notes
