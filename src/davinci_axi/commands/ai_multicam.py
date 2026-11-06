"""`ai deblur` and `ai multicam create|smart-switch|flatten` (U20, R35).

Deblur and multicam creation add clips to the media pool, so they are verified by the new clip
names (the call returns the clips). Smart switch and flatten have no getter and follow KTD23.
Option dicts are one comma-separated `--set k=v,...` (KTD25): keys are checked against the
`.pyi` TypedDict copied below and values are coerced by a per-key table. Enum values are words
mapped to `resolve.<CONST>` at run time (KTD24), so a typo exits 2 before Resolve is touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, cast

from davinci_axi.commands.ai_status import (
    AiPool,
    item_call,
    open_item,
    require_ok,
)
from davinci_axi.commands.audio_common import (
    FOLDER_FLAG,
    MEDIA_READS,
    SCOPE_ARGS,
    enum,
    flag_words,
    pick_scope,
    verb,
)
from davinci_axi.commands.item_detail import str_arg
from davinci_axi.commands.media_folders import (
    CLIPS,
    clips_target,
    open_pool,
    resolve_clips,
    show_hint,
    where_is,
)
from davinci_axi.commands.render_common import force_words, methods_of
from davinci_axi.commands.timeline_tracks import TIMELINE_FLAG
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import Change, MutationPlan, coerce_like, parse_assignments, run_mutation
from davinci_axi.output import hint
from davinci_axi.resolve_types import MediaPoolItemApi
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

SYNC_MODES = ("timecode", "audio", "in", "out", "marker")
GRADES = {
    "copy": "FLATTEN_MULTICAM_COPY_GRADE",
    "angle": "FLATTEN_MULTICAM_RETAIN_GRADE_FROM_ANGLE",
}
TIMECODE = re.compile(r"^\d{2}:\d{2}:\d{2}[:;]\d{2}$")
AUDIO_SYNC = "MULTICAM_ANGLE_SYNC_AUDIO"
ITEM = Positional("item", help="the multicam timeline item: V1:2 or a unique id")


@dataclass(frozen=True)
class Opt:
    """How one option key is read: `kind` is str, bool, int, float, tc, choice, enum or channel."""

    kind: str
    words: tuple[str, ...] = ()
    prefix: str = ""
    low: float | None = None
    high: float | None = None


@dataclass(frozen=True)
class EnumRef:
    """A `resolve.<name>` constant, looked up when the session is known (KTD24)."""

    name: str


def _bad(key: str, text: str, wanted: str) -> AxiError:
    return AxiError(
        "VALIDATION_ERROR",
        f"cannot read {text} as the value of {key}",
        help=[f"Pass {wanted}"],
    )


def _number(key: str, text: str, opt: Opt) -> object:
    value = coerce_like(text, 0 if opt.kind == "int" else 0.0)
    assert isinstance(value, (int, float))
    if (opt.low is not None and value < opt.low) or (opt.high is not None and value > opt.high):
        raise _bad(key, text, f"a number from {opt.low:g} to {opt.high:g}")
    return value


def coerce_option(key: str, text: str, opt: Opt) -> object:
    """`text` as the typed value of `key`; a bad value is VALIDATION_ERROR (exit 2)."""
    if opt.kind == "str":
        return text
    if opt.kind == "bool":
        return coerce_like(text, True)
    if opt.kind in ("int", "float"):
        return _number(key, text, opt)
    if opt.kind == "tc":
        if not TIMECODE.match(text):
            raise _bad(key, text, "a timecode such as 01:00:00:00")
        return text
    if opt.kind == "choice":
        if text not in opt.words:
            raise _bad(key, text, f"one of {', '.join(opt.words)}")
        return text
    if opt.kind == "channel" and text.isdigit():
        if not 1 <= int(text) <= 8:
            raise _bad(key, text, "a channel from 1 to 8, or automatic, or mix")
        return int(text)
    words = opt.words if opt.kind == "enum" else ("automatic", "mix")
    prefix = opt.prefix if opt.kind == "enum" else "AUDIO_SYNC_CHANNEL_"
    word = text.strip().lower()
    if word not in words:
        raise _bad(key, text, f"one of {', '.join(words)}")
    return EnumRef(prefix + word.replace("-", "_").upper())


@dataclass
class Options:
    """A typed option dict: values may hold `EnumRef`s, `shown` is what the dry run prints."""

    values: dict[str, object] = field(default_factory=dict)
    shown: dict[str, str] = field(default_factory=dict)

    def add(self, key: str, value: object, shown: str) -> None:
        self.values[key] = value
        self.shown[key] = shown

    def resolved(self, session: Session) -> dict[str, Any]:
        return {
            key: enum(session, value.name) if isinstance(value, EnumRef) else value
            for key, value in self.values.items()
        }

    @property
    def text(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in self.shown.items()) or "(defaults)"


def parse_options(
    raw: str | None, table: dict[str, Opt], what: str, reserved: dict[str, str] | None = None
) -> Options:
    """`--set k=v,...` against `table`; unknown or reserved keys exit 2."""
    options = Options()
    if raw is None:
        return options
    pairs = parse_assignments(raw)
    unknown = [key for key in pairs if key not in table]
    if unknown:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown {what} {', '.join(unknown)}",
            help=[f"Valid keys: {', '.join(table)}"],
        )
    for key in pairs:
        if reserved and key in reserved:
            raise AxiError(
                "VALIDATION_ERROR",
                f"{key} has its own flag",
                help=[f"Use {reserved[key]} instead of --set {key}=..."],
            )
    for key, text in pairs.items():
        options.add(key, coerce_option(key, text, table[key]), text)
    return options


def _bools(*keys: str) -> dict[str, Opt]:
    return {key: Opt("bool") for key in keys}


DEBLUR_OPTS: dict[str, Opt] = {
    **{k: Opt("str") for k in ("FileName", "Format", "Codec", "EncodingProfile")},
    "Encoder": Opt("choice", words=("Native", "MainConcept")),
    **_bools("UseExtremeMode", "UseMarkInMarkOut", "RenderAtSourceRes", "UseMoreGpuMemory"),
}
MULTICAM_OPTS: dict[str, Opt] = {
    "name": Opt("str"),
    "startTimecode": Opt("tc"),
    "frameRate": Opt("float", low=1.0, high=1000.0),
    "angleSyncMode": Opt("enum", words=SYNC_MODES, prefix="MULTICAM_ANGLE_SYNC_"),
    "channelConfig": Opt("channel"),
    "multicamAudioMode": Opt(
        "enum", words=("adaptive", "source", "reference", "all"), prefix="MULTICAM_AUDIO_"
    ),
    "angleNameMode": Opt(
        "enum",
        words=("sequential", "angle", "camera", "clip", "file"),
        prefix="MULTICAM_ANGLE_NAME_",
    ),
    **_bools("splitAtGaps", "useFullClipExtents", "createBinForSourceClips"),
    "detectSameCameraClipsMode": Opt(
        "enum",
        words=(
            "by-camera-number",
            "by-angle",
            "by-reel-number",
            "by-reel-name",
            "by-roll-card",
            "none",
        ),
        prefix="MULTICAM_DETECT_",
    ),
}
SMART_SWITCH_OPTS: dict[str, Opt] = {
    "minEditDuration": Opt("float", low=0.5, high=10.0),
    "editChangeDelay": Opt("float", low=0.0, high=2.0),
    "isAutoDetectWideAngle": Opt("bool"),
    "analysisMode": Opt(
        "enum",
        words=("none", "detect-wide-angle", "audio-only"),
        prefix="SMART_SWITCH_ANALYSIS_MODE_",
    ),
    "wideAngleID": Opt("str"),
    "wideAngleFrequency": Opt(
        "enum", words=("low", "medium", "high"), prefix="SMART_SWITCH_WIDE_ANGLE_FREQ_"
    ),
    **_bools("isUseWideAngleForIntroOutro", "isUseWideAngleForSilence", "switchOnVideoOnly"),
    "quality": Opt("enum", words=("faster", "better"), prefix="SMART_SWITCH_QUALITY_"),
}
SET_FLAG = Flag("set", "str", None, help="options as key=value,key=value (see the valid keys)")


# ---- deblur ---------------------------------------------------------------------------------


def run_deblur(inv: Invocation) -> Result:
    options = parse_options(str_arg(inv, "set"), DEBLUR_OPTS, "deblur option")
    scope = pick_scope(inv, ["ai", "deblur"])
    ctx = scope.ctx
    resolved = options.resolved(ctx.session)
    plan = MutationPlan(
        command="ai deblur",
        target=scope.target,
        changes=[Change("new clips", None, "(new clips)")],
        force_cmd=force_words(inv, [*scope.words, *flag_words(inv, ("set",))]),
        extra={"options": options.text},
        warning="renders deblurred copies into the media pool; needs AI Extras",
        next=[hint("media", "list the clips")],
    )
    created: list[MediaPoolItemApi] = []

    def apply() -> None:
        for target in scope.objects:
            if scope.folder is not None:
                pairs = target.RemoveMotionBlur(dict(resolved) or None)
                require_ok(pairs is not None, "Folder.RemoveMotionBlur")
                created.extend(pair[1] for pair in pairs or [] if len(pair) > 1 and pair[1])
            else:
                clip = target.RemoveMotionBlur(dict(resolved))
                require_ok(clip, "MediaPoolItem.RemoveMotionBlur")
                created.append(clip)
        names = ", ".join(clip.GetName() for clip in created) or "(none)"
        plan.changes[0] = Change("new clips", None, names)

    def verify() -> dict[str, object]:
        found = [c.GetName() for c in created if where_is(ctx, c.GetUniqueId()) is not None]
        return {"new clips": ", ".join(found) or "(none)"}

    return run_mutation(inv, ctx.session, plan, apply, verify)


# ---- multicam -------------------------------------------------------------------------------


def run_create(inv: Invocation) -> Result:
    reserved = {"name": "--name", "angleSyncMode": "--sync"}
    options = parse_options(str_arg(inv, "set"), MULTICAM_OPTS, "multicam option", reserved)
    name, sync = str_arg(inv, "name"), str_arg(inv, "sync")
    if name is not None:
        options.add("name", name, name)
    if sync is not None:
        options.add("angleSyncMode", EnumRef(f"MULTICAM_ANGLE_SYNC_{sync.upper()}"), sync)
    audio = isinstance(sync_ref := options.values.get("angleSyncMode"), EnumRef) and (
        sync_ref.name == AUDIO_SYNC
    )
    for key in ("channelConfig", "splitAtGaps"):
        if key in options.values and not audio:
            raise AxiError(
                "VALIDATION_ERROR",
                f"{key} only applies with --sync audio",
                help=["Valid example: --sync audio --set channelConfig=1"],
            )
    ctx = open_pool(inv)
    found = resolve_clips(ctx, inv.positionals["clips"])
    resolved = options.resolved(ctx.session)
    clips_text = inv.positionals["clips"]
    words = ["ai", "multicam", "create", clips_text, *flag_words(inv, ("name", "sync", "set"))]
    plan = MutationPlan(
        command="ai multicam create",
        target=clips_target(found),
        changes=[Change("new clips", None, name or "(new multicam clip)")],
        force_cmd=force_words(inv, words),
        extra={"options": options.text},
        warning=f"creates multicam clips in the media pool from the {len(found)} clips",
        next=[show_hint(found)],
    )
    created: list[MediaPoolItemApi] = []

    def apply() -> None:
        pool = cast(AiPool, ctx.pool)
        result = pool.CreateMulticamClip([f.clip for f in found], dict(resolved))
        if not result:
            raise api_failed(
                "MediaPool.CreateMulticamClip", "Resolve created no multicam clip from these clips"
            )
        created.extend(result)
        plan.changes[0] = Change("new clips", None, ", ".join(c.GetName() for c in created))

    def verify() -> dict[str, object]:
        found_names = [c.GetName() for c in created if where_is(ctx, c.GetUniqueId()) is not None]
        return {"new clips": ", ".join(found_names) or "(none)"}

    return run_mutation(inv, ctx.session, plan, apply, verify)


def run_smart_switch(inv: Invocation) -> Result:
    options = parse_options(str_arg(inv, "set"), SMART_SWITCH_OPTS, "smart switch option")
    sel = open_item(inv)
    resolved = options.resolved(sel.session)
    words = ["ai", "multicam", "smart-switch", sel.ref, *flag_words(inv, ("set",))]
    return item_call(
        inv,
        sel,
        "ai multicam smart-switch",
        words,
        lambda item: item.PerformMulticamSmartSwitch(dict(resolved)),
        "TimelineItem.PerformMulticamSmartSwitch",
        "re-cuts the multicam item between its angles; needs AI Extras",
        extra={"options": options.text},
    )


def run_flatten(inv: Invocation) -> Result:
    grade = str(inv.args["grade"])
    sel = open_item(inv)
    constant = enum(sel.session, GRADES[grade])
    return item_call(
        inv,
        sel,
        "ai multicam flatten",
        ["ai", "multicam", "flatten", sel.ref, "--grade", grade],
        lambda item: item.FlattenMulticam(constant),
        "TimelineItem.FlattenMulticam",
        "replaces the multicam item with its flattened clips",
        irreversible=True,
        extra={"grade": grade},
    )


# ---- specs ----------------------------------------------------------------------------------

DEBLUR_SPEC = verb(
    "deblur",
    "Remove motion blur from clips or a folder into new clips (needs AI Extras)",
    run_deblur,
    positionals=SCOPE_ARGS,
    flags=(FOLDER_FLAG, SET_FLAG),
    examples=(
        "davinci-axi ai deblur A001_beach",
        "davinci-axi ai deblur A001_beach --set Format=mp4,Codec=H265 --force",
        "davinci-axi ai deblur --folder Master/Footage --force",
    ),
    methods=methods_of(MediaPoolItem="RemoveMotionBlur", Folder="RemoveMotionBlur") + MEDIA_READS,
)
CREATE_SPEC = verb(
    "create",
    "Create a multicam clip from clips in the media pool",
    run_create,
    positionals=(CLIPS,),
    flags=(
        Flag("name", "str", None, help="name of the multicam clip"),
        Flag("sync", "str", None, choices=SYNC_MODES, help="how to sync the angles"),
        SET_FLAG,
    ),
    examples=(
        "davinci-axi ai multicam create A001_beach,A002_city --sync audio",
        "davinci-axi ai multicam create A001_beach,A002_city --name Interview --force",
    ),
    methods=methods_of(MediaPool="CreateMulticamClip") + MEDIA_READS,
)
SMART_SWITCH_SPEC = verb(
    "smart-switch",
    "Run Multicam SmartSwitch on a multicam timeline item (not read back)",
    run_smart_switch,
    positionals=(ITEM,),
    flags=(SET_FLAG, TIMELINE_FLAG),
    examples=(
        "davinci-axi ai multicam smart-switch V1:1",
        "davinci-axi ai multicam smart-switch V1:1 --set minEditDuration=2,quality=better --force",
    ),
    methods=methods_of(TimelineItem="PerformMulticamSmartSwitch GetName GetUniqueId")
    + methods_of(Timeline="GetItemListInTrack GetTrackCount GetName GetUniqueId"),
)
FLATTEN_SPEC = verb(
    "flatten",
    "Flatten a multicam timeline item into its clips (irreversible, not read back)",
    run_flatten,
    positionals=(ITEM,),
    flags=(
        Flag("grade", "str", None, required=True, choices=tuple(GRADES), help="grade to keep"),
        TIMELINE_FLAG,
    ),
    examples=(
        "davinci-axi ai multicam flatten V1:1 --grade copy",
        "davinci-axi ai multicam flatten V1:1 --grade angle --force",
    ),
    methods=methods_of(TimelineItem="FlattenMulticam GetName GetUniqueId")
    + methods_of(Timeline="GetItemListInTrack GetTrackCount GetName GetUniqueId"),
)
MULTICAM_SUBS: dict[str, CommandSpec] = {
    "create": CREATE_SPEC,
    "smart-switch": SMART_SWITCH_SPEC,
    "flatten": FLATTEN_SPEC,
}
MULTICAM_SPEC = CommandSpec(
    name="multicam",
    summary="Create multicam clips, run SmartSwitch, flatten",
    subcommands=MULTICAM_SUBS,
    examples=(
        "davinci-axi ai multicam create A001_beach,A002_city --sync audio",
        "davinci-axi ai multicam flatten V1:1 --grade copy",
    ),
    reads_only=False,
    methods=tuple(dict.fromkeys(m for s in MULTICAM_SUBS.values() for m in s.methods)),
)

__all__ = ["DEBLUR_SPEC", "MULTICAM_SPEC"]
