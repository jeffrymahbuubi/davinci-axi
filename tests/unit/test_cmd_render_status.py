"""`render status [jobs]`: the queue list and the per-job detail (F28a/b)."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_render
from davinci_axi.fake.core import FakeProject

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_render.extend()
    monkeypatch.setitem(registry.COMMANDS, "render", "davinci_axi.commands.render:SPEC")


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _rich_job(world: FakeWorld) -> None:
    _project(world).render_jobs[0].update(
        {
            "FrameRate": "24",
            "MarkIn": 86400,
            "MarkOut": 86400 + 5015,
            "AudioCodec": "aac",
            "AudioSampleRate": 48000,
            "PresetName": "H.264 Master",
            "RenderMode": "Single clip",
            "PixelAspectRatio": 1.0,
        }
    )
    _project(world).render_status["job-1"]["TimeTakenToRenderInMs"] = 4200


def test_status_with_a_job_id_shows_the_detail_row(run_cli: Run, fake_world: FakeWorld) -> None:
    _rich_job(fake_world)
    code, out, err = run_cli(["render", "status", "job-1", "--json"])
    assert (code, err) == (0, "")
    data = json.loads(out)
    assert "by_status" not in data and len(data["jobs"]) == 1
    job = data["jobs"][0]
    assert job["id"] == "job-1" and job["status"] == "Complete"
    assert (job["mark_in"], job["mark_out"], job["frames"]) == (86400, 91415, 5016)
    assert job["duration"] == "00:03:29:00"  # 5016 frames at 24 fps = 209 s
    assert job["frame_rate"] == "24" and job["audio_codec"] == "aac"
    assert job["preset"] == "H.264 Master" and job["audio_sample_rate"] == 48000
    assert job["time_taken_ms"] == 4200 and job["export_video"] is True
    assert "error" not in job and "encoding_profile" not in job
    assert "no bit rate or quality" in data["note"]
    assert "timeline mark show" in data["note"]
    assert "absolute timeline frames" in data["note"] and "relative to its start" in data["note"]
    assert fake_world.write_calls() == []


def test_status_detail_survives_missing_range_fields(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "status", "job-2", "--json"])
    job = json.loads(out)["jobs"][0]
    assert code == 0 and job["id"] == "job-2"
    assert "frames" not in job and "duration" not in job and "mark_in" not in job


def test_status_with_several_ids_returns_a_row_each(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "status", "job-2,job-1", "--json"])
    assert code == 0
    assert [j["id"] for j in json.loads(out)["jobs"]] == ["job-2", "job-1"]


def test_status_unknown_job_lists_the_queue(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "status", "job-9"])
    assert code == 1 and "NOT_FOUND" in out and "job-1, job-2" in out


def test_status_new_field_names_work_in_the_list(run_cli: Run, fake_world: FakeWorld) -> None:
    _rich_job(fake_world)
    code, out, _ = run_cli(["render", "status", "--fields", "id,mark_in,preset,frame_rate"])
    assert code == 0
    assert "jobs[2]{id,mark_in,preset,frame_rate}:" in out
    assert '  job-1,86400,H.264 Master,"24"' in out


def test_status_without_ids_keeps_the_list_shape(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["render", "status"])
    assert code == 0 and "by_status[2]{status,count}:" in out
    assert "jobs[2]{id,name,timeline,status,percent}:" in out
    assert "note" not in parse_toon_keys(out)


def test_status_detail_honours_fields(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "status", "job-1", "--fields", "id,status,percent", "--json"])
    assert code == 0
    rows = json.loads(out)["jobs"]
    assert [list(row) for row in rows] == [["id", "status", "percent"]]
    assert "apply to the queue list" not in out


def test_status_detail_unknown_field_lists_the_detail_names(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, err = run_cli(["render", "status", "job-1", "--fields", "bogus"])
    assert code == 2
    assert "mark_in" in out + err and "time_taken_ms" in out + err


def test_status_detail_limit_still_only_shapes_the_list(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["render", "status", "job-1,job-2", "--limit", "1", "--json"])
    assert code == 0
    assert [row["id"] for row in json.loads(out)["jobs"]] == ["job-1", "job-2"]
    assert "--limit applies to the queue list" in out


def test_status_help_shows_the_job_positional_example(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "status", "--help"])
    assert code == 0 and "davinci-axi render status job-2" in out
    assert fake_world.calls == []


def test_status_help_carries_the_mark_note(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["render", "status", "--help"])
    assert code == 0 and "absolute timeline frames" in out
    assert fake_world.calls == []
