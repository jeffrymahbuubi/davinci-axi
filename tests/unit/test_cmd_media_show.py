"""`media show` hides empty properties and metadata unless `--all` is given (F3)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_writes_core
from davinci_axi.fake import ext_media_edit as ext
from davinci_axi.fake.media import FakeMediaPoolItem

Run = Callable[[list[str]], tuple[int, str, str]]


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext.extend()
    monkeypatch.setitem(registry.COMMANDS, "media", "davinci_axi.commands.media:SPEC")
    yield


def _keys(out: str) -> dict[str, str]:
    found = (re.match(r"^([A-Za-z_][\w.-]*): (.*)$", line) for line in out.splitlines())
    return {m.group(1): m.group(2) for m in found if m}


def _has(out: str, key: str) -> bool:
    """Whether an indented `key:` line is printed (TOON quotes only keys with spaces)."""
    return re.search(rf'^\s+"?{re.escape(key)}"?: ', out, re.MULTILINE) is not None


def _clip(world: FakeWorld, name: str) -> FakeMediaPoolItem:
    project = world.resolve.pm.current_project
    assert project is not None
    root = project.media_pool.root
    for folder in [root, *root.subfolders]:
        for clip in folder.clips:
            if clip.name == name:
                return clip
    raise LookupError(name)


def test_empty_properties_and_metadata_are_hidden_by_default(run_cli: Run) -> None:
    code, out, err = run_cli(["media", "show", "A001_beach"])
    keys = _keys(out)
    assert (code, err) == (0, "")
    assert not _has(out, "Scene") and not _has(out, "Comments")
    assert keys["properties_hidden"] == "4" and keys["metadata_hidden"] == "1"
    assert "media show A001_beach --all" in out


def test_all_shows_the_empty_entries(run_cli: Run) -> None:
    code, out, _ = run_cli(["media", "show", "A001_beach", "--all"])
    assert code == 0
    assert _has(out, "Scene") and _has(out, "Comments")
    assert "_hidden" not in out and "--all" not in out


def test_useful_properties_survive_a_wall_of_empties(run_cli: Run, fake_world: FakeWorld) -> None:
    _clip(fake_world, "A001_beach").properties.update(
        {f"Empty property {i}": "" for i in range(70)}
    )
    code, out, _ = run_cli(["media", "show", "A001_beach"])
    assert code == 0 and "properties_note" not in out
    for name in ("Resolution", "Video Codec", "Frames", "FPS", "Date Created"):
        assert _has(out, name)
    code, out, _ = run_cli(["media", "show", "A001_beach", "--all"])
    assert code == 0 and "properties_note" in out
    code, out, _ = run_cli(["media", "show", "A001_beach", "--all", "--full"])
    assert code == 0 and "properties_note" not in out and "metadata_note" not in out


def test_full_and_all_compose_in_the_hint(run_cli: Run, fake_world: FakeWorld) -> None:
    _clip(fake_world, "A001_beach").properties.update(
        {f"Empty property {i}": "" for i in range(70)}
    )
    code, out, _ = run_cli(["media", "show", "A001_beach", "--all"])
    note = _keys(out)["properties_note"]
    assert code == 0 and "--full" in note and "--all" in note


def test_json_output_keeps_the_same_filtering(run_cli: Run) -> None:
    code, out, _ = run_cli(["media", "show", "A001_beach", "--json"])
    assert code == 0
    data = json.loads(out)
    assert data["properties_hidden"] == 4 and data["metadata_hidden"] == 1
    assert "Scene" not in data["properties"] and "Comments" not in data["metadata"]


def test_none_string_properties_are_hidden(run_cli: Run, fake_world: FakeWorld) -> None:
    _clip(fake_world, "A001_beach").properties.update({"Alpha mode": "None", "Proxy": "none"})
    code, out, _ = run_cli(["media", "show", "A001_beach"])
    assert code == 0
    assert not _has(out, "Alpha mode") and not _has(out, "Proxy")
    assert _keys(out)["properties_hidden"] == "6"
    code, out, _ = run_cli(["media", "show", "A001_beach", "--all"])
    assert code == 0 and _has(out, "Alpha mode")


def test_none_string_metadata_is_kept(run_cli: Run, fake_world: FakeWorld) -> None:
    _clip(fake_world, "A001_beach").metadata["Comments"] = "None"
    code, out, _ = run_cli(["media", "show", "A001_beach"])
    assert code == 0
    assert _has(out, "Comments") and "Comments: None" in out
    assert "metadata_hidden" not in out


def test_nonempty_words_argument() -> None:
    from davinci_axi.commands.media_show import NONE_WORDS, nonempty

    assert nonempty({"a": "None", "b": "x"}, words=NONE_WORDS) == ({"b": "x"}, 1)
    assert nonempty({"a": "None", "b": "x", "c": None, "d": ""}) == ({"a": "None", "b": "x"}, 2)
