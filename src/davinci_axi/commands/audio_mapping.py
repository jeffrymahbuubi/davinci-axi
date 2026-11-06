"""`audio voice-isolation`, `audio mapping` and `audio channel-mapping` (U18, R35).

Voice isolation reads and writes `{isEnabled, amount}` for a timeline item or an audio track.
Audio mappings are JSON strings: `MediaPoolItem.GetAudioMapping` / `SetAudioMapping` for clips and
`TimelineItem.GetSourceAudioChannelMapping` / `SetSourceAudioChannelMapping` for items. Only the
`track_mapping` section is processed on write (README.md, "Updating audio mappings"), so the string
a getter returns is accepted back, and the read-back compares that section. Writes on timeline
items run inside `with_current_timeline` and note a restore in the output.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from davinci_axi.commands.audio_common import (
    MEDIA_READS,
    TIMELINE_READS,
    AudioClip,
    AudioItem,
    AudioTimeline,
    flag_words,
    timeline_step,
    verb,
)
from davinci_axi.commands.item_detail import resolve_item, str_arg
from davinci_axi.commands.media_folders import open_pool, resolve_clip
from davinci_axi.commands.render_common import force_words, methods_of
from davinci_axi.commands.timeline_tracks import TIMELINE_FLAG
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import (
    Change,
    MutationPlan,
    check_snapshot_flags,
    run_mutation,
    with_current_timeline,
)
from davinci_axi.output import empty_state, hint
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

ITEM = Positional("item", required=False, help="timeline item: A1:1 or a unique id")
CLIP = Positional("clip", help="clip id or exact clip name")
MAPPING = Positional("mapping", help="audio mapping as a JSON string (only track_mapping is used)")

# ---- voice isolation ------------------------------------------------------------------------


@dataclass
class VoiceTarget:
    session: Session
    timeline: AudioTimeline
    label: str
    read: Callable[[], dict[str, Any]]
    write: Callable[[dict[str, Any]], bool]
    words: list[str]


def _open_voice(inv: Invocation, base: list[str]) -> VoiceTarget:
    track, ref = inv.args.get("track"), inv.positionals.get("item")
    if (track is None) == (ref is None):
        raise AxiError(
            "VALIDATION_ERROR",
            "pass an item or --track <n>, exactly one of them",
            help=["Valid examples: audio voice-isolation get A1:1, ... get --track 1"],
        )
    check_snapshot_flags(inv)
    session = inv.connect()
    timeline = cast(AudioTimeline, session.require_timeline(str_arg(inv, "timeline")))
    name = timeline.GetName()
    if isinstance(track, int):
        count = timeline.GetTrackCount("audio")
        if not 1 <= track <= count:
            raise not_found(
                "audio track",
                str(track),
                f"This timeline has {count} audio tracks (tracks are numbered from 1)",
            )
        return VoiceTarget(
            session,
            timeline,
            f"audio track A{track} in timeline {name}",
            lambda: dict(timeline.GetVoiceIsolationState(track) or {}),
            lambda state: bool(timeline.SetVoiceIsolationState(track, state)),
            [*base, "--track", str(track)],
        )
    one = resolve_item(timeline, str(ref))
    item = cast(AudioItem, one.item)
    return VoiceTarget(
        session,
        timeline,
        f"item {one.track}:{one.position} ({item.GetName()}) in timeline {name}",
        lambda: dict(item.GetVoiceIsolationState() or {}),
        lambda state: bool(item.SetVoiceIsolationState(state)),
        [*base, str(ref)],
    )


def run_voice_get(inv: Invocation) -> Result:
    target = _open_voice(inv, ["audio", "voice-isolation", "get"])
    state = target.read()
    return Result(
        {
            "target": target.label,
            "enabled": state.get("isEnabled"),
            "amount": state.get("amount"),
        },
        [hint("audio voice-isolation set <item> --on --amount 60", "change it (add --force)")],
    )


def run_voice_set(inv: Invocation) -> Result:
    on, off, amount = inv.args.get("on"), inv.args.get("off"), inv.args.get("amount")
    if on and off:
        raise AxiError(
            "VALIDATION_ERROR", "--on and --off contradict each other", help=["Pass one"]
        )
    if not (on or off or amount is not None):
        raise AxiError(
            "VALIDATION_ERROR",
            "nothing to change: pass --on, --off or --amount",
            help=["Valid example: davinci-axi audio voice-isolation set A1:1 --on --amount 60"],
        )
    if amount is not None and (not isinstance(amount, int) or not 0 <= amount <= 100):
        raise AxiError(
            "VALIDATION_ERROR", f"--amount must be 0 to 100, got {amount}", help=["Valid: 60"]
        )
    target = _open_voice(inv, ["audio", "voice-isolation", "set"])
    live = target.read()
    enabled = True if on else False if off else bool(live.get("isEnabled"))
    wanted_amount = amount if amount is not None else live.get("amount", 0)
    changes = []
    if enabled != bool(live.get("isEnabled")):
        changes.append(Change("isEnabled", bool(live.get("isEnabled")), enabled))
    if wanted_amount != live.get("amount"):
        changes.append(Change("amount", live.get("amount"), wanted_amount))
    words = [*target.words, *flag_words(inv, ("amount",))]
    plan = MutationPlan(
        command="audio voice-isolation set",
        target=target.label,
        changes=changes,
        force_cmd=force_words(inv, words, ("on", "off")),
        next=[hint("audio voice-isolation get", "read it back (pass the item or --track)")],
    )

    def apply() -> None:
        with timeline_step(target.session, target.timeline, plan):
            if not target.write({"isEnabled": enabled, "amount": wanted_amount}):
                raise api_failed("SetVoiceIsolationState", "Resolve rejected the state")

    def verify() -> dict[str, object]:
        with with_current_timeline(target.session, target.timeline):
            now = target.read()
        return {"isEnabled": bool(now.get("isEnabled")), "amount": now.get("amount")}

    return run_mutation(inv, target.session, plan, apply, verify)


# ---- audio mapping (media pool clips) and channel mapping (timeline items) --------------------


def track_mapping(text: object) -> str:
    """The `track_mapping` part of a mapping string as canonical JSON ("" if there is none)."""
    try:
        parsed = json.loads(str(text))
    except ValueError:
        return ""
    section = parsed.get("track_mapping") if isinstance(parsed, dict) else None
    return json.dumps(section, sort_keys=True) if isinstance(section, dict) else ""


def _mapping_arg(inv: Invocation) -> str:
    text = inv.positionals["mapping"]
    if not track_mapping(text):
        raise AxiError(
            "VALIDATION_ERROR",
            "the mapping must be a JSON object with a track_mapping section",
            help=["Copy the string from `audio mapping get` and edit track_mapping, then pass it"],
        )
    return text


def _mapping_view(label: str, text: str, hint_line: str) -> Result:
    data: dict[str, object] = {"target": label, "mapping": text}
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    tracks = parsed.get("track_mapping") if isinstance(parsed, dict) else None
    if isinstance(tracks, dict) and tracks:
        data["tracks"] = [
            {
                "track": number,
                "type": t.get("type"),
                "mute": t.get("mute"),
                "channels": ",".join(str(c) for c in t.get("channel_idx") or []),
            }
            for number, t in tracks.items()
        ]
    elif not text:
        data["mapping"] = empty_state("mapping", f"for {label}", {})
    return Result(data, [hint_line])


def _mapping_plan(
    inv: Invocation,
    command: str,
    label: str,
    words: list[str],
    before: str,
    mapping: str,
) -> MutationPlan:
    wanted = track_mapping(mapping)
    return MutationPlan(
        command=command,
        target=label,
        changes=[Change("track_mapping", track_mapping(before), wanted)]
        if track_mapping(before) != wanted
        else [],
        force_cmd=force_words(inv, [*words, mapping]),
        warning="changes how source audio channels map to tracks",
    )


def run_mapping_get(inv: Invocation) -> Result:
    ctx = open_pool(inv)
    found = resolve_clip(ctx, inv.positionals["clip"])
    name = found.clip.GetName()
    text = found.clip.GetAudioMapping() or ""
    return _mapping_view(
        f"clip {name}", text, hint(f"audio mapping set {name} '<json>'", "change it (add --force)")
    )


def run_mapping_set(inv: Invocation) -> Result:
    mapping = _mapping_arg(inv)
    ctx = open_pool(inv)
    found = resolve_clip(ctx, inv.positionals["clip"])
    clip = cast(AudioClip, found.clip)
    plan = _mapping_plan(
        inv,
        "audio mapping set",
        f"clip {clip.GetName()}",
        ["audio", "mapping", "set", inv.positionals["clip"]],
        clip.GetAudioMapping() or "",
        mapping,
    )

    def apply() -> None:
        if not clip.SetAudioMapping(mapping):
            raise api_failed("MediaPoolItem.SetAudioMapping", "Resolve rejected the mapping")

    def verify() -> dict[str, object]:
        return {"track_mapping": track_mapping(clip.GetAudioMapping() or "")}

    return run_mutation(inv, ctx.session, plan, apply, verify)


def _open_channel_item(inv: Invocation) -> tuple[Session, AudioTimeline, AudioItem, str]:
    check_snapshot_flags(inv)
    session = inv.connect()
    timeline = cast(AudioTimeline, session.require_timeline(str_arg(inv, "timeline")))
    one = resolve_item(timeline, inv.positionals["item"])
    item = cast(AudioItem, one.item)
    label = f"item {one.track}:{one.position} ({item.GetName()}) in timeline {timeline.GetName()}"
    return session, timeline, item, label


def run_channel_get(inv: Invocation) -> Result:
    _, _, item, label = _open_channel_item(inv)
    ref = inv.positionals["item"]
    return _mapping_view(
        label,
        item.GetSourceAudioChannelMapping() or "",
        hint(f"audio channel-mapping set {ref} '<json>'", "change it (add --force)"),
    )


def run_channel_set(inv: Invocation) -> Result:
    mapping = _mapping_arg(inv)
    session, timeline, item, label = _open_channel_item(inv)
    words = ["audio", "channel-mapping", "set", inv.positionals["item"]]
    plan = _mapping_plan(
        inv,
        "audio channel-mapping set",
        label,
        words,
        item.GetSourceAudioChannelMapping() or "",
        mapping,
    )

    def apply() -> None:
        with timeline_step(session, timeline, plan):
            if not item.SetSourceAudioChannelMapping(mapping):
                raise api_failed("TimelineItem.SetSourceAudioChannelMapping", "mapping rejected")

    def verify() -> dict[str, object]:
        with with_current_timeline(session, timeline):
            return {"track_mapping": track_mapping(item.GetSourceAudioChannelMapping() or "")}

    return run_mutation(inv, session, plan, apply, verify)


# ---- specs ----------------------------------------------------------------------------------

JSON_HELP = '\'{"track_mapping": {"1": {"channel_idx": [1, 2], "mute": false}}}\''
TIMELINE_ONLY = (TIMELINE_FLAG,)
VOICE_FLAGS = (TIMELINE_FLAG, Flag("track", "int", None, help="an audio track number instead"))
VOICE_METHODS = methods_of(
    Timeline="GetVoiceIsolationState SetVoiceIsolationState",
    TimelineItem="GetVoiceIsolationState SetVoiceIsolationState",
)

VOICE_GET_SPEC = CommandSpec(
    name="get",
    summary="Show voice isolation for an item or an audio track",
    run=run_voice_get,
    positionals=(ITEM,),
    flags=VOICE_FLAGS,
    examples=(
        "davinci-axi audio voice-isolation get A1:1",
        "davinci-axi audio voice-isolation get --track 1",
    ),
    methods=VOICE_METHODS + TIMELINE_READS,
)
VOICE_SET_SPEC = verb(
    "set",
    "Turn voice isolation on or off and set its amount for an item or an audio track",
    run_voice_set,
    positionals=(ITEM,),
    flags=(
        *VOICE_FLAGS,
        Flag("on", "bool", False, help="enable voice isolation"),
        Flag("off", "bool", False, help="disable voice isolation"),
        Flag("amount", "int", None, help="strength from 0 to 100"),
    ),
    examples=(
        "davinci-axi audio voice-isolation set A1:1 --on --amount 60",
        "davinci-axi audio voice-isolation set --track 1 --off --force",
    ),
    methods=VOICE_METHODS + TIMELINE_READS,
)
MAPPING_GET_SPEC = CommandSpec(
    name="get",
    summary="Show the audio mapping of a media pool clip",
    run=run_mapping_get,
    positionals=(CLIP,),
    examples=("davinci-axi audio mapping get music_bed",),
    methods=methods_of(MediaPoolItem="GetAudioMapping") + MEDIA_READS,
)
MAPPING_SET_SPEC = verb(
    "set",
    "Set the audio mapping of a media pool clip from a JSON string",
    run_mapping_set,
    positionals=(CLIP, MAPPING),
    examples=(f"davinci-axi audio mapping set music_bed {JSON_HELP}",),
    methods=methods_of(MediaPoolItem="GetAudioMapping SetAudioMapping") + MEDIA_READS,
)
CHANNEL_GET_SPEC = CommandSpec(
    name="get",
    summary="Show the source audio channel mapping of a timeline item",
    run=run_channel_get,
    positionals=(Positional("item", help="timeline item: A1:1 or a unique id"),),
    flags=TIMELINE_ONLY,
    examples=("davinci-axi audio channel-mapping get A1:1",),
    methods=methods_of(TimelineItem="GetSourceAudioChannelMapping") + TIMELINE_READS,
)
CHANNEL_SET_SPEC = verb(
    "set",
    "Set the source audio channel mapping of a timeline item from a JSON string",
    run_channel_set,
    positionals=(Positional("item", help="timeline item: A1:1 or a unique id"), MAPPING),
    flags=TIMELINE_ONLY,
    examples=(f"davinci-axi audio channel-mapping set A1:1 {JSON_HELP}",),
    methods=methods_of(TimelineItem="GetSourceAudioChannelMapping SetSourceAudioChannelMapping")
    + TIMELINE_READS,
)


def _group(name: str, summary: str, **subs: CommandSpec) -> CommandSpec:
    methods = tuple(dict.fromkeys(m for s in subs.values() for m in s.methods))
    return CommandSpec(
        name=name, summary=summary, subcommands=subs, reads_only=False, methods=methods
    )


SPECS: dict[str, CommandSpec] = {
    "voice-isolation": _group(
        "voice-isolation", "Show or change voice isolation", get=VOICE_GET_SPEC, set=VOICE_SET_SPEC
    ),
    "mapping": _group(
        "mapping", "Show or set clip audio mappings", get=MAPPING_GET_SPEC, set=MAPPING_SET_SPEC
    ),
    "channel-mapping": _group(
        "channel-mapping",
        "Show or set item source audio channel mappings",
        get=CHANNEL_GET_SPEC,
        set=CHANNEL_SET_SPEC,
    ),
}
