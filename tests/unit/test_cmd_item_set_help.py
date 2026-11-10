"""`item set` documents Pan/Tilt/Zoom units, signs and safe ranges (F19)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_writes_core
from davinci_axi.fake import ext_item_edit as ext

Run = Callable[[list[str]], tuple[int, str, str]]


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext.extend()
    monkeypatch.setitem(registry.COMMANDS, "item", "davinci_axi.commands.item_edit:SPEC")
    yield


def test_item_set_help_states_units_signs_and_ranges(run_cli: Run) -> None:
    code, out, err = run_cli(["item", "set", "--help"])
    assert (code, err) == (0, "")
    for phrase in ("output pixels", "Tilt + moves it UP", "(z-1)*W/2", "cannot show it"):
        assert phrase in out
    assert "ZoomX=1.25,Tilt=-200" in out


def test_item_set_tilt_dry_run_still_works(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, err = run_cli(["item", "set", "V1:1", "Tilt=-200"])
    assert (code, err) == (0, "")
    assert "  Tilt,0,-200" in out
