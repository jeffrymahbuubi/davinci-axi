"""F47b: the pure parts of the `timeline title add` style flags."""

from __future__ import annotations

import pytest

from davinci_axi.commands.timeline_title_style import (
    COLORS,
    STYLE_FLAGS,
    Style,
    check_style_flags,
    resolve_style,
    style_inputs,
    style_summary,
    style_words,
)
from davinci_axi.errors import AxiError
from davinci_axi.spec import GlobalOpts, Invocation


def _inv(**args: object) -> Invocation:
    def no_connect() -> None:
        raise AssertionError("the style checks must not connect")

    return Invocation(GlobalOpts(), dict(args), {}, no_connect)  # type: ignore[arg-type]


def _fail(**args: object) -> AxiError:
    with pytest.raises(AxiError) as caught:
        check_style_flags(_inv(**args))
    assert caught.value.code == "VALIDATION_ERROR"
    return caught.value


def test_the_four_flags_are_declared() -> None:
    assert [f.name for f in STYLE_FLAGS] == ["size", "font", "color", "y"]


def test_no_flags_check_to_nothing() -> None:
    assert check_style_flags(_inv(size=None, font=None, color=None, y=None)) == {}


@pytest.mark.parametrize("size", [0.0, -0.1, 1.5, float("nan")])
def test_size_outside_zero_to_one_is_refused_with_a_valid_example(size: float) -> None:
    err = _fail(size=size)
    assert "--size" in err.message and "Valid: --size 0.12" in err.help[0]


@pytest.mark.parametrize("y", [-0.1, 1.5, float("inf")])
def test_y_outside_zero_to_one_is_refused_with_a_valid_example(y: float) -> None:
    err = _fail(y=y)
    assert "--y" in err.message and "Valid: --y 0.15" in err.help[0]


def test_the_bounds_themselves_pass() -> None:
    assert check_style_flags(_inv(size=1.0, y=0.0)) == {"size": 1.0, "y": 0.0}


@pytest.mark.parametrize("color", ["teal", "#fff", "#ggg000", "ffd400", ""])
def test_a_bad_color_is_refused_with_a_valid_example(color: str) -> None:
    err = _fail(color=color)
    assert "--color" in err.message and "Valid: --color '#ffd400'" in err.help[0]


@pytest.mark.parametrize("font", ["", "  ", "x" * 101])
def test_a_bad_font_is_refused(font: str) -> None:
    assert "--font" in _fail(font=font).message


def test_hex_and_name_give_the_same_color() -> None:
    upper = check_style_flags(_inv(color="#FFD400"))["color"]
    assert upper == (1.0, 0.831, 0.0)
    assert check_style_flags(_inv(color="#ffd400"))["color"] == upper
    assert check_style_flags(_inv(color="yellow"))["color"] == (1.0, 1.0, 0.0)
    assert check_style_flags(_inv(color="Yellow"))["color"] == (1.0, 1.0, 0.0)
    assert set(COLORS) == {"white", "black", "red", "green", "blue", "yellow"}


def test_landscape_keeps_fusions_default_and_portrait_scales_it() -> None:
    assert resolve_style({}, 1920, 1080).size is None
    assert resolve_style({}, 1080, 1080).size is None
    portrait = resolve_style({}, 1080, 1920)
    assert portrait.size == 0.142 and portrait.size_auto is True


def test_an_explicit_size_wins_and_is_not_auto() -> None:
    style = resolve_style({"size": 0.2}, 1080, 1920)
    assert style.size == 0.2 and style.size_auto is False
    assert resolve_style({"size": 0.2}, 1920, 1080).size == 0.2


def test_inputs_come_in_order_with_a_centre_table() -> None:
    style = resolve_style(
        check_style_flags(_inv(size=0.12, font="Open Sans", color="yellow", y=0.2)), 1920, 1080
    )
    assert style_inputs(style) == [
        ("Size", 0.12),
        ("Font", "Open Sans"),
        ("Red1", 1.0),
        ("Green1", 1.0),
        ("Blue1", 0.0),
        ("Center", {1: 0.5, 2: 0.2}),
    ]
    assert style_inputs(Style(None, False, None, None, None)) == []


def test_words_round_trip_the_flags_the_user_passed() -> None:
    inv = _inv(size=0.12, font="Open Sans", color="#ffd400", y=0.2)
    assert style_words(inv) == [
        "--size",
        "0.12",
        "--font",
        "Open Sans",
        "--color",
        "#ffd400",
        "--y",
        "0.2",
    ]
    assert style_words(_inv(size=None, font=None, color=None, y=None)) == []


def test_the_summary_names_the_size_source_and_omits_absent_flags() -> None:
    auto = style_summary(resolve_style({}, 1080, 1920), 1080, 1920)
    assert auto == {"size": 0.142, "size_source": "auto (1080x1920)"}
    flagged = resolve_style(check_style_flags(_inv(size=0.1, color="#ffd400", y=0.3)), 1920, 1080)
    assert style_summary(flagged, 1920, 1080) == {
        "size": 0.1,
        "size_source": "flag",
        "color": "#ffd400",
        "y": 0.3,
    }
    assert style_summary(Style(None, False, None, None, None)) == {}
