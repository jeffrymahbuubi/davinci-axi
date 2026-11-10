"""`media property set --save-restore` / `--restore`: the saved file, the Super Scale state and the
restore verb.

The set verb lives in `media_property`; this module holds what both share (the property names,
`wire_value`, the failure text) so neither imports the other.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from davinci_axi.commands.media_extra_common import ClipExtra
from davinci_axi.commands.media_folders import (
    Ctx,
    Found,
    clips_target,
    field_names,
    props,
    show_hint,
    walk_clips,
)
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import Change, MutationPlan, coerce_like, force_cmd, run_mutation
from davinci_axi.output import collapse_home
from davinci_axi.spec import Invocation, Result

RESTORE_KIND, RESTORE_VERSION = "davinci-axi media property restore", 1
SUPER_SCALE = "Super Scale"
SHARPNESS, NOISE = "SuperScale Sharpness", "SuperScale Noise Reduction"
SCALE_KEYS = (SUPER_SCALE, SHARPNESS, NOISE)
SCALE_ACCEPTS = (
    "Super Scale accepts 1, 2, 3, 4 (off, 2x, 3x, 4x) or 2x-enhanced (sent as 2 plus "
    "sharpness and noise reduction; Resolve reads back 2 for both)"
)
_FILE_FACT = "derived from the file; Resolve refuses writes"
HARD_TO_SET: dict[str, str] = {
    SUPER_SCALE: SCALE_ACCEPTS,
    **{
        k: f"{k} is {_FILE_FACT}"
        for k in (
            "File Path", "Resolution", "Frames", "Duration", "FPS", "Video Codec", "Audio Codec",
            "Type", "Format", "Usage", "Date Added", "Date Created", "Date Modified",
            "Sample Rate", "Audio Ch", "Bit Depth",
        )
    },
}  # fmt: skip
PROJECT_WIDE = "clip properties are project-wide: every timeline that uses these clips changes"
SAVE_HELP = "Save one with: media property set --used 'Super Scale=1' --save-restore <file>"
ENHANCED = "Super Scale=2x-enhanced,SuperScale Sharpness=0.5,SuperScale Noise Reduction=0.5"


def wire_value(key: str, text: str, live: object) -> object:
    """The value sent to Resolve: numbers and booleans keep the type Resolve reports today."""
    if key == SUPER_SCALE:
        return coerce_like(text, 1)  # always an integer, whatever the read-back type is
    if isinstance(live, bool | int | float):
        return coerce_like(text, live)
    return text


def lenient(expected: str, actual: Any) -> object:
    """Resolve normalises numbers ("10" reads back "10.0"); treat equal numbers as equal."""
    if str(actual) == expected:
        return actual
    try:
        return expected if float(expected) == float(actual) else actual
    except (TypeError, ValueError):
        return actual


def set_failed(key: str, label: str, done: list[str], clips_done: int, many: bool) -> AxiError:
    applied = ", ".join(done) or "none"
    if many:
        s = "" if clips_done == 1 else "s"
        detail = (
            f"could not set {key} on {label} ({clips_done} clip{s} done; "
            f"already applied on this clip: {applied})"
        )
    else:
        detail = f"could not set {key} (already applied: {applied})"
    err = api_failed("MediaPoolItem.SetClipProperty", detail)
    if key in HARD_TO_SET:
        err.help.insert(0, HARD_TO_SET[key])
    return err


def _number(value: object) -> float | None:
    try:
        return float(str(value))
    except ValueError:
        return None


def scale_state(live: dict[str, object]) -> str:
    """`1`, `2`, `2 (sharpness 0.5, noise 0.5)` or `2 (sharpness/noise not reported)`."""
    number = _number(live.get(SUPER_SCALE))
    if number is None or number < 2:
        return "1"
    shown = f"{number:g}"
    sharp, noise = _number(live.get(SHARPNESS)), _number(live.get(NOISE))
    if sharp is None or noise is None:
        return f"{shown} (sharpness/noise not reported)"
    return f"{shown} (sharpness {sharp:g}, noise {noise:g})"


def before_counts(states: list[str]) -> dict[str, int]:
    """How many clips are in each prior state, in first-seen order."""
    counts: dict[str, int] = {}
    for state in states:
        counts[state] = counts.get(state, 0) + 1
    return counts


@dataclass(frozen=True)
class SavedClip:
    id: str
    name: str
    properties: dict[str, object]


def write_restore(
    path: Path, *, project: str, command: str, keys: list[str], clips: list[SavedClip]
) -> None:
    """Write the clips' prior values as JSON (the file `--restore` reads)."""
    body = {
        "kind": RESTORE_KIND,
        "version": RESTORE_VERSION,
        "saved": datetime.now().isoformat(timespec="seconds"),
        "project": project,
        "command": command,
        "keys": keys,
        "clips": [{"id": c.id, "name": c.name, "properties": c.properties} for c in clips],
    }
    path.write_text(json.dumps(body, indent=2, default=str) + "\n", encoding="utf-8")


def _unreadable(path: Path, why: str) -> AxiError:
    return AxiError(
        "VALIDATION_ERROR",
        f"{collapse_home(path)} {why}",
        help=[SAVE_HELP, "Pass the file that --save-restore wrote"],
    )


