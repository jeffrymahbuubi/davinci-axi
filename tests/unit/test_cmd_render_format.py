from __future__ import annotations

from collections.abc import Callable

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_render

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register_render(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_render.extend()
    monkeypatch.setitem(registry.COMMANDS, "render", "davinci_axi.commands.render:SPEC")


def _current(world: FakeWorld) -> tuple[str, str]:
    project = world.resolve.pm.current_project
    assert project is not None
    now = project.GetCurrentRenderFormatAndCodec()
    return str(now["format"]), str(now["codec"])


def test_same_format_and_codec_says_already_selected(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fmt, codec = _current(fake_world)
    code, out, _ = run_cli(["render", "format", "set", fmt, codec, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false"
    assert keys["note"] == "already selected"
    assert not fake_world.calls_to("Project.SetCurrentRenderFormatAndCodec")


def test_different_codec_has_no_note(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    fmt, codec = _current(fake_world)
    other = next(str(c) for c in project.GetRenderCodecs(fmt).values() if str(c) != codec)
    code, out, _ = run_cli(["render", "format", "set", fmt, other, "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and "note" not in keys
    assert _current(fake_world) == (fmt, other)
