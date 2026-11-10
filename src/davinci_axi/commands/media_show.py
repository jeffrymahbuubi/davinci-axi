"""`media show <clip>`: the detail view of one media pool clip (never writes)."""

from __future__ import annotations

import shlex
from typing import Any

from davinci_axi.commands.item_detail import _properties as truncated_block
from davinci_axi.commands.media_edit import CLIP
from davinci_axi.commands.media_folders import folder_hint, open_pool, props, resolve_clip
from davinci_axi.output import hint
from davinci_axi.resolve_types import MediaPoolItemApi
from davinci_axi.spec import CommandSpec, Flag, Invocation, Result


def _timeline_name(clip: MediaPoolItemApi) -> str | None:
    """The timeline a timeline-type clip stands for, else `None`."""
    try:
        timeline = clip.GetTimeline()
    except Exception:  # OQ23: may raise on a clip that is not a timeline
        return None
    return timeline.GetName() if timeline is not None else None


ALL_FLAG = Flag(
    "all",
    "bool",
    False,
    help='also show empty or "None" properties and metadata (hidden by default)',
)

NONE_WORDS = frozenset({"None", "none"})


def nonempty(
    block: dict[str, Any], *, words: frozenset[str] = frozenset()
) -> tuple[dict[str, Any], int]:
    """(entries whose value is not None/"" or a string in `words`, count hidden)."""
    kept = {
        k: v
        for k, v in block.items()
        if v is not None and v != "" and not (isinstance(v, str) and v in words)
    }
    return kept, len(block) - len(kept)


def run_show(inv: Invocation) -> Result:
    ref, full = inv.positionals["clip"], bool(inv.args.get("full"))
    show_all = bool(inv.args.get("all"))
    ctx = open_pool(inv)
    found = resolve_clip(ctx, ref)
    clip = found.clip
    base_cmd = f"{inv.bin_name} media show {shlex.quote(ref)}"
    full_cmd = f"{base_cmd} --full" + (" --all" if show_all else "")
    metadata = clip.GetMetadata()
    raw_props = props(clip)
    raw_meta = metadata if isinstance(metadata, dict) else {}
    props_hidden = meta_hidden = 0
    if not show_all:
        raw_props, props_hidden = nonempty(raw_props, words=NONE_WORDS)
        raw_meta, meta_hidden = nonempty(raw_meta)
    properties, properties_note = truncated_block(raw_props, full, full_cmd)
    meta_block, metadata_note = truncated_block(raw_meta, full, full_cmd)
    data: dict[str, object] = {
        "id": clip.GetUniqueId(),
        "name": clip.GetName(),
        "media_id": clip.GetMediaId(),
        "folder": found.folder_path,
        "timeline": _timeline_name(clip),
        "color": clip.GetClipColor() or None,
        "flags": list(clip.GetFlagList() or []),
        "properties": properties,
    }
    if props_hidden:
        data["properties_hidden"] = props_hidden
    if properties_note:
        data["properties_note"] = properties_note
    data["metadata"] = meta_block
    if meta_hidden:
        data["metadata_hidden"] = meta_hidden
    if metadata_note:
        data["metadata_note"] = metadata_note
    data["markers"] = [
        {"frame": f, "color": m.get("color"), "name": m.get("name"), "duration": m.get("duration")}
        for f, m in sorted((clip.GetMarkers() or {}).items())
    ]
    helps = [folder_hint(found.folder_path, "list the clips next to it")]
    if props_hidden or meta_hidden:
        helps.append(
            hint(
                f"media show {shlex.quote(ref)} --all",
                f"include the {props_hidden + meta_hidden} empty properties and metadata",
            )
        )
    return Result(data, helps)


SHOW_SPEC = CommandSpec(
    name="show",
    summary="Show one media pool clip: properties, metadata, flags and markers",
    run=run_show,
    positionals=(CLIP,),
    flags=(Flag("full", "bool", False, help="do not truncate long property blocks"), ALL_FLAG),
    examples=("davinci-axi media show A001_beach", "davinci-axi media show clip-1 --full --json"),
    methods=(
        "MediaPoolItem.GetClipProperty",
        "MediaPoolItem.GetMetadata",
        "MediaPoolItem.GetMediaId",
        "MediaPoolItem.GetTimeline",
        "Timeline.GetName",
    ),
)
