"""`media property set`: set clip properties through SetClipProperty (dry run until `--force`)."""

from __future__ import annotations

import shlex
from dataclasses import replace
from pathlib import Path
from typing import cast

from davinci_axi.commands.item_detail import walk_items
from davinci_axi.commands.media_extra_common import ITEM, ClipExtra
from davinci_axi.commands.media_folders import (
    Ctx,
    Found,
    clips_target,
    field_names,
    open_pool,
    props,
    resolve_clips,
    show_hint,
    verb_spec,
    walk_clips,
)
from davinci_axi.commands.media_property_restore import (
    HARD_TO_SET,
    NOISE,
    PROJECT_WIDE,
    SCALE_ACCEPTS,
    SCALE_KEYS,
    SHARPNESS,
    SUPER_SCALE,
    SavedClip,
    before_counts,
    lenient,
    read_restore,
    run_property_restore,
    scale_state,
    set_failed,
    wire_value,
    write_restore,
)
from davinci_axi.commands.timeline_tracks import TIMELINE_FLAG, context_words, text_arg
from davinci_axi.errors import AxiError
from davinci_axi.mutate import (
    Change,
    MutationPlan,
    force_cmd,
    parse_assignments,
    refuse_existing,
    run_mutation,
)
from davinci_axi.output import collapse_home, hint
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result


def _group(name: str, summary: str, **subcommands: CommandSpec) -> CommandSpec:
    methods = tuple(dict.fromkeys(m for s in subcommands.values() for m in s.methods))
    return CommandSpec(
        name=name, summary=summary, subcommands=subcommands, reads_only=False, methods=methods
    )


SCALE_WORDS = {
    "1": 1, "off": 1, "none": 1,
    "2": 2, "2x": 2,
    "3": 3, "3x": 3,
    "4": 4, "4x": 4,
}  # fmt: skip


def _unit_float(key: str, text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = -1.0
    if not 0.0 <= value <= 1.0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"{key} must be a number from 0 to 1, got {text}",
            help=["Pass a value like 0.5"],
        )
    return value


def super_scale_args(text: str, pairs: dict[str, str]) -> tuple[object, ...]:
    """Arguments after the property name: `(2,)`, or `(2, sharpness, noise)` for 2x-enhanced."""
    word = text.strip().lower()
    if word in SCALE_WORDS:
        return (SCALE_WORDS[word],)
    if word in ("2x-enhanced", "enhanced"):
        sharp = _unit_float(SHARPNESS, pairs.pop(SHARPNESS, "0.5"))
        noise = _unit_float(NOISE, pairs.pop(NOISE, "0.5"))
        return (2, sharp, noise)
    raise AxiError("VALIDATION_ERROR", f"cannot read {text} as a Super Scale", help=[SCALE_ACCEPTS])


USED_FLAG = Flag(
    "used",
    "bool",
    False,
    help="the pool clips behind the video items of the current timeline (or --timeline)",
)


def used_clips(ctx: Ctx, timeline: TimelineApi) -> list[Found]:
    """The distinct pool clips behind the video items of `timeline`, in timeline order."""
    folders = {f.clip.GetUniqueId(): f.folder_path for f in walk_clips(ctx.root)}
    found: list[Found] = []
    seen: set[str] = set()
    for located in walk_items(timeline, ("video",)):
        clip = located.item.GetMediaPoolItem()
        if clip is None:
            continue
        uid = clip.GetUniqueId()
        if uid not in seen:
            seen.add(uid)
            found.append(Found(folders.get(uid, ""), clip))
    if not found:
        raise AxiError(
            "VALIDATION_ERROR",
            f"no video items use a pool clip on timeline {timeline.GetName()}",
            help=[
                "Add clips first with `davinci-axi timeline append`, or name the clips: "
                "`media property set A001,A002 'Super Scale=1'`",
            ],
        )
    return found


def _split_positionals(inv: Invocation, used: bool) -> tuple[str, str]:
    """(clips, assignments): with `--used` the one positional is the assignments."""
    given = [inv.positionals[p] for p in ("clips", "assignments") if p in inv.positionals]
    if used:
        if len(given) != 1:
            raise AxiError(
                "VALIDATION_ERROR",
                "with --used pass only the assignments",
                help=[
                    "Valid example: media property set --used 'Super Scale=1' --force",
                    "Without --used, name the clips: media property set A001,A002 'Super Scale=1'",
                ],
            )
        return "", given[0]
    if len(given) != 2:
        raise AxiError(
            "VALIDATION_ERROR",
            "pass <clips> and <assignments>",
            help=[
                "One or more clips: media property set A001 'Alpha mode=Straight'",
                "Every clip the timeline uses: media property set --used 'Super Scale=1'",
            ],
        )
    return given[0], given[1]


