"""`ai_state`: the remembered last error of each AI verb (F11 U2)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from davinci_axi import ai_state
from davinci_axi.errors import AxiError


def _home(tmp_path: Path) -> Path:
    return tmp_path / "axi-home"


def test_record_load_forget_round_trip() -> None:
    assert ai_state.load() == {}
    ai_state.record("ai slate", AxiError("NOT_AVAILABLE", "no slate"), "Proj")
    ai_state.record("audio speech", AxiError("API_FAILED", "boom"), None)
    loaded = ai_state.load()
    assert set(loaded) == {"ai slate", "audio speech"}
    slate = loaded["ai slate"]
    assert (slate.command, slate.code, slate.message, slate.project) == (
        "ai slate",
        "NOT_AVAILABLE",
        "no slate",
        "Proj",
    )
    assert loaded["audio speech"].project is None
    assert ai_state.label(slate) == f"NOT_AVAILABLE at {slate.at}"
    ai_state.forget("ai slate")
    assert set(ai_state.load()) == {"audio speech"}
    ai_state.forget("never recorded")  # no error
    assert ai_state.clear() == 1
    assert ai_state.load() == {}
    assert ai_state.clear() == 0


def test_at_is_a_minute_stamp() -> None:
    ai_state.record("ai slate", AxiError("NOT_AVAILABLE", "x"), None)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", ai_state.load()["ai slate"].at)


def test_a_newer_error_replaces_the_older_one() -> None:
    ai_state.record("ai slate", AxiError("NOT_AVAILABLE", "first"), None)
    ai_state.record("ai slate", AxiError("JOB_FAILED", "second"), None)
    assert [e.message for e in ai_state.load().values()] == ["second"]


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"ai slate": "text"}', ""])
def test_corrupt_file_loads_as_empty_and_is_overwritten(tmp_path: Path, content: str) -> None:
    home = _home(tmp_path)
    home.mkdir(parents=True)
    (home / ai_state.STATE_FILE).write_text(content, encoding="utf-8")
    assert ai_state.load() == {}
    ai_state.record("ai slate", AxiError("NOT_AVAILABLE", "x"), None)
    assert set(ai_state.load()) == {"ai slate"}


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_home_does_not_raise(tmp_path: Path) -> None:
    home = _home(tmp_path)
    home.mkdir(parents=True)
    home.chmod(0o500)
    try:
        ai_state.record("ai slate", AxiError("NOT_AVAILABLE", "x"), None)
        ai_state.forget("ai slate")
        assert ai_state.load() == {}
    finally:
        home.chmod(0o700)
