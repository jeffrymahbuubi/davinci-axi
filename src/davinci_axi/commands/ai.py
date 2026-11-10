"""`ai`: Studio AI analysis verbs (U20, R35).

This module holds the parent `SPEC` and the clip, folder and item verbs: Intellisearch, slate,
magic mask, stabilize and smart reframe. Timeline analysis (scene cuts, subtitles) lives in
`ai_timeline`, deblur and multicam in `ai_multicam`, status helpers in `ai_status`. Every verb is
a dry run until `--force`, then save, snapshot, write and read-back through
`mutate.run_mutation` (R27-R29). Long jobs poll their status getter (progress on stderr,
`--no-wait` returns at once); calls with no getter follow KTD23 and the summary says "not read
back". A False from an AI call is `NOT_AVAILABLE`: Studio with AI Extras is missing.
"""

from __future__ import annotations

from typing import cast

from davinci_axi.commands.ai_extras import EXTRAS_SPEC
from davinci_axi.commands.ai_multicam import DEBLUR_SPEC, MULTICAM_SPEC
from davinci_axi.commands.ai_preflight import IGNORE_FLAG, ai_mutation
from davinci_axi.commands.ai_status import (
    ITEM_READS,
    REFRAME,
    SLATE,
    STATUS_SPEC,
    UNREAD,
    AiClip,
    AiProject,
    item_call,
    open_item,
    refuse_no_wait_switch,
    require_ok,
    run_job,
    timeline_scope,
)
from davinci_axi.commands.ai_timeline import SCENE_CUTS_SPEC, SUBTITLES_SPEC
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
from davinci_axi.commands.render_common import force_words, methods_of, words
from davinci_axi.commands.timeline_tracks import TIMELINE_FLAG
from davinci_axi.errors import AxiError
from davinci_axi.mutate import Change, MutationPlan
from davinci_axi.output import hint
from davinci_axi.poll import POLL_FLAGS, poll_args
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

MARKER_WORDS = words(
    "none blue cyan green yellow red pink purple fuchsia rose lavender sky mint lemon sand "
    "cocoa cream"
)
MASK_MODES = {"forward": "F", "backward": "B", "both": "BI"}
ITEM = Positional("item", help="timeline item: V1:2 or a unique id")


# ---- intellisearch --------------------------------------------------------------------------


def run_analyze(inv: Invocation) -> Result:
    faces, better = bool(inv.args.get("faces")), bool(inv.args.get("better"))
    package = "AI IntelliSearch - Better" if better else "AI IntelliSearch - Faster"
    scope = pick_scope(inv, ["ai", "intellisearch", "analyze"])
    plan = MutationPlan(
        command="ai intellisearch analyze",
        target=scope.target,
        changes=[Change("called", False, True)],
        force_cmd=force_words(inv, scope.words, ("faces", "better", "ignore-preflight")),
        warning=f"analysis can take minutes and needs the AI Extras package {package}; {UNREAD}",
        next=[hint("ai status", "check the analysis jobs")],
    )

    def apply() -> None:
        for target in scope.objects:
            require_ok(target.AnalyzeForIntellisearch(faces, better), "AnalyzeForIntellisearch")

    return ai_mutation(
        inv, scope.ctx.session, plan, apply, lambda: {"called": True}, package=package
    )


def run_reset(inv: Invocation) -> Result:
    session = inv.connect()
    project = cast(AiProject, session.require_project())
    plan = MutationPlan(
        command="ai intellisearch reset",
        target=f"project {project.GetName()}",
        changes=[Change("called", False, True)],
        force_cmd=force_words(inv, ["ai", "intellisearch", "reset"]),
        irreversible=True,
        warning=f"discards the Intellisearch analysis of the whole project; {UNREAD}",
        next=[hint("ai intellisearch analyze <clips>", "analyze again")],
    )

    def apply() -> None:
        require_ok(project.ResetIntellisearchAnalysis(), "Project.ResetIntellisearchAnalysis")

    return ai_mutation(inv, session, plan, apply, lambda: {"called": True})


# ---- slate ----------------------------------------------------------------------------------


def _marker_word(inv: Invocation) -> str:
    word = (str_arg(inv, "marker") or "blue").strip().lower()
    if word not in MARKER_WORDS:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown marker color {word}",
            help=[f"Valid colors: {', '.join(MARKER_WORDS)}"],
        )
    return word


