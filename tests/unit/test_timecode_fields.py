"""Pure unit tests for the timecode field helpers in `commands/timeline_tracks.py` (F26b)."""

from __future__ import annotations

import pytest

from davinci_axi.commands.timeline_tracks import check_timecode_fields, nominal_fps
from davinci_axi.errors import AxiError

DF_2997 = {"timelineFrameRate": 29.97, "timelineDropFrameTimecode": "1"}


def _fails(code: str, settings: dict[str, object], needle: str) -> None:
    with pytest.raises(AxiError) as info:
        check_timecode_fields(code, settings)
    assert info.value.code == "VALIDATION_ERROR"
    assert needle in info.value.message


@pytest.mark.parametrize(
    ("rate", "expected"),
    [(29.97, 30), ("23.976", 24), (25, 25), (59.94, 60), (47.952, 48), (24.0, 24), ("bad", 24)],
)
def test_nominal_fps(rate: object, expected: int) -> None:
    assert nominal_fps(rate) == expected


def test_frame_out_of_range() -> None:
    _fails("01:00:11:30", {"timelineFrameRate": 30.0}, "00-29")


def test_frame_in_range_passes() -> None:
    check_timecode_fields("01:00:11:29", {"timelineFrameRate": 30.0})


def test_frame_rate_defaults_to_24() -> None:
    _fails("01:00:00:24", {}, "00-23")


@pytest.mark.parametrize("code", ["24:00:00:00", "01:60:00:00", "01:00:60:00"])
def test_other_fields_out_of_range(code: str) -> None:
    _fails(code, {"timelineFrameRate": 30.0}, "out of range")


def test_dropped_frame_rejected() -> None:
    _fails("01:01:00;01", DF_2997, "dropped frame")


@pytest.mark.parametrize("code", ["01:10:00;01", "01:01:00;02", "01:01:01;00"])
def test_drop_frame_valid_codes_pass(code: str) -> None:
    check_timecode_fields(code, DF_2997)


def test_dropped_frame_by_semicolon_alone() -> None:
    _fails("01:01:00;00", {"timelineFrameRate": 29.97}, "dropped frame")


def test_dropped_frame_60() -> None:
    settings = {"timelineFrameRate": 59.94, "timelineDropFrameTimecode": "1"}
    _fails("01:01:00:03", settings, "dropped frame")
    check_timecode_fields("01:01:00:04", settings)


def test_semicolon_on_24_fps_passes() -> None:
    check_timecode_fields("01:00:02;00", {"timelineFrameRate": 24.0})
    check_timecode_fields("01:00:02;00", {})
