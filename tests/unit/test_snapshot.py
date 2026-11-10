from __future__ import annotations

import json
from pathlib import Path

import pytest

from davinci_axi.connect import connect
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeWorld
from davinci_axi.snapshot import NEVER_SAVED_HINT, snapshot_root, take_snapshot
from davinci_axi.spec import GlobalOpts


def test_snapshot_writes_directory_and_three_files(fake_world: FakeWorld, tmp_path: Path) -> None:
    snap = take_snapshot(connect(GlobalOpts()), reason="unit test")
    assert snap.dir.parent == snapshot_root()
    assert snap.dir.parent.parent == tmp_path / "axi-home"
    assert snap.id.endswith("-davinci-axi-test")
    assert sorted(p.name for p in snap.dir.iterdir()) == ["meta.json", "project.drp", "state.json"]
    meta = json.loads((snap.dir / "meta.json").read_text())
    assert meta["project"] == "davinci-axi-test"
    assert meta["reason"] == "unit test"
    assert meta["with_stills_luts"] is True
    assert snap.drp == snap.dir / "project.drp" and snap.state == snap.dir / "state.json"


def test_manual_flag_is_written_to_meta(fake_world: FakeWorld) -> None:
    session = connect(GlobalOpts())
    manual = take_snapshot(session, reason="x", manual=True)
    auto = take_snapshot(session, reason="render add")
    assert manual.manual is True and auto.manual is False
    assert json.loads((manual.dir / "meta.json").read_text())["manual"] is True
    assert json.loads((auto.dir / "meta.json").read_text())["manual"] is False


def test_state_json_nests_timelines_tracks_items_markers(fake_world: FakeWorld) -> None:
    snap = take_snapshot(connect(GlobalOpts()), reason="r")
    state = json.loads(snap.state.read_text())
    assert state["project"] == "davinci-axi-test"
    assert [t["name"] for t in state["timelines"]] == ["Edit v1", "Edit v2"]
    edit = state["timelines"][0]
    assert [(t["type"], t["index"]) for t in edit["tracks"]] == [
        ("video", 1),
        ("video", 2),
        ("audio", 1),
    ]
    items = [i for t in edit["tracks"] for i in t["items"]]
    assert len(items) == 6
    assert edit["markers"][0]["frame"] == 100
    first = edit["tracks"][0]["items"][0]
    assert first["markers"][0]["note"] == "check cut"
    assert first["media"]["path"].endswith("A001_beach.mov")


def test_only_export_project_is_a_write_call(fake_world: FakeWorld) -> None:
    take_snapshot(connect(GlobalOpts()), reason="r", with_stills_luts=False)
    assert fake_world.write_calls() == ["ProjectManager.ExportProject"]
    (args,) = fake_world.calls_to("ProjectManager.ExportProject")
    assert args[0] == "davinci-axi-test" and args[2] is False
    assert fake_world.page_changes() == []


def test_export_failure_keeps_state(fake_world: FakeWorld) -> None:
    fake_world.inject("ProjectManager.ExportProject", False)
    with pytest.raises(AxiError) as caught:
        take_snapshot(connect(GlobalOpts()), reason="r")
    err = caught.value
    assert err.code == "SNAPSHOT_FAILED"
    assert NEVER_SAVED_HINT in err.help
    state = Path(str(err.detail["state"]))
    assert state.name == "state.json" and state.exists()
    assert not (state.parent / "project.drp").exists()


def test_out_path_is_used_verbatim(fake_world: FakeWorld, tmp_path: Path) -> None:
    target = tmp_path / "custom" / "snap"
    snap = take_snapshot(connect(GlobalOpts()), reason="r", out=target)
    assert snap.dir == target
    assert (target / "project.drp").exists()
    assert not snapshot_root().exists()


def test_axi_home_env_is_respected(
    fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DAVINCI_AXI_HOME", str(tmp_path / "elsewhere"))
    snap = take_snapshot(connect(GlobalOpts()), reason="r")
    assert snap.dir.parent == tmp_path / "elsewhere" / "snapshots"


def test_two_snapshots_never_collide(fake_world: FakeWorld) -> None:
    session = connect(GlobalOpts())
    first = take_snapshot(session, reason="a")
    second = take_snapshot(session, reason="b")
    assert first.dir != second.dir


def test_no_project_is_a_coded_error(fake_world: FakeWorld) -> None:
    fake_world.resolve.pm.current_project = None
    with pytest.raises(AxiError) as caught:
        take_snapshot(connect(GlobalOpts()), reason="r")
    assert caught.value.code == "NO_PROJECT"
    assert fake_world.write_calls() == []
