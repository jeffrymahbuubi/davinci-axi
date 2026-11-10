"""Lazy command registry: `name -> "module:SPEC"`. Modules are imported only on dispatch."""

from __future__ import annotations

import importlib

from davinci_axi.spec import CommandSpec

HOME = "davinci_axi.commands.home:SPEC"

# One line per command, added by the lead in this order: projects, timelines, items, item,
# render, project, skill, hook. `home` is implicit (no arguments, or the `home` command).
COMMANDS: dict[str, str] = {
    "help": "davinci_axi.commands.help_find:SPEC",
    "projects": "davinci_axi.commands.projects:SPEC",
    "timelines": "davinci_axi.commands.timelines:SPEC",
    "timeline": "davinci_axi.commands.timeline_extra:SPEC",
    "items": "davinci_axi.commands.items:SPEC",
    "item": "davinci_axi.commands.item_extra:SPEC",
    "media": "davinci_axi.commands.media_extra:SPEC",
    "render": "davinci_axi.commands.render:SPEC",
    "fusion": "davinci_axi.commands.fusion_comp:SPEC",
    "color": "davinci_axi.commands.color:SPEC",
    "audio": "davinci_axi.commands.audio:SPEC",
    "ai": "davinci_axi.commands.ai:SPEC",
    "storage": "davinci_axi.commands.storage:SPEC",
    "presets": "davinci_axi.commands.presets:SPEC",
    "dctl": "davinci_axi.commands.dctl:SPEC",
    "project": "davinci_axi.commands.project_extra:SPEC",
    "snapshot": "davinci_axi.commands.snapshot:SPEC",
    "page": "davinci_axi.commands.page:SPEC",
    "app": "davinci_axi.commands.app:SPEC",
    "skill": "davinci_axi.commands.skill:SPEC",
    "hook": "davinci_axi.commands.hook:SPEC",
}


def load(ref: str) -> CommandSpec:
    """Import `module:ATTR` and return the `CommandSpec` it names."""
    module_name, _, attr = ref.partition(":")
    spec = getattr(importlib.import_module(module_name), attr)
    if not isinstance(spec, CommandSpec):
        raise TypeError(f"{ref} is not a CommandSpec")
    return spec


def command_names() -> list[str]:
    """`home` first, then the registered commands in registry order."""
    return ["home", *COMMANDS]


def lookup(name: str) -> CommandSpec | None:
    """The spec for a top-level command name, or None when it is not registered."""
    if name == "home":
        return load(HOME)
    ref = COMMANDS.get(name)
    return load(ref) if ref else None
