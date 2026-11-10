"""`snapshot diff` and `timeline diff` on the fake (U2)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import snapshot as snapshot_cmd
from davinci_axi.commands import timeline_extra
from davinci_axi.commands.diff_cmds import SNAPSHOT_DIFF_SPEC, TIMELINE_DIFF_SPEC
from davinci_axi.fake import FakeWorld
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]

SNAPSHOT_SPEC: CommandSpec = replace(
    snapshot_cmd.SPEC,
    subcommands={**snapshot_cmd.SPEC.subcommands, "diff": SNAPSHOT_DIFF_SPEC},
)
TIMELINE_SPEC: CommandSpec = replace(
    timeline_extra.SPEC,
    subcommands={**timeline_extra.SPEC.subcommands, "diff": TIMELINE_DIFF_SPEC},
)


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.COMMANDS, "snapshot", f"{__name__}:SNAPSHOT_SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timeline", f"{__name__}:TIMELINE_SPEC")


def _root(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _take(run_cli: Run, tmp_path: Path, reason: str = "base") -> str:
    before = set(_root(tmp_path).glob("*")) if _root(tmp_path).exists() else set()
    assert run_cli(["project", "snapshot", "--reason", reason])[0] == 0
    (new,) = set(_root(tmp_path).glob("*")) - before
    return new.name


def _json(run_cli: Run, argv: list[str]) -> dict[str, object]:
    code, out, _ = run_cli([*argv, "--json"])
    assert code == 0, out
    data = json.loads(out)
    assert isinstance(data, dict)
    return data


def _delete_second_clip(run_cli: Run) -> None:
    assert run_cli(["item", "delete", "V1:2", "--ripple", "--force"])[0] == 0


def _pretend_it_was_longer(tmp_path: Path, snap: str) -> None:
    """The fake's ripple delete leaves `end_frame` alone; Resolve shortens the timeline by 80."""
    path = _root(tmp_path) / snap / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["timelines"][0]["end_frame"] += 80
    path.write_text(json.dumps(state), encoding="utf-8")


