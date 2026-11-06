"""Clip attribute verbs: `media rename|metadata|color|flag|marker|proxy` (U13).

Each verb takes the clip first (id or exact name), plans the change as `Change` rows and hands
`apply` and `verify` to `mutate.run_mutation`. Colours and flags mirror `item_edit`: clip colours
use `CLIP_COLORS`, flags and markers use `MARKER_COLORS`. Marker frames are offsets from the
start of the clip.
"""

from __future__ import annotations

import os
from typing import cast

from davinci_axi.commands.media_folders import (
    Ctx,
    Found,
    abs_path,
    clips_target,
    open_pool,
    props,
    resolve_clip,
    show_hint,
    verb_spec,
)
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.markers import (
    CLIP_COLORS,
    MARKER_ADD_FLAGS,
    MARKER_COLORS,
    MARKER_DELETE_FLAGS,
    add_plan,
    check_add_flags,
    check_delete_flags,
    delete_plan,
)
from davinci_axi.mutate import (
    Change,
    MutationPlan,
    force_cmd,
    parse_assignments,
    run_mutation,
)
from davinci_axi.resolve_types_edit import MediaPoolItemEdit
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

CLIP = Positional("clip", help="clip id or exact clip name")
POOL_ITEM = "MediaPoolItem."
PROXY_KEY = "Proxy Media Path"


def _open(inv: Invocation) -> tuple[Ctx, Found, MediaPoolItemEdit]:
    ctx = open_pool(inv)
    found = resolve_clip(ctx, inv.positionals["clip"])
    return ctx, found, cast(MediaPoolItemEdit, found.clip)


def _plan(
    found: Found, command: str, changes: list[Change], *words: str, inv: Invocation
) -> MutationPlan:
    return MutationPlan(
        command=command,
        target=clips_target([found]),
        changes=changes,
        force_cmd=force_cmd(inv, "media", *command.split()[1:], found.clip.GetName(), *words),
        next=[show_hint([found])],
    )


def _color_choice(color: str, choices: tuple[str, ...]) -> str:
    if color not in choices:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown colour {color}",
            help=[f"Valid colours: {', '.join(choices)}"],
        )
    return color


def run_rename(inv: Invocation) -> Result:
    new = str(inv.args.get("to") or "").strip()
    if not new:
        raise AxiError(
            "VALIDATION_ERROR", "--to needs a new name", help=["Valid example: --to beach_wide"]
        )
    ctx, found, clip = _open(inv)
    old = clip.GetName()
    plan = _plan(
        found,
        "media rename",
        [Change("name", old, new)] if old != new else [],
        "--to",
        new,
        inv=inv,
    )

    def apply() -> None:
        if not clip.SetName(new):
            raise api_failed("MediaPoolItem.SetName", f"could not rename the clip to {new}")

    return run_mutation(inv, ctx.session, plan, apply, lambda: {"name": clip.GetName()})


def run_metadata_set(inv: Invocation) -> Result:
    wanted = parse_assignments(inv.positionals["assignments"])
    ctx, found, clip = _open(inv)
    live = clip.GetMetadata()
    live = live if isinstance(live, dict) else {}
    changed = {k: v for k, v in wanted.items() if str(live.get(k, "")) != v}
    plan = _plan(
        found,
        "media metadata set",
        [Change(k, live.get(k, ""), v) for k, v in changed.items()],
        inv.positionals["assignments"],
        inv=inv,
    )

    def apply() -> None:
        if not clip.SetMetadata(dict(changed)):
            raise api_failed("MediaPoolItem.SetMetadata", "no metadata key was applied")

    def verify() -> dict[str, object]:
        now = clip.GetMetadata()
        now = now if isinstance(now, dict) else {}
        return {k: now.get(k) for k in changed}

    return run_mutation(inv, ctx.session, plan, apply, verify)


def run_color_set(inv: Invocation) -> Result:
    color = _color_choice(inv.positionals["color"], CLIP_COLORS)
    ctx, found, clip = _open(inv)
    before = clip.GetClipColor() or ""
    plan = _plan(
        found,
        "media color set",
        [Change("color", before, color)] if before != color else [],
        color,
        inv=inv,
    )

    def apply() -> None:
        if not clip.SetClipColor(color):
            raise api_failed("MediaPoolItem.SetClipColor", f"could not set the colour {color}")

    return run_mutation(inv, ctx.session, plan, apply, lambda: {"color": clip.GetClipColor() or ""})


def run_color_clear(inv: Invocation) -> Result:
    ctx, found, clip = _open(inv)
    before = clip.GetClipColor() or ""
    plan = _plan(
        found, "media color clear", [Change("color", before, "")] if before else [], inv=inv
    )

    def apply() -> None:
        if not clip.ClearClipColor():
            raise api_failed("MediaPoolItem.ClearClipColor", "could not clear the clip colour")

    return run_mutation(inv, ctx.session, plan, apply, lambda: {"color": clip.GetClipColor() or ""})


