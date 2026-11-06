"""`presets` and `dctl` (U22), against the fake."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from davinci_axi import registry
from davinci_axi.commands import dctl, presets
from davinci_axi.fake import FakeWorld, ext_presets
from davinci_axi.spec import CommandSpec

Run = Callable[[list[str]], tuple[int, str, str]]
Keys = Callable[[str], dict[str, str]]

SAFETY = ["ProjectManager.SaveProject", "ProjectManager.ExportProject"]


@pytest.fixture(autouse=True)
def _registered(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ext_presets.extend()
    ext_presets.extend()  # idempotent
    monkeypatch.setitem(registry.COMMANDS, "presets", "davinci_axi.commands.presets:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "dctl", "davinci_axi.commands.dctl:SPEC")
    yield


def _snapshots(tmp_path: Path) -> Path:
    return tmp_path / "axi-home" / "snapshots"


def _backups(tmp_path: Path) -> list[Path]:
    return sorted((_snapshots(tmp_path) / "presets").glob("*.preset"))


def _json(out: str) -> dict[str, object]:
    body = json.loads(out)
    assert isinstance(body, dict)
    return body


def _file(tmp_path: Path, name: str, text: str = "fake preset: x\n") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- list and current -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["presets", "layout", "list"], "Default"),
        (["presets", "keyboard", "list"], "Premiere Pro"),
        (["presets", "userprefs", "list"], "Default"),
        (["presets", "burnin", "list"], "Source Timecode"),
    ],
)
def test_each_kind_lists_its_presets_without_writes(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, argv: list[str], expected: str
) -> None:
    code, out, err = run_cli(argv)
    assert (code, err) == (0, "")
    assert expected in out and parse_toon_keys(out)["kind"] == argv[1]
    assert fake_world.write_calls() == []


def test_keyboard_current_reads_the_active_preset(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["presets", "keyboard", "current"])
    assert code == 0 and parse_toon_keys(out)["current"] == "DaVinci Resolve"
    code, out, _ = run_cli(["presets", "keyboard", "list"])
    assert "presets keyboard current" in out


def test_an_empty_list_says_so(run_cli: Run, fake_world: FakeWorld) -> None:
    fake_world.resolve.layout_presets.clear()
    code, out, _ = run_cli(["presets", "layout", "list"])
    assert code == 0 and "0 layout presets in Resolve" in out


def test_verbs_a_kind_does_not_have_exit_2(run_cli: Run, fake_world: FakeWorld) -> None:
    for argv in (
        ["presets", "keyboard", "save", "mine"],
        ["presets", "keyboard", "update", "mine"],
        ["presets", "burnin", "save", "mine"],
        ["presets", "userprefs", "update", "mine"],
        ["presets", "layout", "current"],
    ):
        code, out, _ = run_cli(argv)
        assert code == 2 and "Valid subcommands" in out, argv
    assert fake_world.calls == []


# --- save -----------------------------------------------------------------------------------


def test_layout_save_dry_run_writes_nothing(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["presets", "layout", "save", "mine"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "false" and keys["command"] == "presets layout save"
    assert "project snapshot lacks it" in out
    assert "davinci-axi presets layout save mine --force" in out
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()


def test_layout_save_force_snapshots_writes_and_reads_back(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["presets", "layout", "save", "mine", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true"
    assert fake_world.write_calls() == [*SAFETY, "Resolve.SaveLayoutPreset"]
    assert fake_world.resolve.layout_presets == ["Default", "mine"]
    assert (_snapshots(tmp_path) / keys["snapshot"]).is_dir()


def test_save_of_an_existing_name_is_refused(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["presets", "userprefs", "save", "Default", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "ALREADY_EXISTS"
    assert fake_world.write_calls() == []


def test_save_aborts_before_the_write_when_the_project_snapshot_fails(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("ProjectManager.ExportProject", False)
    code, out, _ = run_cli(["presets", "layout", "save", "mine", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert "Resolve.SaveLayoutPreset" not in fake_world.write_calls()
    assert fake_world.resolve.layout_presets == ["Default"]


def test_save_failure_is_api_failed_with_the_snapshot_id(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    fake_world.inject("Resolve.SaveLayoutPreset", False)
    code, out, _ = run_cli(["presets", "layout", "save", "mine", "--force", "--json"])
    body = _json(out)
    detail = body["detail"]
    assert code == 1 and body["code"] == "API_FAILED" and isinstance(detail, dict)
    assert detail["snapshot"] == next(p for p in _snapshots(tmp_path).iterdir()).name


# --- update and delete (backup first) -------------------------------------------------------


def test_layout_update_exports_a_copy_before_the_write(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["presets", "layout", "update", "Default"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["irreversible"] == "true" and keys["applied"] == "false"
    assert fake_world.write_calls() == [] and not _snapshots(tmp_path).exists()
    code, out, _ = run_cli(["presets", "layout", "update", "Default", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["irreversible"] == "true" and keys["applied"] == "true"
    assert fake_world.write_calls() == [
        *SAFETY,
        "Resolve.ExportLayoutPreset",
        "Resolve.UpdateLayoutPreset",
    ]
    (backup,) = _backups(tmp_path)
    assert backup.name.startswith("layout-Default-") and backup.exists()
    assert "preset_backup" in keys and backup.name in keys["preset_backup"]


def test_update_of_an_unknown_preset_is_not_found(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["presets", "layout", "update", "nope", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND" and "Default" in out
    assert fake_world.write_calls() == []


def test_layout_delete_removes_the_preset_and_keeps_a_copy(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["presets", "layout", "delete", "Default", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["irreversible"] == "true" and keys["applied"] == "true"
    assert fake_world.write_calls() == [
        *SAFETY,
        "Resolve.ExportLayoutPreset",
        "Resolve.DeleteLayoutPreset",
    ]
    assert fake_world.resolve.layout_presets == []
    assert len(_backups(tmp_path)) == 1


def test_delete_aborts_when_the_preset_cannot_be_exported_first(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    fake_world.inject("Resolve.ExportKeyboardPreset", False)
    code, out, _ = run_cli(["presets", "keyboard", "delete", "Premiere Pro", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "SNAPSHOT_FAILED"
    assert "Resolve.DeleteKeyboardPreset" not in fake_world.write_calls()
    assert "Premiere Pro" in fake_world.resolve.keyboard_presets


# --- load -----------------------------------------------------------------------------------


def test_keyboard_load_takes_no_snapshot_and_reads_the_current_preset(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["presets", "keyboard", "load", "Premiere Pro"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert "current preset,DaVinci Resolve,Premiere Pro" in out
    assert fake_world.write_calls() == []
    code, out, _ = run_cli(["presets", "keyboard", "load", "Premiere Pro", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["snapshot"] == "none"
    assert fake_world.write_calls() == ["Resolve.LoadKeyboardPreset"]
    assert fake_world.resolve.keyboard_preset == "Premiere Pro"
    assert not _snapshots(tmp_path).exists()
    code, out, _ = run_cli(["presets", "keyboard", "load", "Premiere Pro", "--force"])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"


def test_layout_load_is_a_called_change_with_a_warning(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["presets", "layout", "load", "Default", "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and "called,false,true" in out
    assert "cannot read which layout preset is loaded" in out
    assert fake_world.write_calls() == ["Resolve.LoadLayoutPreset"]


def test_load_of_an_unknown_preset_lists_the_available_ones(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["presets", "layout", "load", "nope", "--force"])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND" and "Default" in out
    assert fake_world.write_calls() == []


def test_burnin_load_goes_to_the_project_or_to_one_item(
    run_cli: Run, fake_world: FakeWorld
) -> None:
    assert run_cli(["presets", "burnin", "load", "Source Timecode", "--force"])[0] == 0
    assert fake_world.write_calls() == ["Project.LoadBurnInPreset"]
    fake_world.calls.clear()
    code, out, _ = run_cli(
        ["presets", "burnin", "load", "Source Timecode", "--item", "V1:1", "--force"]
    )
    assert code == 0 and "on item V1:1" in out
    assert fake_world.write_calls() == ["TimelineItem.LoadBurnInPreset"]
    code, out, _ = run_cli(["presets", "burnin", "load", "Source Timecode", "--item", "V9:9"])
    assert code == 1 and "NOT_FOUND" in out


# --- import and export ----------------------------------------------------------------------


def test_import_without_a_name_counts_presets_and_reports_the_new_one(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _file(tmp_path, "wide.preset")
    code, out, _ = run_cli(["presets", "layout", "import", str(source)])
    assert code == 0 and parse_toon_keys(out)["applied"] == "false"
    assert "presets,1,2" in out and fake_world.write_calls() == []
    code, out, _ = run_cli(["presets", "layout", "import", str(source), "--force"])
    keys = parse_toon_keys(out)
    assert code == 0 and keys["applied"] == "true" and keys["imported_as"] == "wide"
    assert fake_world.write_calls() == [*SAFETY, "Resolve.ImportLayoutPreset"]
    assert fake_world.calls_to("Resolve.ImportLayoutPreset") == [(str(source), None)]


def test_import_with_a_name_passes_it_and_refuses_a_taken_name(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _file(tmp_path, "wide.preset")
    code, _, _ = run_cli(
        ["presets", "keyboard", "import", str(source), "--name", "mine", "--force"]
    )
    assert code == 0 and "mine" in fake_world.resolve.keyboard_presets
    assert fake_world.calls_to("Resolve.ImportKeyboardPreset") == [(str(source), "mine")]
    code, out, _ = run_cli(["presets", "keyboard", "import", str(source), "--name", "mine"])
    assert code == 1 and parse_toon_keys(out)["code"] == "ALREADY_EXISTS"


def test_import_of_a_missing_file_is_not_found_before_connecting(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["presets", "layout", "import", str(tmp_path / "nope.preset")])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    assert fake_world.calls == []


def test_burnin_import_has_no_name_flag(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _file(tmp_path, "slate.preset")
    code, out, _ = run_cli(["presets", "burnin", "import", str(source), "--name", "x"])
    assert code == 2 and "unknown flag --name" in out and fake_world.calls == []
    assert run_cli(["presets", "burnin", "import", str(source), "--force"])[0] == 0
    assert fake_world.resolve.burn_in_presets == ["Source Timecode", "slate"]
    assert fake_world.calls_to("Resolve.ImportBurnInPreset") == [(str(source),)]


def test_export_writes_the_file_and_refuses_to_overwrite(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "out" / "u.xml"
    code, out, _ = run_cli(["presets", "userprefs", "export", "Default", str(target)])
    keys = parse_toon_keys(out)
    assert code == 0 and target.read_text(encoding="utf-8").startswith("fake preset: Default")
    assert int(keys["bytes"]) == target.stat().st_size and keys["preset"] == "Default"
    assert fake_world.write_calls() == ["Resolve.ExportUserPreferencesPreset"]
    assert not _snapshots(tmp_path).exists()
    code, out, _ = run_cli(["presets", "userprefs", "export", "Default", str(target)])
    assert code == 1 and parse_toon_keys(out)["code"] == "FILE_EXISTS"
    assert (
        run_cli(["presets", "userprefs", "export", "Default", str(target), "--overwrite"])[0] == 0
    )


def test_export_of_an_unknown_preset_is_not_found_and_writes_no_file(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    target = tmp_path / "x.preset"
    code, out, _ = run_cli(["presets", "layout", "export", "nope", str(target)])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND" and not target.exists()
    fake_world.inject("Resolve.ExportLayoutPreset", False)
    code, out, _ = run_cli(["presets", "layout", "export", "Default", str(target)])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"


# --- dctl -----------------------------------------------------------------------------------


def test_validate_a_good_file(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _file(tmp_path, "ok.dctl", "__DEVICE__ float3 f(float3 c) { return c; }\n")
    code, out, err = run_cli(["dctl", "validate", str(source)])
    keys = parse_toon_keys(out)
    assert (code, err) == (0, "") and keys["valid"] == "true"
    assert fake_world.calls_to("Resolve.ValidateDCTL") == [(source.read_text(encoding="utf-8"),)]
    assert fake_world.write_calls() == ["Resolve.ValidateDCTL"]
    assert not _snapshots(tmp_path).exists()
    assert "dctl encrypt" in out


def test_validate_a_bad_file_exits_1_with_the_message_in_detail(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _file(tmp_path, "bad.dctl", "ERROR\n")
    code, out, _ = run_cli(["dctl", "validate", str(source), "--json"])
    body = _json(out)
    detail = body["detail"]
    assert code == 1 and body["code"] == "DCTL_INVALID" and isinstance(detail, dict)
    assert "unexpected token" in str(detail["message"]) and detail["file"] == str(source)


def test_validate_a_missing_or_binary_file(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    code, out, _ = run_cli(["dctl", "validate", str(tmp_path / "nope.dctl")])
    assert code == 1 and parse_toon_keys(out)["code"] == "NOT_FOUND"
    binary = tmp_path / "bin.dctl"
    binary.write_bytes(b"\xff\xfe\x00")
    code, out, _ = run_cli(["dctl", "validate", str(binary)])
    assert code == 2 and parse_toon_keys(out)["code"] == "VALIDATION_ERROR"
    assert fake_world.calls == []


def test_encrypt_writes_the_dctle_into_out_dir_and_refuses_to_overwrite(
    run_cli: Run, parse_toon_keys: Keys, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _file(tmp_path, "ok.dctl", "float3 f() {}\n")
    out_dir = tmp_path / "signed"
    code, out, _ = run_cli(["dctl", "encrypt", str(source), "--out-dir", str(out_dir)])
    keys = parse_toon_keys(out)
    assert code == 0 and (out_dir / "ok.dctle").is_file()
    assert keys["output"] == str(out_dir / "ok.dctle") and keys["expiry"] == "none"
    assert fake_world.calls_to("Resolve.EncryptDCTL") == [
        (str(source), {"OutputFolder": str(out_dir)})
    ]
    assert fake_world.write_calls() == ["Resolve.EncryptDCTL"] and not _snapshots(tmp_path).exists()
    code, out, _ = run_cli(["dctl", "encrypt", str(source), "--out-dir", str(out_dir)])
    assert code == 1 and parse_toon_keys(out)["code"] == "FILE_EXISTS"
    fake_world.inject("Resolve.EncryptDCTL", False)
    code, out, _ = run_cli(["dctl", "encrypt", str(source), "--out-dir", str(tmp_path / "b")])
    assert code == 1 and parse_toon_keys(out)["code"] == "API_FAILED"
    fake_world.fail.clear()
    args = ["dctl", "encrypt", str(source), "--out-dir", str(out_dir), "--overwrite"]
    assert run_cli(args)[0] == 0


def test_encrypt_defaults_to_the_input_folder_and_passes_name_and_expiry(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path
) -> None:
    source = _file(tmp_path, "ok.dctl", "float3 f() {}\n")
    code, out, _ = run_cli(
        ["dctl", "encrypt", str(source), "--name", "grade-v2", "--expiry", "2027-12-31T23:59:59Z"]
    )
    assert code == 0 and (tmp_path / "grade-v2.dctle").is_file() and "2027-12-31" in out
    options = fake_world.calls_to("Resolve.EncryptDCTL")[0][1]
    assert options == {
        "OutputFolder": str(tmp_path),
        "Name": "grade-v2",
        "Expiry": "2027-12-31T23:59:59Z",
    }


@pytest.mark.parametrize(
    "extra",
    [
        ["--expiry", "next week"],
        ["--expiry", "2027-02-30"],
        ["--name", "a/b"],
        ["--name", " "],
        ["--force"],
    ],
)
def test_encrypt_rejects_bad_options_before_connecting(
    run_cli: Run, fake_world: FakeWorld, tmp_path: Path, extra: list[str]
) -> None:
    source = _file(tmp_path, "ok.dctl", "float3 f() {}\n")
    code, out, _ = run_cli(["dctl", "encrypt", str(source), *extra])
    assert code == 2 and "VALIDATION_ERROR" in out and fake_world.calls == []


# --- specs ----------------------------------------------------------------------------------


def _walk(
    path: tuple[str, ...], spec: CommandSpec
) -> Iterator[tuple[tuple[str, ...], CommandSpec]]:
    yield path, spec
    for name, child in spec.subcommands.items():
        yield from _walk((*path, name), child)


def test_every_presets_and_dctl_path_has_help_and_examples(run_cli: Run) -> None:
    for root, spec in (("presets", presets.SPEC), ("dctl", dctl.SPEC)):
        for path, _ in _walk((root,), spec):
            code, out, err = run_cli([*path, "--help"])
            assert code == 0 and err == "" and "examples[" in out, path


def test_declared_methods_exist_on_the_fake_and_cover_the_calls(fake_world: FakeWorld) -> None:
    from davinci_axi.fake import FakeProject, FakeResolve, FakeTimelineItem
    from davinci_axi.fake.core import FakeProjectManager

    classes: dict[str, type] = {
        "Resolve": FakeResolve,
        "Project": FakeProject,
        "ProjectManager": FakeProjectManager,
        "TimelineItem": FakeTimelineItem,
    }
    declared = {*presets.SPEC.methods, *dctl.SPEC.methods}
    for name in declared:
        cls, _, method = name.partition(".")
        assert hasattr(classes[cls], method), name
    for expected in (
        "Resolve.SaveLayoutPreset",
        "Resolve.UpdateLayoutPreset",
        "Resolve.GetCurrentKeyboardPreset",
        "Project.LoadBurnInPreset",
        "TimelineItem.LoadBurnInPreset",
        "Resolve.ValidateDCTL",
        "Resolve.EncryptDCTL",
        "ProjectManager.SaveProject",
    ):
        assert expected in declared
    assert not any(name.startswith("Resolve.LoadBurnInPreset") for name in declared)