def read_restore(path: Path) -> list[SavedClip]:
    """The clips saved in `path`; VALIDATION_ERROR when it is missing or not a restore file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise _unreadable(path, "cannot be read") from None
    except ValueError:
        raise _unreadable(path, "is not valid JSON") from None
    if not isinstance(data, dict) or data.get("kind") != RESTORE_KIND:
        raise _unreadable(path, "is not a media property restore file")
    if data.get("version") != RESTORE_VERSION or not isinstance(data.get("clips"), list):
        raise _unreadable(path, f"is not a version {RESTORE_VERSION} restore file")
    clips: list[SavedClip] = []
    for entry in data["clips"]:
        if not (
            isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and isinstance(entry.get("properties"), dict)
        ):
            raise _unreadable(path, "has a clip entry without id and properties")
        clips.append(
            SavedClip(entry["id"], str(entry.get("name", entry["id"])), entry["properties"])
        )
    return clips


def _scale_target(path: Path, clip: SavedClip) -> tuple[int, tuple[float, ...]]:
    """The integer to send for Super Scale plus sharpness/noise when the file has both."""
    raw = clip.properties[SUPER_SCALE]
    number = _number(raw)
    if number is None:
        raise _unreadable(path, f"holds an unreadable Super Scale ({raw}) for clip {clip.name}")
    sharp, noise = _number(clip.properties.get(SHARPNESS)), _number(clip.properties.get(NOISE))
    extras = (sharp, noise) if sharp is not None and noise is not None else ()
    return int(number), cast("tuple[float, ...]", extras)


def _scale_differs(live: dict[str, object], number: int, extras: tuple[float, ...]) -> bool:
    if _number(live.get(SUPER_SCALE)) != number:
        return True
    if not extras:
        return False
    return (_number(live.get(SHARPNESS)), _number(live.get(NOISE))) != extras


def run_property_restore(inv: Invocation, ctx: Ctx, saved: list[SavedClip]) -> Result:
    """Put the saved values back: one dry run / snapshot for every clip in the file."""
    text = str(inv.args["restore"])
    path = Path(text).expanduser().resolve()
    by_id = {f.clip.GetUniqueId(): f for f in walk_clips(ctx.root)}
    found: list[Found] = []
    for item in saved:
        one = by_id.get(item.id)
        if one is None:
            raise AxiError(
                "VALIDATION_ERROR",
                f"clip {item.name} ({item.id}) is no longer in the media pool",
                help=["Remove it from the file, or restore the project snapshot that --force took"],
            )
        found.append(one)
    labels = field_names(found)
    changes: list[Change] = []
    todo: list[tuple[str, ClipExtra, dict[str, object], tuple[float, ...]]] = []
    states: list[str] = []
    plain: list[str] = []
    unchanged = 0
    for label, one, item in zip(labels, found, saved, strict=True):
        live = props(one.clip)
        states.append(scale_state(live))
        changed: dict[str, object] = {}
        extras: tuple[float, ...] = ()
        for key, raw in item.properties.items():
            if key in (SHARPNESS, NOISE):
                continue  # they travel with Super Scale
            if key == SUPER_SCALE:
                number, extras = _scale_target(path, item)
                if _scale_differs(live, number, extras):
                    changed[key] = number
                    if number >= 2 and not extras:
                        plain.append(item.name)
            else:
                value = wire_value(key, str(raw), live.get(key, ""))
                if str(live.get(key, "")) != str(value):
                    changed[key] = value
        if not changed:
            unchanged += 1
            continue
        changes += [Change(f"{label}.{k}", live.get(k, ""), v) for k, v in changed.items()]
        todo.append((label, cast(ClipExtra, one.clip), changed, extras))
    many = len(found) > 1
    warn = [PROJECT_WIDE] if many else []
    if plain:
        s = "" if len(plain) == 1 else "s"
        names = shlex.quote(",".join(plain))
        warn.append(
            f"{len(plain)} clip{s} saved as Super Scale 2 or more without sharpness/noise "
            f"values: plain scaling is restored; for 2x-enhanced run `media property set "
            f"{names} '{ENHANCED}' --force`"
        )
    extra: dict[str, object] = {
        "clips": len(found),
        "unchanged": unchanged,
        "restore_file": collapse_home(path),
    }
    if any(SUPER_SCALE in item.properties for item in saved):
        extra["super_scale_before"] = before_counts(states)
    plan = MutationPlan(
        command="media property set",
        target=clips_target(found),
        changes=changes,
        force_cmd=force_cmd(inv, "media", "property", "set", "--restore", text),
        warning="; ".join(warn),
        extra=extra,
        next=[show_hint(found[:1])],
    )

    def apply() -> None:
        for clips_done, (label, clip, changed, extras) in enumerate(todo):
            done: list[str] = []
            for key, value in changed.items():
                more = extras if key == SUPER_SCALE else ()
                if not clip.SetClipProperty(key, value, *more):
                    raise set_failed(key, label, done, clips_done, many)
                done.append(key)

    def verify() -> dict[str, object]:
        return {
            f"{label}.{k}": lenient(str(v), clip.GetClipProperty(k))
            for label, clip, changed, _ in todo
            for k, v in changed.items()
        }

    return run_mutation(inv, ctx.session, plan, apply, verify)
