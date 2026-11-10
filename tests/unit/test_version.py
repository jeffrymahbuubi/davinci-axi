"""version.describe(): commit from direct_url.json or the source checkout; never raises."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from davinci_axi import __version__, version

NOTE = " (source checkout HEAD; reinstall after new commits)"


class FakeDist:
    def __init__(self, direct_url: dict[str, Any] | str | None) -> None:
        self._text = json.dumps(direct_url) if isinstance(direct_url, dict) else direct_url

    def read_text(self, name: str) -> str | None:
        assert name == "direct_url.json"
        return self._text


def _patch_dist(monkeypatch: pytest.MonkeyPatch, dist: object) -> None:
    def fake(name: str) -> object:
        assert name == "davinci-axi"
        return dist

    monkeypatch.setattr(version, "distribution", fake)


def _patch_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> object:
        raise version.PackageNotFoundError(name)

    monkeypatch.setattr(version, "distribution", missing)


def _patch_run(monkeypatch: pytest.MonkeyPatch, **kw: Any) -> None:
    def fake(*a: Any, **k: Any) -> object:
        if "raises" in kw:
            raise kw["raises"]
        return SimpleNamespace(returncode=kw.get("code", 0), stdout=kw.get("stdout", ""))

    monkeypatch.setattr(version.subprocess, "run", fake)


def test_vcs_info_commit_is_exact_without_note(monkeypatch: pytest.MonkeyPatch) -> None:
    commit = "abc1234def5678abc1234def5678abc1234def56"
    direct = {"url": "https://x/y.git", "vcs_info": {"commit_id": commit}}
    _patch_dist(monkeypatch, FakeDist(direct))
    assert version.describe() == f"{__version__}+abc1234"


def test_file_url_reads_checkout_with_note(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[Path] = []

    def fake_describe(path: Path) -> str:
        seen.append(path)
        return "a475092"

    _patch_dist(monkeypatch, FakeDist({"url": tmp_path.as_uri(), "dir_info": {}}))
    monkeypatch.setattr(version, "_git_describe", fake_describe)
    out = version.describe()
    assert out == f"{__version__}+a475092{NOTE}"
    assert out.split()[0] == f"{__version__}+a475092"
    assert seen == [tmp_path]


def test_file_url_dirty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_dist(monkeypatch, FakeDist({"url": tmp_path.as_uri()}))
    _patch_run(monkeypatch, stdout="a475092-dirty\n")
    assert version.describe() == f"{__version__}+a475092.dirty{NOTE}"


def test_file_url_git_failure_falls_back_to_bare(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_dist(monkeypatch, FakeDist({"url": tmp_path.as_uri()}))
    _patch_run(monkeypatch, code=128)
    assert version.describe() == __version__


@pytest.mark.parametrize(
    "text", [None, "", "not json", "[]", '{"url": 5}', '{"vcs_info": {"commit_id": 7}}']
)
def test_unusable_direct_url_gives_bare_version(
    monkeypatch: pytest.MonkeyPatch, text: str | None
) -> None:
    _patch_dist(monkeypatch, FakeDist(text))
    assert version.describe() == __version__


def test_not_installed_uses_source_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_missing(monkeypatch)
    monkeypatch.setattr(version, "_git_describe", lambda p: "deadbee")
    root = Path(version.__file__).resolve().parents[2]
    if (root / ".git").exists():
        assert version.describe() == f"{__version__}+deadbee{NOTE}"
    else:
        assert version.describe() == __version__


def test_not_installed_without_git_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_missing(monkeypatch)
    monkeypatch.setattr(version, "__file__", str(tmp_path / "a" / "b" / "version.py"))
    monkeypatch.setattr(version, "_git_describe", lambda p: pytest.fail("no .git, no call"))
    assert version.describe() == __version__


def test_garbage_commit_is_dropped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_dist(monkeypatch, FakeDist({"url": tmp_path.as_uri()}))
    monkeypatch.setattr(version, "_git_describe", lambda p: "fatal: not a repo")
    assert version.describe() == __version__


@pytest.mark.parametrize(
    ("kw", "want"),
    [
        ({"stdout": "a475092\n"}, "a475092"),
        ({"stdout": "a475092-dirty\n"}, "a475092.dirty"),
        ({"code": 128, "stdout": ""}, None),
        ({"stdout": "\n"}, None),
        ({"raises": FileNotFoundError("git")}, None),
        ({"raises": subprocess.TimeoutExpired("git", 2)}, None),
    ],
)
def test_git_describe(
    monkeypatch: pytest.MonkeyPatch, kw: dict[str, Any], want: str | None
) -> None:
    _patch_run(monkeypatch, **kw)
    assert version._git_describe(Path("/x")) == want


def test_describe_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(name: str) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(version, "distribution", boom)
    assert version.describe() == __version__


def test_real_git_describe_in_a_throwaway_repo(tmp_path: Path) -> None:
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run([*git, "add", "f"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "m"], check=True)
    clean = version._git_describe(tmp_path)
    assert clean is not None and version._valid(clean) == clean and ".dirty" not in clean
    (tmp_path / "f").write_text("y")
    assert version._git_describe(tmp_path) == clean + ".dirty"


def test_version_module_imports_nothing_heavy() -> None:
    tree = ast.parse(Path(version.__file__).read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    forbidden = ("davinci_axi.commands", "davinci_axi.connect", "davinci_axi.fake")
    assert not any(n.startswith(forbidden) for n in names)


REPO = Path(version.__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "reinstall.sh"
DEV_ONLY = pytest.mark.skipif(
    not SCRIPT.exists() or not (REPO / ".gitignore").exists(),
    reason="dev checkout only: scripts/ and .gitignore are not in the public release",
)


def _bake(monkeypatch: pytest.MonkeyPatch, commit: object, dirty: object = False) -> None:
    info = SimpleNamespace(COMMIT=commit, DIRTY=dirty)
    monkeypatch.setitem(sys.modules, "davinci_axi._build_info", info)


def test_baked_commit_wins_without_note(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _bake(monkeypatch, "abc1234")
    _patch_dist(monkeypatch, FakeDist({"url": tmp_path.as_uri()}))
    monkeypatch.setattr(version, "_git_describe", lambda p: pytest.fail("baked skips git"))
    assert version.describe() == f"{__version__}+abc1234"


def test_baked_dirty_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _bake(monkeypatch, "abc1234", dirty=True)
    _patch_missing(monkeypatch)
    assert version.describe() == f"{__version__}+abc1234.dirty"


@pytest.mark.parametrize("bad", ["fatal: x", "", None, 7, "ABC1234"])
def test_garbage_baked_commit_falls_through(monkeypatch: pytest.MonkeyPatch, bad: object) -> None:
    _bake(monkeypatch, bad)
    _patch_dist(monkeypatch, FakeDist({"vcs_info": {"commit_id": "deadbee" + "0" * 33}}))
    assert version.describe() == f"{__version__}+deadbee"


def test_no_build_info_module_gives_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "davinci_axi._build_info", raising=False)
    assert version._baked() == (None, False)


@DEV_ONLY
def test_gitignore_lists_build_info() -> None:
    lines = (REPO / ".gitignore").read_text().splitlines()
    assert "src/davinci_axi/_build_info.py" in lines


@DEV_ONLY
def test_reinstall_script_is_executable_and_parses() -> None:
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    helped = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert helped.returncode == 0 and "reinstall" in helped.stdout


def _throwaway_repo(path: Path) -> Path:
    git = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    (path / "src" / "davinci_axi").mkdir(parents=True)
    (path / "f").write_text("x")
    subprocess.run([*git, "add", "f"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "m"], check=True)
    return path / "src" / "davinci_axi" / "_build_info.py"


def _run_script(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = ["bash", str(SCRIPT), "--root", str(root), *args]
    return subprocess.run(cmd, capture_output=True, text=True)


@DEV_ONLY
def test_reinstall_bake_step_writes_commit_and_dirty(tmp_path: Path) -> None:
    info = _throwaway_repo(tmp_path)
    done = _run_script(tmp_path, "--bake-only")
    assert done.returncode == 0, done.stderr
    ns: dict[str, Any] = {}
    exec(info.read_text(), ns)
    assert version._valid(ns["COMMIT"]) == ns["COMMIT"] and ".dirty" not in ns["COMMIT"]
    assert ns["DIRTY"] is False and ns["BUILT_AT"].endswith("Z")
    # a stale file refuses to run until --clean
    again = _run_script(tmp_path, "--bake-only")
    assert again.returncode == 1 and "--clean" in again.stderr
    (tmp_path / "f").write_text("y")
    assert _run_script(tmp_path, "--bake-only", "--clean").returncode == 0
    ns = {}
    exec(info.read_text(), ns)
    assert ns["DIRTY"] is True