SAVE_FLAG = Flag(
    "save-restore",
    "str",
    None,
    help="write the clips' prior values to this JSON file first (restore with --restore)",
)
RESTORE_FLAG = Flag(
    "restore",
    "str",
    None,
    help="put back the values saved by --save-restore (no clips, no --used)",
)
OVERWRITE_FLAG = Flag(
    "overwrite", "bool", False, help="replace the --save-restore file if it exists"
)
RESTORE_USAGE = "Valid: media property set --restore ~/ss.json --force"


def _path_arg(inv: Invocation, name: str) -> Path | None:
    text = inv.args.get(name)
    if text is None:
        return None
    if not isinstance(text, str) or not text.strip():
        flag = name.replace("_", "-")
        raise AxiError("VALIDATION_ERROR", f"--{flag} needs a file path", help=[RESTORE_USAGE])
    return Path(text).expanduser().resolve()


def _shown(path: Path) -> str:
    """The path as a command line shows it: `~` kept, spaces quoted."""
    text = collapse_home(path)
    return "~/" + shlex.quote(text[2:]) if text.startswith("~/") else shlex.quote(text)


def _save_target(inv: Invocation) -> Path | None:
    """The `--save-restore` file, checked before Resolve is touched."""
    path = _path_arg(inv, "save_restore")
    if path is None:
        if inv.args.get("overwrite"):
            raise AxiError(
                "VALIDATION_ERROR",
                "--overwrite needs --save-restore",
                help=["Valid: media property set --used 'Super Scale=1' --save-restore f.json"],
            )
        return None
    if not path.parent.is_dir():
        raise AxiError(
            "VALIDATION_ERROR",
            f"the folder for {collapse_home(path)} does not exist",
            help=["Create the folder, or pick another path for --save-restore"],
        )
    refuse_existing(path, bool(inv.args.get("overwrite")))
    return path


def _run_restore(inv: Invocation) -> Result:
    others = ("used", "save_restore", "overwrite")
    if inv.positionals or any(inv.args.get(k) for k in others):
        raise AxiError(
            "VALIDATION_ERROR",
            "--restore takes no clips, assignments, --used or --save-restore",
            help=[RESTORE_USAGE],
        )
    path = _path_arg(inv, "restore")
    assert path is not None
    saved = read_restore(path)
    return run_property_restore(inv, open_pool(inv), saved)


def _write_saved(
    save: Path,
    plan: MutationPlan,
    ctx: Ctx,
    pairs: dict[str, str],
    found: list[Found],
    lives: list[dict[str, object]],
) -> None:
    """Write the `--save-restore` file and tell the plan where it is."""
    keys = list(dict.fromkeys([*pairs, *(SCALE_KEYS if SUPER_SCALE in pairs else ())]))
    saved = [
        SavedClip(one.clip.GetUniqueId(), one.clip.GetName(), {k: v[k] for k in keys if k in v})
        for one, v in zip(found, lives, strict=True)
    ]
    write_restore(
        save,
        project=ctx.session.require_project().GetName(),
        command=plan.force_cmd.removesuffix(" --force"),
        keys=keys,
        clips=saved,
    )
    command = f"media property set --restore {_shown(save)} --force"
    plan.extra["restore_file"] = collapse_home(save)
    plan.extra["restore_with"] = f"{plan.force_cmd.split(' ', 1)[0]} {command}"  # shown on dry runs
    plan.next.insert(0, hint(command, "put the saved values back in one snapshot"))


def run_property_set(inv: Invocation) -> Result:
    if inv.args.get("restore") is not None:
        return _run_restore(inv)
    used = bool(inv.args.get("used"))
    clips_text, assignments = _split_positionals(inv, used)
    pairs = parse_assignments(assignments)
    scale: tuple[object, ...] = ()
    if SUPER_SCALE in pairs:
        scale = super_scale_args(pairs[SUPER_SCALE], pairs)
    save = _save_target(inv)
    ctx = open_pool(inv)
    found = (
        used_clips(ctx, ctx.session.require_timeline(text_arg(inv, "timeline")))
        if used
        else resolve_clips(ctx, clips_text)
    )
    labels = field_names(found)
    lives = [props(one.clip) for one in found]
    changes: list[Change] = []
    todo: list[tuple[str, ClipExtra, dict[str, object]]] = []
    unchanged = 0
    for label, one, live in zip(labels, found, lives, strict=True):
        wanted = {
            k: wire_value(k, v, live.get(k, "")) for k, v in pairs.items() if k != SUPER_SCALE
        }
        if scale:
            wanted[SUPER_SCALE] = wire_value(SUPER_SCALE, str(scale[0]), live.get(SUPER_SCALE))
        changed = {
            k: v
            for k, v in wanted.items()
            if str(live.get(k, "")) != str(v) or (k == SUPER_SCALE and len(scale) > 1)
        }
        if not changed:
            unchanged += 1
            continue
        changes += [Change(f"{label}.{k}", live.get(k, ""), v) for k, v in changed.items()]
        todo.append((label, cast(ClipExtra, one.clip), changed))
    notes = dict.fromkeys(
        HARD_TO_SET[k]
        for _, _, changed in todo
        for k in changed
        if k in HARD_TO_SET and k != SUPER_SCALE
    )
    many = used or len(found) > 1
    extra: dict[str, object] = {"clips": len(found), "unchanged": unchanged, "used": used}
    if SUPER_SCALE in pairs:
        extra["super_scale_before"] = before_counts([scale_state(live) for live in lives])
    plan = MutationPlan(
        command="media property set",
        target=clips_target(found),
        changes=changes,
        force_cmd=force_cmd(
            inv,
            "media",
            "property",
            "set",
            *(["--used"] if used else [clips_text]),
            assignments,
            *context_words(inv),
        ),
        warning="; ".join([*notes, *([PROJECT_WIDE] if many else [])]),
        extra=extra,
        next=[show_hint(found[:1])],
    )
    if save is not None:
        _write_saved(save, plan, ctx, pairs, found, lives)

    def apply() -> None:
        for clips_done, (label, clip, changed) in enumerate(todo):
            done: list[str] = []
            for key, value in changed.items():
                more = tuple(float(str(x)) for x in scale[1:]) if key == SUPER_SCALE else ()
                if not clip.SetClipProperty(key, value, *more):
                    raise set_failed(key, label, done, clips_done, many)
                done.append(key)

    def verify() -> dict[str, object]:
        return {
            f"{label}.{k}": lenient(str(v), clip.GetClipProperty(k))
            for label, clip, changed in todo
            for k, v in changed.items()
        }

    return run_mutation(inv, ctx.session, plan, apply, verify)


