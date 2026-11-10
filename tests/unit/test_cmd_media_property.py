"""`media property set` (moved from test_cmd_media_extra with the command)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeProject, FakeWorld, ext_media_edit, ext_storage, ext_writes_core
from davinci_axi.fake.media import FakeFolder, FakeMediaPoolItem

Run = Callable[[list[str]], tuple[int, str, str]]

SAVE, EXPORT = "ProjectManager.SaveProject", "ProjectManager.ExportProject"


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


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _root(world: FakeWorld) -> FakeFolder:
    return _project(world).media_pool.root


def _clip(world: FakeWorld, name: str) -> FakeMediaPoolItem:
    for folder in [_root(world), *_root(world).subfolders]:
        for clip in folder.clips:
            if clip.name == name:
                return clip
    raise LookupError(name)


def _untouched(world: FakeWorld, tmp_path: Path) -> None:
    snapshots = tmp_path / "axi-home" / "snapshots"
    assert world.write_calls() == [] and not snapshots.exists()


def test_property_set_reads_back_each_key(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    args = ["media", "property", "set", "A001_beach", "Alpha mode=Straight,Comments=ok"]
    code, out, _ = run_cli(args)
    assert code == 0 and "Alpha mode" in out and _keys(out)["applied"] == "false"
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    assert fake_world.write_calls() == [SAVE, EXPORT] + ["MediaPoolItem.SetClipProperty"] * 2
    assert _clip(fake_world, "A001_beach").properties["Alpha mode"] == "Straight"


def test_property_set_treats_equal_numbers_as_equal(run_cli: Run, fake_world: FakeWorld) -> None:
    clip = _clip(fake_world, "A001_beach")
    clip.properties["Gain"] = "1"
    original = type(clip).SetClipProperty

    def normalising(self: FakeMediaPoolItem, name: str, value: str) -> bool:
        ok = original(self, name, value)
        self.properties[name] = f"{float(value)}"
        return ok

    type(clip).SetClipProperty = normalising  # type: ignore[method-assign]
    try:
        code, out, _ = run_cli(["media", "property", "set", "A001_beach", "Gain=2", "--force"])
    finally:
        type(clip).SetClipProperty = original  # type: ignore[method-assign]
    assert code == 0 and _keys(out)["applied"] == "true"


def test_property_set_failure_names_the_key_and_what_was_applied(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    clip = _clip(fake_world, "A001_beach")
    original = type(clip).SetClipProperty

    def refuse_second(self: FakeMediaPoolItem, name: str, value: str) -> bool:
        return False if name == "Two" else original(self, name, value)

    type(clip).SetClipProperty = refuse_second  # type: ignore[method-assign]
    try:
        code, out, _ = run_cli(["media", "property", "set", "A001_beach", "One=1,Two=2", "--force"])
    finally:
        type(clip).SetClipProperty = original  # type: ignore[method-assign]
    assert code == 1 and "could not set Two (already applied: One)" in out


def _setcalls(world: FakeWorld) -> list[tuple[object, ...]]:
    return world.calls_to("MediaPoolItem.SetClipProperty")


def test_super_scale_word_is_sent_as_an_integer(run_cli: Run, fake_world: FakeWorld) -> None:
    args = ["media", "property", "set", "A001_beach", "Super Scale=2x"]
    code, out, _ = run_cli(args)
    assert code == 0 and "Super Scale,1,2" in out and _setcalls(fake_world) == []
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    assert _setcalls(fake_world) == [("Super Scale", 2)]
    assert _clip(fake_world, "A001_beach").properties["Super Scale"] == 2


def test_super_scale_sent_as_text_fails_with_the_accepted_values(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("davinci_axi.commands.media_property.wire_value", lambda k, t, live: t)
    code, out, _ = run_cli(["media", "property", "set", "A001_beach", "Super Scale=2x", "--force"])
    assert code == 1 and "API_FAILED" in out
    assert "Super Scale accepts 1, 2, 3, 4" in out.split("help", 1)[1].splitlines()[1]


def test_super_scale_enhanced_passes_sharpness_and_noise(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    value = "Super Scale=2x-enhanced,SuperScale Sharpness=0.7"
    code, _, _ = run_cli(["media", "property", "set", "A001_beach", value, "--force"])
    assert code == 0 and len(_setcalls(fake_world)) == 1
    assert _setcalls(fake_world) == [("Super Scale", 2, 0.7, 0.5)]
    assert _clip(fake_world, "A001_beach").properties["SuperScale Sharpness"] == "0.7"


def test_super_scale_rejects_unknown_word_before_any_call(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["media", "property", "set", "A001_beach", "Super Scale=5x", "--force"])
    assert code == 2 and "Super Scale accepts 1, 2, 3, 4" in out
    _untouched(fake_world, tmp_path)


def test_file_fact_dry_run_warns_it_is_read_only(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["media", "property", "set", "A001_beach", "Resolution=1x1"])
    assert code == 0 and "refuses writes" in _keys(out)["warning"]
    assert _setcalls(fake_world) == []


def test_numeric_live_property_is_sent_as_an_integer(run_cli: Run, fake_world: FakeWorld) -> None:
    _clip(fake_world, "A001_beach").properties["Gain"] = 1
    code, _, _ = run_cli(["media", "property", "set", "A001_beach", "Gain=2", "--force"])
    assert code == 0 and _setcalls(fake_world) == [("Gain", 2)]


def test_help_states_the_super_scale_render_cost(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["media", "property", "set", "--help"])
    assert code == 0 and "about 40x" in out and "2x-enhanced,SuperScale Sharpness=0.5" in out


# ---- many clips: a list or --used ---------------------------------------------------------


def _put(world: FakeWorld, timeline: str, clips: list[str]) -> None:
    """Replace V1 of `timeline` with items on the named pool clips (repeats allowed)."""
    target = next(t for t in _project(world).timelines if t.name == timeline)
    if not target.tracks["video"]:
        target.add_track("video")
    target.tracks["video"][0].items.clear()
    for number, name in enumerate(clips):
        target.add_item("video", 1, f"i{number}", 1000 + 10 * number, 10, media=_clip(world, name))


def test_used_sets_each_distinct_clip_once_under_one_snapshot(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _put(fake_world, "Edit v1", ["A001_beach", "A002_city", "A001_beach"])
    for name in ("A001_beach", "A002_city"):
        _clip(fake_world, name).properties["Super Scale"] = 2
    args = ["media", "property", "set", "--used", "Super Scale=1"]
    code, out, _ = run_cli(args)
    keys = _keys(out)
    assert code == 0 and keys["applied"] == "false" and keys["clips"] == "2"
    assert "2 clips" in keys["target"] and "project-wide" in keys["warning"]
    assert "media property set --used 'Super Scale=1' --force" in out
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    assert fake_world.write_calls().count("MediaPoolItem.SetClipProperty") == 2
    assert [c[1] for c in _setcalls(fake_world)] == [1, 1]
    assert fake_world.write_calls().count(EXPORT) == 1
    for name in ("A001_beach", "A002_city"):
        assert _clip(fake_world, name).properties["Super Scale"] == 1
    assert len(list((tmp_path / "axi-home" / "snapshots").iterdir())) == 1


def test_clip_list_changes_every_clip_and_counts_the_unchanged(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _clip(fake_world, "A002_city").properties["Gain"] = "2"
    args = ["media", "property", "set", "A001_beach,A002_city,A003_dusk", "Gain=2"]
    code, out, _ = run_cli(args)
    assert code == 0 and _keys(out)["unchanged"] == "1" and _keys(out)["clips"] == "3"
    assert "A001_beach.Gain" in out and "A003_dusk.Gain" in out and "A002_city.Gain" not in out
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and len(_setcalls(fake_world)) == 2
    assert _clip(fake_world, "A003_dusk").properties["Gain"] == "2"


def test_list_with_super_scale_makes_one_change_per_clip(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["media", "property", "set", "A001_beach,A002_city", "Super Scale=2"])
    assert code == 0 and _keys(out)["clips"] == "2" and out.count(".Super Scale,") == 2
    code, _, _ = run_cli(
        ["media", "property", "set", "A001_beach,A002_city", "Super Scale=2", "--force"]
    )
    assert code == 0 and _setcalls(fake_world) == [("Super Scale", 2)] * 2


def test_second_clip_failing_names_it_and_the_clips_done(
    run_cli: Run, fake_world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = FakeMediaPoolItem.SetClipProperty

    def refuse_city(self: FakeMediaPoolItem, name: str, value: object, *extra: float) -> bool:
        return False if self.name == "A002_city" else original(self, name, value, *extra)  # type: ignore[arg-type]

    monkeypatch.setattr(FakeMediaPoolItem, "SetClipProperty", refuse_city)
    args = ["media", "property", "set", "A001_beach,A002_city", "Gain=3", "--force"]
    code, out, _ = run_cli(args)
    assert code == 1 and "API_FAILED" in out
    assert "A002_city" in out and "1 clip done" in out
    assert "snapshot restore" in out


def test_used_with_two_positionals_is_a_usage_error(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["media", "property", "set", "--used", "A001_beach", "Gain=3"])
    assert code == 2 and "with --used pass only the assignments" in out
    code, out, _ = run_cli(["media", "property", "set", "--used"])
    assert code == 2 and "with --used pass only the assignments" in out
    _untouched(fake_world, tmp_path)


def test_no_positional_or_one_positional_names_both_forms(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    for argv in (["media", "property", "set"], ["media", "property", "set", "A001_beach"]):
        code, out, _ = run_cli(argv)
        assert code == 2 and "pass <clips> and <assignments>" in out and "--used" in out
    _untouched(fake_world, tmp_path)


def test_used_on_a_timeline_without_video_items_is_refused(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(
        ["media", "property", "set", "--used", "Gain=1", "--timeline", "Edit v2"]
    )
    assert code == 2 and "no video items use a pool clip" in out and "timeline append" in out
    _untouched(fake_world, tmp_path)


def test_used_timeline_flag_picks_that_timelines_clips(run_cli: Run, fake_world: FakeWorld) -> None:
    _put(fake_world, "Edit v2", ["A003_dusk"])
    args = ["media", "property", "set", "--used", "Gain=5", "--timeline", "Edit v2"]
    code, out, _ = run_cli(args)
    assert code == 0 and _keys(out)["clips"] == "1" and "A003_dusk.Gain" in out
    assert "A001_beach" not in out.split("changes", 1)[1]
    assert "--used Gain=5 --timeline 'Edit v2' --force" in out
    code, _, _ = run_cli([*args, "--force"])
    assert code == 0 and _clip(fake_world, "A003_dusk").properties["Gain"] == "5"
    assert "Gain" not in _clip(fake_world, "A001_beach").properties


def test_single_clip_has_no_project_wide_warning(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["media", "property", "set", "A001_beach", "Gain=2"])
    assert code == 0 and "project-wide" not in out


def test_help_shows_both_positionals_optional_and_the_recipe(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["media", "property", "set", "--help"])
    assert code == 0 and "--used" in out and "--timeline" in out
    assert "[clips]" in out or "clips (optional" in out or "optional" in out
    assert "Draft recipe" in out and "project-wide" in out


# ---- F52: super_scale_before, --save-restore, wording, target cap ------------------------------


def _enhanced(world: FakeWorld, name: str, with_floats: bool = True) -> None:
    props = _clip(world, name).properties
    props["Super Scale"] = 2
    if with_floats:
        props["SuperScale Sharpness"] = "0.5"
        props["SuperScale Noise Reduction"] = "0.5"


def test_super_scale_before_counts_each_prior_state(run_cli: Run, fake_world: FakeWorld) -> None:
    _enhanced(fake_world, "A001_beach")
    _enhanced(fake_world, "A002_city")
    _enhanced(fake_world, "A003_dusk", with_floats=False)
    args = ["media", "property", "set", "A001_beach,A002_city,A003_dusk", "Super Scale=1"]
    code, out, _ = run_cli(args)
    assert code == 0 and "super_scale_before" in out
    assert "2 (sharpness 0.5, noise 0.5)" in out and "2 (sharpness/noise not reported)" in out
    code, out, _ = run_cli([*args, "--json"])
    before = json.loads(out)["super_scale_before"]
    assert before == {"2 (sharpness 0.5, noise 0.5)": 2, "2 (sharpness/noise not reported)": 1}


def test_super_scale_before_counts_plain_clips_and_is_absent_for_other_keys(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    args = ["media", "property", "set", "A001_beach,A002_city", "Super Scale=2", "--json"]
    code, out, _ = run_cli(args)
    assert code == 0 and json.loads(out)["super_scale_before"] == {"1": 2}
    code, out, _ = run_cli(["media", "property", "set", "A001_beach", "Alpha mode=Straight"])
    assert code == 0 and "super_scale_before" not in out


def test_target_over_five_clips_is_capped(run_cli: Run, fake_world: FakeWorld) -> None:
    names = [f"Z{n:03d}" for n in range(6)]
    for name in names:
        _root(fake_world).clips.append(FakeMediaPoolItem(fake_world, name, f"/m/{name}.mov"))
    code, out, _ = run_cli(["media", "property", "set", ",".join(names), "Gain=2"])
    assert code == 0
    assert "6 clips (Z000, Z001, Z002, Z003, Z004 and 1 more)" in _keys(out)["target"]


def test_save_restore_dry_run_writes_the_file_and_hints_the_restore(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _enhanced(fake_world, "A001_beach")
    save = tmp_path / "ss.json"
    args = ["media", "property", "set", "A001_beach,A002_city", "Super Scale=1"]
    code, out, _ = run_cli([*args, "--save-restore", str(save)])
    assert code == 0 and _keys(out)["applied"] == "false" and "restore_file" in out
    assert "restore_with" in out and "media property set --restore" in out
    assert "ss.json --force" in _keys(out)["restore_with"]
    _untouched(fake_world, tmp_path)
    code, out, _ = run_cli([*args, "--save-restore", str(save), "--overwrite", "--force"])
    assert code == 0 and "Run `davinci-axi media property set --restore" in out
    body = json.loads(save.read_text())
    assert body["kind"] == "davinci-axi media property restore" and body["version"] == 1
    assert body["project"] and body["command"].endswith("'Super Scale=1'")
    assert "--force" not in body["command"] and "--save-restore" not in body["command"]
    assert body["keys"] == ["Super Scale", "SuperScale Sharpness", "SuperScale Noise Reduction"]
    first, second = body["clips"]
    assert first["name"] == "A001_beach" and first["id"]
    assert first["properties"] == {
        "Super Scale": 2,
        "SuperScale Sharpness": "0.5",
        "SuperScale Noise Reduction": "0.5",
    }
    assert second["properties"] == {"Super Scale": 1}


def test_save_restore_refuses_an_existing_file_until_overwrite(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    save = tmp_path / "ss.json"
    save.write_text("old")
    args = ["media", "property", "set", "A001_beach", "Super Scale=2", "--save-restore", str(save)]
    code, out, _ = run_cli(args)
    assert code == 1 and "FILE_EXISTS" in out and "--overwrite" in out and save.read_text() == "old"
    code, _, _ = run_cli([*args, "--overwrite"])
    assert code == 0 and json.loads(save.read_text())["kind"].endswith("restore")


def test_save_restore_into_a_missing_folder_is_a_usage_error(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = str(tmp_path / "nope" / "ss.json")
    args = ["media", "property", "set", "A001_beach", "Gain=2", "--save-restore", target]
    code, out, _ = run_cli(args)
    assert code == 2 and "does not exist" in out and "Create the folder" in out
    code, out, _ = run_cli(["media", "property", "set", "A001_beach", "Gain=2", "--overwrite"])
    assert code == 2 and "--overwrite needs --save-restore" in out
    _untouched(fake_world, tmp_path)


def test_help_names_save_restore_restore_and_the_read_back_limit(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["media", "property", "set", "--help"])
    assert code == 0 and "--save-restore" in out and "--restore" in out and "--overwrite" in out
    assert "reads Super Scale back as an integer" in out and "super_scale_before" in out
    code, out, _ = run_cli(["media", "property", "set", "A001_beach", "Super Scale=7"])
    assert code == 2 and "Resolve reads back 2 for both" in out
