"""`media` with the extra pool verbs (U21): `SPEC` wraps `media.SPEC` and the registry points here.

Adds `refresh`, `selection`, `third-party`, `property`, `replace`, `fullres`, `monitor`, `stereo`
and extends the `metadata`, `marker` and `folder` groups of U13 (KTD15, wrap-and-repoint, as
`project_extra` does for `project`). Every verb is a dry run until `--force` and goes through
`mutate.run_mutation`; calls without a getter (`RefreshFolders`, `LinkFullResolutionMedia`,
`MonitorGrowingFile`) say "not read back" and verify by the call's return value (KTD23). The mark
and custom marker verbs live in `media_extra_marks`, the file verbs in `media_extra_io`.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from davinci_axi.commands import media
from davinci_axi.commands.item_detail import _properties as truncated_block
from davinci_axi.commands.media_edit import CLIP, MARKER_SPEC, METADATA_SPEC
from davinci_axi.commands.media_extra_common import ITEM, POOL, clip_plan, open_clip, pool_of
from davinci_axi.commands.media_extra_io import (
    FOLDER_EXPORT_SPEC,
    FOLDER_IMPORT_SPEC,
    METADATA_EXPORT_SPEC,
)
from davinci_axi.commands.media_extra_marks import CUSTOM_SPEC, MARK_SPEC
from davinci_axi.commands.media_folders import (
    FOLDER_SPEC,
    abs_path,
    missing,
    open_pool,
    props,
    resolve_clip,
    verb_spec,
    where_is,
)
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import Change, MutationPlan, force_cmd, parse_assignments, run_mutation
from davinci_axi.output import empty_state, hint
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

PATH = Positional("path", help="absolute path of the full resolution media")
FILE = Positional("file", help="the replacement media file")
NOT_READ_BACK = "not read back: Resolve has no getter for this change"


def run_refresh(inv: Invocation) -> Result:
    ctx = open_pool(inv)
    pool = pool_of(ctx)
    plan = MutationPlan(
        command="media refresh",
        target="the media pool",
        changes=[Change("called", False, True)],
        force_cmd=force_cmd(inv, "media", "refresh"),
        snapshot="none",
        extra={"note": NOT_READ_BACK},
        next=[hint("media", "list the media pool")],
    )

    def apply() -> None:
        if not pool.RefreshFolders():
            raise api_failed(
                "MediaPool.RefreshFolders",
                "Resolve refused; this only applies in collaboration mode",
            )

    return run_mutation(inv, ctx.session, plan, apply, lambda: {"called": True})


def run_selection_show(inv: Invocation) -> Result:
    ctx = open_pool(inv)
    chosen = list(ctx.pool.GetSelectedClips() or [])
    rows = [
        {"id": c.GetUniqueId(), "name": c.GetName(), "folder": where_is(ctx, c.GetUniqueId())}
        for c in chosen
    ]
    selected = rows or empty_state("clips", "selected in the media pool", {})
    return Result({"selected": selected})


def run_selection_set(inv: Invocation) -> Result:
    ctx, found, clip = open_clip(inv)
    pool = pool_of(ctx)
    names = [c.GetName() for c in ctx.pool.GetSelectedClips() or []]
    same = [c.GetUniqueId() for c in ctx.pool.GetSelectedClips() or []] == [clip.GetUniqueId()]
    plan = clip_plan(
        inv,
        found,
        "media selection set",
        [] if same else [Change("selected", names, [clip.GetName()])],
        snapshot="none",
    )

    def apply() -> None:
        if not pool.SetSelectedClip(clip):
            raise api_failed("MediaPool.SetSelectedClip", f"could not select {clip.GetName()}")

    def verify() -> dict[str, object]:
        return {"selected": [c.GetName() for c in ctx.pool.GetSelectedClips() or []]}

    return run_mutation(inv, ctx.session, plan, apply, verify)


def run_third_party_show(inv: Invocation) -> Result:
    ref, full = inv.positionals["clip"], bool(inv.args.get("full"))
    ctx = open_pool(inv)
    clip = resolve_clip(ctx, ref).clip
    live = clip.GetThirdPartyMetadata()
    full_cmd = f"{inv.bin_name} media third-party show {shlex.quote(ref)} --full"
    block, note = truncated_block(live if isinstance(live, dict) else {}, full, full_cmd)
    data: dict[str, object] = {"id": clip.GetUniqueId(), "name": clip.GetName()}
    data["third_party"] = block or empty_state(
        "third party metadata", f"on clip {clip.GetName()}", {}
    )
    if note:
        data["third_party_note"] = note
    return Result(data)


def run_third_party_set(inv: Invocation) -> Result:
    wanted = parse_assignments(inv.positionals["assignments"])
    ctx, found, clip = open_clip(inv)
    live = clip.GetThirdPartyMetadata()
    live = live if isinstance(live, dict) else {}
    changed = {k: v for k, v in wanted.items() if str(live.get(k, "")) != v}
    plan = clip_plan(
        inv,
        found,
        "media third-party set",
        [Change(k, live.get(k, ""), v) for k, v in changed.items()],
        inv.positionals["assignments"],
    )

    def apply() -> None:
        if not clip.SetThirdPartyMetadata(dict(changed)):
            raise api_failed("MediaPoolItem.SetThirdPartyMetadata", "no metadata key was applied")

    def verify() -> dict[str, object]:
        now = clip.GetThirdPartyMetadata()
        now = now if isinstance(now, dict) else {}
        return {k: now.get(k) for k in changed}

    return run_mutation(inv, ctx.session, plan, apply, verify)


def _lenient(expected: str, actual: Any) -> object:
    """Resolve normalises numbers ("10" reads back "10.0"); treat equal numbers as equal."""
    if str(actual) == expected:
        return actual
    try:
        return expected if float(expected) == float(actual) else actual
    except (TypeError, ValueError):
        return actual


def run_property_set(inv: Invocation) -> Result:
    wanted = parse_assignments(inv.positionals["assignments"])
    ctx, found, clip = open_clip(inv)
    live = props(clip)
    changed = {k: v for k, v in wanted.items() if str(live.get(k, "")) != v}
    plan = clip_plan(
        inv,
        found,
        "media property set",
        [Change(k, live.get(k, ""), v) for k, v in changed.items()],
        inv.positionals["assignments"],
    )

    def apply() -> None:
        done: list[str] = []
        for key, value in changed.items():
            if not clip.SetClipProperty(key, value):
                applied = ", ".join(done) or "none"
                raise api_failed(
                    "MediaPoolItem.SetClipProperty",
                    f"could not set {key} (already applied: {applied})",
                )
            done.append(key)

    def verify() -> dict[str, object]:
        return {k: _lenient(v, clip.GetClipProperty(k)) for k, v in changed.items()}

    return run_mutation(inv, ctx.session, plan, apply, verify)


def run_replace(inv: Invocation) -> Result:
    given = inv.positionals["file"]
    path = abs_path(given)
    if not os.path.isfile(path):
        raise missing(given, "file")
    preserve = bool(inv.args.get("preserve_subclip"))
    ctx, found, clip = open_clip(inv)
    before = str(props(clip).get("File Path") or "")
    plan = clip_plan(
        inv,
        found,
        "media replace",
        [Change("path", before, path)] if before != path else [],
        given,
        *(["--preserve-subclip"] if preserve else []),
        warning="replaces the underlying file and the metadata of the clip; the snapshot undoes it",
    )

    def apply() -> None:
        method = "ReplaceClipPreserveSubClip" if preserve else "ReplaceClip"
        ok = clip.ReplaceClipPreserveSubClip(path) if preserve else clip.ReplaceClip(path)
        if not ok:
            raise api_failed(f"MediaPoolItem.{method}", f"could not replace the clip with {path}")

    def verify() -> dict[str, object]:
        return {"path": str(props(clip).get("File Path") or "")}

    return run_mutation(inv, ctx.session, plan, apply, verify)


def run_fullres_link(inv: Invocation) -> Result:
    given = inv.positionals["path"]
    path = abs_path(given)
    if not os.path.exists(path):
        raise missing(given, "full resolution media")
    ctx, found, clip = open_clip(inv)
    plan = clip_plan(
        inv,
        found,
        "media fullres link",
        [Change("called", False, True)],
        given,
        warning="links proxy media to this full resolution file; " + NOT_READ_BACK,
    )

    def apply() -> None:
        if not clip.LinkFullResolutionMedia(path):
            raise api_failed("MediaPoolItem.LinkFullResolutionMedia", f"could not link {path}")

    return run_mutation(inv, ctx.session, plan, apply, lambda: {"called": True})


def run_monitor(inv: Invocation) -> Result:
    ctx, found, clip = open_clip(inv)
    plan = clip_plan(
        inv,
        found,
        "media monitor",
        [Change("called", False, True)],
        warning="watches the file while it keeps growing; " + NOT_READ_BACK,
    )

    def apply() -> None:
        if not clip.MonitorGrowingFile():
            raise api_failed("MediaPoolItem.MonitorGrowingFile", "could not monitor the file")

    return run_mutation(inv, ctx.session, plan, apply, lambda: {"called": True})


def run_stereo_create(inv: Invocation) -> Result:
    ctx = open_pool(inv)
    left = resolve_clip(ctx, inv.positionals["left"]).clip
    right = resolve_clip(ctx, inv.positionals["right"]).clip
    if left.GetUniqueId() == right.GetUniqueId():
        raise AxiError(
            "VALIDATION_ERROR",
            "the left and right clip are the same clip",
            help=["Pass two different clips, such as: media stereo create A001_beach A002_city"],
        )
    plan = MutationPlan(
        command="media stereo create",
        target=f"2 clips ({left.GetName()}, {right.GetName()})",
        changes=[Change("stereo clip", None, "created")],
        force_cmd=force_cmd(
            inv, "media", "stereo", "create", inv.positionals["left"], inv.positionals["right"]
        ),
        warning="replaces the two input clips in the media pool with one stereoscopic clip",
        next=[hint("media", "list the media pool")],
    )
    made: list[str] = []

    def apply() -> None:
        clip = pool_of(ctx).CreateStereoClip(left, right)
        if clip is None:
            raise api_failed("MediaPool.CreateStereoClip", "Resolve created no stereo clip")
        made.append(clip.GetUniqueId())
        plan.extra["created"] = clip.GetName()

    def verify() -> dict[str, object]:
        return {"stereo clip": "created" if made and where_is(ctx, made[0]) else "not in the pool"}

    return run_mutation(inv, ctx.session, plan, apply, verify)


def _group(name: str, summary: str, **subcommands: CommandSpec) -> CommandSpec:
    methods = tuple(dict.fromkeys(m for s in subcommands.values() for m in s.methods))
    return CommandSpec(
        name=name, summary=summary, subcommands=subcommands, reads_only=False, methods=methods
    )


REFRESH_SPEC = verb_spec(
    "refresh",
    "Refresh the media pool folders in collaboration mode (not read back; no snapshot)",
    run_refresh,
    (),
    (),
    ("davinci-axi media refresh", "davinci-axi media refresh --force"),
    (POOL + "RefreshFolders",),
)
SELECTION_SHOW_SPEC = CommandSpec(
    name="show",
    summary="List the clips selected in the media pool",
    run=run_selection_show,
    examples=("davinci-axi media selection show", "davinci-axi media selection show --json"),
    methods=(POOL + "GetSelectedClips",),
)
SELECTION_SET_SPEC = verb_spec(
    "set",
    "Select one clip in the media pool (no snapshot)",
    run_selection_set,
    (CLIP,),
    (),
    (
        "davinci-axi media selection set A001_beach",
        "davinci-axi media selection set A001_beach --force",
    ),
    (POOL + "GetSelectedClips", POOL + "SetSelectedClip"),
)
SELECTION_SPEC = _group(
    "selection",
    "Show or set the media pool selection",
    show=SELECTION_SHOW_SPEC,
    set=SELECTION_SET_SPEC,
)
THIRD_PARTY_SHOW_SPEC = CommandSpec(
    name="show",
    summary="Show the third party metadata of a clip",
    run=run_third_party_show,
    positionals=(CLIP,),
    flags=(Flag("full", "bool", False, help="do not truncate long blocks"),),
    examples=(
        "davinci-axi media third-party show A001_beach",
        "davinci-axi media third-party show A001_beach --full --json",
    ),
    methods=(ITEM + "GetThirdPartyMetadata",),
)
THIRD_PARTY_SET_SPEC = verb_spec(
    "set",
    "Set third party metadata keys on a clip",
    run_third_party_set,
    (CLIP, Positional("assignments", help="comma-separated Key=value pairs")),
    (),
    (
        "davinci-axi media third-party set A001_beach Reel=A12",
        "davinci-axi media third-party set A001_beach Reel=A12,Take=3 --force",
    ),
    (ITEM + "GetThirdPartyMetadata", ITEM + "SetThirdPartyMetadata"),
)
THIRD_PARTY_SPEC = _group(
    "third-party",
    "Show or set the third party metadata of a clip",
    show=THIRD_PARTY_SHOW_SPEC,
    set=THIRD_PARTY_SET_SPEC,
)
PROPERTY_SET_SPEC = verb_spec(
    "set",
    "Set clip properties such as Alpha mode or Input LUT",
    run_property_set,
    (CLIP, Positional("assignments", help="comma-separated Property=value pairs")),
    (),
    (
        "davinci-axi media property set A001_beach 'Alpha mode=Straight'",
        "davinci-axi media property set A001_beach 'Alpha mode=Straight' --force",
    ),
    (ITEM + "GetClipProperty", ITEM + "SetClipProperty"),
)
PROPERTY_SPEC = _group("property", "Set clip properties", set=PROPERTY_SET_SPEC)
REPLACE_SPEC = verb_spec(
    "replace",
    "Replace the file behind a clip (keeps its place in timelines)",
    run_replace,
    (CLIP, FILE),
    (Flag("preserve-subclip", "bool", False, help="keep the sub clip extents of the clip"),),
    (
        "davinci-axi media replace A001_beach ~/new/A001_beach.mov",
        "davinci-axi media replace A001_beach ~/new.mov --preserve-subclip --force",
    ),
    (ITEM + "GetClipProperty", ITEM + "ReplaceClip", ITEM + "ReplaceClipPreserveSubClip"),
)
FULLRES_LINK_SPEC = verb_spec(
    "link",
    "Link proxy clips to their full resolution media (not read back)",
    run_fullres_link,
    (CLIP, PATH),
    (),
    (
        "davinci-axi media fullres link A001_beach /Volumes/Media/A001_beach.mov",
        "davinci-axi media fullres link A001_beach /Volumes/Media/A001_beach.mov --force",
    ),
    (ITEM + "LinkFullResolutionMedia",),
)
FULLRES_SPEC = _group("fullres", "Link full resolution media", link=FULLRES_LINK_SPEC)
MONITOR_SPEC = verb_spec(
    "monitor",
    "Monitor a clip's file while it keeps growing (not read back)",
    run_monitor,
    (CLIP,),
    (),
    ("davinci-axi media monitor A001_beach", "davinci-axi media monitor A001_beach --force"),
    (ITEM + "MonitorGrowingFile",),
)
STEREO_CREATE_SPEC = verb_spec(
    "create",
    "Combine a left and a right clip into one stereoscopic clip",
    run_stereo_create,
    (Positional("left", help="left eye clip"), Positional("right", help="right eye clip")),
    (),
    (
        "davinci-axi media stereo create A001_beach A002_city",
        "davinci-axi media stereo create A001_beach A002_city --force",
    ),
    (POOL + "CreateStereoClip", POOL + "GetRootFolder"),
)
STEREO_SPEC = _group("stereo", "Create stereoscopic clips", create=STEREO_CREATE_SPEC)

METADATA_EXT_SPEC = replace(
    METADATA_SPEC,
    subcommands={**METADATA_SPEC.subcommands, "export": METADATA_EXPORT_SPEC},
    methods=METADATA_EXPORT_SPEC.methods,
)
MARKER_EXT_SPEC = replace(
    MARKER_SPEC,
    subcommands={**MARKER_SPEC.subcommands, "custom": CUSTOM_SPEC},
    methods=CUSTOM_SPEC.methods,
)
FOLDER_EXT_SPEC = replace(
    FOLDER_SPEC,
    subcommands={
        **FOLDER_SPEC.subcommands,
        "import": FOLDER_IMPORT_SPEC,
        "export": FOLDER_EXPORT_SPEC,
    },
    methods=tuple(dict.fromkeys((*FOLDER_IMPORT_SPEC.methods, *FOLDER_EXPORT_SPEC.methods))),
)
_EXTRA: dict[str, CommandSpec] = {
    "refresh": REFRESH_SPEC,
    "selection": SELECTION_SPEC,
    "metadata": METADATA_EXT_SPEC,
    "third-party": THIRD_PARTY_SPEC,
    "property": PROPERTY_SPEC,
    "replace": REPLACE_SPEC,
    "fullres": FULLRES_SPEC,
    "monitor": MONITOR_SPEC,
    "mark": MARK_SPEC,
    "marker": MARKER_EXT_SPEC,
    "stereo": STEREO_SPEC,
    "folder": FOLDER_EXT_SPEC,
}


def _methods(specs: Iterable[CommandSpec]) -> Iterable[str]:
    for spec in specs:
        yield from spec.methods
        yield from _methods(spec.subcommands.values())


SPEC = replace(
    media.SPEC,
    subcommands={**media.SPEC.subcommands, **_EXTRA},
    examples=(
        *media.SPEC.examples,
        "davinci-axi media selection show",
        "davinci-axi media replace A001_beach ~/new.mov --force",
    ),
    methods=tuple(dict.fromkeys((*media.SPEC.methods, *_methods(_EXTRA.values())))),
)
