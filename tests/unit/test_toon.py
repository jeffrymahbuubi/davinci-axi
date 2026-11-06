from __future__ import annotations

import pytest

from davinci_axi.toon import encode


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("hello world", "k: hello world"),
        ("05", 'k: "05"'),
        ("1e6", 'k: "1e6"'),
        ("true", 'k: "true"'),
        ("null", 'k: "null"'),
        ("-x", 'k: "-x"'),
        ("#tag", 'k: "#tag"'),
        ("a,b", 'k: "a,b"'),
        ("x:y", 'k: "x:y"'),
        ("", 'k: ""'),
        (" padded", 'k: " padded"'),
        ("a\nb", 'k: "a\\nb"'),
        ('say "hi"', 'k: "say \\"hi\\""'),
        ("tab\there", 'k: "tab\\there"'),
        ("caf\u00e9 ok", "k: caf\u00e9 ok"),
        (True, "k: true"),
        (False, "k: false"),
        (None, "k: null"),
        (3, "k: 3"),
        (1.0, "k: 1"),
        (1.5, "k: 1.5"),
        (-0.0, "k: 0"),
        (float("nan"), "k: null"),
        (float("inf"), "k: null"),
        (1e-7, "k: 0.0000001"),
    ],
)
def test_scalar_rendering(value: object, expected: str) -> None:
    assert encode({"k": value}) == expected


def test_key_quoting() -> None:
    assert encode({"my-key": 1, "ok_key.1": 2}) == '"my-key": 1\nok_key.1: 2'


def test_nested_object_indentation() -> None:
    out = encode({"a": {"b": {"c": 1}, "d": 2}, "e": 3})
    assert out == "a:\n  b:\n    c: 1\n  d: 2\ne: 3"


def test_empty_object_and_array() -> None:
    assert encode({"a": {}, "b": []}) == "a:\nb: []"


def test_primitive_array_inline() -> None:
    assert encode({"tags": ["a", "b", "c"]}) == "tags[3]: a,b,c"
    assert encode({"n": [1, 2.5, True, None]}) == "n[4]: 1,2.5,true,null"


def test_tabular_array_for_uniform_rows() -> None:
    rows = [{"name": "a", "n": 1}, {"name": "b,c", "n": 2}]
    assert encode({"rows": rows}) == 'rows[2]{name,n}:\n  a,1\n  "b,c",2'


def test_list_form_when_keys_mismatch() -> None:
    out = encode({"rows": [{"a": 1, "b": 2}, {"a": 3, "c": 4}]})
    assert out == "rows[2]:\n  - a: 1\n    b: 2\n  - a: 3\n    c: 4"


def test_list_form_when_row_has_nested_value() -> None:
    out = encode({"rows": [{"a": 1, "tags": ["x"]}, {"a": 2, "tags": []}]})
    assert out.startswith("rows[2]:\n  - a: 1\n    tags[1]: x")
    assert "    tags: []" in out


def test_help_uses_list_form_and_hints_stay_prose() -> None:
    out = encode({"help": ["Run `davinci-axi items` to list items", "second"]})
    assert out == "help[2]:\n  - Run `davinci-axi items` to list items\n  - second"


def test_list_keys_override() -> None:
    assert encode({"examples": ["a", "b"]}, list_keys=frozenset({"examples"})) == (
        "examples[2]:\n  - a\n  - b"
    )


def test_mixed_list_items() -> None:
    out = encode({"mix": [1, {"a": 1}, [2, 3]]})
    assert out == "mix[3]:\n  - 1\n  - a: 1\n  - [2]: 2,3"


def test_no_trailing_newline_and_lf_only() -> None:
    out = encode({"a": 1, "b": {"c": [1, 2]}})
    assert not out.endswith("\n")
    assert "\r" not in out


def test_root_array_and_scalar() -> None:
    assert encode([1, 2]) == "[2]: 1,2"
    assert encode([]) == "[0]:"
    assert encode("x") == "x"


def test_unpaired_surrogate_is_an_error() -> None:
    with pytest.raises(ValueError, match="surrogate"):
        encode({"k": "\ud800"})


def test_control_character_escape() -> None:
    assert encode({"k": "a\x01b"}) == 'k: "a\\u0001b"'
