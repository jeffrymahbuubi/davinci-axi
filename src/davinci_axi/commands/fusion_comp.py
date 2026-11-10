"""`fusion comp add|import|export|load|rename|delete`, `fusion clip`, `insert` and `cache` (U17b).

These run on the Resolve side (timeline item and timeline calls), so Fusion's own undo cannot
reverse them: a dry run until `--force`, then the project snapshot, the call and a read-back.
`comp delete` also exports the comp first. `SPEC` wraps U17a's `fusion.SPEC` (KTD15) with every
mutating verb; the registry points `fusion` here.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

from davinci_axi.commands import fusion
from davinci_axi.commands.fusion_clip import CACHE_SPEC, CLIP_SPEC, INSERT_SPEC
from davinci_axi.commands.fusion_edit import (
    ITEM_EXAMPLE,
    ITEM_FLAGS,
    ItemEdit,
    ItemFusionEdit,
    comp_names,
    head_extra,
    item_mutation,
    mut_spec,
    open_item,
    restore_hint,
)
from davinci_axi.commands.fusion_keys import KEYFRAME_SPEC, UNDO_SPEC
from davinci_axi.commands.fusion_tools import TOOL_SPEC
from davinci_axi.commands.item_detail import str_arg
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.fusion_guard import (
    FUSION_FLAGS,
    attrs_of,
    export_comp,
    fusion_app,
    fusion_call,
    preflight,
    resolve_comp,
    validate_target,
)
from davinci_axi.mutate import Change, MutationPlan, check_snapshot_flags, refuse_existing
from davinci_axi.output import collapse_home
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result


def _need_comp(names: list[str], name: str, ref: str) -> None:
    if name not in names:
        raise not_found("comp", f"{name} on item {ref}", f"Comps: {', '.join(names) or '(none)'}")


ITEM_COMP_NOTE = (
    "verified on 21.1.1: effects added through the API to an item comp do not render, "
    "in `render frame` stills or in full renders (a FilmGrain + Blur comp, links read back, "
    "rendered unchanged); standalone Text+ titles from `fusion insert title` do render"
)


def run_add(inv: Invocation) -> Result:
    edit = open_item(inv)
    item = edit.item
    before = comp_names(item)

    def apply(plan: MutationPlan) -> None:
        if fusion_call("AddFusionComp", item.AddFusionComp) is None:
            raise api_failed("AddFusionComp", "Resolve did not add a comp")
        new = [n for n in comp_names(item) if n not in before]
        count = int(fusion_call("GetFusionCompCount", item.GetFusionCompCount) or 0)
        plan.extra["count"] = count
        plan.extra["adopted"] = bool(new) and count >= len(before) + 1
        if not plan.extra["adopted"]:
            plan.next.insert(
                0,
                "the item does not list the new comp (F22, observed on 21.1.1); see the notes "
                "in `fusion --help`, and use `fusion insert title` for an effect that renders",
            )

    def verify() -> dict[str, object]:
        return {"comps": len(comp_names(item))}

    changes = [Change("comps", len(before), len(before) + 1)]
    warning = f"(F29/F22) {ITEM_COMP_NOTE}" + (
        ""
        if before
        else "; the item's first comp is permanent through the API (F17: DeleteFusionCompByName "
        "refuses an only comp) and the snapshot restores the project without it"
    )
    return item_mutation(
        inv, edit, "fusion comp add", ["fusion", "comp", "add"], changes, apply, verify,
        warning=warning,
    )  # fmt: skip


def _active_name(session: Session, names: list[str]) -> str | None:
    current = fusion_call("GetCurrentComp", fusion_app(session).GetCurrentComp)
    found = attrs_of(current).get("COMPS_Name") if current is not None else None
    return str(found) if found in names else None


def run_import(inv: Invocation) -> Result:
    path = Path(inv.positionals["file"]).expanduser().resolve()
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path; nothing was changed")
    edit = open_item(inv)
    item = edit.item
    before = comp_names(item)
    previous = _active_name(edit.session, before) or (before[0] if before else None)

    def apply(plan: MutationPlan) -> None:
        # quirk 2: ImportFusionComp does nothing on some items, so import through a dummy comp
        if fusion_call("AddFusionComp", item.AddFusionComp) is None:
            raise api_failed("AddFusionComp", "Resolve did not add the helper comp")
        dummy = next((n for n in comp_names(item) if n not in before), "")
        fusion_call("LoadFusionCompByName", item.LoadFusionCompByName, previous or dummy)
        imported = fusion_call("ImportFusionComp", item.ImportFusionComp, str(path))
        fusion_call("DeleteFusionCompByName", item.DeleteFusionCompByName, dummy)
        if imported is None:
            raise api_failed("ImportFusionComp", f"Resolve could not import {path.name}")

    def verify() -> dict[str, object]:
        return {"comps": len(comp_names(item))}

    words = ["fusion", "comp", "import", inv.positionals["file"]]
    return item_mutation(
        inv,
        edit,
        "fusion comp import",
        words,
        [Change("comps", len(before), len(before) + 1)],
        apply,
        verify,
        warning="adds a new comp through a helper comp (quirk 2); existing comps are untouched",
        extra={"file": collapse_home(path)},
    )


def run_export(inv: Invocation) -> Result:
    path = Path(inv.positionals["file"]).expanduser().resolve()
    refuse_existing(path, bool(inv.args.get("overwrite")))
    validate_target(inv, mutating=True)
    session = inv.connect()
    pre = preflight(session, inv)
    ref = resolve_comp(session, inv, mutating=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    item = cast(ItemFusionEdit, ref.item.item if ref.item else None)
    if not fusion_call("ExportFusionComp", item.ExportFusionComp, str(path), ref.comp_index):
        raise api_failed("ExportFusionComp", f"could not export comp {ref.comp_name}")
    if not path.is_file():
        raise api_failed("ExportFusionComp", f"Resolve wrote no {path.name}")
    data = head_extra(pre, ref.item_ref, ref.comp_name)
    data.update({"path": collapse_home(path), "bytes": path.stat().st_size})
    return Result(data, [restore_hint(path, ref.item_ref)])


def run_load(inv: Invocation) -> Result:
    name = inv.positionals["name"]
    edit = open_item(inv)
    item = edit.item
    names = comp_names(item)
    _need_comp(names, name, edit.item_ref)
    active = _active_name(edit.session, names)
    loaded: dict[str, str | None] = {}

    def apply(plan: MutationPlan) -> None:
        found = fusion_call("LoadFusionCompByName", item.LoadFusionCompByName, name)
        loaded["name"] = name if found is not None else None

    changes = [Change("active_comp", active, name)] if active != name else []
    words = ["fusion", "comp", "load", name]
    return item_mutation(
        inv, edit, "fusion comp load", words, changes, apply,
        lambda: {"active_comp": loaded.get("name")}, policy="none",
    )  # fmt: skip


def run_rename(inv: Invocation) -> Result:
    old, new = inv.positionals["name"], str(inv.args.get("to") or "").strip()
    if not new:
        raise AxiError(
            "VALIDATION_ERROR", "--to needs a new name", help=["Valid example: --to Hero"]
        )
    edit = open_item(inv)
    item = edit.item
    names = comp_names(item)
    _need_comp(names, old, edit.item_ref)
    if new != old and new in names:
        raise AxiError(
            "VALIDATION_ERROR",
            f"a comp named {new} already exists",
            help=[
                "Pick another name with --to",
                "List the comps with `davinci-axi fusion comps --item <ref>`",
            ],
        )

    def apply(plan: MutationPlan) -> None:
        if not fusion_call("RenameFusionCompByName", item.RenameFusionCompByName, old, new):
            raise api_failed("RenameFusionCompByName", f"could not rename comp {old}")

    def verify() -> dict[str, object]:
        return {"name": new if new in comp_names(item) else None}

    changes = [Change("name", old, new)] if new != old else []
    words = ["fusion", "comp", "rename", old, "--to", new]
    return item_mutation(inv, edit, "fusion comp rename", words, changes, apply, verify)


def run_delete(inv: Invocation) -> Result:
    name = inv.positionals["name"]
    check_snapshot_flags(inv)
    validate_target(inv, mutating=True)
    session = inv.connect()
    pre = preflight(session, inv)
    ref = resolve_comp(session, replace(inv, args={**inv.args, "comp": name}), mutating=True)
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    edit = ItemEdit(session, timeline, pre, ref.item_ref, ref.item)
    item = edit.item
    names = comp_names(item)
    only = len(names) == 1
    active = _active_name(session, names) == name

    def apply(plan: MutationPlan) -> None:
        path = export_comp(ref, "fusion comp delete")
        plan.extra["comp_snapshot"] = collapse_home(path)
        plan.next.append(restore_hint(path, ref.item_ref))
        if not only and active:
            other = next(n for n in names if n != name)
            fusion_call("LoadFusionCompByName", item.LoadFusionCompByName, other)
            plan.extra["loaded_first"] = other
        if not fusion_call("DeleteFusionCompByName", item.DeleteFusionCompByName, name):
            cause = (
                "Resolve 21.1.1 refused to delete an item's only comp live (F17); "
                "an empty comp is a MediaIn -> MediaOut pass-through and renders normally"
                if only
                else "The comp may be the loaded one; run `fusion comp load <other>` then retry "
                f"(comps: {', '.join(names)})"
            )
            raise AxiError(
                "API_FAILED",
                f"Resolve call DeleteFusionCompByName failed: could not delete comp {name}",
                help=[
                    cause,
                    "To drop the item's Fusion processing entirely, restore the snapshot taken "
                    "before `fusion comp add`",
                ],
                detail={"only_comp": only},
            )

    return item_mutation(
        inv, edit, "fusion comp delete", ["fusion", "comp", "delete", name],
        [Change("comp", name, None)], apply,
        lambda: {"comp": name if name in comp_names(item) else None},
        irreversible=True,
        warning=(
            "this is the item's only comp; Resolve 21.1.1 refused to delete an only comp live "
            "(F17): expect API_FAILED, or keep it as a pass-through"
            if only
            else "Fusion undo cannot bring a deleted comp back; the exported .comp file can"
        ),
        extra={"comps_before": len(names), "only_comp": only},
    )  # fmt: skip


_NAME = Positional("name", help="comp name on the item, such as Composition 1")
_METHODS = ("Resolve.Fusion", "TimelineItem.GetFusionCompNameList")

COMP_VERBS: dict[str, CommandSpec] = {
    "add": mut_spec(
        "add",
        "Add an empty comp to a timeline item",
        run_add,
        flags=ITEM_FLAGS,
        examples=(
            f"davinci-axi fusion comp add {ITEM_EXAMPLE}",
            f"davinci-axi fusion comp add {ITEM_EXAMPLE} --force",
        ),
        methods=(*_METHODS, "TimelineItem.AddFusionComp", "TimelineItem.GetFusionCompCount"),
        notes=(
            "Read-back: comps (names) and count (GetFusionCompCount); adopted is false when the "
            "item does not list the new comp",
            ITEM_COMP_NOTE,
            "`--via-page` fails when the Fusion page shows a different comp than the one the "
            "API created (observed on 21.1.1)",
        ),
    ),
    "import": mut_spec(
        "import",
        "Import a .comp file as a new comp (through a helper comp, quirk 2)",
        run_import,
        positionals=(Positional("file", help="the .comp file"),),
        flags=ITEM_FLAGS,
        examples=(f"davinci-axi fusion comp import ~/blur.comp {ITEM_EXAMPLE} --force",),
        methods=(
            *_METHODS,
            "Resolve.Fusion",
            "TimelineItem.AddFusionComp",
            "TimelineItem.ImportFusionComp",
            "TimelineItem.LoadFusionCompByName",
            "TimelineItem.DeleteFusionCompByName",
        ),
    ),
    "export": mut_spec(
        "export",
        "Export a comp to a .comp file",
        run_export,
        positionals=(Positional("file", help="output file"),),
        flags=(*FUSION_FLAGS, Flag("overwrite", "bool", False, help="replace the file")),
        examples=(f"davinci-axi fusion comp export ~/blur.comp {ITEM_EXAMPLE}",),
        methods=(*_METHODS, "TimelineItem.GetFusionCompByIndex", "TimelineItem.ExportFusionComp"),
    ),
    "load": mut_spec(
        "load",
        "Make a comp the active comp of its item",
        run_load,
        positionals=(_NAME,),
        flags=ITEM_FLAGS,
        examples=(f"davinci-axi fusion comp load 'Composition 2' {ITEM_EXAMPLE} --force",),
        methods=(*_METHODS, "TimelineItem.LoadFusionCompByName"),
    ),
    "rename": mut_spec(
        "rename",
        "Rename a comp",
        run_rename,
        positionals=(_NAME,),
        flags=(Flag("to", "str", None, required=True, help="new comp name"), *ITEM_FLAGS),
        examples=(
            f"davinci-axi fusion comp rename 'Composition 1' --to Hero {ITEM_EXAMPLE} --force",
        ),
        methods=(*_METHODS, "TimelineItem.RenameFusionCompByName"),
    ),
    "delete": mut_spec(
        "delete",
        "Delete a comp (irreversible; the comp is exported first)",
        run_delete,
        positionals=(_NAME,),
        flags=ITEM_FLAGS,
        examples=(f"davinci-axi fusion comp delete 'Composition 2' {ITEM_EXAMPLE} --force",),
        methods=(
            *_METHODS,
            "TimelineItem.ExportFusionComp",
            "TimelineItem.LoadFusionCompByName",
            "TimelineItem.DeleteFusionCompByName",
        ),
    ),
}

COMP_SPEC = replace(
    fusion.COMP_SPEC,
    summary="Inspect and manage the Fusion comps of a timeline item",
    subcommands={**fusion.COMP_SPEC.subcommands, **COMP_VERBS},
    reads_only=False,
    examples=(*fusion.COMP_SPEC.examples, f"davinci-axi fusion comp add {ITEM_EXAMPLE}"),
    methods=tuple(
        dict.fromkeys(
            m for s in COMP_VERBS.values() for m in (*fusion.COMP_SPEC.methods, *s.methods)
        )
    ),
)

_EXTRA: dict[str, CommandSpec] = {
    "tool": TOOL_SPEC,
    "keyframe": KEYFRAME_SPEC,
    "undo": UNDO_SPEC,
    "comp": COMP_SPEC,
    "clip": CLIP_SPEC,
    "insert": INSERT_SPEC,
    "cache": CACHE_SPEC,
}

SPEC = replace(
    fusion.SPEC,
    summary="Read and edit Fusion comps, tools and wiring of a timeline item",
    subcommands={**fusion.SPEC.subcommands, **_EXTRA},
    reads_only=False,
    examples=(
        *fusion.SPEC.examples,
        f"davinci-axi fusion tool add Blur {ITEM_EXAMPLE}",
        "davinci-axi fusion undo-stack --item V1:2",
    ),
    methods=tuple(
        dict.fromkeys((*fusion.SPEC.methods, *(m for s in _EXTRA.values() for m in s.methods)))
    ),
    notes=(
        *fusion.SPEC.notes,
        f"F29/F22, {ITEM_COMP_NOTE}. In `render frame` stills no ladder step changed the render "
        "(strength 3 and Blur 60 gave an identical md5); `fusion cache set auto|enabled|disabled` "
        'fails with API_FAILED on API-built comps; `--via-page` fails with "the Fusion page '
        'shows Composition4, not Composition 1" because the page opens a different comp than '
        "the one the API created. The steps tried, in order: 1 `fusion comp export` after the "
        "edit, if the .comp lacks your tool Resolve did not keep it; 2 `page set fusion --force` "
        "with the playhead on the item, then `fusion tools --active`; 3 `fusion cache set "
        "auto|enabled|disabled --item <id> --force` and re-render; 4 redo the edit with "
        "`--via-page`",
    ),
)
