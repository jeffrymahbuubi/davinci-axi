"""`render deliver --max-mb`: the size-capped render job (U5, feature D)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import render
from davinci_axi.commands.render_deliver import (
    DELIVER_SPEC,
    deliver_kbps,
    drift_pct,
    encoder_floor_kbps,
    fitting_size,
    needed_mb,
    predicted_kbps,
    required_kbps,
)
from davinci_axi.commands.render_jobs import ADD_SPEC
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeWorld, ext_render
from davinci_axi.fake.core import FakeProject

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]


@pytest.fixture(autouse=True)
def _register(monkeypatch: pytest.MonkeyPatch) -> None:
    ext_render.extend()
    monkeypatch.setitem(registry.COMMANDS, "render", "davinci_axi.commands.render:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")
    monkeypatch.setitem(render.SUBCOMMANDS, "deliver", DELIVER_SPEC)


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    path = (tmp_path / "out").resolve()
    path.mkdir()
    return path


def _project(world: FakeWorld) -> FakeProject:
    project = world.resolve.pm.current_project
    assert project is not None
    return project


def _deliver(out_dir: Path, *extra: str, mb: str = "10") -> list[str]:
    return ["render", "deliver", "--target-dir", str(out_dir), "--max-mb", mb, *extra]


def test_deliver_kbps_formula() -> None:
    # decimal MB: 10 MB over 10 s = 8000 kbps; 5 % headroom = 7600; minus 320 audio = 7280 for
    # video plus its modelled overshoot, so a rate far above the floor (2 % drift) gets 7137
    assert deliver_kbps(10, 10, 320, 5, 0) == 7137
    assert deliver_kbps(10, 10, 320, 5, 2766) == 7137
    assert deliver_kbps(10, 10, 0, 0, 0) == 7843  # 8000 / 1.02
    # near the floor the overshoot is 30 %: 5 MB / 20 s = 2000 * 0.95 - 320 = 1580 -> 1215
    assert deliver_kbps(5, 20, 320, 5, 2766) == 1215


def test_drift_falls_from_30_at_the_floor_to_2_far_above_it() -> None:
    assert drift_pct(2766, 2766) == 30
    assert drift_pct(1000, 2766) == 30
    assert drift_pct(3043, 2766) == pytest.approx(28.1, abs=0.05)
    assert drift_pct(5532, 2766) == pytest.approx(11.3, abs=0.05)  # r = 2.0
    assert drift_pct(7544, 2766) == 2
    assert drift_pct(9000, 2766) == 2
    assert drift_pct(1000, 0) == 2  # floor unknown
    assert predicted_kbps(2766, 2766) == 3596 and predicted_kbps(1000, 0) == 1020


def test_the_solved_rate_always_fits_and_one_more_does_not() -> None:
    for mb, secs, floor in [(10, 10, 2766), (11.11, 20, 2766), (6, 8, 1748), (6, 30, 0)]:
        kbps = deliver_kbps(mb, secs, 320, 5, floor)
        spare = mb * 8000 / secs * 0.95
        assert predicted_kbps(kbps, floor) + 320 <= spare
        assert predicted_kbps(kbps + 1, floor) + 320 > spare


def test_deliver_kbps_below_the_floor_is_a_validation_error() -> None:
    with pytest.raises(AxiError) as caught:
        deliver_kbps(0.1, 10, 320, 5, 0)
    assert caught.value.code == "VALIDATION_ERROR"
    assert "below 500 kbps" in str(caught.value) and "10.0 s" in str(caught.value)


def test_dry_run_computes_the_rate_and_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, out, err = run_cli(_deliver(out_dir))
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "")
    assert keys["applied"] == "false"
    assert keys["command"] == "render deliver" and "seconds: 10" in out
    assert "video_kbps: 7137" in out and "audio_kbps: 320" in out and "max_mb: 10" in out
    assert "Run `davinci-axi render deliver" in out
    assert "--max-mb 10" in out and "render add" not in out
    assert fake_world.write_calls() == []


def test_dry_run_predicts_a_size_within_the_cap(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, out, _ = run_cli(_deliver(out_dir, "--json"))
    assert code == 0
    deliver = json.loads(out)["deliver"]
    assert deliver["seconds"] == 10.0 and deliver["video_kbps"] == 7137
    assert deliver["headroom_pct"] == 5.0 and deliver["predicted_mb"] <= 10
    assert deliver["drift_pct"] == 2.0 and deliver["predicted_kbps"] == 7280
    assert deliver["predicted_mb_worst"] <= 10
    assert fake_world.write_calls() == []


def test_force_queues_one_job_with_the_computed_video_quality(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, out_dir: Path
) -> None:
    jobs_before = len(_project(fake_world).render_jobs)
    code, out, _ = run_cli(_deliver(out_dir, "--force"))
    assert code == 0 and parse_toon_keys(out)["applied"] == "true"
    sent = fake_world.calls_to("Project.SetRenderSettings")
    assert len(sent) == 2 and sent[1] == ({"VideoQuality": 0},)  # queue, then the reset
    settings = sent[0][0]
    assert isinstance(settings, dict) and settings["VideoQuality"] == 7137
    assert len(fake_world.calls_to("Project.AddRenderJob")) == 1
    assert len(_project(fake_world).render_jobs) == jobs_before + 1
    assert "VideoQuality=7137" in out and "video_kbps: 7137" in out


def test_defaults_budget_320_audio_and_5_percent(run_cli: Run, out_dir: Path) -> None:
    code, out, _ = run_cli(_deliver(out_dir, "--json"))
    deliver = json.loads(out)["deliver"]
    assert code == 0
    assert deliver["audio_kbps"] == 320 and deliver["headroom_pct"] == 5.0
    assert deliver["predicted_mb_worst"] <= 10 and "warning" not in json.loads(out)


def test_zero_headroom_still_fits_the_modelled_size(run_cli: Run, out_dir: Path) -> None:
    code, out, _ = run_cli(_deliver(out_dir, "--headroom", "0", "--json", mb="6"))
    data = json.loads(out)
    assert code == 0 and data["deliver"]["predicted_mb_worst"] <= 6 and "warning" not in data


def test_headroom_20_is_still_accepted(run_cli: Run, out_dir: Path) -> None:
    code, out, _ = run_cli(_deliver(out_dir, "--headroom", "20", "--json", mb="10"))
    deliver = json.loads(out)["deliver"]
    assert code == 0 and deliver["headroom_pct"] == 20.0
    assert deliver["video_kbps"] < 7137  # a bigger buffer, a lower rate
    assert deliver["video_kbps"] > 5000


def test_far_from_the_floor_the_rate_is_within_2_percent_drift(run_cli: Run, out_dir: Path) -> None:
    code, out, _ = run_cli(_deliver(out_dir, "--set", "FormatWidth=320,FormatHeight=240", "--json"))
    deliver = json.loads(out)["deliver"]
    assert code == 0 and deliver["floor_kbps"] == 969
    assert deliver["video_kbps"] == pytest.approx((7600 - 320) / 1.02, abs=2)  # 2 % drift
    assert deliver["video_kbps"] > 7600 - 320 - 0.025 * (7600 - 320)


def test_force_resets_video_quality_after_queueing(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    ext_render.add_opens_deliver(_project(fake_world))
    code, out, _ = run_cli(_deliver(out_dir, "--force", "--json"))
    data = json.loads(out)
    assert code == 0 and data["applied"] is True
    assert data["video_quality_reset"] is True and "video_quality_left" not in data
    sent = fake_world.calls_to("Project.SetRenderSettings")
    assert len(sent) == 2 and sent[-1] == ({"VideoQuality": 0},)
    assert fake_world.resolve.page == "edit" and data["page_restored"] is True
    assert not any("render settings set VideoQuality=0" in line for line in data["help"])


def test_a_refused_reset_keeps_the_left_hint(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = FakeProject.SetRenderSettings
    seen: list[dict[str, object]] = []

    def refuse_second(self: FakeProject, settings: dict[str, object]) -> bool:
        seen.append(settings)
        return real(self, settings) if len(seen) == 1 else False

    monkeypatch.setattr(FakeProject, "SetRenderSettings", refuse_second)
    code, out, _ = run_cli(_deliver(out_dir, "--force", "--json"))
    data = json.loads(out)
    assert code == 0 and data["video_quality_reset"] is False
    assert data["video_quality_left"] == 7137
    assert any("render settings set VideoQuality=0 --force" in line for line in data["help"])


def test_dry_run_does_not_claim_a_left_quality(run_cli: Run, out_dir: Path) -> None:
    code, out, _ = run_cli(_deliver(out_dir, "--json"))
    assert code == 0 and "video_quality_left" not in out


def test_user_settings_are_kept_beside_the_computed_quality(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, _, _ = run_cli(_deliver(out_dir, "--set", "FormatWidth=1280", "--force"))
    assert code == 0
    settings = fake_world.calls_to("Project.SetRenderSettings")[0][0]
    assert isinstance(settings, dict)
    assert settings["FormatWidth"] == 1280 and settings["VideoQuality"] == 7137


def test_the_force_line_repeats_the_deliver_flags(run_cli: Run, out_dir: Path) -> None:
    code, out, _ = run_cli(
        _deliver(out_dir, "--audio-kbps", "128", "--headroom", "10", "--name", "cut", "--mkdir")
    )
    assert code == 0
    line = next(row for row in out.splitlines() if "Run `davinci-axi render deliver" in row)
    for part in ("--max-mb 10", "--audio-kbps 128", "--headroom 10", "--name cut", "--mkdir"):
        assert part in line
    assert line.rstrip().endswith("--force` to apply this change")


def test_set_video_quality_is_refused(run_cli: Run, fake_world: FakeWorld, out_dir: Path) -> None:
    code, out, _ = run_cli(_deliver(out_dir, "--set", "VideoQuality=1"))
    assert code == 2 and "deliver computes VideoQuality" in out
    assert fake_world.write_calls() == []


@pytest.mark.parametrize(
    "extra",
    [
        ("--audio-kbps", "-1"),
        ("--headroom", "51"),
        ("--headroom", "-1"),
    ],
)
def test_bad_numbers_exit_2(run_cli: Run, out_dir: Path, extra: tuple[str, ...]) -> None:
    code, out, _ = run_cli(_deliver(out_dir, *extra))
    assert code == 2 and "VALIDATION_ERROR" in out


def test_max_mb_must_be_positive(run_cli: Run, out_dir: Path) -> None:
    code, out, _ = run_cli(_deliver(out_dir, mb="0"))
    assert code == 2 and "--max-mb must be greater than 0" in out


def test_too_small_a_cap_exits_2(run_cli: Run, fake_world: FakeWorld, out_dir: Path) -> None:
    code, out, _ = run_cli(_deliver(out_dir, mb="0.1"))
    assert code == 2 and "below 500 kbps" in out
    assert fake_world.write_calls() == []


def test_set_marks_define_the_duration(run_cli: Run, out_dir: Path) -> None:
    code, out, _ = run_cli(_deliver(out_dir, "--set", "MarkIn=86400,MarkOut=86519", "--json"))
    assert code == 0 and '"seconds": 5.0' in out


def test_timeline_marks_define_the_duration_unless_all_frames(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    timeline = _project(fake_world).current_timeline
    assert timeline is not None
    timeline.start_frame, timeline.end_frame = 108000, 108000 + 240
    timeline.mark_in_out = {"video": {"in": 100, "out": 200}}  # relative, 101 frames inclusive
    code, out, _ = run_cli(_deliver(out_dir, "--json"))
    assert code == 0 and '"seconds": 4.21' in out
    code, out, _ = run_cli(_deliver(out_dir, "--all-frames", "--json"))
    assert code == 0 and '"seconds": 10.0' in out


def test_other_codecs_warn(run_cli: Run, fake_world: FakeWorld, out_dir: Path) -> None:
    _project(fake_world).format_codec = {"format": "mov", "codec": "ProRes422"}
    code, out, _ = run_cli(_deliver(out_dir))
    assert code == 0 and "verified for H.264/H.265 only" in out


def test_h264_does_not_warn(run_cli: Run, out_dir: Path) -> None:
    code, out, _ = run_cli(_deliver(out_dir))
    assert code == 0 and "verified for H.264/H.265 only" not in out


def test_the_spec_reuses_the_add_flags() -> None:
    names = [flag.name for flag in DELIVER_SPEC.flags]
    assert names[:3] == ["max-mb", "audio-kbps", "headroom"]
    assert names[3] == "below-floor"
    assert names[4:] == [flag.name for flag in ADD_SPEC.flags]


# F43/F45: the H.264 encoder floor. 20 s at 24 fps, 5 MB -> 1280 kbps (decimal MB), under the
# 3043 kbps a 1920x1080 job needs (2766 estimate + 10 % safety).
LONG = "MarkIn=86400,MarkOut=86879"


def test_encoder_floor_scales_with_pixels() -> None:
    assert encoder_floor_kbps(1080, 1920) == 2766
    assert encoder_floor_kbps(728, 1294) == 1748
    assert encoder_floor_kbps(540, 960) == 1367
    assert encoder_floor_kbps(0, 0) == 0
    assert required_kbps(1080, 1920) == 3043
    assert required_kbps(0, 5) == 0


@pytest.mark.parametrize("kbps", [1280, 1318, 2000])
def test_fitting_size_passes_its_own_check(kbps: int) -> None:
    fit = fitting_size(1080, 1920, kbps)
    assert fit is not None
    width, height = fit
    assert width % 2 == 0 and height % 2 == 0
    assert abs(width / height - 1080 / 1920) < 0.02
    assert required_kbps(width, height) <= kbps


def test_fitting_size_is_none_when_nothing_fits() -> None:
    assert fitting_size(1080, 1920, 990) is None


def _help_values(data: dict[str, object]) -> tuple[str, str]:
    """The `FormatWidth=..,FormatHeight=..` size and the `--max-mb ..` cap a refusal suggests."""
    lines = [str(line) for line in data["help"]]  # type: ignore[attr-defined]
    size = next(line for line in lines if "FormatWidth=" in line)
    cap = next(line for line in lines if "--max-mb" in line)
    return size.split("--set ")[1], cap.split("--max-mb ")[1]


def test_the_suggested_size_and_cap_are_not_refused(run_cli: Run, out_dir: Path) -> None:
    code, out, _ = run_cli(_deliver(out_dir, "--set", LONG, "--force", "--json", mb="5"))
    assert code == 2
    size, cap = _help_values(json.loads(out))
    code, out, _ = run_cli(_deliver(out_dir, "--set", f"{LONG},{size}", "--json", mb="5"))
    deliver = json.loads(out)["deliver"]
    assert code == 0 and deliver["video_kbps"] >= deliver["required_kbps"]
    code, out, _ = run_cli(_deliver(out_dir, "--set", LONG, "--json", mb=cap))
    deliver = json.loads(out)["deliver"]
    assert code == 0 and deliver["video_kbps"] >= deliver["required_kbps"]


def test_a_given_size_that_is_still_too_big_asks_for_smaller_still(
    run_cli: Run, out_dir: Path
) -> None:
    code, out, _ = run_cli(
        _deliver(out_dir, "--set", f"{LONG},FormatWidth=1080,FormatHeight=1920", "--json", mb="5")
    )
    assert code == 2 and "Render smaller still: --set FormatWidth=" in out


def test_refusal_message_has_no_size_below_the_cap(run_cli: Run, out_dir: Path) -> None:
    code, out, _ = run_cli(_deliver(out_dir, "--set", LONG, "--json", mb="5"))
    assert code == 2
    assert "stays near 2766" in out and "needs 3043" in out and "(about" not in out


def test_below_floor_flag_queues_with_a_warning(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, out, _ = run_cli(
        _deliver(out_dir, "--set", LONG, "--below-floor", "--force", "--json", mb="5")
    )
    data = json.loads(out)
    assert code == 0 and data["applied"] is True
    assert "under the 3043 kbps the encoder floor at 1920x1080 needs" in data["warning"]
    deliver = data["deliver"]
    assert deliver["floor_kbps"] == 2766 and deliver["output_size"] == "1920x1080"
    assert deliver["required_kbps"] == 3043 and deliver["floor_margin_pct"] == 10.0
    assert deliver["predicted_mb_worst"] > 5
    assert len(fake_world.calls_to("Project.AddRenderJob")) == 1


def test_a_rate_above_the_floor_is_not_refused(run_cli: Run, out_dir: Path) -> None:
    code, out, _ = run_cli(_deliver(out_dir, "--set", LONG, "--json", mb="60"))
    data = json.loads(out)
    assert code == 0 and "floor" not in data.get("warning", "")
    assert data["deliver"]["video_kbps"] > data["deliver"]["required_kbps"]


def test_set_format_size_drives_the_floor(run_cli: Run, out_dir: Path) -> None:
    code, _, _ = run_cli(_deliver(out_dir, "--set", LONG, mb="5"))
    assert code == 2
    small = f"{LONG},FormatWidth=540,FormatHeight=960"
    code, out, _ = run_cli(_deliver(out_dir, "--set", small, "--json", mb="6"))
    data = json.loads(out)
    assert code == 0 and data["deliver"]["output_size"] == "540x960"
    assert data["deliver"]["floor_kbps"] == 1367


def test_other_codecs_only_warn(run_cli: Run, fake_world: FakeWorld, out_dir: Path) -> None:
    _project(fake_world).format_codec = {"format": "mov", "codec": "ProRes422"}
    code, out, _ = run_cli(_deliver(out_dir, "--set", LONG, "--json", mb="5"))
    data = json.loads(out)
    assert code == 0 and "verified for H.264/H.265 only" in data["warning"]
    assert "encoder floor" in data["warning"]


def test_the_force_line_repeats_below_floor(run_cli: Run, out_dir: Path) -> None:
    code, out, _ = run_cli(_deliver(out_dir, "--below-floor"))
    line = next(row for row in out.splitlines() if "Run `davinci-axi render deliver" in row)
    assert code == 0 and "--below-floor" in line


def test_force_resets_format_with_video_quality(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    small = f"{LONG},FormatWidth=540,FormatHeight=960"
    code, out, _ = run_cli(_deliver(out_dir, "--set", small, "--force", "--json"))
    data = json.loads(out)
    assert code == 0 and data["applied"] is True
    expected = {"VideoQuality": 0, "FormatWidth": 1920, "FormatHeight": 1080}
    assert fake_world.calls_to("Project.SetRenderSettings")[-1] == (expected,)
    assert data["format_reset"] == "1920x1080" and data["video_quality_reset"] is True
    assert "format_left" not in data


def test_no_format_in_set_means_no_format_reset(
    run_cli: Run, fake_world: FakeWorld, out_dir: Path
) -> None:
    code, out, _ = run_cli(_deliver(out_dir, "--set", LONG, "--force", "--json", mb="20"))
    data = json.loads(out)
    assert code == 0 and "format_reset" not in data and "format_left" not in data
    assert fake_world.calls_to("Project.SetRenderSettings")[-1] == ({"VideoQuality": 0},)


def test_a_refused_reset_names_the_format_left(
    run_cli: Run, out_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = FakeProject.SetRenderSettings
    seen: list[dict[str, object]] = []

    def refuse_second(self: FakeProject, settings: dict[str, object]) -> bool:
        seen.append(settings)
        return real(self, settings) if len(seen) == 1 else False

    monkeypatch.setattr(FakeProject, "SetRenderSettings", refuse_second)
    small = f"{LONG},FormatWidth=540,FormatHeight=960"
    code, out, _ = run_cli(_deliver(out_dir, "--set", small, "--force", "--json"))
    data = json.loads(out)
    assert code == 0 and data["video_quality_reset"] is False
    assert data["format_left"] == "540x960" and "format_reset" not in data
    assert any("render settings set FormatWidth=" in line for line in data["help"])


def test_max_mb_help_says_decimal(run_cli: Run) -> None:
    code, out, _ = run_cli(["render", "deliver", "--help"])
    assert code == 0 and "1,000,000 bytes" in out


def test_f45_point_is_refused_with_a_cap_that_then_passes(run_cli: Run, out_dir: Path) -> None:
    # 10.51 MB over 20 s at 1080p: 3994 - 320 leaves 2836 kbps, under the 3043 gate
    code, out, _ = run_cli(_deliver(out_dir, "--set", LONG, "--json", mb="10.51"))
    assert code == 2
    _, cap = _help_values(json.loads(out))
    assert float(cap) == pytest.approx(11.1, abs=0.05)
    assert needed_mb(1920, 1080, 20, 320, 5) == float(cap)
    code, out, _ = run_cli(_deliver(out_dir, "--set", LONG, "--json", mb=cap))
    deliver = json.loads(out)["deliver"]
    assert code == 0 and deliver["video_kbps"] >= 3043
    assert deliver["predicted_mb_worst"] <= float(cap)
    assert deliver["predicted_mb_worst"] <= float(cap) * 0.95 + 0.01  # the 5 % buffer is spare
    assert deliver["drift_pct"] == pytest.approx(28.1, abs=0.3)


def test_help_describes_the_modelled_drift(run_cli: Run) -> None:
    code, out, _ = run_cli(["render", "deliver", "--help"])
    assert code == 0 and "overshoot" in out and "default 5%" in out
    assert "19%" not in out and "default 20%" not in out
