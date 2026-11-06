"""`media mark set|clear` and `media marker custom get|set` (U21): in/out marks and custom data.

Mark in/out is read back with `GetMarkInOut`, which returns `{"video": {"in", "out"}, "audio":
{...}}`; the default type `all` sets both ranges, so it reads back as one pair only while the
ranges agree. Marks are session-like state and take no project snapshot; custom marker data is
project state and does. Marker frames are the same offsets `media marker add` uses.
"""

from __future__ import annotations

from typing import Any

from davinci_axi.commands.item_detail import str_arg
from davinci_axi.commands.media_edit import CLIP
from davinci_axi.commands.media_extra_common import ITEM, clip_plan, open_clip
from davinci_axi.commands.media_folders import verb_spec
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import Change, run_mutation
from davinci_axi.output import hint
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

TYPES = ("video", "audio", "all")


def _marks(live: object, kind: str) -> dict[str, object]:
    """`{"in", "out"}` of the marks of `kind`, `{}` when none are set, `mixed` when they differ."""
    marks = live if isinstance(live, dict) else {}
    kinds = [kind] if kind in ("video", "audio") else ["video", "audio"]
    ranges = [r for r in (marks.get(k) for k in kinds) if isinstance(r, dict) and r]
    if not ranges:
        return {}
    first = {"in": ranges[0].get("in"), "out": ranges[0].get("out")}
    if all({"in": r.get("in"), "out": r.get("out")} == first for r in ranges):
        return first
    return {"in": "mixed", "out": "mixed"}


def _frame_arg(inv: Invocation, key: str) -> int:
    value = inv.args.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--{key} needs a frame number of 0 or more",
            help=["Valid example: --in 10 --out 50"],
        )
    return value


def run_mark_set(inv: Invocation) -> Result:
    mark_in, mark_out = _frame_arg(inv, "in"), _frame_arg(inv, "out")
    if mark_in > mark_out:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--in {mark_in} is after --out {mark_out}",
            help=["Valid example: --in 10 --out 50"],
        )
    kind = str_arg(inv, "type") or "all"
    ctx, found, clip = open_clip(inv)
    before = _marks(clip.GetMarkInOut(), kind)
    changed = before != {"in": mark_in, "out": mark_out}
    changes = (
        [
            Change(f"{kind} in", before.get("in"), mark_in),
            Change(f"{kind} out", before.get("out"), mark_out),
        ]
        if changed
        else []
    )
    words = ["--in", str(mark_in), "--out", str(mark_out), "--type", kind]
    plan = clip_plan(inv, found, "media mark set", changes, *words, snapshot="none")

    def apply() -> None:
        if not clip.SetMarkInOut(mark_in, mark_out, kind):
            raise api_failed("MediaPoolItem.SetMarkInOut", f"could not set the {kind} marks")

    def verify() -> dict[str, object]:
        now = _marks(clip.GetMarkInOut(), kind)
        return {f"{kind} in": now.get("in"), f"{kind} out": now.get("out")}

    return run_mutation(inv, ctx.session, plan, apply, verify)


def run_mark_clear(inv: Invocation) -> Result:
    kind = str_arg(inv, "type") or "all"
    ctx, found, clip = open_clip(inv)
    before = _marks(clip.GetMarkInOut(), kind)
    shown = f"{before.get('in')}-{before.get('out')}" if before else ""
    plan = clip_plan(
        inv,
        found,
        "media mark clear",
        [Change(f"{kind} marks", shown, "cleared")] if before else [],
        "--type",
        kind,
        snapshot="none",
    )

    def apply() -> None:
        if not clip.ClearMarkInOut(kind):
            raise api_failed("MediaPoolItem.ClearMarkInOut", f"could not clear the {kind} marks")

    def verify() -> dict[str, object]:
        left = _marks(clip.GetMarkInOut(), kind)
        return {f"{kind} marks": "still set" if left else "cleared"}

    return run_mutation(inv, ctx.session, plan, apply, verify)


def _frames(markers: Any) -> dict[int, dict[str, Any]]:
    return {int(k): v for k, v in (markers or {}).items()}


def _frame_list(by_frame: dict[int, dict[str, Any]]) -> str:
    return "Marker frames on this clip: " + (", ".join(map(str, sorted(by_frame))) or "(none)")


