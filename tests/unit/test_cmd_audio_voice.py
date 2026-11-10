"""`audio voice-isolation get` reads the item's track too (F31c) and the help notes (F27)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_audio

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_audio.extend()
    monkeypatch.setitem(registry.COMMANDS, "audio", "davinci_axi.commands.audio:SPEC")
    yield


def _track_state(world: FakeWorld, state: dict[str, object]) -> None:
    project = world.resolve.pm.current_project
    assert project is not None
    timeline = next(t for t in project.timelines if t.name == "Edit v1")
    timeline.tracks["audio"][0].__dict__["voice_isolation"] = state


def test_get_for_an_item_also_shows_the_track(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _track_state(fake_world, {"isEnabled": True, "amount": 30})
    code, out, err = run_cli(["audio", "voice-isolation", "get", "A1:1"])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["enabled"] == "false" and keys["track"] == "A1"
    assert keys["track_enabled"] == "true" and keys["track_amount"] == "30"
    assert "separate" in keys["note"]
    assert fake_world.write_calls() == []


def test_get_for_a_track_keeps_its_shape_and_adds_the_note(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["audio", "voice-isolation", "get", "--track", "1"])
    keys = parse_toon_keys(out)
    assert code == 0 and "track_enabled" not in keys and "track" not in keys
    assert "separate" in keys["note"]


def test_set_help_warns_about_ambience(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["audio", "voice-isolation", "set", "--help"])
    assert code == 0 and "ambience" in out and "separate settings" in out


def test_normalize_help_explains_the_common_gain(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["audio", "normalize", "--help"])
    assert code == 0 and "ONE common gain" in out