def run_slate(inv: Invocation) -> Result:
    poll_args(inv)
    word = _marker_word(inv)
    scope = pick_scope(inv, ["ai", "slate"])
    session = scope.ctx.session
    color = enum(session, f"MARKER_{word.upper()}")
    plan = MutationPlan(
        command="ai slate",
        target=scope.target,
        changes=[],
        force_cmd=force_words(
            inv, [*scope.words, *flag_words(inv, ("marker",))], ("ignore-preflight",)
        ),
        extra={"marker": word},
        warning="marks the clips where a slate is found; needs the AI Extras package AI Slate ID",
        next=[hint("ai status", "check the analysis jobs"), hint("media", "list the clips")],
    )

    def start() -> None:
        for target in scope.objects:
            require_ok(cast(AiClip, target).AnalyzeForSlate(color), "AnalyzeForSlate")

    return run_job(inv, session, plan, SLATE, start, package="AI Slate ID")


# ---- magic mask, stabilize, reframe ---------------------------------------------------------


def run_mask_create(inv: Invocation) -> Result:
    mode = str_arg(inv, "mode") or "forward"
    sel = open_item(inv)
    words = ["ai", "magic-mask", "create", sel.ref, *flag_words(inv, ("mode",))]
    return item_call(
        inv,
        sel,
        "ai magic-mask create",
        words,
        lambda item: item.CreateMagicMask(MASK_MODES[mode]),
        "TimelineItem.CreateMagicMask",
        f"adds a magic mask to the item (tracking {mode}); needs AI Extras",
    )


def run_mask_regenerate(inv: Invocation) -> Result:
    sel = open_item(inv)
    return item_call(
        inv,
        sel,
        "ai magic-mask regenerate",
        ["ai", "magic-mask", "regenerate", sel.ref],
        lambda item: item.RegenerateMagicMask(),
        "TimelineItem.RegenerateMagicMask",
        "regenerates the magic mask of the item; needs AI Extras",
    )


def run_stabilize(inv: Invocation) -> Result:
    sel = open_item(inv)
    return item_call(
        inv,
        sel,
        "ai stabilize",
        ["ai", "stabilize", sel.ref],
        lambda item: item.Stabilize(),
        "TimelineItem.Stabilize",
        "analyzes and stabilizes the item",
    )


def run_reframe(inv: Invocation) -> Result:
    wait, _, _ = poll_args(inv)
    sel = open_item(inv)
    refuse_no_wait_switch(sel.session, sel.timeline, wait)
    plan = MutationPlan(
        command="ai reframe",
        target=sel.target,
        changes=[],
        force_cmd=force_words(inv, ["ai", "reframe", sel.ref]),
        warning="analyzes the item and changes its framing; needs AI Extras",
        next=[hint(f"item {sel.ref}", "show the item")],
    )

    def start() -> None:
        require_ok(sel.item.SmartReframe(), "TimelineItem.SmartReframe")

    return run_job(
        inv,
        sel.session,
        plan,
        REFRAME,
        start,
        scope=lambda: timeline_scope(sel.session, sel.timeline, plan),
    )


# ---- specs ----------------------------------------------------------------------------------


def _group(
    name: str,
    summary: str,
    subs: dict[str, CommandSpec],
    *examples: str,
    notes: tuple[str, ...] = (),
) -> CommandSpec:
    return CommandSpec(
        name=name,
        summary=summary,
        subcommands=subs,
        examples=examples,
        notes=notes,
        reads_only=False,
        methods=tuple(dict.fromkeys(m for s in subs.values() for m in s.methods)),
    )


