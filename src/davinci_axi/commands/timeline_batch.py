"""`timeline transitions` and `timeline fades`: edit many items under one snapshot (U4, C).

`transitions` puts a transition on every cut of a track (adjacent items whose end meets the next
start), `fades` sets fade lengths on every selected item. Both are dry runs until `--force`; the
specs are spliced into `timeline_extra.NEW` by the lead.
"""

from __future__ import annotations

from itertools import pairwise
from typing import cast

from davinci_axi.commands.item_batch import split_items
from davinci_axi.commands.item_detail import (
    BY_PREFIX,
    FADES_MINIMUM,
    TRACK_TYPES,
    Located,
    str_arg,
    walk_items,
)
from davinci_axi.commands.item_extra_types import ItemExtra
from davinci_axi.commands.item_markers import ITEM_FLAGS, tl_words
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import Change, MutationPlan, force_cmd, run_mutation
from davinci_axi.output import hint
from davinci_axi.resolve_types import Info, TimelineApi
from davinci_axi.resolve_types_edit import TimelineItemEdit
from davinci_axi.spec import CommandSpec, Flag, Invocation, Result

CATEGORIES = ("simple", "fusion", "ofx", "audio")
FADE_KEYS = {"FadeIn": "fade_in", "FadeOut": "fade_out"}
TRACK_TEXT = "must be 1 or more (tracks are numbered from 1)"

Cut = tuple[int, Located, Located]


def _ref(one: Located) -> str:
    return f"{one.track}:{one.position}"


def _int_flag(inv: Invocation, key: str, minimum: int, text: str) -> int | None:
    value = inv.args.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        flag = key.replace("_", "-")
        raise AxiError("VALIDATION_ERROR", f"--{flag} {text}", help=[f"Valid example: --{flag} 12"])
    return value


def select_items(
    inv: Invocation, timeline: TimelineApi, default_type: str = "video"
) -> list[Located]:
    """`--items V1:1,V1:2`, else every item of `--track-type` (default `default_type`) on `--track`.

    A video selection without `--track` means track 1; an audio selection without it means all.
    """
    text = str_arg(inv, "items")
    if text:
        return split_items(timeline, text)
    track_type = str_arg(inv, "track_type") or default_type
    track = _int_flag(inv, "track", 1, TRACK_TEXT)
    if track is None and track_type == "video":
        track = 1
    return list(walk_items(timeline, (track_type,), track))


def cuts_on(items: list[Located]) -> list[Cut]:
    """`(frame, left, right)` for every pair of neighbours on a track with `end == start`."""
    tracks: dict[str, list[Located]] = {}
    for one in items:
        tracks.setdefault(one.track, []).append(one)
    cuts: list[Cut] = []
    for ordered in tracks.values():
        ordered.sort(key=lambda one: int(one.item.GetStart()))
        for left, right in pairwise(ordered):
            if int(left.item.GetEnd()) == int(right.item.GetStart()):
                cuts.append((int(right.item.GetStart()), left, right))
    return cuts


def _track_items(timeline: TimelineApi, listed: list[Located]) -> list[Located]:
    """Every item of the tracks the listed items sit on (a cut needs the left neighbour too)."""
    pool: list[Located] = []
    for label in dict.fromkeys(one.track for one in listed):
        pool += walk_items(timeline, (BY_PREFIX[label[0]],), int(label[1:]))
    return pool


def _find_cuts(inv: Invocation, timeline: TimelineApi) -> list[Cut]:
    text = str_arg(inv, "items")
    if not text:
        return cuts_on(select_items(inv, timeline, "video"))
    listed = split_items(timeline, text)
    wanted = {one.item.GetUniqueId() for one in listed}
    cuts = cuts_on(_track_items(timeline, listed))
    return [cut for cut in cuts if cut[2].item.GetUniqueId() in wanted]


def _words(inv: Invocation, head: list[str], flags: tuple[str, ...]) -> list[str]:
    words = [*head, *tl_words(inv)]
    for flag in flags:
        value = inv.args.get(flag.replace("-", "_"))
        if value not in (None, ""):
            words += [f"--{flag}", str(value)]
    return words


