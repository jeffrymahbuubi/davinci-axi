"""`timeline append --edl` (F14a): edit-list rows, one plan, and the new item ids reported."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands.timeline_append import APPEND_SPEC, read_edl
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeProject, FakeTimeline, FakeWorld, ext_edit_extra

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

APPEND = "MediaPool.AppendToTimeline"
ROWS: list[dict[str, object]] = [
    {"clip": "A001_beach", "in": 0, "out": 30, "track": 1},
    {"clip": "A001_beach", "in": 30, "out": 60, "track": 1},
    {"clip": "music_bed", "track": 1},
]
CSV = "clip,in,out,track\nA001_beach,0,30,1\n\nA001_beach,30,60,1\nmusic_bed,,,1\n"


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_edit_extra.extend()
    monkeypatch.setitem(registry.COMMANDS, "timeline", "davinci_axi.commands.timeline_extra:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "timelines", "davinci_axi.commands.timelines:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "items", "davinci_axi.commands.items:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _edit(world: FakeWorld) -> FakeTimeline:
    return next(t for t in _project(world).timelines if t.name == "Edit v1")


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _write(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _json_edl(tmp_path: Path, rows: object = ROWS) -> str:
    return _write(tmp_path, "cuts.json", json.dumps(rows))


def test_json_edl_appends_every_row_in_one_call_and_lists_ids(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, err = run_cli(
        ["timeline", "append", "--edl", _json_edl(tmp_path), "--force", "--json"]
    )
    assert (code, err) == (0, "")
    data = json.loads(out)
    assert fake_world.write_calls().count(APPEND) == 1
    (infos,) = fake_world.calls_to(APPEND)[0]
    assert isinstance(infos, list) and len(infos) == 3
    assert (infos[0]["startFrame"], infos[0]["endFrame"]) == (0, 30)
    assert "startFrame" not in infos[2]
    assert [d.name for d in _snapshots(tmp_path).iterdir()] == [data["snapshot"]]
    items = data["items"]
    assert [row["row"] for row in items] == [1, 2, 3]
    assert len({row["id"] for row in items}) == 3
    assert all(row["id"] for row in items)
    frames = [row["record_frame"] for row in items[:2]]
    assert frames[0] < frames[1]
    assert [row["frames"] for row in items[:2]] == [30, 30]
    assert items[0]["track"] == "V1" and items[2]["track"] == "A1"
    assert data["rows"] == 3
    assert data["changes"] == [{"field": "appended", "before": 0, "after": 3}]  # twice for A001


def test_csv_edl_dry_run_reports_rows_and_source_frames(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _write(tmp_path, "cuts.csv", CSV)
    code, out, _ = run_cli(["timeline", "append", "--edl", path])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false"
    assert keys["rows"] == "3" and keys["source_frames"] == "1500"
    assert "appended,0,3" in out
    assert f"--edl {path} --force" in out
    assert fake_world.write_calls() == []
    assert not _snapshots(tmp_path).exists()


def test_csv_edl_force_applies_all_rows(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    path = _write(tmp_path, "cuts.csv", CSV)
    code, _, _ = run_cli(["timeline", "append", "--edl", path, "--force"])
    assert code == 0
    assert len(fake_world.calls_to(APPEND)[0][0]) == 3  # type: ignore[arg-type]


def test_media_type_and_record_frame_are_passed_through(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    rows = [{"clip": "music_bed", "media_type": "audio", "record_frame": 5000}]
    code, _, _ = run_cli(["timeline", "append", "--edl", _json_edl(tmp_path, rows), "--force"])
    assert code == 0
    info = fake_world.calls_to(APPEND)[0][0][0]  # type: ignore[index]
    assert (info["mediaType"], info["recordFrame"]) == (2, 5000)


@pytest.mark.parametrize(
    ("rows", "needle"),
    [
        ([{"clip": "A001_beach", "bogus": 1}], "unknown column bogus"),
        ([{"clip": "A001_beach", "in": "x", "out": 9}], "row 1: in must be an integer"),
        ([{"clip": "A001_beach"}, {"clip": "A001_beach", "track": "two"}], "row 2: track"),
        ([{"clip": "A001_beach", "in": 5}], "row 1: in and out go together"),
        ([{"clip": "A001_beach", "in": 9, "out": 9}], "row 1: in and out go together"),
        ([{"track": 1}], "row 1: clip is required"),
        ([{"clip": "A001_beach", "media_type": "film"}], "media_type must be"),
        ([], "no rows"),
        ({"clip": "A001_beach"}, "JSON array"),
    ],
)
def test_bad_edl_rows_are_refused_before_any_resolve_call(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, rows: object, needle: str
) -> None:
    code, out, _ = run_cli(["timeline", "append", "--edl", _json_edl(tmp_path, rows), "--force"])
    assert code == 2 and needle in out
    assert fake_world.calls == []


def test_edl_with_clips_or_neither_or_clip_flags_is_refused(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    edl = _json_edl(tmp_path)
    assert run_cli(["timeline", "append", "A001_beach", "--edl", edl, "--force"])[0] == 2
    assert run_cli(["timeline", "append", "--force"])[0] == 2
    assert run_cli(["timeline", "append", "--edl", edl, "--track", "1", "--force"])[0] == 2
    assert fake_world.calls == []


def test_bad_edl_files_are_refused(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(["timeline", "append", "--edl", str(tmp_path / "nope.json")])
    assert code == 1 and "NOT_FOUND" in out
    code, out, _ = run_cli(["timeline", "append", "--edl", _write(tmp_path, "c.txt", "x")])
    assert code == 2 and "use .json or .csv" in out
    code, out, _ = run_cli(["timeline", "append", "--edl", _write(tmp_path, "b.json", "{")])
    assert code == 2 and "not valid JSON" in out
    assert fake_world.calls == []


def test_read_edl_maps_in_and_numbers(tmp_path: Path) -> None:
    rows = read_edl(Path(_write(tmp_path, "c.csv", "clip,in,out,media_type\nA,1,9,video\n")))
    assert rows == [{"clip": "A", "in_": 1, "out": 9, "media_type": 1}]
    with pytest.raises(AxiError, match="row 1"):
        read_edl(Path(_write(tmp_path, "d.csv", "clip,in,out\nA,x,9\n")))


def test_out_past_the_clip_end_and_a_missing_track_use_the_known_wording(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    rows = [{"clip": "A001_beach", "in": 0, "out": 121}]
    code, out, _ = run_cli(["timeline", "append", "--edl", _json_edl(tmp_path, rows), "--force"])
    assert code == 2 and "past the end of A001_beach (120 frames)" in out
    rows = [{"clip": "A001_beach", "track": 5}]
    code, out, _ = run_cli(["timeline", "append", "--edl", _json_edl(tmp_path, rows), "--force"])
    assert code == 2 and "has 2 video tracks, not 5" in out
    assert fake_world.write_calls() == []


def test_unknown_clip_in_a_row_is_not_found(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    rows = [{"clip": "nosuchclip"}]
    code, _, _ = run_cli(["timeline", "append", "--edl", _json_edl(tmp_path, rows), "--force"])
    assert code == 1
    assert fake_world.write_calls() == []


def test_fewer_items_than_rows_is_api_failed_with_the_snapshot(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject(APPEND, [_edit(fake_world).tracks["video"][0].items[0]])
    code, out, _ = run_cli(["timeline", "append", "--edl", _json_edl(tmp_path), "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    assert "appended 1 of 3 rows" in out
    assert "snapshot" in out


def test_clip_path_reports_the_new_item_id(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["timeline", "append", "A003_dusk", "--force", "--json"])
    assert code == 0
    items = json.loads(out)["items"]
    assert len(items) == 1 and items[0]["id"] and items[0]["clip"] == "A003_dusk"
    assert items[0]["row"] == 1 and items[0]["track"] == "V1"


def test_duplicate_clip_names_count_once_per_row_in_verify(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    rows = [{"clip": "A002_city"}, {"clip": "A002_city"}]
    code, out, _ = run_cli(["timeline", "append", "--edl", _json_edl(tmp_path, rows), "--force"])
    assert code == 0 and "appended,0,2" in out


def test_help_explains_the_units_and_the_edl() -> None:
    flags = {flag.name: flag.help for flag in APPEND_SPEC.flags}
    assert "clip's own frame rate" in flags["in"] and "exclusive" in flags["out"]
    assert "JSON or CSV" in flags["edl"]
    assert any("112 frames at 30 fps" in note for note in APPEND_SPEC.notes)
    assert not APPEND_SPEC.positionals[0].required


# F26(c): --duration for stills and generators, absolute --record-frame
def _still(world: FakeWorld) -> None:
    _project(world).media_pool.root.add_clip(
        "title_card",
        "/media/davinci-axi-test/title_card.png",
        clip_type="Still",
        duration="00:00:05:00",
        frames="1",
        video_codec="",
        audio_ch="0",
    )


def test_duration_appends_a_still_with_that_many_frames(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _still(fake_world)
    args = ["timeline", "append", "title_card", "--track", "2", "--record-frame", "86500"]
    code, out, err = run_cli([*args, "--duration", "72", "--force"])
    assert (code, err) == (0, "")
    (info,) = fake_world.calls_to(APPEND)[0][0]  # type: ignore[misc]
    assert {k: v for k, v in info.items() if k != "mediaPoolItem"} == {
        "startFrame": 0,
        "endFrame": 72,
        "trackIndex": 2,
        "recordFrame": 86500,
    }
    assert "frames,null,72" in out and "appended,0,1" in out
    assert parse_toon_keys(out)["duration"] == "72"
    assert "standard still duration" in out
    code, out, _ = run_cli([*args, "--duration", "72", "--force", "--json"])
    assert json.loads(out)["items"][0]["frames"] == 72


def test_duration_dry_run_names_itself_in_the_force_command(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    _still(fake_world)
    code, out, _ = run_cli(["timeline", "append", "title_card", "--duration", "90"])
    assert code == 0 and "title_card --duration 90 --force" in out
    assert "--in" not in out and fake_world.write_calls() == []


def test_a_still_that_resolve_keeps_at_its_own_length_is_verify_failed(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    _still(fake_world)
    long_item = _edit(fake_world).tracks["video"][0].items[0]
    assert long_item.duration != 72
    fake_world.inject(APPEND, [long_item])
    code, out, _ = run_cli(["timeline", "append", "title_card", "--duration", "72", "--force"])
    keys = parse_toon_keys(out)
    assert code == 1 and keys["code"] == "VERIFY_FAILED"
    assert "frames is " in out and "expected 72" in out
    assert "snapshot" in out


def test_duration_on_footage_checks_the_clip_length_and_sets_the_span(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, _, _ = run_cli(["timeline", "append", "A001_beach", "--duration", "60", "--force"])
    assert code == 0
    info = fake_world.calls_to(APPEND)[0][0][0]  # type: ignore[index]
    assert (info["startFrame"], info["endFrame"]) == (0, 60)
    code, out, _ = run_cli(["timeline", "append", "A001_beach", "--duration", "500", "--force"])
    assert code == 2 and "past the end of A001_beach (120 frames); for stills use --duration" in out


@pytest.mark.parametrize(
    "extra", [["--duration", "0"], ["--duration", "10", "--in", "0", "--out", "5"]]
)
def test_bad_duration_is_refused_before_connecting(
    run_cli: Run, fake_world: FakeWorld, extra: list[str]
) -> None:
    code, out, _ = run_cli(["timeline", "append", "A001_beach", *extra, "--force"])
    assert code == 2 and ("1 or more" in out or "not both" in out)
    assert fake_world.calls == []


def test_edl_rows_take_a_duration_column(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    _still(fake_world)
    rows = [
        {"clip": "title_card", "duration": 48, "track": 2},
        {"clip": "A001_beach", "duration": 30, "track": 1},
    ]
    code, out, _ = run_cli(["timeline", "append", "--edl", _json_edl(tmp_path, rows), "--force"])
    assert code == 0 and "frames,null,48" in out
    infos = fake_world.calls_to(APPEND)[0][0]
    assert [(i["startFrame"], i["endFrame"]) for i in infos] == [(0, 48), (0, 30)]  # type: ignore[index,union-attr]
    both = [{"clip": "A001_beach", "duration": 5, "in": 0, "out": 5}]
    code, out, _ = run_cli(["timeline", "append", "--edl", _json_edl(tmp_path, both), "--force"])
    assert code == 2 and "row 1: use duration or in/out, not both" in out
    zero = [{"clip": "A001_beach", "duration": 0}]
    code, out, _ = run_cli(["timeline", "append", "--edl", _json_edl(tmp_path, zero), "--force"])
    assert code == 2 and "row 1: duration must be 1 or more" in out
    code, _, _ = run_cli(["timeline", "append", "--edl", _json_edl(tmp_path), "--duration", "9"])
    assert code == 2


def test_help_says_record_frame_is_absolute_and_nothing_trims() -> None:
    flags = {flag.name: flag.help for flag in APPEND_SPEC.flags}
    assert "absolute" in flags["record-frame"] and "108000" in flags["record-frame"]
    assert "stills and generators" in flags["duration"] and "duration" in flags["edl"]
    notes = " ".join(APPEND_SPEC.notes)
    assert "No API trims, slips or re-times" in notes and "--record-frame is absolute" in notes
    assert any("--duration" in example for example in APPEND_SPEC.examples)