PROPERTY_SET_SPEC = replace(
    verb_spec(
        "set",
        "Set clip properties such as Alpha mode or Input LUT",
        run_property_set,
        (
            Positional(
                "clips",
                required=False,
                help="clip ids or exact names, comma-separated (omit with --used)",
            ),
            Positional("assignments", required=False, help="comma-separated Property=value pairs"),
        ),
        (USED_FLAG, TIMELINE_FLAG, SAVE_FLAG, RESTORE_FLAG, OVERWRITE_FLAG),
        (
            "davinci-axi media property set A001_beach 'Alpha mode=Straight'",
            "davinci-axi media property set A001_beach 'Alpha mode=Straight' --force",
            "davinci-axi media property set A001_beach "
            "'Super Scale=2x-enhanced,SuperScale Sharpness=0.5,SuperScale Noise Reduction=0.5' "
            "--force",
            "davinci-axi media property set --used 'Super Scale=1' --save-restore "
            "~/super-scale.json --force",
            "davinci-axi media property set --restore ~/super-scale.json --force",
            "davinci-axi media property set A001,A002 "
            "'Super Scale=2x-enhanced,SuperScale Sharpness=0.5,SuperScale Noise Reduction=0.5' "
            "--force",
        ),
        (
            "Project.GetCurrentTimeline",
            "Project.GetTimelineCount",
            "Project.GetTimelineByIndex",
            "Timeline.GetItemListInTrack",
            "Timeline.GetTrackCount",
            "Timeline.GetName",
            "TimelineItem.GetMediaPoolItem",
            ITEM + "GetUniqueId",
            ITEM + "GetClipProperty",
            ITEM + "SetClipProperty",
        ),
    ),
    notes=(
        "Super Scale takes 1-4 or off|2x|3x|4x|2x-enhanced (add "
        "'SuperScale Sharpness=0.5,SuperScale Noise Reduction=0.5' for enhanced); "
        "it is sent as an integer",
        "Numbers are sent typed (int or float), text as text; quote values with spaces",
        "File facts such as Resolution or Frames are read-only in Resolve",
        "<clips> is a comma-separated list; --used takes every distinct clip behind a video item "
        "of the current timeline (or --timeline) and then only the assignments are given. One "
        "snapshot covers all clips",
        "Clip properties are project-wide: every timeline that uses these clips changes",
        "Draft recipe: `media property set --used 'Super Scale=1' --save-restore "
        "~/super-scale.json --force`, render the draft, then `media property set --restore "
        "~/super-scale.json --force` before the final render; the plan's super_scale_before "
        "counts each prior state",
        "Resolve reads Super Scale back as an integer: 2 for both 2x and 2x-enhanced. The "
        "sharpness and noise reduction of 2x-enhanced are read back only if Resolve reports "
        "SuperScale Sharpness and SuperScale Noise Reduction (--save-restore records them when "
        "it does; otherwise --restore puts back plain 2x and says so)",
        "Super Scale costs render time: about 40x live (a 209 s 1080x1920 timeline went from "
        "40 s to 27 m 48 s with 2x-enhanced on 45 clips) for a slight sharpness gain; it does "
        "not remove source banding. Turn it on for the final render only",
    ),
)
PROPERTY_SPEC = _group("property", "Set clip properties", set=PROPERTY_SET_SPEC)
