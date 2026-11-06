"""`presets <kind> <verb>`: layout, keyboard, user-preference and burn-in presets (U22, R32, R35).

One table of `Kind` rows drives every verb (only the verbs each kind has, from the `.pyi`);
`project_extra.py` reuses `build_kind_spec` for project settings presets. Presets live in Resolve,
not in the project, so `update` and `delete` export the preset to `snapshot_root()/presets/` first
inside `apply()` and abort when that fails. `load` changes the UI only; `export` writes a file.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from davinci_axi.commands.item_detail import resolve_item
from davinci_axi.commands.project_io import (
    SAFETY_METHODS,
    clean_name,
    exists_error,
    next_steps,
    text_arg,
)
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    SnapshotPolicy,
    check_snapshot_flags,
    force_cmd,
    refuse_existing,
    run_mutation,
)
from davinci_axi.output import collapse_home, empty_state
from davinci_axi.session import Session
from davinci_axi.snapshot import snapshot_root
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

SCOPE = "this Resolve (presets are not stored in the project)"
APP_WARNING = "the preset lives in Resolve, not in the project, so the project snapshot lacks it"
WHERE = "this Resolve"
KIND_ORDER = ("layout", "keyboard", "userprefs", "burnin")


@dataclass(frozen=True)
class Kind:
    """One preset family: where its calls live and which verbs it has."""

    key: str
    noun: str
    words: tuple[str, ...]  # the command path before the verb
    owner: str  # "Resolve" or "Project": the API class the calls go to
    list_method: str
    verbs: dict[str, tuple[str, str]]  # verb word -> (role, API method)
    sample: str = "Default"
    current_method: str | None = None
    rows: bool = False  # the list returns {Name, Width, Height} dicts
    import_names: bool = True  # the import call takes an optional name
    load_policy: SnapshotPolicy = "none"


def _verbs(noun: str, *roles: str) -> dict[str, tuple[str, str]]:
    return {role: (role, f"{role.capitalize()}{noun}") for role in roles}


def _app(key: str, noun: str, api: str, roles: tuple[str, ...], **more: Any) -> Kind:
    list_method = f"Get{api}List"
    return Kind(key, noun, ("presets", key), "Resolve", list_method, _verbs(api, *roles), **more)


_FULL = ("load", "save", "update", "delete", "import", "export")
_NO_SAVE = ("load", "delete", "import", "export")
KINDS: dict[str, Kind] = {
    "layout": _app("layout", "layout preset", "LayoutPreset", _FULL),
    "keyboard": _app(
        "keyboard",
        "keyboard preset",
        "KeyboardPreset",
        _NO_SAVE,
        sample="Premiere Pro",
        current_method="GetCurrentKeyboardPreset",
    ),
    "userprefs": _app(
        "userprefs",
        "user preferences preset",
        "UserPreferencesPreset",
        ("load", "save", *_NO_SAVE[1:]),
    ),
    "burnin": _app(
        "burnin",
        "burn-in preset",
        "BurnInPreset",
        _NO_SAVE,
        sample="Source Timecode",
        import_names=False,
    ),
}


def _call(target: object, method: str, *args: object) -> Any:
    return getattr(target, method)(*args)


def _owner(session: Session, kind: Kind) -> object:
    return session.resolve if kind.owner == "Resolve" else session.require_project()


def names_of(kind: Kind, owner: object) -> list[str]:
    raw = _call(owner, kind.list_method) or []
    if kind.rows:
        return [str(r.get("Name")) for r in raw if isinstance(r, dict)]
    return [str(n) for n in raw]


def _require(kind: Kind, names: list[str], name: str) -> None:
    if name not in names:
        listing = ", ".join(names) if names else "(none)"
        raise not_found(kind.noun, name, f"Available {kind.noun}s: {listing}")


def _method(kind: Kind, role: str) -> str:
    return next(m for r, m in kind.verbs.values() if r == role)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "preset"


def backup_preset(kind: Kind, owner: object, name: str) -> Path:
    """Export the preset to `snapshot_root()/presets/`; SNAPSHOT_FAILED (nothing changed) if not."""
    root = snapshot_root() / "presets"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = root / f"{kind.key}-{_slug(name)}-{stamp}.preset"
    number = 1
    while path.exists():
        number += 1
        path = root / f"{kind.key}-{_slug(name)}-{stamp}-{number}.preset"
    ok = False
    try:
        root.mkdir(parents=True, exist_ok=True)
        ok = bool(_call(owner, _method(kind, "export"), name, str(path))) and path.exists()
    except OSError:
        ok = False
    if not ok:
        raise AxiError(
            "SNAPSHOT_FAILED",
            f"Resolve could not export {kind.noun} {name} before the change",
            help=["No change was made", "Check that Resolve is idle (no modal dialog), then retry"],
        )
    return path


def _hint(kind: Kind, verb: str, purpose: str) -> list[str]:
    return next_steps((" ".join((*kind.words, verb)), purpose))


def _run_list(kind: Kind) -> Callable[[Invocation], Result]:
    def run(inv: Invocation) -> Result:
        owner = _owner(inv.connect(), kind)
        raw = _call(owner, kind.list_method) or []
        keys = {"name": "Name", "width": "Width", "height": "Height"}
        rows = [{k: r.get(v) for k, v in keys.items()} for r in raw if isinstance(r, dict)]
        shown: object = rows if kind.rows else [str(n) for n in raw]
        data: dict[str, object] = {
            "scope": SCOPE,
            "kind": kind.key,
            "presets": shown or empty_state(f"{kind.noun}s", "in Resolve", {}),
        }
        current = _hint(kind, "current", f"show the current {kind.noun}")
        return Result(data, current if kind.current_method else [])

    return run


def _run_current(kind: Kind) -> Callable[[Invocation], Result]:
    def run(inv: Invocation) -> Result:
        owner = _owner(inv.connect(), kind)
        assert kind.current_method is not None
        data: dict[str, object] = {"kind": kind.key, "current": _call(owner, kind.current_method)}
        return Result(data, _hint(kind, "list", f"list the {kind.noun}s"))

    return run


def _load_target(inv: Invocation, session: Session, kind: Kind) -> tuple[object, str]:
    """(object to call, plan target text with a `{name}` slot) for the load call."""
    if kind.key != "burnin":
        return _owner(session, kind), f"{kind.noun} {{name}}"
    ref = text_arg(inv, "item")
    if ref is None:
        return session.require_project(), "burn-in preset {name} on the project"
    located = resolve_item(session.require_timeline(), ref)
    return located.item, f"burn-in preset {{name}} on item {ref}"


def _run_load(kind: Kind, verb: str) -> Callable[[Invocation], Result]:
    method = _method(kind, "load")

    def run(inv: Invocation) -> Result:
        name = clean_name(inv.positionals["name"], "preset name")
        check_snapshot_flags(inv)
        session = inv.connect()
        owner = _owner(session, kind)
        names = names_of(kind, owner)
        _require(kind, names, name)
        target, label = _load_target(inv, session, kind)
        before = _call(owner, kind.current_method) if kind.current_method else None
        item = text_arg(inv, "item")
        words = [*kind.words, verb, name, *(["--item", item] if item else [])]
        if kind.current_method:
            changes = [] if before == name else [Change("current preset", before, name)]
            warning = "already the current preset" if before == name else ""
        else:
            changes = [Change("called", False, True)]
            warning = f"the API cannot read which {kind.noun} is loaded; the call result is checked"
        if kind.load_policy == "required":
            warning = "replaces the project's settings with the preset's; the snapshot holds them"
        plan = MutationPlan(
            command=" ".join((*kind.words, verb)),
            target=label.format(name=name),
            changes=changes,
            force_cmd=force_cmd(inv, *words),
            warning=warning,
            snapshot=kind.load_policy,
        )
        done = {"ok": False}

        def apply() -> None:
            if not _call(target, method, name):
                raise api_failed(method, f"could not load {kind.noun} {name}")
            done["ok"] = True

        def verify() -> dict[str, object]:
            if kind.current_method:
                return {"current preset": _call(owner, kind.current_method)}
            return {"called": done["ok"] and name in names_of(kind, owner)}

        return run_mutation(inv, session, plan, apply, verify)

    return run


def _run_change(kind: Kind, verb: str, role: str) -> Callable[[Invocation], Result]:
    """`save`, `update` and `delete`: one flow that differs in the guard, change and backup."""
    method = _method(kind, role)

    def run(inv: Invocation) -> Result:
        name = clean_name(inv.positionals["name"], "preset name")
        check_snapshot_flags(inv)
        session = inv.connect()
        owner = _owner(session, kind)
        names = names_of(kind, owner)
        if role == "save" and name in names:
            raise exists_error(kind.noun, name, WHERE)
        if role != "save":
            _require(kind, names, name)
        change = {
            "save": Change("preset", None, name),
            "update": Change("called", False, True),
            "delete": Change("preset", name, None),
        }[role]
        warning = APP_WARNING
        if role != "save":
            warning = f"{'overwrites' if role == 'update' else 'deletes'} {kind.noun} {name}; "
            warning += f"{APP_WARNING}; a copy is exported first"
        plan = MutationPlan(
            command=" ".join((*kind.words, verb)),
            target=f"{kind.noun} {name}",
            changes=[change],
            force_cmd=force_cmd(inv, *kind.words, verb, name),
            irreversible=role != "save",
            warning=warning,
            snapshot="required",
            next=_hint(kind, "list", f"list the {kind.noun}s"),
        )
        done = {"ok": False}

        def apply() -> None:
            if role != "save":
                plan.extra["preset_backup"] = collapse_home(backup_preset(kind, owner, name))
            if not _call(owner, method, name):
                raise api_failed(method, f"could not {role} {kind.noun} {name}")
            done["ok"] = True

        def verify() -> dict[str, object]:
            listed = name in names_of(kind, owner)
            if role == "update":
                return {"called": done["ok"] and listed}
            return {"preset": name if listed else None}

        return run_mutation(inv, session, plan, apply, verify)

    return run


def _run_import(kind: Kind, verb: str) -> Callable[[Invocation], Result]:
    method = _method(kind, "import")

    def run(inv: Invocation) -> Result:
        raw_file = inv.positionals["file"]
        path = Path(raw_file).expanduser().resolve()
        given = text_arg(inv, "name")
        name = clean_name(given, "preset name") if given is not None else None
        if not path.is_file():
            raise not_found("file", collapse_home(path), f"Check the path of the {kind.noun} file")
        check_snapshot_flags(inv)
        session = inv.connect()
        owner = _owner(session, kind)
        before = names_of(kind, owner)
        guess = name or path.stem
        if guess in before:
            raise exists_error(kind.noun, guess, WHERE)
        words = [*kind.words, verb, raw_file]
        if name:
            words += ["--name", name]
        count = len(before)
        change = Change("preset", None, name) if name else Change("presets", count, count + 1)
        plan = MutationPlan(
            command=" ".join((*kind.words, verb)),
            target=f"file {collapse_home(path)}",
            changes=[change],
            force_cmd=force_cmd(inv, *words),
            warning=APP_WARNING
            if name
            else f"the {kind.noun} is named after the file's own name; {APP_WARNING}",
            snapshot="required",
            extra={"file": collapse_home(path)},
            next=_hint(kind, "list", f"list the {kind.noun}s"),
        )

        def apply() -> None:
            args: tuple[object, ...] = (str(path),)
            if name and kind.import_names:
                args = (str(path), name)
            if not _call(owner, method, *args):
                raise api_failed(method, f"Resolve refused the {kind.noun} file {path.name}")
            added = [n for n in names_of(kind, owner) if n not in before]
            plan.extra["imported_as"] = ", ".join(added) if added else "(unknown)"

        def verify() -> dict[str, object]:
            now = names_of(kind, owner)
            if name:
                return {"preset": name if name in now else None}
            return {"presets": len(now)}

        return run_mutation(inv, session, plan, apply, verify)

    return run


def _run_export(kind: Kind, verb: str) -> Callable[[Invocation], Result]:
    method = _method(kind, "export")

    def run(inv: Invocation) -> Result:
        name = clean_name(inv.positionals["name"], "preset name")
        path = Path(inv.positionals["file"]).expanduser().resolve()
        refuse_existing(path, bool(inv.args.get("overwrite")))
        owner = _owner(inv.connect(), kind)
        _require(kind, names_of(kind, owner), name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not _call(owner, method, name, str(path)):
            raise api_failed(method, f"could not export {kind.noun} {name} to {path.name}")
        if not path.exists():
            raise api_failed(method, f"Resolve reported success but wrote no {path.name}")
        shown, size = collapse_home(path), path.stat().st_size
        data: dict[str, object] = {"kind": kind.key, "preset": name, "path": shown, "bytes": size}
        return Result(data, _hint(kind, "import", f"import the file as a {kind.noun}"))

    return run


_RUNNERS: dict[str, Callable[[Kind, str], Callable[[Invocation], Result]]] = {
    "load": _run_load,
    "save": lambda k, v: _run_change(k, v, "save"),
    "update": lambda k, v: _run_change(k, v, "update"),
    "delete": lambda k, v: _run_change(k, v, "delete"),
    "import": _run_import,
    "export": _run_export,
}


def _verb_spec(kind: Kind, verb: str, role: str, method: str) -> CommandSpec:
    noun, cls, sample = kind.noun, kind.owner, kind.sample
    path = " ".join((*kind.words, verb))
    call = (f"{cls}.{method}", f"{cls}.{kind.list_method}")
    save = (*SAFETY_METHODS, *call)
    both = (*save, f"{cls}.{_method(kind, 'export')}")
    loads = (*call, *((f"{cls}.{kind.current_method}",) if kind.current_method else ()))
    applies = kind.load_policy == "none"
    summary = {
        "load": f"Load a {noun}; changes the UI or session only (dry run until --force)"
        if applies
        else f"Apply a {noun} to the project (dry run until --force)",
        "save": f"Save the current state as a new {noun} (dry run until --force)",
        "update": f"Overwrite a {noun} with the current state (irreversible; exports a copy first)",
        "delete": f"Delete a {noun} (irreversible; exports a copy first)",
        "import": f"Import a {noun} from a file (dry run until --force)",
        "export": f"Export a {noun} to a file (no --force needed; never overwrites)",
    }[role]
    methods = {
        "load": loads if applies else (*SAFETY_METHODS, *loads),
        "save": save,
        "update": both,
        "delete": both,
        "import": save,
        "export": call,
    }[role]
    named = (Positional("name", help=f"the {noun} name"),)
    file_arg = Positional("file", help=f"path of the {noun} file")
    positionals = {"import": (file_arg,), "export": (*named, file_arg)}.get(role, named)
    name_flag = (Flag("name", "str", None, help="name for the preset"),)
    flags: tuple[Flag, ...] = MUTATION_FLAGS
    if role == "import" and kind.import_names:
        flags = (*MUTATION_FLAGS, *name_flag)
    if role == "export":
        flags = (Flag("overwrite", "bool", False, help="replace the file if it exists"),)
    target, sample_file = _q("mine" if role == "save" else sample), f"~/presets/{sample}.preset"
    examples: tuple[str, ...] = (
        f"davinci-axi {path} {target}",
        f"davinci-axi {path} {target} --force",
    )
    if role in ("import", "export"):
        first = target if role == "export" else ""
        examples = (f"davinci-axi {path} {first} {sample_file}".replace("  ", " "),)
    if kind.key == "burnin" and role == "load":
        item = Flag("item", "str", None, help="load it onto this timeline item (V1:1 or its id)")
        flags = (*MUTATION_FLAGS, item)
        methods = ("Project.LoadBurnInPreset", "TimelineItem.LoadBurnInPreset", call[1])
        examples = (*examples, f"davinci-axi {path} {target} --item V1:1 --force")
    return CommandSpec(
        name=verb,
        summary=summary,
        run=_RUNNERS[role](kind, verb),
        positionals=positionals,
        flags=flags,
        examples=examples,
        reads_only=False,
        methods=methods,
    )


def _q(text: str) -> str:
    return f"'{text}'" if " " in text else text


def build_kind_spec(kind: Kind, name: str | None = None) -> CommandSpec:
    """The parent spec for one preset kind: `list`, `current` (if any) and its verbs."""
    path = " ".join(kind.words)
    list_spec = CommandSpec(
        name="list",
        summary=f"List the {kind.noun}s",
        run=_run_list(kind),
        examples=(f"davinci-axi {path} list", f"davinci-axi {path} list --json"),
        methods=(f"{kind.owner}.{kind.list_method}",),
    )
    subs: dict[str, CommandSpec] = {"list": list_spec}
    if kind.current_method:
        subs["current"] = CommandSpec(
            name="current",
            summary=f"Show the current {kind.noun}",
            run=_run_current(kind),
            examples=(f"davinci-axi {path} current",),
            methods=(f"{kind.owner}.{kind.current_method}",),
        )
    for verb, (role, method) in kind.verbs.items():
        subs[verb] = _verb_spec(kind, verb, role, method)
    return CommandSpec(
        name=name or kind.key,
        summary=f"Manage {kind.noun}s: list, use, import and export them",
        subcommands=subs,
        examples=(f"davinci-axi {path} list", f"davinci-axi {path} export --help"),
        reads_only=False,
        methods=tuple(dict.fromkeys(m for s in subs.values() for m in s.methods)),
    )


_SUBCOMMANDS: dict[str, CommandSpec] = {k: build_kind_spec(KINDS[k]) for k in KIND_ORDER}

SPEC = CommandSpec(
    name="presets",
    summary="Manage layout, keyboard, user-preference and burn-in presets",
    subcommands=_SUBCOMMANDS,
    examples=(
        "davinci-axi presets layout list",
        "davinci-axi presets keyboard load 'Premiere Pro' --force",
        "davinci-axi presets burnin load 'Source Timecode' --item V1:1 --force",
    ),
    reads_only=False,
    methods=tuple(dict.fromkeys(m for s in _SUBCOMMANDS.values() for m in s.methods)),
)
