"""`item take add` on an item without a take selector (live-verify B2): Resolve turns the item's
own clip into take 1, so the first add makes two takes, and the new clip is verified, not just the
count."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeProject, FakeWorld, ext_edit_extra

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_edit_extra.extend()
    monkeypatch.setitem(registry.COMMANDS, "item", "davinci_axi.commands.item_extra:SPEC")


def test_first_take_add_counts_the_original_as_take_1(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    args = ["item", "take", "add", "V1:1", "A002_city", "--in", "30", "--out", "120", "--force"]
    code, out, _ = run_cli(args)
    keys = parse_toon_keys(out)
    assert code == 0, out
    assert keys["applied"] == "true" and keys["original_became_take_1"] == "true"
    assert "takes,0,2" in out
    assert "A002_city 30..120" in out
    assert fake_world.write_calls()[-1] == "TimelineItem.AddTake"
    code, out, _ = run_cli(["item", "take", "list", "V1:1"])
    assert "1,true,A001_beach,0,80" in out and "2,false,A002_city,30,120" in out


def test_take_add_verifies_the_new_clip(run_cli: Run, fake_world: FakeWorld) -> None:
    project: FakeProject | None = fake_world.resolve.pm.current_project
    assert project is not None
    other = project.media_pool.root.subfolders[0].clips[2]  # A003_dusk, not the clip we add
    fake_world.inject(
        "TimelineItem.GetTakeByIndex", {"startFrame": 30, "endFrame": 120, "mediaPoolItem": other}
    )
    args = ["item", "take", "add", "V1:1", "A002_city", "--in", "30", "--out", "120", "--force"]
    code, out, _ = run_cli(args)
    assert code == 1 and "VERIFY_FAILED" in out and "snapshot" in out