ANALYZE_SPEC = verb(
    "analyze",
    "Analyze clips or a folder for Intellisearch (not read back)",
    run_analyze,
    positionals=SCOPE_ARGS,
    flags=(
        FOLDER_FLAG,
        Flag("faces", "bool", False, help="identify faces"),
        Flag("better", "bool", False, help="use the slower, better analysis mode"),
        IGNORE_FLAG,
    ),
    examples=(
        "davinci-axi ai intellisearch analyze A001_beach --faces",
        "davinci-axi ai intellisearch analyze --folder Master/Footage --better --force",
    ),
    methods=methods_of(MediaPoolItem="AnalyzeForIntellisearch", Folder="AnalyzeForIntellisearch")
    + MEDIA_READS,
)
RESET_SPEC = verb(
    "reset",
    "Discard the Intellisearch analysis of the whole project (irreversible, not read back)",
    run_reset,
    examples=("davinci-axi ai intellisearch reset", "davinci-axi ai intellisearch reset --force"),
    methods=methods_of(Project="ResetIntellisearchAnalysis GetName"),
)
SLATE_SPEC = verb(
    "slate",
    "Find slates in clips or a folder and wait for the job (progress on stderr)",
    run_slate,
    positionals=SCOPE_ARGS,
    flags=(
        FOLDER_FLAG,
        Flag("marker", "str", None, help=f"marker color (default blue): {', '.join(MARKER_WORDS)}"),
        *POLL_FLAGS,
        IGNORE_FLAG,
    ),
    examples=(
        "davinci-axi ai slate A001_beach --marker green",
        "davinci-axi ai slate --folder Master/Footage --force",
        "davinci-axi ai slate A001_beach --force --no-wait",
    ),
    methods=methods_of(
        MediaPoolItem="AnalyzeForSlate",
        Folder="AnalyzeForSlate",
        Project="GetAnalyzeForSlateStatus",
    )
    + MEDIA_READS,
)
MASK_NOTES = (
    "Resolve returns only True/False: the mask cannot be read back, listed or tracked by script, "
    "and no scripted effect (Object Removal, blur, node key) can be attached to it; use it from "
    "the Color page.",
    "Check the node that holds it with `color graph --item V1:1` (the node's tools).",
)
MASK_CREATE_SPEC = verb(
    "create",
    "Create a magic mask on a timeline item (not read back)",
    run_mask_create,
    positionals=(ITEM,),
    flags=(
        Flag(
            "mode",
            "str",
            "forward",
            choices=tuple(MASK_MODES),
            help="tracking direction (default forward)",
        ),
        TIMELINE_FLAG,
    ),
    examples=(
        "davinci-axi ai magic-mask create V1:1 --mode both",
        "davinci-axi ai magic-mask create V1:1 --force",
    ),
    notes=MASK_NOTES,
    methods=methods_of(TimelineItem="CreateMagicMask") + ITEM_READS,
)
MASK_REGENERATE_SPEC = verb(
    "regenerate",
    "Regenerate the magic mask of a timeline item (not read back)",
    run_mask_regenerate,
    positionals=(ITEM,),
    flags=(TIMELINE_FLAG,),
    examples=(
        "davinci-axi ai magic-mask regenerate V1:1",
        "davinci-axi ai magic-mask regenerate V1:1 --force",
    ),
    notes=MASK_NOTES,
    methods=methods_of(TimelineItem="RegenerateMagicMask") + ITEM_READS,
)
STABILIZE_SPEC = verb(
    "stabilize",
    "Stabilize a timeline item (synchronous; not read back)",
    run_stabilize,
    positionals=(ITEM,),
    flags=(TIMELINE_FLAG,),
    examples=("davinci-axi ai stabilize V1:1", "davinci-axi ai stabilize V1:1 --force"),
    notes=(
        "Synchronous: Stabilize() returns when the analysis is done (1-5 s per clip live); nothing "
        "reads the result back, check a still with `render frame`",
    ),
    methods=methods_of(TimelineItem="Stabilize") + ITEM_READS,
)
REFRAME_SPEC = verb(
    "reframe",
    "Smart-reframe a timeline item and wait for the job (progress on stderr)",
    run_reframe,
    positionals=(ITEM,),
    flags=(TIMELINE_FLAG, *POLL_FLAGS),
    examples=(
        "davinci-axi ai reframe V1:1",
        "davinci-axi ai reframe V1:1 --force",
        "davinci-axi ai reframe V1:1 --force --no-wait",
    ),
    methods=methods_of(TimelineItem="SmartReframe", Project="GetSmartReframeStatus") + ITEM_READS,
)

SUBCOMMANDS: dict[str, CommandSpec] = {
    "extras": EXTRAS_SPEC,
    "intellisearch": _group(
        "intellisearch",
        "Intellisearch analysis of clips and folders",
        {"analyze": ANALYZE_SPEC, "reset": RESET_SPEC},
        "davinci-axi ai intellisearch analyze A001_beach --faces",
        "davinci-axi ai intellisearch reset",
    ),
    "slate": SLATE_SPEC,
    "magic-mask": _group(
        "magic-mask",
        "Create or regenerate magic masks on timeline items",
        {"create": MASK_CREATE_SPEC, "regenerate": MASK_REGENERATE_SPEC},
        "davinci-axi ai magic-mask create V1:1 --mode both",
        "davinci-axi ai magic-mask regenerate V1:1",
        notes=(
            "Write-only in the API: create or regenerate, then finish in the Color page. "
            "For intrusions a scripted route is `item set` (zoom/pan) or a trim.",
        ),
    ),
    "stabilize": STABILIZE_SPEC,
    "reframe": REFRAME_SPEC,
    "scene-cuts": SCENE_CUTS_SPEC,
    "subtitles": SUBTITLES_SPEC,
    "deblur": DEBLUR_SPEC,
    "multicam": MULTICAM_SPEC,
    "status": STATUS_SPEC,
}

SPEC = CommandSpec(
    name="ai",
    summary="Studio AI analysis: Intellisearch, slate, magic mask, reframe, scene cuts, subtitles",
    subcommands=SUBCOMMANDS,
    examples=(
        "davinci-axi ai status",
        "davinci-axi ai scene-cuts --force",
        "davinci-axi ai subtitles --language english --preset netflix --force",
    ),
    reads_only=False,
    methods=tuple(dict.fromkeys(m for s in SUBCOMMANDS.values() for m in s.methods)),
)
