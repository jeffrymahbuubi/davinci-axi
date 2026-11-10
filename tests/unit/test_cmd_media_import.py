"""`media import` output: count and sample in the dry run, K/N result and a failed list (F5)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.fake import FakeWorld, ext_writes_core
from davinci_axi.fake import ext_media_edit as ext

Run = Callable[[list[str]], tuple[int, str, str]]


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_writes_core.extend()
    ext.extend()
    monkeypatch.setitem(registry.COMMANDS, "media", "davinci_axi.commands.media:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "snapshot", "davinci_axi.commands.snapshot:SPEC")
    yield


def _keys(out: str) -> dict[str, str]:
    found = (re.match(r"^([A-Za-z_][\w.-]*): (.*)$", line) for line in out.splitlines())
    return {m.group(1): m.group(2) for m in found if m}


def _files(tmp_path: Path, *names: str) -> list[Path]:
    paths = [tmp_path / n for n in names]
    for path in paths:
        path.write_text("x", encoding="utf-8")
    return paths


def _arg(paths: list[Path]) -> str:
    return ",".join(str(p) for p in paths)


def test_dry_run_shows_count_and_sample_not_every_name(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    files = _files(tmp_path, "a.mov", "b.mov", "c.mov", "d.mov", "e.mov")
    code, out, err = run_cli(["media", "import", _arg(files)])
    keys = _keys(out)
    assert (code, err) == (0, "") and keys["applied"] == "false"
    assert keys["files"] == "5"
    assert keys["sample"] == '"a.mov, b.mov, c.mov, +2 more"'
    # the re-run command in `help` must still carry every path; no other line names d or e
    body = "\n".join(line for line in out.splitlines() if "--force" not in line)
    assert "d.mov" not in body and "e.mov" not in body
    assert fake_world.write_calls() == []


def test_dry_run_with_few_files_has_no_more_suffix(run_cli: Run, tmp_path: Path) -> None:
    files = _files(tmp_path, "a.mov", "b.mov", "c.mov")
    code, out, _ = run_cli(["media", "import", _arg(files)])
    assert code == 0 and _keys(out)["sample"] == '"a.mov, b.mov, c.mov"'


def test_dry_run_json_has_the_full_file_list(run_cli: Run, tmp_path: Path) -> None:
    names = ["a.mov", "b.mov", "c.mov", "d.mov", "e.mov"]
    files = _files(tmp_path, *names)
    code, out, _ = run_cli(["--json", "media", "import", _arg(files)])
    data = json.loads(out)
    assert code == 0 and data["files"] == 5 and data["files_list"] == names
    assert "sample" not in data


def test_force_reports_imported_ratio(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    files = _files(tmp_path, "a.mov", "b.wav")
    code, out, err = run_cli(["media", "import", _arg(files), "--force"])
    keys = _keys(out)
    assert (code, err) == (0, "") and keys["imported"] == "2/2"
    assert "failed" not in keys and "imported_names" not in keys
    (c,) = _files(tmp_path, "c.mov")
    code, out, _ = run_cli(["--json", "media", "import", str(c), "--force"])
    assert code == 0 and json.loads(out)["imported"] == "1/1"


def test_json_force_lists_the_imported_names(run_cli: Run, tmp_path: Path) -> None:
    files = _files(tmp_path, "a.mov", "b.wav")
    code, out, _ = run_cli(["--json", "media", "import", _arg(files), "--force"])
    data = json.loads(out)
    assert code == 0 and data["imported"] == "2/2" and data["imported_names"] == ["a", "b"]


def test_partial_import_is_an_error_with_the_failed_list(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    a, b = _files(tmp_path, "a.mov", "b.wav")
    pool = fake_world.resolve.pm.current_project.media_pool  # type: ignore[union-attr]
    clip = pool.current.add_clip("a", "/elsewhere/a.mov")
    fake_world.inject("MediaPool.ImportMedia", [clip])
    code, out, _ = run_cli(["--json", "media", "import", _arg([a, b]), "--force"])
    data = json.loads(out)
    assert code == 1 and data["code"] == "IMPORT_PARTIAL"
    assert data["detail"]["failed"] == ["b.wav"] and data["detail"]["imported"] == "1/2"
    helps = "\n".join(data["help"])
    assert "snapshot restore" in helps and "Master" in helps


def test_clip_matched_by_stem_when_the_path_differs(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _files(tmp_path, "a.mov", "b.wav")
    pool = fake_world.resolve.pm.current_project.media_pool  # type: ignore[union-attr]

    def elsewhere(self: object, infos: list[object]) -> list[object]:
        return [pool.current.add_clip("a", "/elsewhere/a.mov"), pool.current.add_clip("b", str(b))]

    monkeypatch.setattr(type(pool), "ImportMedia", elsewhere)
    code, out, _ = run_cli(["media", "import", _arg([a, b]), "--force"])
    assert code == 0 and _keys(out)["imported"] == "2/2"


def test_nothing_imported_is_still_api_failed(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    (a,) = _files(tmp_path, "a.mov")
    fake_world.inject("MediaPool.ImportMedia", [])
    code, out, _ = run_cli(["media", "import", str(a), "--force"])
    assert code == 1 and _keys(out)["code"] == "API_FAILED"


def _root(fake_world: FakeWorld):  # type: ignore[no-untyped-def]
    return fake_world.resolve.pm.current_project.media_pool.root  # type: ignore[union-attr]


def _footage(fake_world: FakeWorld):  # type: ignore[no-untyped-def]
    return next(f for f in _root(fake_world).subfolders if f.name == "Footage")


def _import_calls(fake_world: FakeWorld) -> list[tuple[str, str, tuple[object, ...]]]:
    return [c for c in fake_world.calls if c[:2] == ("MediaPool", "ImportMedia")]


def _dupe_setup(fake_world: FakeWorld, tmp_path: Path, *pooled: str) -> list[Path]:
    files = _files(tmp_path, "a.mov", "b.mov")
    for path in files:
        if path.name in pooled:
            _root(fake_world).add_clip(path.stem, str(path))
    return files


def test_before_is_the_folder_clip_count(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    files = _files(tmp_path, "a.mov", "b.mov")
    args = ["media", "import", _arg(files), "--folder", "Master/Footage"]
    code, out, _ = run_cli(args)
    assert code == 0 and re.search(r"clips,\s*3,\s*5", out)
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and _keys(out)["applied"] == "true"
    assert len(_footage(fake_world).clips) == 5


def test_duplicate_is_detected_by_file_path_and_skipped(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    files = _dupe_setup(fake_world, tmp_path, "a.mov")
    args = ["media", "import", _arg(files), "--folder", "Master/Footage"]
    code, out, _ = run_cli(args)
    keys = _keys(out)
    assert code == 0 and keys["files"] == "1" and keys["skipped"] == "1"
    assert keys["duplicates"] == "1" and re.search(r"clips,\s*3,\s*4", out)
    assert "already in media pool: a (id" in out and "skipped" in out
    code, out, _ = run_cli([*args, "--force"])
    assert code == 0 and _keys(out)["imported"] == "1/1"
    calls = _import_calls(fake_world)
    assert len(calls) == 1 and calls[0][2] == ([str(files[1])],)


def test_all_duplicates_is_a_no_op_with_exit_zero(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    files = _dupe_setup(fake_world, tmp_path, "a.mov", "b.mov")
    code, out, err = run_cli(["media", "import", _arg(files), "--force"])
    keys = _keys(out)
    assert (code, err) == (0, "") and keys["applied"] == "false"
    assert "--allow-duplicates" in keys["note"]
    assert _import_calls(fake_world) == [] and fake_world.write_calls() == []


def test_allow_duplicates_imports_them_anyway(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    files = _dupe_setup(fake_world, tmp_path, "a.mov", "b.mov")
    base = ["media", "import", _arg(files), "--folder", "Master/Footage", "--allow-duplicates"]
    code, out, _ = run_cli(base)
    assert code == 0 and re.search(r"clips,\s*3,\s*5", out)
    assert "imported again" in out and "media import" in out and "--allow-duplicates --force" in out
    code, out, _ = run_cli([*base, "--force"])
    assert code == 0 and _keys(out)["imported"] == "2/2"
    assert _import_calls(fake_world)[0][2] == ([str(f) for f in files],)


def test_duplicate_match_uses_abs_path_on_both_sides(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "x").mkdir()
    (a,) = _files(tmp_path / "x", "a.mov")
    _root(fake_world).add_clip("a", "~/x/a.mov")
    code, out, _ = run_cli(["media", "import", str(a)])
    assert code == 0 and _keys(out)["duplicates"] == "1" and "already in media pool" in out


def test_json_lists_duplicates(run_cli: Run, fake_world: FakeWorld, tmp_path: Path) -> None:
    files = _dupe_setup(fake_world, tmp_path, "a.mov")
    code, out, _ = run_cli(["--json", "media", "import", _arg(files)])
    data = json.loads(out)
    assert code == 0 and data["duplicates_list"][0]["name"] == "a"
    assert data["duplicates_list"][0]["folder"] == "Master"
    assert data["files_list"] == ["b.mov"]


def test_help_names_the_current_folder_default(run_cli: Run, tmp_path: Path) -> None:
    (a,) = _files(tmp_path, "a.mov")
    _, out, _ = run_cli(["media", "import", str(a)])
    assert "media import <paths> --folder Master` to choose the folder" in out
    assert "this run used the current folder Master" in out
    _, out, _ = run_cli(["media", "import", str(a), "--folder", "Master"])
    assert "to choose the folder" not in out


def test_import_help_describes_the_default_folder(run_cli: Run) -> None:
    _, out, _ = run_cli(["media", "import", "--help"])
    assert "whatever is open in Resolve" in out and "`--folder Master` is the root" in out
