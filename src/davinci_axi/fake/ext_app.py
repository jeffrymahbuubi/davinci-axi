"""Fake app-level session switches for U23 (KTD17): priority, keyframe mode, background tasks.

`SetHighPriority`, `SetKeyframeMode` and `DisableBackgroundTasksForCurrentResolveSession` store
state on `FakeResolve`: `keyframe_mode`, and `flags["high_priority"]` and
`flags["background_tasks_disabled"]` (the real API has no getter for those two, so tests read
`resolve.flags`). Imports only from `davinci_axi.fake.core`, never from the `davinci_axi.fake`
package, because the package is still
mid-import when `EXTENSIONS` run. Each call keeps the `_rec` injection contract: an injected value
is returned (as `bool`), otherwise the side effect happens. `extend()` is idempotent.
"""

from __future__ import annotations

from davinci_axi.fake.core import FakeResolve


def _set_high_priority(self: FakeResolve, highPriority: bool) -> bool:
    injected, value = self._rec("SetHighPriority", highPriority)
    if injected:
        return bool(value)
    _flags(self)["high_priority"] = bool(highPriority)
    return True


def _set_keyframe_mode(self: FakeResolve, keyframeMode: float) -> bool:
    injected, value = self._rec("SetKeyframeMode", keyframeMode)
    if injected:
        return bool(value)
    known = {getattr(self, f"KEYFRAME_MODE_{name}", None) for name in ("ALL", "COLOR", "SIZING")}
    if keyframeMode not in known:
        return False
    self.keyframe_mode = int(keyframeMode)
    return True


def _disable_background_tasks(self: FakeResolve) -> bool:
    injected, value = self._rec("DisableBackgroundTasksForCurrentResolveSession")
    if injected:
        return bool(value)
    _flags(self)["background_tasks_disabled"] = True
    return True


def _flags(self: FakeResolve) -> dict[str, bool]:
    store: dict[str, bool] | None = self.__dict__.get("_app_flags")
    if store is None:
        store = {"high_priority": False, "background_tasks_disabled": False}
        self.__dict__["_app_flags"] = store
    return store


def extend() -> None:
    """Add the three session-switch methods and the `flags` property to `FakeResolve`."""
    methods = {
        "SetHighPriority": _set_high_priority,
        "SetKeyframeMode": _set_keyframe_mode,
        "DisableBackgroundTasksForCurrentResolveSession": _disable_background_tasks,
        "flags": property(_flags),
    }
    for name, method in methods.items():
        setattr(FakeResolve, name, method)


__all__ = ["extend"]