def run_flag_add(inv: Invocation) -> Result:
    color = _color_choice(inv.positionals["color"], MARKER_COLORS)
    ctx, found, clip = _open(inv)
    before = sorted(clip.GetFlagList() or [])
    plan = _plan(
        found,
        "media flag add",
        [Change("flags", before, sorted({*before, color}))] if color not in before else [],
        color,
        inv=inv,
    )

    def apply() -> None:
        if not clip.AddFlag(color):
            raise api_failed("MediaPoolItem.AddFlag", f"could not add the {color} flag")

    return run_mutation(
        inv, ctx.session, plan, apply, lambda: {"flags": sorted(clip.GetFlagList() or [])}
    )


def run_flag_clear(inv: Invocation) -> Result:
    raw = inv.args.get("color")
    color = str(raw) if isinstance(raw, str) and raw else "All"
    ctx, found, clip = _open(inv)
    before = sorted(clip.GetFlagList() or [])
    after = [] if color == "All" else [f for f in before if f != color]
    words = ["--color", color] if color != "All" else []
    plan = _plan(
        found,
        "media flag clear",
        [Change("flags", before, after)] if before != after else [],
        *words,
        inv=inv,
    )

    def apply() -> None:
        if not clip.ClearFlags(color):
            raise api_failed("MediaPoolItem.ClearFlags", f"could not clear the {color} flags")

    return run_mutation(
        inv, ctx.session, plan, apply, lambda: {"flags": sorted(clip.GetFlagList() or [])}
    )


def _marker(inv: Invocation, verb: str) -> Result:
    (check_add_flags if verb == "add" else check_delete_flags)(inv)
    ctx, found, clip = _open(inv)
    build = add_plan if verb == "add" else delete_plan
    words = ["media", "marker", verb, found.clip.GetName()]
    plan, apply, verify = build(inv, clip, clips_target([found]), words)
    plan.command = f"media marker {verb}"
    plan.next = [show_hint([found])]
    return run_mutation(inv, ctx.session, plan, apply, verify)


def run_marker_add(inv: Invocation) -> Result:
    return _marker(inv, "add")


def run_marker_delete(inv: Invocation) -> Result:
    return _marker(inv, "delete")


def run_proxy_link(inv: Invocation) -> Result:
    given = str(inv.args.get("path") or "")
    proxy = abs_path(given)
    if not os.path.isfile(proxy):
        raise AxiError(
            "NOT_FOUND", f"proxy file {given} does not exist", help=["Check the path and retry"]
        )
    ctx, found, clip = _open(inv)
    before = str(props(clip).get(PROXY_KEY) or "")
    plan = _plan(
        found,
        "media proxy link",
        [Change("proxy", before, proxy)] if before != proxy else [],
        "--path",
        given,
        inv=inv,
    )

    def apply() -> None:
        if not clip.LinkProxyMedia(proxy):
            raise api_failed("MediaPoolItem.LinkProxyMedia", f"could not link {proxy}")

    return run_mutation(
        inv, ctx.session, plan, apply, lambda: {"proxy": str(props(clip).get(PROXY_KEY) or "")}
    )


def run_proxy_unlink(inv: Invocation) -> Result:
    ctx, found, clip = _open(inv)
    before = str(props(clip).get(PROXY_KEY) or "")
    plan = _plan(
        found, "media proxy unlink", [Change("proxy", before, "")] if before else [], inv=inv
    )

    def apply() -> None:
        if not clip.UnlinkProxyMedia():
            raise api_failed("MediaPoolItem.UnlinkProxyMedia", "could not unlink the proxy")

    return run_mutation(
        inv, ctx.session, plan, apply, lambda: {"proxy": str(props(clip).get(PROXY_KEY) or "")}
    )


def _group(name: str, summary: str, **subcommands: CommandSpec) -> CommandSpec:
    return CommandSpec(name=name, summary=summary, subcommands=subcommands, reads_only=False)


COLOR_ARG = Positional("color", help=f"one of {', '.join(CLIP_COLORS)}")
FLAG_ARG = Positional("color", help=f"one of {', '.join(MARKER_COLORS)}")

