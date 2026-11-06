"""Smoke tests against a real Resolve with `davinci-axi-test` open. Run: `uv run pytest --live`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from davinci_axi.session import Session

Run = Callable[[list[str]], tuple[int, str, str]]

READ_COMMANDS: list[list[str]] = [
    [],
    ["projects"],
    ["timelines"],
    ["items"],
    ["render", "status"],
    ["render", "presets"],
]


@pytest.mark.live
@pytest.mark.parametrize("argv", READ_COMMANDS, ids=lambda argv: " ".join(argv) or "home")
def test_read_command_exits_zero(live_session: Session, run_cli: Run, argv: list[str]) -> None:
    code, out, _ = run_cli(argv)
    assert code == 0, out


@pytest.mark.live
def test_project_snapshot_writes_into_tmp_path(
    live_session: Session, run_cli: Run, tmp_path: Path
) -> None:
    out_dir = tmp_path / "snapshot"
    code, out, _ = run_cli(["project", "snapshot", "--out", str(out_dir), "--reason", "live-test"])
    assert code == 0, out
    assert out_dir.exists()