def run_transitions(inv: Invocation) -> Result:
    kind = str(inv.args.get("type") or "").strip()
    if not kind:
        raise AxiError(
            "VALIDATION_ERROR",
            "--type needs a transition name",
            help=["Valid example: --type 'Cross Dissolve'"],
        )
    duration = _int_flag(inv, "duration", 1, "must be 1 or more frames")
    _int_flag(inv, "track", 1, TRACK_TEXT)
    session = inv.connect()
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    cuts = _find_cuts(inv, timeline)
    options: Info = {
        "type": kind,
        "category": str(inv.args.get("category") or "simple"),
        "position": "start",
        "alignment": "center",
    }
    if duration is not None:
        options["duration"] = duration
    rights = [_ref(right) for _, _, right in cuts]
    fields = [
        f"{ref} {right.item.GetName()}" for ref, (_, _, right) in zip(rights, cuts, strict=True)
    ]
    words = _words(
        inv, ["timeline", "transitions", "--type", kind], ("category", "duration", "track", "items")
    )
    plan = MutationPlan(
        command="timeline transitions",
        target=f"{len(cuts)} cuts in timeline {timeline.GetName()}",
        changes=[Change(field, "cut", kind) for field in fields],
        force_cmd=force_cmd(inv, *words),
        warning=(
            f"adds {len(cuts)} {kind} transitions; Resolve lists them among the clips, so later "
            "item refs on the track shift (list again with `items`); a cut without handles is "
            "refused and listed under failed"
        ),
        extra={
            "cuts": [
                {"at": frame, "left": _ref(left), "right": _ref(right)}
                for frame, left, right in cuts
            ]
        },
        next=[hint("items", "list the items, transitions included")],
    )
    if not cuts:
        plan.warning = ""
        plan.extra["note"] = "no cuts: adjacent items must touch (end == start)"
    created: dict[str, str] = {}

    def apply() -> None:
        failed: list[str] = []
        for ref, (_, _, right) in zip(rights, cuts, strict=True):
            made = cast(ItemExtra, right.item).AddTransition(dict(options))
            if made is None:
                failed.append(ref)
            else:
                created[ref] = made.GetUniqueId()
        if failed:
            raise AxiError(
                "API_FAILED",
                f"added {len(created)} of {len(cuts)} transitions; refused on {', '.join(failed)}",
                help=[
                    "A cut whose clips have no spare frames (handles) cannot take a dissolve; "
                    "use `item fades` or re-append with --in/--out leaving handles",
                    "The transitions that were added stay; the snapshot restores the state "
                    "before all of them",
                ],
                detail={"added": list(created), "failed": failed},
            )

    def verify() -> dict[str, object]:
        present = {found.item.GetUniqueId() for found in walk_items(timeline, TRACK_TYPES)}
        return {
            field: kind if created.get(ref) in present else None
            for field, ref in zip(fields, rights, strict=True)
        }

    return run_mutation(inv, session, plan, apply, verify)


