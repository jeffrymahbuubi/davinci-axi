"""Fake preset, DCTL, database, cloud and project-settings-preset methods for U22 (KTD17).

App-level presets (layout, keyboard, user preferences, burn-in) live in the lists already on
`FakeResolve`; project settings presets live in `FakeProject.settings_presets`. Imports only from
`davinci_axi.fake.core|timeline|ext_writes_core`, never from the `davinci_axi.fake` package,
because the package is still mid-import when `EXTENSIONS` run. Each method keeps the `_rec`
injection contract: an injected value is returned (as `bool`, or as the object or string where
the API returns one), otherwise the side effect happens. `extend()` is idempotent.

Test helpers (not API calls): `add_database`, `seed_settings_presets`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from davinci_axi.fake.core import FakeProject, FakeProjectManager, FakeResolve
from davinci_axi.fake.ext_writes_core import PLACEHOLDER as DRP_PLACEHOLDER
from davinci_axi.fake.timeline import FakeTimelineItem
from davinci_axi.fake.world import FakeWorld

PRESET_PLACEHOLDER = "fake preset: "
DCTLE_PLACEHOLDER = "fake dctle: "
_CLOUD_SYNC = ("NONE", "PROXY_ONLY", "PROXY_AND_ORIG")


# --- app-level presets on FakeResolve -------------------------------------------------------


def _load(method: str, attr: str, current: str | None) -> Callable[..., bool]:
    def load(self: FakeResolve, presetName: str) -> bool:
        injected, value = self._rec(method, presetName)
        if injected:
            return bool(value)
        if presetName not in getattr(self, attr):
            return False
        if current is not None:
            setattr(self, current, presetName)
        return True

    return load


def _save(method: str, attr: str) -> Callable[..., bool]:
    def save(self: FakeResolve, presetName: str) -> bool:
        injected, value = self._rec(method, presetName)
        if injected:
            return bool(value)
        names: list[str] = getattr(self, attr)
        if not presetName or presetName in names:
            return False
        names.append(presetName)
        return True

    return save


def _update(method: str, attr: str) -> Callable[..., bool]:
    def update(self: FakeResolve, presetName: str) -> bool:
        injected, value = self._rec(method, presetName)
        if injected:
            return bool(value)
        return presetName in getattr(self, attr)

    return update


def _delete(method: str, attr: str) -> Callable[..., bool]:
    def delete(self: FakeResolve, presetName: str) -> bool:
        injected, value = self._rec(method, presetName)
        if injected:
            return bool(value)
        names: list[str] = getattr(self, attr)
        if presetName not in names:
            return False
        names.remove(presetName)
        return True

    return delete


def _import(method: str, attr: str, named: bool) -> Callable[..., bool]:
    def do_import(self: FakeResolve, presetFilePath: str, presetName: str | None = None) -> bool:
        args = (presetFilePath, presetName) if named else (presetFilePath,)
        injected, value = self._rec(method, *args)
        if injected:
            return bool(value)
        path = Path(presetFilePath)
        name = (presetName if named else None) or path.stem
        names: list[str] = getattr(self, attr)
        if not path.is_file() or not name or name in names:
            return False
        names.append(name)
        return True

    return do_import


def _export(method: str, attr: str) -> Callable[..., bool]:
    def export(self: FakeResolve, presetName: str, exportPath: str) -> bool:
        injected, value = self._rec(method, presetName, exportPath)
        if injected:
            return bool(value)
        if presetName not in getattr(self, attr):
            return False
        try:
            Path(exportPath).write_text(f"{PRESET_PLACEHOLDER}{presetName}\n", encoding="utf-8")
        except OSError:
            return False
        return True

    return export


def _burn_in_load(self: Any, presetName: str) -> bool:
    injected, value = self._rec("LoadBurnInPreset", presetName)
    if injected:
        return bool(value)
    if presetName not in self._world.resolve.burn_in_presets:
        return False
    self.__dict__["burn_in_preset"] = presetName
    return True


# --- DCTL -----------------------------------------------------------------------------------


def _validate_dctl(self: FakeResolve, dctlSource: str) -> str | None:
    injected, value = self._rec("ValidateDCTL", dctlSource)
    if injected:
        return value if isinstance(value, str) else None
    if "ERROR" in dctlSource:
        return "line 1: unexpected token ERROR"
    return None


def _encrypt_dctl(
    self: FakeResolve, inputPath: str, encryptDCTLOptions: dict[str, Any] | None = None
) -> bool:
    injected, value = self._rec("EncryptDCTL", inputPath, encryptDCTLOptions)
    if injected:
        return bool(value)
    source = Path(inputPath)
    options = encryptDCTLOptions or {}
    if not source.is_file():
        return False
    folder = Path(str(options.get("OutputFolder") or source.parent))
    name = str(options.get("Name") or source.stem)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        text = f"{DCTLE_PLACEHOLDER}{name} expiry={options.get('Expiry') or ''}\n"
        (folder / f"{name}.dctle").write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True


# --- project settings presets on FakeProject ------------------------------------------------


def _find(self: FakeProject, name: str) -> dict[str, Any] | None:
    return next((p for p in self.settings_presets if p.get("Name") == name), None)


def _current_size(self: FakeProject) -> tuple[int, int]:
    width = int(str(self.settings.get("timelineResolutionWidth", 1920)))
    height = int(str(self.settings.get("timelineResolutionHeight", 1080)))
    return width, height


def _set_settings_preset(self: FakeProject, presetName: str) -> bool:
    injected, value = self._rec("SetProjectSettingsPreset", presetName)
    if injected:
        return bool(value)
    preset = _find(self, presetName)
    if preset is None:
        return False
    self.settings["timelineResolutionWidth"] = str(preset["Width"])
    self.settings["timelineResolutionHeight"] = str(preset["Height"])
    return True


def _save_settings_preset(self: FakeProject, presetName: str) -> bool:
    injected, value = self._rec("SaveCurrentProjectSettingsAsNewPreset", presetName)
    if injected:
        return bool(value)
    if not presetName or _find(self, presetName) is not None:
        return False
    width, height = _current_size(self)
    self.settings_presets.append({"Name": presetName, "Width": width, "Height": height})
    return True


def _update_settings_preset(self: FakeProject, presetName: str) -> bool:
    injected, value = self._rec("UpdateProjectSettingsPreset", presetName)
    if injected:
        return bool(value)
    preset = _find(self, presetName)
    if preset is None:
        return False
    preset["Width"], preset["Height"] = _current_size(self)
    return True


def _delete_settings_preset(self: FakeProject, presetName: str) -> bool:
    injected, value = self._rec("DeleteProjectSettingsPreset", presetName)
    if injected:
        return bool(value)
    preset = _find(self, presetName)
    if preset is None:
        return False
    self.settings_presets.remove(preset)
    return True


def _import_settings_preset(
    self: FakeProject, presetFilePath: str, presetName: str | None = None
) -> bool:
    injected, value = self._rec("ImportProjectSettingsPreset", presetFilePath, presetName)
    if injected:
        return bool(value)
    path = Path(presetFilePath)
    name = presetName or path.stem
    if not path.is_file() or not name or _find(self, name) is not None:
        return False
    width, height = _current_size(self)
    self.settings_presets.append({"Name": name, "Width": width, "Height": height})
    return True


def _export_settings_preset(self: FakeProject, presetName: str, exportPath: str) -> bool:
    injected, value = self._rec("ExportProjectSettingsPreset", presetName, exportPath)
    if injected:
        return bool(value)
    if _find(self, presetName) is None:
        return False
    try:
        Path(exportPath).write_text(f"{PRESET_PLACEHOLDER}{presetName}\n", encoding="utf-8")
    except OSError:
        return False
    return True


# --- project manager: restore, databases, cloud ---------------------------------------------


def _load_drp(self: FakeProjectManager, filePath: str, projectName: str | None) -> bool:
    try:
        with open(filePath, encoding="utf-8") as handle:
            first = handle.readline().rstrip("\n")
    except OSError:
        return False
    if not first.startswith(DRP_PLACEHOLDER):
        return False
    name = projectName or first[len(DRP_PLACEHOLDER) :]
    if not name or name in self.folder.projects:
        return False
    self.add_project(name)
    return True


def _restore_project(
    self: FakeProjectManager, filePath: str, projectName: str | None = None
) -> bool:
    injected, value = self._rec("RestoreProject", filePath, projectName)
    if injected:
        return bool(value)
    return _load_drp(self, filePath, projectName)


def _extra_databases(self: FakeProjectManager) -> list[dict[str, Any]]:
    extras: list[dict[str, Any]] = self.__dict__.setdefault("extra_databases", [])
    return extras


def _database_list(self: FakeProjectManager) -> list[dict[str, Any]]:
    listed = [dict(self.database), *(dict(d) for d in _extra_databases(self))]
    return self._ret("GetDatabaseList", listed)


def _set_current_database(self: FakeProjectManager, dbInfo: dict[str, Any]) -> bool:
    injected, value = self._rec("SetCurrentDatabase", dbInfo)
    if injected:
        return bool(value)
    extras = _extra_databases(self)
    wanted = dbInfo.get("DbName")
    kind = dbInfo.get("DbType")
    target = next(
        (d for d in extras if d.get("DbName") == wanted and kind in (None, d.get("DbType"))), None
    )
    if target is None:
        return self.database.get("DbName") == wanted
    extras.remove(target)
    extras.append(dict(self.database))
    self.database = dict(target)
    self.current_project = None
    return True


def _cloud(self: FakeProjectManager) -> dict[str, FakeProject]:
    projects: dict[str, FakeProject] = self.__dict__.setdefault("cloud_projects", {})
    return projects


def _cloud_name(settings: dict[str, Any]) -> str:
    return str(settings.get("projectName") or "")


def _create_cloud_project(
    self: FakeProjectManager, cloudSettings: dict[str, Any]
) -> FakeProject | None:
    injected, value = self._rec("CreateCloudProject", cloudSettings)
    if injected:
        return value if isinstance(value, FakeProject) else None
    name = _cloud_name(cloudSettings)
    if not name or name in _cloud(self):
        return None
    project = FakeProject(self._world, name)
    if cloudSettings.get("isCollab"):
        project.attributes["liveCollaborationMode"] = "collaboration"
    _cloud(self)[name] = project
    self.current_project = project
    return project


def _load_cloud_project(
    self: FakeProjectManager, cloudSettings: dict[str, Any]
) -> FakeProject | None:
    injected, value = self._rec("LoadCloudProject", cloudSettings)
    if injected:
        return value if isinstance(value, FakeProject) else None
    project = _cloud(self).get(_cloud_name(cloudSettings))
    if project is not None:
        self.current_project = project
    return project


def _import_cloud_project(
    self: FakeProjectManager, filePath: str, cloudSettings: dict[str, Any]
) -> bool:
    injected, value = self._rec("ImportCloudProject", filePath, cloudSettings)
    if injected:
        return bool(value)
    name = _cloud_name(cloudSettings)
    if not Path(filePath).is_file() or not name or name in _cloud(self):
        return False
    _cloud(self)[name] = FakeProject(self._world, name)
    return True


def _restore_cloud_project(
    self: FakeProjectManager, folderPath: str, cloudSettings: dict[str, Any]
) -> bool:
    injected, value = self._rec("RestoreCloudProject", folderPath, cloudSettings)
    if injected:
        return bool(value)
    name = _cloud_name(cloudSettings)
    if not Path(folderPath).is_dir() or not name or name in _cloud(self):
        return False
    _cloud(self)[name] = FakeProject(self._world, name)
    return True


def _methods() -> dict[tuple[type[Any], str], Callable[..., Any]]:
    table: dict[tuple[type[Any], str], Callable[..., Any]] = {}
    kinds = (
        ("Layout", "layout_presets", None, True),
        ("Keyboard", "keyboard_presets", "keyboard_preset", True),
        ("UserPreferences", "user_presets", None, True),
        ("BurnIn", "burn_in_presets", None, False),
    )
    for suffix, attr, current, named in kinds:
        name = f"{suffix}Preset"
        table[(FakeResolve, f"Delete{name}")] = _delete(f"Delete{name}", attr)
        table[(FakeResolve, f"Import{name}")] = _import(f"Import{name}", attr, named)
        table[(FakeResolve, f"Export{name}")] = _export(f"Export{name}", attr)
        if suffix != "BurnIn":
            table[(FakeResolve, f"Load{name}")] = _load(f"Load{name}", attr, current)
        if suffix in ("Layout", "UserPreferences"):
            table[(FakeResolve, f"Save{name}")] = _save(f"Save{name}", attr)
        if suffix == "Layout":
            table[(FakeResolve, f"Update{name}")] = _update(f"Update{name}", attr)
    table[(FakeProject, "LoadBurnInPreset")] = _burn_in_load
    table[(FakeTimelineItem, "LoadBurnInPreset")] = _burn_in_load
    table[(FakeResolve, "ValidateDCTL")] = _validate_dctl
    table[(FakeResolve, "EncryptDCTL")] = _encrypt_dctl
    table[(FakeProject, "SetProjectSettingsPreset")] = _set_settings_preset
    table[(FakeProject, "SaveCurrentProjectSettingsAsNewPreset")] = _save_settings_preset
    table[(FakeProject, "UpdateProjectSettingsPreset")] = _update_settings_preset
    table[(FakeProject, "DeleteProjectSettingsPreset")] = _delete_settings_preset
    table[(FakeProject, "ImportProjectSettingsPreset")] = _import_settings_preset
    table[(FakeProject, "ExportProjectSettingsPreset")] = _export_settings_preset
    table[(FakeProjectManager, "RestoreProject")] = _restore_project
    table[(FakeProjectManager, "GetDatabaseList")] = _database_list
    table[(FakeProjectManager, "SetCurrentDatabase")] = _set_current_database
    table[(FakeProjectManager, "CreateCloudProject")] = _create_cloud_project
    table[(FakeProjectManager, "LoadCloudProject")] = _load_cloud_project
    table[(FakeProjectManager, "ImportCloudProject")] = _import_cloud_project
    table[(FakeProjectManager, "RestoreCloudProject")] = _restore_cloud_project
    return table


def add_database(world: FakeWorld, name: str, db_type: str = "Disk") -> None:
    """Test helper (not an API call): make `name` a second database `SetCurrentDatabase` can use."""
    _extra_databases(world.resolve.pm).append({"DbType": db_type, "DbName": name})


def seed_settings_presets(project: FakeProject, *names: str) -> None:
    """Test helper (not an API call): give `project` settings presets sized like its settings."""
    width, height = _current_size(project)
    for name in names:
        project.settings_presets.append({"Name": name, "Width": width, "Height": height})


def extend() -> None:
    """Add the preset, DCTL, restore, database and cloud methods and the cloud sync enums."""
    for (cls, name), method in _methods().items():
        setattr(cls, name, method)
    for number, suffix in enumerate(_CLOUD_SYNC, start=1):
        setattr(FakeResolve, f"CLOUD_SYNC_{suffix}", 1000.0 + number)


__all__ = ["add_database", "extend", "seed_settings_presets"]
