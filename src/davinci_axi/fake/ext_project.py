"""Fake write methods for the project lifecycle (U10, KTD17).

Adds project and folder deletion, folder navigation, archiving and `Project.SetSettings`.
Imports only from `davinci_axi.fake.core|world`, never from the `davinci_axi.fake` package,
because the package is still mid-import when `EXTENSIONS` run. Each method keeps the `_rec`
injection contract: an injected value is returned as `bool`, otherwise the side effect happens.
`extend()` is idempotent.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from davinci_axi.fake.core import FakeProject, FakeProjectManager

PLACEHOLDER = "fake drp: "


def _delete_project(self: FakeProjectManager, projectName: str) -> bool:
    injected, value = self._rec("DeleteProject", projectName)
    if injected:
        return bool(value)
    project = self.folder.projects.get(projectName)
    if project is None or project is self.current_project:
        return False
    del self.folder.projects[projectName]
    return True


def _create_folder(self: FakeProjectManager, folderName: str) -> bool:
    injected, value = self._rec("CreateFolder", folderName)
    if injected:
        return bool(value)
    if not folderName or folderName in self.folder.children:
        return False
    self.add_folder(folderName)
    return True


def _delete_folder(self: FakeProjectManager, folderName: str) -> bool:
    injected, value = self._rec("DeleteFolder", folderName)
    if injected:
        return bool(value)
    child = self.folder.children.get(folderName)
    if child is None or child.projects or child.children:
        return False
    del self.folder.children[folderName]
    return True


def _open_folder(self: FakeProjectManager, folderName: str) -> bool:
    injected, value = self._rec("OpenFolder", folderName)
    if injected:
        return bool(value)
    if folderName not in self.folder.children:
        return False
    self.path.append(folderName)
    return True


def _goto_parent(self: FakeProjectManager) -> bool:
    injected, value = self._rec("GotoParentFolder")
    if injected:
        return bool(value)
    if not self.path:
        return False
    self.path.pop()
    return True


def _goto_root(self: FakeProjectManager) -> bool:
    injected, value = self._rec("GotoRootFolder")
    if injected:
        return bool(value)
    self.path.clear()
    return True


def _archive_project(
    self: FakeProjectManager,
    projectName: str,
    filePath: str,
    isArchiveSrcMedia: bool = True,
    isArchiveRenderCache: bool = True,
    isArchiveProxyMedia: bool = False,
) -> bool:
    injected, value = self._rec(
        "ArchiveProject",
        projectName,
        filePath,
        isArchiveSrcMedia,
        isArchiveRenderCache,
        isArchiveProxyMedia,
    )
    if injected:
        return bool(value)
    if projectName not in self.folder.projects:
        return False
    try:
        target = Path(filePath)
        target.mkdir(parents=True, exist_ok=True)
        (target / "project.drp").write_text(f"{PLACEHOLDER}{projectName}\n", encoding="utf-8")
    except OSError:
        return False
    return True


def _like(old: Any, raw: Any) -> Any:
    """Convert `raw` to the type of the current setting `old`, as Resolve normalises values."""
    if isinstance(old, bool):
        return str(raw).strip().lower() in ("true", "1", "yes", "on")
    if isinstance(old, int):
        return int(str(raw))
    if isinstance(old, float):
        return float(str(raw))
    return str(raw)


def _set_settings(self: FakeProject, settings: dict[str, Any]) -> bool:
    """Apply keys in order; the first unknown or unreadable key stops and returns False."""
    injected, value = self._rec("SetSettings", dict(settings))
    if injected:
        return bool(value)
    for key, raw in settings.items():
        if key not in self.settings:
            return False
        try:
            self.settings[key] = _like(self.settings[key], raw)
        except ValueError:
            return False
    return True


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeProjectManager, "DeleteProject"): _delete_project,
    (FakeProjectManager, "CreateFolder"): _create_folder,
    (FakeProjectManager, "DeleteFolder"): _delete_folder,
    (FakeProjectManager, "OpenFolder"): _open_folder,
    (FakeProjectManager, "GotoParentFolder"): _goto_parent,
    (FakeProjectManager, "GotoRootFolder"): _goto_root,
    (FakeProjectManager, "ArchiveProject"): _archive_project,
    (FakeProject, "SetSettings"): _set_settings,
}


def extend() -> None:
    """Add the project-lifecycle write methods to the fake core classes."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)


__all__ = ["PLACEHOLDER", "extend"]