def fit_fades(
    wanted: dict[str, int], current: dict[str, object], duration: int
) -> tuple[dict[str, int], list[dict[str, object]]]:
    """`(full {FadeIn, FadeOut}, clamped rows {fade, asked, set, why})` for one item.

    A fade is capped at `duration - 1`; then, when in + out still exceed `duration - 1`, the
    budget left after the fades not being set is shared between the ones being set (in proportion
    for two; when over by exactly one frame only the fade-out loses it). Resolve accepts
    in + out == length but trims the fade-out by a frame (F42), so the budget is length - 1.
    """
    full = {key: int(cast(int, current.get(key) or 0)) for key in FADE_KEYS}
    limit = max(duration - 1, 0)
    rows: dict[str, dict[str, object]] = {}
    for key, value in wanted.items():
        full[key] = min(value, limit)
        if full[key] != value:
            rows[key] = {
                "fade": FADE_KEYS[key],
                "asked": value,
                "set": full[key],
                "why": f"item is {duration} frames",
            }
    if sum(full.values()) > limit:
        keys = list(wanted)
        budget = max(limit - sum(v for k, v in full.items() if k not in keys), 0)
        before = dict(full)
        if len(keys) == 2:
            total = before["FadeIn"] + before["FadeOut"]
            if total - budget == 1:  # over by one frame: trim the fade-out, as Resolve does
                full["FadeOut"] = before["FadeOut"] - 1
            else:
                full["FadeIn"] = budget * before["FadeIn"] // total
                full["FadeOut"] = budget - full["FadeIn"]
        else:
            full[keys[0]] = budget
        for key in keys:
            if full[key] != before[key] or key in rows:
                rows[key] = {
                    "fade": FADE_KEYS[key],
                    "asked": wanted[key],
                    "set": full[key],
                    "why": (
                        f"in + out must stay under the {duration}-frame item "
                        "(Resolve keeps 1 frame)"
                    ),
                }
    return full, list(rows.values())


def run_fades(inv: Invocation) -> Result:
    wanted: dict[str, int] = {}
    for flag, key in (("in", "FadeIn"), ("out", "FadeOut")):
        value = _int_flag(inv, flag, 0, "must be 0 or more frames")
        if value is not None:
            wanted[key] = value
    if not wanted:
        raise AxiError(
            "VALIDATION_ERROR",
            "pass at least one of --in and --out",
            help=["Valid example: --in 12 --out 24 (fade lengths in frames)"],
        )
    _int_flag(inv, "track", 1, TRACK_TEXT)
    session = inv.connect()
    session.require_version("timeline fades", FADES_MINIMUM)
    timeline = session.require_timeline(str_arg(inv, "timeline"))
    items = select_items(inv, timeline, "audio")
    changes: list[Change] = []
    sends: list[tuple[str, TimelineItemEdit, dict[str, int]]] = []
    clamped: list[dict[str, object]] = []
    fitted: list[str] = []
    for one in items:
        item = cast(TimelineItemEdit, one.item)
        ref, current = _ref(one), item.GetFades() or {}
        full, rows = fit_fades(wanted, current, int(item.GetDuration()))
        clamped += [{"item": ref, **row} for row in rows]
        if any(str(row["why"]).startswith("in + out") for row in rows):
            sums = [wanted.get(key, int(current.get(key) or 0)) for key in FADE_KEYS]
            fitted.append(f"{ref} ({sums[0]}+{sums[1]} > {int(item.GetDuration()) - 1})")
        edits = [
            Change(f"{ref} {FADE_KEYS[key]}", int(current.get(key) or 0), full[key])
            for key in wanted
            if int(current.get(key) or 0) != full[key]
        ]
        if edits:
            changes += edits
            sends.append((ref, item, full))
    notes: list[str] = []
    if fitted:
        shown = ", ".join(fitted[:3]) + (", ..." if len(fitted) > 3 else "")
        pairs = sum(str(row["why"]).startswith("in + out") for row in clamped)
        notes.append(
            "Resolve needs in + out <= item length - 1 (it trims the fade-out otherwise); "
            f"clamped on {len(fitted)} item(s) ({pairs} fade(s), listed under clamped): {shown}"
        )
    if any(str(row["why"]).startswith("item is") for row in clamped):
        count = sum(str(row["why"]).startswith("item is") for row in clamped)
        notes.append(f"{count} fade(s) are longer than their item allows and are clamped")
    words = _words(inv, ["timeline", "fades"], ("in", "out", "track-type", "track", "items"))
    plan = MutationPlan(
        command="timeline fades",
        target=f"{len(items)} items in timeline {timeline.GetName()}",
        changes=changes,
        force_cmd=force_cmd(inv, *words),
        warning="; ".join(notes),
        next=[hint("items", "list the items")],
    )
    if clamped:
        plan.extra["clamped"] = clamped
    if not changes:
        plan.extra["note"] = (
            "no items matched the selection" if not items else "every fade already has that length"
        )
    failed: list[str] = []

    def apply() -> None:
        for ref, item, full in sends:
            if not item.SetFades(dict(full)):
                failed.append(ref)
        if not failed:
            plan.extra["set"] = [ref for ref, _, _ in sends]
            return
        plan.changes[:] = [c for c in plan.changes if c.field.split(" ")[0] not in failed]
        plan.extra["failed"] = list(failed)
        if len(failed) == len(sends):
            raise api_failed(
                "TimelineItem.SetFades",
                f"fades were not set on any of the {len(sends)} items (first refused: {failed[0]})",
            )
        plan.extra["set"] = [ref for ref, _, _ in sends if ref not in failed]
        skipped = f"{len(failed)} item(s) refused SetFades and were skipped: {', '.join(failed)}"
        plan.warning = f"{plan.warning}; {skipped}" if plan.warning else skipped

    def verify() -> dict[str, object]:
        live: dict[str, object] = {}
        for ref, item, _ in sends:
            if ref in failed:
                continue
            fades = item.GetFades() or {}
            for key, name in FADE_KEYS.items():
                live[f"{ref} {name}"] = int(fades.get(key) or 0)
        return live

    return run_mutation(inv, session, plan, apply, verify)


