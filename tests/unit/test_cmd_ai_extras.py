"""`ai extras` and the `app show` ai_extras field (F11 U1). Called directly: the spec is spliced
into `ai.py` by the lead afterwards."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi.commands.ai_extras import EXTRAS_SPEC, run_extras
from davinci_axi.fake import FakeWorld
from davinci_axi.fake.ext_extras import write_package
from davinci_axi.output import render
from davinci_axi.session import Session
from davinci_axi.spec import GlobalOpts, Invocation, Result

Run = Callable[[list[str]], tuple[int, str, str]]


def _no_resolve() -> Session:
    raise AssertionError("ai extras must not connect to Resolve")


def _run(*, as_json: bool = False) -> Result:
    inv = Invocation(GlobalOpts(json=as_json), {}, {}, _no_resolve)
    return run_extras(inv)


def _rows(result: Result, key: str) -> dict[str, dict[str, object]]:
    rows = result.data[key]
    assert isinstance(rows, list)
    label = "command" if key == "features" else "name"
    return {str(r[label]): r for r in rows}


def test_unknown_by_default(fake_world: FakeWorld) -> None:
    result = _run()
    assert result.exit_code == 0
    assert result.data["status"] == "not found"
    assert result.data["installed"] == 0
    assert "unnamed" not in result.data
    assert {r["status"] for r in _rows(result, "features").values()} == {"unknown"}
    assert any("DAVINCI_AXI_EXTRAS_DIR" in line for line in result.help)
    assert fake_world.calls == []


def test_seeded_packages_are_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    write_package(tmp_path, "AI Motion Deblur")
    write_package(tmp_path, "AI Slate ID")
    monkeypatch.setenv("DAVINCI_AXI_EXTRAS_DIR", str(tmp_path))
    result = _run()
    assert result.data["status"] == "readable"
    assert result.data["installed"] == 2
    features = _rows(result, "features")
    assert features["ai deblur"]["status"] == "installed"
    assert features["ai slate"]["status"] == "installed"
    assert features["audio speech"]["status"] == "missing"
    assert features["audio transcribe"]["status"] == "optional"
    assert features["ai subtitles"]["status"] == "optional"
    packages = _rows(result, "packages")
    assert len(packages) == 8
    assert packages["AI Motion Deblur"]["installed"] is True
    assert packages["AI Voice Training"]["installed"] is False


def test_unnamed_directory_is_counted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "mystery").mkdir()
    monkeypatch.setenv("DAVINCI_AXI_EXTRAS_DIR", str(tmp_path))
    result = _run()
    assert result.data["unnamed"] == 1
    assert _rows(result, "features")["ai slate"]["status"] == "unknown"


def test_unlisted_package_is_flagged_only_under_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write_package(tmp_path, "AI Brand New Thing")
    monkeypatch.setenv("DAVINCI_AXI_EXTRAS_DIR", str(tmp_path))
    assert len(_rows(_run(), "packages")) == 8
    extra = _rows(_run(as_json=True), "packages")["AI Brand New Thing"]
    assert extra["known"] is False


def test_unreadable_directory_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("x")
    monkeypatch.setenv("DAVINCI_AXI_EXTRAS_DIR", str(not_a_dir))
    assert _run().data["status"] == "unreadable"


def test_spec_is_read_only_and_makes_no_resolve_call() -> None:
    assert EXTRAS_SPEC.name == "extras"
    assert EXTRAS_SPEC.reads_only is True
    assert EXTRAS_SPEC.methods == ()
    result = _run()
    assert "audio transcribe" in render(result.data, result.help, as_json=False)


def test_app_show_carries_ai_extras(
    run_cli: Run,
    parse_toon_keys: Callable[[str], dict[str, str]],
    fake_world: FakeWorld,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    code, out, _ = run_cli(["app", "show"])
    assert code == 0
    assert parse_toon_keys(out)["ai_extras"].startswith("unknown (")
    assert "davinci-axi ai extras" in out
    write_package(tmp_path, "AI Motion Deblur")
    monkeypatch.setenv("DAVINCI_AXI_EXTRAS_DIR", str(tmp_path))
    code, out, _ = run_cli(["app", "show", "--json"])
    assert code == 0
    assert json.loads(out)["ai_extras"] == "1 installed (AI Motion Deblur)"
