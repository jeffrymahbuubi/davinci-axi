"""`media property set --restore` and the `--save-restore` round trip (fake Resolve)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeProject, FakeWorld, ext_media_edit, ext_storage, ext_writes_core
from davinci_axi.fake.media import FakeMediaPoolItem

Run = Callable[[list[str]], tuple[int, str, str]]

EXPORT = "ProjectManager.ExportProject"
SET = "MediaPoolItem.SetClipProperty"
SHARP, NOISE = "SuperScale Sharpness", "SuperScale Noise Reduction"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext_media_edit.extend()
    ext_storage.extend()
    monkeypatch.setitem(registry.COMMANDS, "media", "davinci_axi.commands.media_extra:SPEC")
    yield


def _keys(out: str) -> dict[str, str]:
    found = (re.match(r"^([A-Za-z_][\w.-]*): (.*)$", line) for line in out.splitlines())
    return {m.group(1): m.group(2) for m in found if m}


def _clip(world: FakeWorld, name: str) -> FakeMediaPoolItem:
    project: FakeProject | None = world.resolve.pm.current_project
    assert project is not None
    root = project.media_pool.root
    for folder in [root, *root.subfolders]:
        for clip in folder.clips:
            if clip.name == name:
                return clip
    raise LookupError(name)


def _snapshots(tmp_path: Path) -> list[Path]:
    folder = tmp_path / "axi-home" / "snapshots"
    return list(folder.iterdir()) if folder.exists() else []


def _enhance(world: FakeWorld, names: list[str], floats: bool = True) -> None:
    for name in names:
        props = _clip(world, name).properties
        props["Super Scale"] = 2
        if floats:
            props[SHARP] = "0.5"
            props[NOISE] = "0.5"


def _save(run_cli: Run, tmp_path: Path, clips: str = "A001_beach,A002_city") -> Path:
    path = tmp_path / "ss.json"
    args = ["media", "property", "set", clips, "Super Scale=1", "--save-restore", str(path)]
    code, _, _ = run_cli([*args, "--force"])
    assert code == 0
    return path


def test_round_trip_resends_sharpness_and_noise_under_one_snapshot(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _enhance(fake_world, ["A001_beach", "A002_city"])
    path = _save(run_cli, tmp_path)
    for name in ("A001_beach", "A002_city"):
        assert _clip(fake_world, name).properties["Super Scale"] == 1
    before = len(fake_world.calls_to(SET))
    code, out, _ = run_cli(["media", "property", "set", "--restore", str(path)])
    assert code == 0 and _keys(out)["applied"] == "false" and _keys(out)["clips"] == "2"
    assert "A001_beach.Super Scale" in out and len(fake_world.calls_to(SET)) == before
    snapshots = len(_snapshots(tmp_path))
    code, out, _ = run_cli(["media", "property", "set", "--restore", str(path), "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    assert fake_world.calls_to(SET)[before:] == [("Super Scale", 2, 0.5, 0.5)] * 2
    assert len(_snapshots(tmp_path)) == snapshots + 1
    for name in ("A001_beach", "A002_city"):
        props = _clip(fake_world, name).properties
        assert props["Super Scale"] == 2 and props[SHARP] == "0.5" and props[NOISE] == "0.5"


def test_restore_when_values_are_already_live_changes_nothing(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _enhance(fake_world, ["A001_beach", "A002_city"])
    path = _save(run_cli, tmp_path)
    run_cli(["media", "property", "set", "--restore", str(path), "--force"])
    calls, snapshots = len(fake_world.calls_to(SET)), len(_snapshots(tmp_path))
    code, out, _ = run_cli(["media", "property", "set", "--restore", str(path), "--force"])
    assert code == 0 and _keys(out)["applied"] == "false" and _keys(out)["unchanged"] == "2"
    assert len(fake_world.calls_to(SET)) == calls and len(_snapshots(tmp_path)) == snapshots


def _write_file(tmp_path: Path, world: FakeWorld, properties: dict[str, object]) -> Path:
    clip = _clip(world, "A001_beach")
    body = {
        "kind": "davinci-axi media property restore",
        "version": 1,
        "saved": "2026-11-10T10:00:00",
        "project": "p",
        "command": "x",
        "keys": list(properties),
        "clips": [{"id": clip.GetUniqueId(), "name": clip.name, "properties": properties}],
    }
    path = tmp_path / "hand.json"
    path.write_text(json.dumps(body))
    return path


def test_plain_two_without_floats_restores_plain_and_warns(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _write_file(tmp_path, fake_world, {"Super Scale": 2})
    code, out, _ = run_cli(["media", "property", "set", "--restore", str(path)])
    warning = _keys(out)["warning"]
    assert code == 0 and "A001_beach.Super Scale,1,2" in out
    assert "2x-enhanced" in warning and "media property set A001_beach" in warning
    assert "SuperScale Sharpness=0.5" in warning
    code, out, _ = run_cli(["media", "property", "set", "--restore", str(path), "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    assert fake_world.calls_to(SET) == [("Super Scale", 2)]


def test_other_keys_are_restored_through_wire_value(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _clip(fake_world, "A001_beach").properties["Alpha mode"] = "Premultiplied"
    path = _write_file(tmp_path, fake_world, {"Alpha mode": "Straight"})
    code, _, _ = run_cli(["media", "property", "set", "--restore", str(path), "--force"])
    assert code == 0 and fake_world.calls_to(SET) == [("Alpha mode", "Straight")]
    assert _clip(fake_world, "A001_beach").properties["Alpha mode"] == "Straight"


@pytest.mark.parametrize(
    "extra",
    [
        ["A001_beach", "Gain=1"],
        ["--used"],
        ["--save-restore", "x.json"],
    ],
)
def test_restore_refuses_clips_used_and_save_restore(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, extra: list[str]
) -> None:
    path = _write_file(tmp_path, fake_world, {"Super Scale": 1})
    code, out, _ = run_cli(["media", "property", "set", "--restore", str(path), *extra])
    assert code == 2 and "VALIDATION_ERROR" in out and "--restore ~/ss.json --force" in out
    assert fake_world.calls_to(SET) == [] and _snapshots(tmp_path) == []


def test_bad_files_are_validation_errors_with_the_save_command(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    good = _write_file(tmp_path, fake_world, {"Super Scale": 1})
    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"kind": "other", "clips": []}))
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    noclip = tmp_path / "noclip.json"
    noclip.write_text(json.dumps({**json.loads(good.read_text()), "clips": [{"name": "x"}]}))
    for target in (tmp_path / "missing.json", foreign, broken, noclip):
        code, out, _ = run_cli(["media", "property", "set", "--restore", str(target)])
        assert code == 2 and "VALIDATION_ERROR" in out and "--save-restore" in out, target


def test_a_clip_no_longer_in_the_pool_is_named(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _write_file(tmp_path, fake_world, {"Super Scale": 1})
    data = json.loads(path.read_text())
    data["clips"][0]["id"] = "gone-id"
    path.write_text(json.dumps(data))
    code, out, _ = run_cli(["media", "property", "set", "--restore", str(path)])
    assert code == 2 and "clip A001_beach (gone-id) is no longer in the media pool" in out
    assert "snapshot that --force took" in out