READS = (
    "Timeline.GetItemListInTrack",
    "Timeline.GetTrackCount",
    "TimelineItem.GetStart",
    "TimelineItem.GetEnd",
    "TimelineItem.GetUniqueId",
    "TimelineItem.GetName",
)
TRANSITIONS_SPEC = CommandSpec(
    name="transitions",
    summary="Put a transition on every cut of a track under one snapshot",
    run=run_transitions,
    flags=(
        Flag("type", "str", None, required=True, help="transition name, e.g. 'Cross Dissolve'"),
        Flag("category", "str", "simple", choices=CATEGORIES, help="transition category"),
        Flag("duration", "int", None, help="length in frames (default: Resolve calculates it)"),
        Flag("track", "int", None, help="video track number (1-based, default 1)"),
        Flag("items", "str", None, help="only cuts whose right-hand item is listed: V1:2,V1:3"),
        *ITEM_FLAGS,
    ),
    examples=(
        "davinci-axi timeline transitions --type 'Cross Dissolve' --duration 12",
        "davinci-axi timeline transitions --type 'Cross Dissolve' --items V1:2,V1:3 --force",
    ),
    reads_only=False,
    methods=("TimelineItem.AddTransition", *READS),
)
FADES_SPEC = CommandSpec(
    name="fades",
    summary=(
        "Set fade-in and fade-out lengths on many items under one snapshot "
        "(default: every audio track; Resolve 21.1+)"
    ),
    run=run_fades,
    flags=(
        Flag("in", "int", None, help="fade-in length in frames"),
        Flag("out", "int", None, help="fade-out length in frames"),
        Flag(
            "track-type",
            "str",
            None,
            choices=("video", "audio"),
            help="video or audio (default audio; video uses track 1 unless --track)",
        ),
        Flag("track", "int", None, help="only this track number (1-based)"),
        Flag("items", "str", None, help="these items instead of a track: A1:1,A1:2"),
        *ITEM_FLAGS,
    ),
    examples=(
        "davinci-axi timeline fades --in 12 --track-type audio",
        "davinci-axi timeline fades --in 12 --out 24 --items A1:1,A1:2 --force",
    ),
    notes=(
        "Scope: every item on every audio track unless --track-type video (V1 unless --track) "
        "or --items",
        "Fades longer than the item, or whose in + out exceed item length - 1 (Resolve trims the "
        "fade-out otherwise), are clamped and listed under "
        "clamped; items Resolve still refuses are listed under failed while the rest are set; "
        "exit 1 only when every item failed",
    ),
    reads_only=False,
    methods=("TimelineItem.GetFades", "TimelineItem.SetFades", *READS),
)
