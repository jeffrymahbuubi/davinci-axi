"""`rowfile.read_rows`: JSON and CSV row files for per-item batch verbs (D, R-D1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from davinci_axi.commands.rowfile import read_rows
from davinci_axi.errors import AxiError


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _error(path: Path, **kwargs: object) -> AxiError:
    with pytest.raises(AxiError) as caught:
        read_rows(path, **kwargs)  # type: ignore[arg-type]
    return caught.value


def test_json_numbers_and_bools_become_strings(tmp_path: Path) -> None:
    path = _write(tmp_path, "r.json", '[{"item": "V1:1", "ZoomX": 1.25, "Tilt": -200, "G": true}]')
    assert read_rows(path) == [{"item": "V1:1", "ZoomX": "1.25", "Tilt": "-200", "G": "true"}]


def test_json_null_and_blank_values_are_dropped(tmp_path: Path) -> None:
    path = _write(tmp_path, "r.json", '[{"item": "V1:1", "ZoomX": null, "Tilt": "  "}]')
    assert read_rows(path) == [{"item": "V1:1"}]


def test_csv_empty_cell_is_dropped_and_values_stripped(tmp_path: Path) -> None:
    path = _write(tmp_path, "r.csv", "﻿item, ZoomX ,Tilt\nV1:1, 1.25 ,-200\nV1:3,1.1,\n")
    assert read_rows(path) == [
        {"item": "V1:1", "ZoomX": "1.25", "Tilt": "-200"},
        {"item": "V1:3", "ZoomX": "1.1"},
    ]


def test_missing_file_is_not_found(tmp_path: Path) -> None:
    assert _error(tmp_path / "nope.csv").code == "NOT_FOUND"


@pytest.mark.parametrize("name", ["rows.txt", "rows"])
def test_other_suffix_is_a_validation_error(tmp_path: Path, name: str) -> None:
    err = _error(_write(tmp_path, name, "item\nV1:1\n"))
    assert err.code == "VALIDATION_ERROR" and "use .json or .csv" in err.message


def test_duplicate_item_names_both_rows(tmp_path: Path) -> None:
    err = _error(_write(tmp_path, "r.csv", "item,ZoomX\nV1:1,1\nV1:1,2\n"))
    assert err.code == "VALIDATION_ERROR" and "row 2" in err.message and "row 1" in err.message


def test_missing_item_names_the_row(tmp_path: Path) -> None:
    err = _error(_write(tmp_path, "r.csv", "item,ZoomX\nV1:1,1\n,2\n"))
    assert err.message == "row 2: item is required"


def test_custom_key_column(tmp_path: Path) -> None:
    path = _write(tmp_path, "r.json", '[{"clip": "a", "x": 1}]')
    assert read_rows(path, key="clip") == [{"clip": "a", "x": "1"}]
    assert _error(path).message == "row 1: item is required"


def test_unknown_column_lists_the_allowed_ones(tmp_path: Path) -> None:
    path = _write(tmp_path, "r.csv", "item,Gain\nV1:1,3\n")
    err = _error(path, allowed=("item", "Slope"))
    assert "row 1" in err.message and "Gain" in err.message
    assert any("item, Slope" in line for line in err.help)


def test_json_must_be_an_array_of_objects(tmp_path: Path) -> None:
    assert "JSON array" in _error(_write(tmp_path, "a.json", '{"item": "V1:1"}')).message
    assert "not valid JSON" in _error(_write(tmp_path, "b.json", "[")).message
    assert "row 1" in _error(_write(tmp_path, "c.json", '["V1:1"]')).message
    assert "row 1" in _error(_write(tmp_path, "d.json", '[{"item": "V1:1", "a": [1]}]')).message


def test_empty_file_and_ragged_csv_row(tmp_path: Path) -> None:
    assert "no rows" in _error(_write(tmp_path, "e.csv", "item,ZoomX\n")).message
    assert "no rows" in _error(_write(tmp_path, "f.json", "[]")).message
    ragged = _error(_write(tmp_path, "g.csv", "item,ZoomX\nV1:1,1,9\n"))
    assert "row 1" in ragged.message
