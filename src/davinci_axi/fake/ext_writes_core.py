"""Fake write methods shared by every phase-2 unit: project rename, import and close (KTD17).

Imports only from `davinci_axi.fake.core|media|world`, never from the `davinci_axi.fake`
package, because the package is still mid-import when `EXTENSIONS` run. Each method keeps the
`_rec` injection contract: an injected value is returned as `bool`, otherwise the side effect
happens. `extend()` is idempotent.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from davinci_axi.fake.core import FakeProject, FakeProjectFolder, FakeProjectManager
from davinci_axi.fake.media import FakeMediaPool

PLACEHOLDER = "fake drp: "


def _folders(folder: FakeProjectFolder) -> list[FakeProjectFolder]:
    found = [folder]
    for child in folder.children.values():
        found.extend(_folders(child))
    return found


def owner_project(pool: FakeMediaPool) -> FakeProject:
    """The project whose media pool is `pool`, found by walking the project-manager tree."""
    root = pool._world.resolve.pm.root
    for folder in _folders(root):
        for project in folder.projects.values():
            if project.media_pool is pool:
                return project
    raise LookupError("media pool belongs to no project in the fake world")


def _project_set_name(self: FakeProject, projectName: str) -> bool:
    injected, value = self._rec("SetName", projectName)
    if injected:
        return bool(value)
    if not projectName:
        return False
    for folder in _folders(self._world.resolve.pm.root):
        if folder.projects.get(self.name) is not self:
            continue
        if projectName != self.name and projectName in folder.projects:
            return False
        folder.projects = {
            (projectName if project is self else name): project
            for name, project in folder.projects.items()
        }
        break
    self.name = projectName
    return True


def _import_project(
    self: FakeProjectManager, filePath: str, projectName: str | None = None
) -> bool:
    injected, value = self._rec("ImportProject", filePath, projectName)
    if injected:
        return bool(value)
    try:
        with open(filePath, encoding="utf-8") as handle:
            first = handle.readline().rstrip("\n")
    except OSError:
        return False
    if not first.startswith(PLACEHOLDER):
        return False
    name = projectName or first[len(PLACEHOLDER) :]
    if not name or name in self.folder.projects:
        return False
    self.add_project(name)
    return True


def _close_project(self: FakeProjectManager, project: FakeProject) -> bool:
    injected, value = self._rec("CloseProject", project)
    if injected:
        return bool(value)
    if project is None or self.current_project is not project:
        return False
    self.current_project = None
    return True


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeProject, "SetName"): _project_set_name,
    (FakeProjectManager, "ImportProject"): _import_project,
    (FakeProjectManager, "CloseProject"): _close_project,
}


def extend() -> None:
    """Add Project.SetName, ProjectManager.ImportProject and ProjectManager.CloseProject."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)


__all__ = ["PLACEHOLDER", "extend", "owner_project"]
