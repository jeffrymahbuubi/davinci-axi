"""`color lut install` and `lut_path_warning` (F21) on the fake; the LUT folder is a tmp dir."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands.color_extras import lut_path_warning
from davinci_axi.fake import FakeWorld, ext_color

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]
REFRESH = "Project.RefreshLUTList"


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    ext_color.extend()
    monkeypatch.setitem(registry.COMMANDS, "color", "davinci_axi.commands.color:SPEC")
    monkeypatch.setenv("DAVINCI_AXI_LUT_DIRS", str(tmp_path / "LUT"))
    yield


def _src(tmp_path: Path, text: str = "LUT_3D_SIZE 2\n") -> Path:
    src = tmp_path / "src" / "film.cube"
    src.parent.mkdir(exist_ok=True)
    src.write_text(text, encoding="utf-8")
    return src


def test_dry_run_changes_nothing_then_force_copies_and_refreshes(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    src = _src(tmp_path)
    argv = ["color", "lut", "install", str(src), "--subdir", "my-project"]
    code, out, err = run_cli(argv)
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "") and keys["applied"] == "false"
    assert keys["lut_arg"] == "my-project/film.cube"
    assert "installed" in out and "called" in out and "changes[2]" in out
    assert not (tmp_path / "LUT").exists()
    assert fake_world.write_calls() == [] and fake_world.calls_to(REFRESH) == []
    code, out, err = run_cli([*argv, "--force"])
    assert (code, err) == (0, "") and parse_toon_keys(out)["applied"] == "true", out
    dest = tmp_path / "LUT" / "my-project" / "film.cube"
    assert dest.read_bytes() == src.read_bytes()
    assert len(fake_world.calls_to(REFRESH)) == 1
    assert not (tmp_path / "axi-home" / "snapshots").exists()
    assert str(dest) in out


def test_installed_file_passes_the_lut_warning(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    src = _src(tmp_path)
    assert run_cli(["color", "lut", "install", str(src), "--force"])[0] == 0
    dest = tmp_path / "LUT" / "film.cube"
    assert dest.is_file()
    code, out, _ = run_cli(["color", "node", "lut", "set", "V1:1", "1", str(dest)])
    assert code == 0 and "outside Resolve's LUT folders" not in out


def test_existing_destination_needs_overwrite(run_cli: Run, tmp_path: Path) -> None:
    src = _src(tmp_path)
    argv = ["color", "lut", "install", str(src), "--force"]
    assert run_cli(argv)[0] == 0
    code, out, _ = run_cli(argv)
    assert code == 1 and "FILE_EXISTS" in out
    src.write_text("LUT_3D_SIZE 3\n", encoding="utf-8")
    assert run_cli([*argv, "--overwrite"])[0] == 0
    assert (tmp_path / "LUT" / "film.cube").read_text(encoding="utf-8") == "LUT_3D_SIZE 3\n"


@pytest.mark.parametrize("subdir", ["../x", "..", ".", "a/b", ""])
def test_bad_subdir_is_a_validation_error(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, subdir: str
) -> None:
    src = _src(tmp_path)
    code, out, _ = run_cli(["color", "lut", "install", str(src), "--subdir", subdir, "--force"])
    if subdir == "":
        assert code == 0  # an empty value means no subfolder
        return
    assert code == 2 and "VALIDATION_ERROR" in out
    assert fake_world.calls == [] and not (tmp_path / "LUT").exists()


def test_missing_source_is_not_found(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    code, out, _ = run_cli(["color", "lut", "install", str(tmp_path / "nope.cube"), "--force"])
    assert code == 1 and "NOT_FOUND" in out and fake_world.calls == []


def test_refused_refresh_is_api_failed_but_the_file_is_there(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    src = _src(tmp_path)
    fake_world.inject(REFRESH, False)
    code, out, _ = run_cli(["color", "lut", "install", str(src), "--force"])
    assert code == 1 and "API_FAILED" in out and "copied to" in out
    assert (tmp_path / "LUT" / "film.cube").is_file()


def test_lut_path_warning(tmp_path: Path) -> None:
    inside = tmp_path / "LUT" / "sub" / "a.cube"
    assert lut_path_warning(inside) == ""
    warning = lut_path_warning(tmp_path / "elsewhere" / "a.cube")
    assert str(tmp_path / "LUT") in warning and "color lut install" in warning
