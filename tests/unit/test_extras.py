"""The AI Extras probe (F11 U1): the real dpl1 sample, the tri-state status, the directory rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from davinci_axi import extras, paths
from davinci_axi.fake.ext_extras import write_package

DATA = Path(__file__).resolve().parents[1] / "data" / "extras"
SAMPLE = DATA / ("4-" + "B" * 41) / "log.dpl1"


def test_real_sample_parses_to_ai_motion_deblur() -> None:
    assert extras.package_name(SAMPLE) == "AI Motion Deblur"
    report = extras.scan(DATA)
    assert report.names() == ["AI Motion Deblur"]
    assert report.status("AI Motion Deblur") == "installed"
    assert report.status("AI Slate ID") == "missing"
    assert report.summary() == "1 installed (AI Motion Deblur)"


def test_missing_dir_is_unknown(tmp_path: Path) -> None:
    report = extras.scan(tmp_path / "nope")
    assert report.status("AI Slate ID") == "unknown"
    assert "not found" in report.summary()


def test_no_directory_at_all_is_unknown() -> None:
    assert extras.Report(None, False, ()).status("AI Slate ID") == "unknown"


def test_empty_directory_is_missing(tmp_path: Path) -> None:
    report = extras.scan(tmp_path)
    assert report.status("AI Slate ID") == "missing"
    assert report.summary() == "none installed"


def test_unnamed_package_makes_absent_names_unknown(tmp_path: Path) -> None:
    write_package(tmp_path, "AI Slate ID")
    bad = tmp_path / "garbage"
    bad.mkdir()
    (bad / "log.dpl1").write_bytes(b"not a log at all")
    report = extras.scan(tmp_path)
    assert report.status("AI Slate ID") == "installed"
    assert report.status("AI Speech Generator") == "unknown"
    assert report.unnamed() == 1


def test_package_directory_without_a_log_is_unnamed(tmp_path: Path) -> None:
    (tmp_path / "empty-package").mkdir()
    assert extras.scan(tmp_path).status("AI Slate ID") == "unknown"


@pytest.mark.parametrize("content", [b"xxxxyyyy", b"", b"1234567", b"4\0\0\0dpl1"])
def test_bad_magic_and_truncated_logs_return_none(tmp_path: Path, content: bytes) -> None:
    log = tmp_path / "log.dpl1"
    log.write_bytes(content)
    assert extras.package_name(log) is None


def test_unreadable_log_returns_none(tmp_path: Path) -> None:
    assert extras.package_name(tmp_path / "absent.dpl1") is None


def test_extras_dir_env_and_fake_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DAVINCI_AXI_EXTRAS_DIR", raising=False)
    assert paths.extras_dir() == paths.axi_home() / "extras"
    monkeypatch.setenv("DAVINCI_AXI_EXTRAS_DIR", str(tmp_path / "elsewhere"))
    assert paths.extras_dir() == tmp_path / "elsewhere"


def test_scan_defaults_to_the_configured_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write_package(tmp_path, "AI Speech Generator")
    monkeypatch.setenv("DAVINCI_AXI_EXTRAS_DIR", str(tmp_path))
    check = extras.check("AI Speech Generator")
    assert (check.package, check.status) == ("AI Speech Generator", "installed")
    assert extras.check("AI Slate ID").status == "missing"


def test_fixture_roundtrip(tmp_path: Path) -> None:
    directory = write_package(tmp_path, "AI Speech Generator")
    assert extras.package_name(directory / "log.dpl1") == "AI Speech Generator"
    assert directory.name == "fake-ai-speech-generator"
    custom = write_package(tmp_path, "AI Slate ID", "pkg-7")
    assert custom.name == "pkg-7"
    assert extras.scan(tmp_path).names() == ["AI Slate ID", "AI Speech Generator"]


def test_install_hint_names_the_menu_and_package() -> None:
    hint = extras.install_hint("AI Slate ID")
    assert "Extras Download Manager" in hint
    assert "'AI Slate ID'" in hint