def test_snapshot_vs_live_after_a_ripple_delete(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    snap = _take(run_cli, tmp_path)
    _delete_second_clip(run_cli)
    _pretend_it_was_longer(tmp_path, snap)
    data = _json(run_cli, ["snapshot", "diff", snap])
    assert data["before"] == snap and data["after"] == "live"
    assert data["items_removed"] == 1 and data["items_moved"] == 1
    assert data["frames[Edit v1]"] == "320 → 240"
    changes = data["changes"]
    assert isinstance(changes, list)
    moved = next(c for c in changes if c["kind"] == "moved")
    assert (moved["before"], moved["after"]) == (86560, 86480)
    removed = next(c for c in changes if c["kind"] == "removed")
    assert removed["where"] == "Edit v1: V1:2 clip-2"


def test_toon_output_prints_the_summary(
    run_cli: Run,
    parse_toon_keys: Callable[[str], dict[str, str]],
    fake_world: FakeWorld,
    tmp_path: Path,
) -> None:
    snap = _take(run_cli, tmp_path)
    _delete_second_clip(run_cli)
    _pretend_it_was_longer(tmp_path, snap)
    code, out, _ = run_cli(["snapshot", "diff", snap])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["items_removed"] == "1" and keys["items_moved"] == "1"
    assert "frames[Edit v1]" in out


def test_two_snapshots_make_no_resolve_calls_and_find_no_differences(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    first = _take(run_cli, tmp_path, "one")
    second = _take(run_cli, tmp_path, "two")
    calls = len(fake_world.calls)
    data = _json(run_cli, ["snapshot", "diff", first, second])
    assert len(fake_world.calls) == calls
    assert data["changes"] == f"no differences between {first} and {second}"
    assert data["after"] == second


def test_unchanged_live_project_has_no_differences(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    snap = _take(run_cli, tmp_path)
    writes = fake_world.write_calls()
    data = _json(run_cli, ["snapshot", "diff", snap])
    assert isinstance(data["changes"], str) and data["changes"].startswith("no differences")
    assert fake_world.write_calls() == writes
    _, out, _ = run_cli(["snapshot", "diff", snap])
    assert "no differences" in out


def test_timeline_filter_hides_other_timelines(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    snap = _take(run_cli, tmp_path)
    _delete_second_clip(run_cli)
    data = _json(run_cli, ["snapshot", "diff", snap, "--timeline", "Edit v2"])
    assert data["items_removed"] == 0
    assert isinstance(data["changes"], str)
    code, out, _ = run_cli(["snapshot", "diff", snap, "--timeline", "nope"])
    assert code == 1 and "NOT_FOUND" in out and "Edit v1" in out


def test_unknown_snapshot_lists_ids(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    snap = _take(run_cli, tmp_path)
    code, out, _ = run_cli(["snapshot", "diff", "nope"])
    assert code == 1 and "NOT_FOUND" in out and snap in out


def test_missing_state_json_is_not_found(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    snap = _take(run_cli, tmp_path)
    (_root(tmp_path) / snap / "state.json").unlink()
    code, out, _ = run_cli(["snapshot", "diff", snap])
    assert code == 1 and "NOT_FOUND" in out and "state.json" in out


def test_unreadable_state_json_is_not_found(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    snap = _take(run_cli, tmp_path)
    (_root(tmp_path) / snap / "state.json").write_text("{nope", encoding="utf-8")
    code, out, _ = run_cli(["snapshot", "diff", snap])
    assert code == 1 and "unreadable" in out


def test_limit_cuts_rows_and_names_the_full_command(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    snap = _take(run_cli, tmp_path)
    _delete_second_clip(run_cli)
    code, out, _ = run_cli(["snapshot", "diff", snap, "--limit", "1"])
    assert code == 0
    assert "note: showing 1 of" in out
    assert f"snapshot diff {snap} --full" in out
    data = _json(run_cli, ["snapshot", "diff", snap, "--full"])
    assert isinstance(data["changes"], list) and len(data["changes"]) >= 2
    assert "note" not in data


def test_bad_limit_is_a_validation_error(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    snap = _take(run_cli, tmp_path)
    assert run_cli(["snapshot", "diff", snap, "--limit", "0"])[0] == 2


def test_other_project_is_refused_unless_allowed(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    snap = _take(run_cli, tmp_path)
    path = _root(tmp_path) / snap / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["project"] = "elsewhere"
    path.write_text(json.dumps(state), encoding="utf-8")
    code, out, _ = run_cli(["snapshot", "diff", snap])
    assert code == 2 and "elsewhere" in out and "--allow-other-project" in out
    code, _, _ = run_cli(["snapshot", "diff", snap, "--allow-other-project"])
    assert code == 0


def test_timeline_diff_against_an_empty_timeline(run_cli: Run, fake_world: FakeWorld) -> None:
    data = _json(run_cli, ["timeline", "diff", "Edit v1", "Edit v2"])
    assert data["before"] == "Edit v1" and data["after"] == "Edit v2"
    assert data["items_removed"] == 6
    assert data["frames"] == "240 → 120"
    assert fake_world.write_calls() == ["Project.SetCurrentTimeline"] * 2


def test_timeline_diff_of_a_timeline_with_itself_is_empty(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    data = _json(run_cli, ["timeline", "diff", "Edit v1", "Edit v1"])
    assert data["changes"] == "no differences between Edit v1 and Edit v1"


def test_timeline_diff_unknown_name_lists_timelines(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timeline", "diff", "Edit v1", "nope"])
    assert code == 1 and "NOT_FOUND" in out and "Edit v2" in out


def test_timeline_diff_limit_and_full(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["timeline", "diff", "Edit v1", "Edit v2", "--limit", "2"])
    assert code == 0 and "showing 2 of" in out
    assert "timeline diff 'Edit v1' 'Edit v2' --full" in out
    data = _json(run_cli, ["timeline", "diff", "Edit v1", "Edit v2", "--full"])
    assert isinstance(data["changes"], list) and len(data["changes"]) > 6


def _rows_for(data: dict[str, object], field: str) -> list[dict[str, object]]:
    changes = data["changes"]
    assert isinstance(changes, list)
    return [c for c in changes if c["field"] == field]


def test_snapshot_diff_sees_fades_set_after_the_snapshot(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    snap = _take(run_cli, tmp_path)
    code, out, _ = run_cli(
        ["timeline", "fades", "--in", "60", "--out", "60", "--track-type", "audio", "--force"]
    )
    assert code == 0, out
    data = _json(run_cli, ["snapshot", "diff", snap, "--full"])
    rows = _rows_for(data, "fade_in")
    assert rows and all((r["before"], r["after"]) == (0, 60) for r in rows)
    assert data["items_changed"] == len(rows)
    assert data["fields_changed"] == 2 * len(rows)
    assert data["tracks_changed"] == 0


def test_snapshot_diff_sees_a_volume_change(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    snap = _take(run_cli, tmp_path)
    assert run_cli(["item", "set", "A1:1", "AudioVolume=6", "--force"])[0] == 0
    data = _json(run_cli, ["snapshot", "diff", snap])
    (row,) = _rows_for(data, "volume")
    assert (row["before"], row["after"]) == (0.0, 6.0)
    assert "A1:1" in str(row["where"])


def test_timeline_diff_reads_each_timeline_while_current_and_restores(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    project = fake_world.resolve.GetProjectManager().GetCurrentProject()
    assert project is not None
    current = project.GetCurrentTimeline()
    assert current is not None
    other = "Edit v2" if current.GetName() == "Edit v1" else "Edit v1"
    data = _json(run_cli, ["timeline", "diff", current.GetName(), other])
    assert data["switched"] == [other]
    now = project.GetCurrentTimeline()
    assert now is not None and now.GetName() == current.GetName()


def test_timeline_diff_of_the_current_timeline_does_not_switch(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    project = fake_world.resolve.GetProjectManager().GetCurrentProject()
    assert project is not None
    current = project.GetCurrentTimeline()
    assert current is not None
    data = _json(run_cli, ["timeline", "diff", current.GetName(), current.GetName()])
    assert "switched" not in data or data["switched"] is None
    assert fake_world.write_calls() == []


def test_snapshot_from_the_old_dump_shape_diffs_clean(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    snap = _take(run_cli, tmp_path)
    path = _root(tmp_path) / snap / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    extras = ("fade_in", "fade_out", "volume", "zoom", "pan", "tilt")
    for timeline in state["timelines"]:
        for track in timeline["tracks"]:
            for item in track["items"]:
                for key in extras:
                    item.pop(key, None)
    path.write_text(json.dumps(state), encoding="utf-8")
    data = _json(run_cli, ["snapshot", "diff", snap])
    assert isinstance(data["changes"], str) and data["changes"].startswith("no differences")
