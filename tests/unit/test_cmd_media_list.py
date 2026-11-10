"""`media` list view: property fields (F2) and `--sort` (F4), fake-backed."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

import pytest

from davinci_axi.commands.media_list import parse_date_created
from davinci_axi.fake import FakeWorld
from davinci_axi.fake.media import FakeMediaPoolItem

Run = Callable[[list[str]], tuple[int, str, str]]

GET_PROP = "MediaPoolItem.GetClipProperty"


def _clips(world: FakeWorld) -> dict[str, FakeMediaPoolItem]:
    project = world.resolve.pm.current_project
    assert project is not None
    root = project.media_pool.root
    return {c.name: c for f in [root, *root.subfolders] for c in f.clips}


def _names(out: str) -> list[str]:
    """Clip names in row order, read from the `name` column of the clips table."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("clips["))
    header = re.search(r"\{(.*)\}", lines[start])
    assert header
    col = header.group(1).split(",").index("name")
    rows = []
    for line in lines[start + 1 :]:
        if not line.startswith("  "):
            break
        rows.append(line.strip().split(",")[col])
    return rows


def _list(run_cli: Run, *argv: str) -> tuple[str, list[str]]:
    code, out, err = run_cli(["media", "--fields", "name", *argv])
    assert (code, err) == (0, ""), out
    return out, _names(out)


def test_default_order_is_by_name(run_cli: Run, fake_world: FakeWorld) -> None:
    out, names = _list(run_cli)
    assert names == ["A001_beach", "A002_city", "A003_dusk", "music_bed"]
    assert "\nsort: name\n" in out


def test_name_sort_is_case_insensitive(run_cli: Run, fake_world: FakeWorld) -> None:
    clips = _clips(fake_world)
    clips["music_bed"].name = "Zed"
    clips["A003_dusk"].name = "alpha"
    assert _list(run_cli)[1] == ["A001_beach", "A002_city", "alpha", "Zed"]


def test_sort_pool_keeps_walk_order(run_cli: Run, fake_world: FakeWorld) -> None:
    out, names = _list(run_cli, "--sort", "pool")
    assert names[0] == "music_bed" and "\nsort: pool\n" in out


def test_sort_by_date_created_and_duration(run_cli: Run, fake_world: FakeWorld) -> None:
    assert _list(run_cli, "--sort", "date_created")[1] == [
        "A002_city",
        "A003_dusk",
        "A001_beach",
        "music_bed",
    ]
    assert _list(run_cli, "--sort", "duration")[1] == [
        "A001_beach",
        "A003_dusk",
        "A002_city",
        "music_bed",
    ]


def test_sort_handles_missing_and_unparseable_dates_last(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    clips = _clips(fake_world)
    clips["A002_city"].properties["Date Created"] = ""
    clips["A003_dusk"].properties["Date Created"] = "garbage"
    # Pool order is music_bed, A001, A002, A003: the two bad dates keep that order at the end.
    assert _list(run_cli, "--sort", "date_created")[1] == [
        "A001_beach",
        "music_bed",
        "A002_city",
        "A003_dusk",
    ]


def test_sort_by_real_resolve_date_format_orders_one_and_two_digit_days(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    """Resolve 21.1.1 writes `Sun Nov 8 2026 11:42:37` (year before time, day not padded)."""
    clips = _clips(fake_world)
    clips["A001_beach"].properties["Date Created"] = "Wed Nov 18 2026 09:00:00"
    clips["A002_city"].properties["Date Created"] = "Sun Nov 8 2026 11:42:37"
    clips["A003_dusk"].properties["Date Created"] = "Sun Nov 8 2026 09:15:00"
    clips["music_bed"].properties["Date Created"] = "Sat Oct 31 2026 23:59:59"
    # A plain string sort would put "Wed Nov 18" and "Sat Oct 31" before the Nov 8 ones.
    assert _list(run_cli, "--sort", "date_created")[1] == [
        "music_bed",
        "A003_dusk",
        "A002_city",
        "A001_beach",
    ]


def test_date_parser_accepts_real_ctime_and_iso_forms() -> None:
    assert parse_date_created("Sun Nov 8 2026 11:42:37") == datetime(2026, 11, 8, 11, 42, 37)
    assert parse_date_created("Sat Nov  1 10:00:00 2025") == datetime(2025, 11, 1, 10)
    assert parse_date_created("2026-11-08 11:42:37") == datetime(2026, 11, 8, 11, 42, 37)
    assert parse_date_created("2026-11-08T11:42:37") == datetime(2026, 11, 8, 11, 42, 37)
    assert parse_date_created("") is None and parse_date_created(None) is None
    assert parse_date_created("garbage") is None


def test_sort_rejects_unknown_value_before_connecting(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["media", "--sort", "size"])
    assert code == 2
    assert "name, date_created, duration, pool" in out
    assert fake_world.calls == []


def test_new_fields_come_from_clip_properties(run_cli: Run, fake_world: FakeWorld) -> None:
    fields = "name,resolution,video_codec,frames,date_created,audio_ch"
    code, out, err = run_cli(["media", "--fields", fields])
    assert (code, err) == (0, "")
    assert f"clips[4]{{{fields}}}:" in out
    assert '  A002_city,1080x1920,H.264,192,"Sat Nov 1 2025 09:00:00","2"' in out
    assert "  music_bed,null,null,1440," in out
    assert len(fake_world.calls_to(GET_PROP)) == 4


def test_default_sort_fetches_properties_only_for_shown_rows(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    assert run_cli(["media", "--limit", "2"])[0] == 0
    assert len(fake_world.calls_to(GET_PROP)) == 2
    fake_world.calls.clear()
    assert run_cli(["media", "--limit", "2", "--sort", "duration"])[0] == 0
    assert 4 <= len(fake_world.calls_to(GET_PROP)) <= 6


def test_sort_applies_before_limit(run_cli: Run, fake_world: FakeWorld) -> None:
    assert _list(run_cli, "--limit", "1")[1] == ["A001_beach"]
    assert _list(run_cli, "--limit", "1", "--sort", "pool")[1] == ["music_bed"]


@pytest.mark.parametrize("sort", ["name", "date_created", "duration", "pool"])
def test_every_sort_is_read_only(run_cli: Run, fake_world: FakeWorld, sort: str) -> None:
    assert run_cli(["media", "--sort", sort])[0] == 0
    assert fake_world.write_calls() == [] and fake_world.page_changes() == []