def run_custom_get(inv: Invocation) -> Result:
    frame, text = inv.args.get("frame"), str_arg(inv, "data")
    if (frame is None) == (text is None):
        raise AxiError(
            "VALIDATION_ERROR",
            "pass exactly one of --frame or --data",
            help=[
                "Valid example: davinci-axi media marker custom get A001_beach --frame 24",
                "Valid example: davinci-axi media marker custom get A001_beach --data note1",
            ],
        )
    _, _, clip = open_clip(inv)
    by_frame = _frames(clip.GetMarkers())
    out: dict[str, object] = {"clip": clip.GetName(), "id": clip.GetUniqueId()}
    if isinstance(frame, int):
        if frame not in by_frame:
            raise not_found("marker", f"at frame {frame}", _frame_list(by_frame))
        out["frame"] = frame
        out["custom_data"] = clip.GetMarkerCustomData(frame)
    else:
        info = clip.GetMarkerByCustomData(str(text))
        if not info:
            raise not_found("marker", f"with custom data {text}", _frame_list(by_frame))
        match = [f for f, m in sorted(by_frame.items()) if m.get("customData") == text]
        out["frame"] = match[0] if match else None
        out["custom_data"] = str(text)
    marker_frame = out["frame"]
    if isinstance(marker_frame, int):
        out["name"] = by_frame.get(marker_frame, {}).get("name")
    return Result(out, [hint(f"media show {clip.GetUniqueId()}", "show the clip and its markers")])


def run_custom_set(inv: Invocation) -> Result:
    frame, data = _frame_arg(inv, "frame"), inv.positionals["data"]
    ctx, found, clip = open_clip(inv)
    by_frame = _frames(clip.GetMarkers())
    if frame not in by_frame:
        raise not_found("marker", f"at frame {frame}", _frame_list(by_frame))
    before = str(clip.GetMarkerCustomData(frame) or "")
    plan = clip_plan(
        inv,
        found,
        "media marker custom set",
        [Change("custom_data", before, data)] if before != data else [],
        data,
        "--frame",
        str(frame),
    )

    def apply() -> None:
        if not clip.UpdateMarkerCustomData(frame, data):
            raise api_failed(
                "MediaPoolItem.UpdateMarkerCustomData", f"could not update the marker at {frame}"
            )

    def verify() -> dict[str, object]:
        return {"custom_data": str(clip.GetMarkerCustomData(frame) or "")}

    return run_mutation(inv, ctx.session, plan, apply, verify)


TYPE_FLAG = Flag("type", "str", None, choices=TYPES, help="which marks (default: all)")

MARK_SET_SPEC = verb_spec(
    "set",
    "Set the in and out marks of a clip (no snapshot)",
    run_mark_set,
    (CLIP,),
    (
        Flag("in", "int", None, required=True, help="mark in frame"),
        Flag("out", "int", None, required=True, help="mark out frame"),
        TYPE_FLAG,
    ),
    (
        "davinci-axi media mark set A001_beach --in 10 --out 50",
        "davinci-axi media mark set A001_beach --in 10 --out 50 --type video --force",
    ),
    (ITEM + "GetMarkInOut", ITEM + "SetMarkInOut"),
)
MARK_CLEAR_SPEC = verb_spec(
    "clear",
    "Clear the in and out marks of a clip (no snapshot)",
    run_mark_clear,
    (CLIP,),
    (TYPE_FLAG,),
    (
        "davinci-axi media mark clear A001_beach",
        "davinci-axi media mark clear A001_beach --type audio --force",
    ),
    (ITEM + "GetMarkInOut", ITEM + "ClearMarkInOut"),
)
MARK_SPEC = CommandSpec(
    name="mark",
    summary="Set or clear the in and out marks of a clip",
    subcommands={"set": MARK_SET_SPEC, "clear": MARK_CLEAR_SPEC},
    reads_only=False,
    methods=tuple(dict.fromkeys((*MARK_SET_SPEC.methods, *MARK_CLEAR_SPEC.methods))),
)

CUSTOM_GET_SPEC = CommandSpec(
    name="get",
    summary="Show the custom data of a clip marker, by frame or by data",
    run=run_custom_get,
    positionals=(CLIP,),
    flags=(
        Flag("frame", "int", None, help="marker frame (offset from the clip start)"),
        Flag("data", "str", None, help="find the first marker with this custom data"),
    ),
    examples=(
        "davinci-axi media marker custom get A001_beach --frame 24",
        "davinci-axi media marker custom get A001_beach --data note1",
    ),
    methods=(
        ITEM + "GetMarkers",
        ITEM + "GetMarkerCustomData",
        ITEM + "GetMarkerByCustomData",
    ),
)
CUSTOM_SET_SPEC = verb_spec(
    "set",
    "Set the custom data of an existing clip marker",
    run_custom_set,
    (CLIP, Positional("data", help="the custom data text")),
    (Flag("frame", "int", None, required=True, help="marker frame (offset from the clip start)"),),
    (
        "davinci-axi media marker custom set A001_beach --frame 24 note1",
        "davinci-axi media marker custom set A001_beach --frame 24 note1 --force",
    ),
    (ITEM + "GetMarkers", ITEM + "GetMarkerCustomData", ITEM + "UpdateMarkerCustomData"),
)
CUSTOM_SPEC = CommandSpec(
    name="custom",
    summary="Read or set the custom data of a clip marker",
    subcommands={"get": CUSTOM_GET_SPEC, "set": CUSTOM_SET_SPEC},
    reads_only=False,
    methods=tuple(dict.fromkeys((*CUSTOM_GET_SPEC.methods, *CUSTOM_SET_SPEC.methods))),
)
