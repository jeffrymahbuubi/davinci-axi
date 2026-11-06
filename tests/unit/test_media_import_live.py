"""Live finding about MediaPool.ImportMedia (Resolve 21.1.1).

`ImportMedia([{"FilePath": f}])` returns None live: the dict form is for image sequences and
needs StartIndex and EndIndex. `ImportMedia([f])` with plain path strings imports the clip.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_writes_core
from davinci_axi.fake import ext_media_edit as ext


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext.extend()
    monkeypatch.setitem(registry.COMMANDS, "media", "davinci_axi.commands.media:SPEC")
    yield


def test_fake_import_media_ignores_file_path_dicts_like_resolve(
    fake_world: FakeWorld, tmp_path: Path
) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    a = tmp_path / "a.mov"
    a.write_text("x", encoding="utf-8")
    pool = project.media_pool
    assert pool.ImportMedia([{"FilePath": str(a)}]) == []
    seq = pool.ImportMedia([{"FilePath": str(a), "StartIndex": 1, "EndIndex": 2}])
    assert [c.name for c in seq] == ["a"]
    assert [c.name for c in pool.ImportMedia([str(a)])] == ["a"]