RENAME_SPEC = verb_spec(
    "rename",
    "Rename a media pool clip",
    run_rename,
    (CLIP,),
    (Flag("to", "str", None, required=True, help="the new name"),),
    (
        "davinci-axi media rename A001_beach --to beach_wide",
        "davinci-axi media rename A001_beach --to b --force",
    ),
    (POOL_ITEM + "GetName", POOL_ITEM + "SetName"),
)
METADATA_SET_SPEC = verb_spec(
    "set",
    "Set clip metadata such as Comments or Keywords",
    run_metadata_set,
    (CLIP, Positional("assignments", help="comma-separated Key=value pairs, e.g. Comments=ok")),
    (),
    (
        "davinci-axi media metadata set A001_beach Comments=ok",
        "davinci-axi media metadata set A001_beach Comments=ok,Keywords=beach --force",
    ),
    (POOL_ITEM + "GetMetadata", POOL_ITEM + "SetMetadata"),
)
COLOR_SET_SPEC = verb_spec(
    "set",
    "Set the clip colour of a media pool clip",
    run_color_set,
    (CLIP, COLOR_ARG),
    (),
    (
        "davinci-axi media color set A001_beach Teal",
        "davinci-axi media color set A001_beach Teal --force",
    ),
    (POOL_ITEM + "GetClipColor", POOL_ITEM + "SetClipColor"),
)
COLOR_CLEAR_SPEC = verb_spec(
    "clear",
    "Remove the clip colour of a media pool clip",
    run_color_clear,
    (CLIP,),
    (),
    (
        "davinci-axi media color clear A001_beach",
        "davinci-axi media color clear A001_beach --force",
    ),
    (POOL_ITEM + "GetClipColor", POOL_ITEM + "ClearClipColor"),
)
FLAG_ADD_SPEC = verb_spec(
    "add",
    "Add a coloured flag to a media pool clip",
    run_flag_add,
    (CLIP, FLAG_ARG),
    (),
    (
        "davinci-axi media flag add A001_beach Blue",
        "davinci-axi media flag add A001_beach Blue --force",
    ),
    (POOL_ITEM + "GetFlagList", POOL_ITEM + "AddFlag"),
)
FLAG_CLEAR_SPEC = verb_spec(
    "clear",
    "Remove the flags of a media pool clip (every flag, or one colour)",
    run_flag_clear,
    (CLIP,),
    (Flag("color", "str", None, help="only this colour", choices=(*MARKER_COLORS, "All")),),
    (
        "davinci-axi media flag clear A001_beach",
        "davinci-axi media flag clear A001_beach --color Red --force",
    ),
    (POOL_ITEM + "GetFlagList", POOL_ITEM + "ClearFlags"),
)
MARKER_ADD_SPEC = verb_spec(
    "add",
    "Add a marker to a clip (frame is an offset from the clip start)",
    run_marker_add,
    (CLIP,),
    MARKER_ADD_FLAGS,
    (
        "davinci-axi media marker add A001_beach --frame 5 --color Green --name Pick",
        "davinci-axi media marker add A001_beach --frame 5 --name Pick --force",
    ),
    (POOL_ITEM + "GetMarkers", POOL_ITEM + "AddMarker"),
)
MARKER_DELETE_SPEC = verb_spec(
    "delete",
    "Delete clip markers by frame, colour or custom data (one selector)",
    run_marker_delete,
    (CLIP,),
    MARKER_DELETE_FLAGS,
    (
        "davinci-axi media marker delete A001_beach --frame 24",
        "davinci-axi media marker delete A001_beach --color Green --force",
    ),
    (
        POOL_ITEM + "GetMarkers",
        POOL_ITEM + "DeleteMarkerAtFrame",
        POOL_ITEM + "DeleteMarkersByColor",
        POOL_ITEM + "DeleteMarkerByCustomData",
    ),
)
PROXY_LINK_SPEC = verb_spec(
    "link",
    "Link a proxy file to a clip",
    run_proxy_link,
    (CLIP,),
    (Flag("path", "str", None, required=True, help="the proxy media file"),),
    (
        "davinci-axi media proxy link A001_beach --path /proxy/A001_beach.mov",
        "davinci-axi media proxy link A001_beach --path /proxy/A001_beach.mov --force",
    ),
    (POOL_ITEM + "GetClipProperty", POOL_ITEM + "LinkProxyMedia"),
)
PROXY_UNLINK_SPEC = verb_spec(
    "unlink",
    "Unlink the proxy file of a clip",
    run_proxy_unlink,
    (CLIP,),
    (),
    (
        "davinci-axi media proxy unlink A001_beach",
        "davinci-axi media proxy unlink A001_beach --force",
    ),
    (POOL_ITEM + "GetClipProperty", POOL_ITEM + "UnlinkProxyMedia"),
)

METADATA_SPEC = _group("metadata", "Set clip metadata", set=METADATA_SET_SPEC)
COLOR_SPEC = _group(
    "color", "Set or clear the clip colour of a clip", set=COLOR_SET_SPEC, clear=COLOR_CLEAR_SPEC
)
FLAG_SPEC = _group("flag", "Add or clear flags on a clip", add=FLAG_ADD_SPEC, clear=FLAG_CLEAR_SPEC)
MARKER_SPEC = _group(
    "marker", "Add or delete markers on one clip", add=MARKER_ADD_SPEC, delete=MARKER_DELETE_SPEC
)
PROXY_SPEC = _group(
    "proxy", "Link or unlink proxy media of a clip", link=PROXY_LINK_SPEC, unlink=PROXY_UNLINK_SPEC
)
