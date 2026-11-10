"""`audio transcribe` and `audio transcription show|clear` (U18).

Transcription is the long job of the audio group: `TranscribeAudio` starts it on clips or a folder
and `poll.ANALYSIS` follows `Project.GetTranscribeAudioStatus` (progress on stderr, the result
block on stdout; `--no-wait` returns after the start call). A False from `TranscribeAudio` is
`NOT_AVAILABLE`, reported as the fact (which folder or clip) and never blamed on AI Extras: single
clips work without the extra package. `--per-clip` calls it clip by clip instead of once on the
folder. The job is verified by its status (KTD23).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from davinci_axi.commands.ai_preflight import ai_mutation
from davinci_axi.commands.audio_common import (
    FOLDER_FLAG,
    MEDIA_READS,
    SCOPE_ARGS,
    AudioClip,
    AudioProject,
    not_available,
    pick_scope,
    segment_count,
    verb,
)
from davinci_axi.commands.item_detail import str_arg
from davinci_axi.commands.media_folders import open_pool, props, resolve_clip
from davinci_axi.commands.render_common import force_words, methods_of
from davinci_axi.errors import AxiError
from davinci_axi.mutate import Change, MutationPlan
from davinci_axi.output import empty_state, hint
from davinci_axi.poll import ANALYSIS, POLL_FLAGS, poll_args, poll_status, settle_start
from davinci_axi.resolve_types import Info
from davinci_axi.spec import TRUNCATE_CHARS, CommandSpec, Flag, Invocation, Positional, Result

BUSY = frozenset({"Initializing", "Analyzing", "Cancelling"})


def status_of(project: AudioProject) -> Info:
    return dict(project.GetTranscribeAudioStatus() or {})


LANGUAGES_PACK = "Extended AI Transcription Language Support 2"


def _transcribable(clip: AudioClip) -> bool:
    """A clip with audio that is not a timeline (ASSUMED Resolve `Type` words)."""
    kind = str(props(clip).get("Type", ""))
    return "Audio" in kind and "Timeline" not in kind


def _folder_false(path: str, clips: list[Any]) -> AxiError:
    names = ",".join(c.GetName() for c in clips[:5]) + (",..." if len(clips) > 5 else "")
    return not_available(
        "Folder.TranscribeAudio",
        message=f"TranscribeAudio returned False for folder {path} ({len(clips)} clips)",
        help=[
            hint(f"audio transcribe {names}", "transcribe the clips one by one"),
            "Or pass --per-clip to transcribe each audio clip of the folder in turn",
            "Check the folder for timeline, compound or offline clips; Resolve refuses the whole "
            "folder when one clip cannot be transcribed (unverified)",
            "Check that Resolve shows no dialog",
        ],
        detail={"folder": path, "clips": len(clips)},
    )


def _clip_false(name: str) -> AxiError:
    return not_available(
        "MediaPoolItem.TranscribeAudio",
        message=f"TranscribeAudio returned False for clip {name}",
        help=[
            hint("media show <clip>", "check that the clip has audio and is online"),
            "Check that Resolve shows no dialog",
        ],
        detail={"clip": name},
    )


def _check_per_clip(inv: Invocation, wait: bool) -> bool:
    per_clip = bool(inv.args.get("per_clip"))
    if per_clip and not str_arg(inv, "folder"):
        raise AxiError(
            "VALIDATION_ERROR",
            "--per-clip only applies with --folder",
            help=["Valid example: davinci-axi audio transcribe --folder Master --per-clip"],
        )
    if per_clip and not wait:
        raise AxiError(
            "VALIDATION_ERROR",
            "--per-clip waits for each clip; drop --no-wait",
            help=["Valid example: davinci-axi audio transcribe --folder Master --per-clip"],
        )
    return per_clip


def run_transcribe(inv: Invocation) -> Result:
    wait, timeout, interval = poll_args(inv)
    per_clip = _check_per_clip(inv, wait)
    speakers = True if inv.args.get("speakers") else None
    nested = bool(inv.args.get("nested"))
    scope = pick_scope(inv, ["audio", "transcribe"])
    session = scope.ctx.session
    project = cast(AudioProject, session.require_project())
    before = str(status_of(project).get("JobStatus") or "unknown")
    if before in BUSY:
        raise AxiError(
            "TRANSCRIBE_BUSY",
            f"a transcription is already {before} in Resolve",
            help=["Wait for it to finish, then retry"],
        )
    found = scope.clips()
    targets = found
    extra: dict[str, object] = {}
    if per_clip:
        folder_path = scope.folder[0] if scope.folder else ""
        targets = [f for f in found if _transcribable(cast(AudioClip, f.clip))]
        if not targets:
            raise AxiError(
                "NOT_FOUND",
                f"no audio clips to transcribe in folder {folder_path}",
                help=[hint(f"media --folder {folder_path} --fields name,type", "list the folder")],
            )
        skipped = [f.clip.GetName() for f in found if f not in targets]
        extra["per_clip"] = f"{len(targets)}/{len(found)} clips"
        extra["skipped"] = ", ".join(skipped) or "(none)"
    plan = MutationPlan(
        command="audio transcribe",
        target=scope.target,
        changes=[Change("status", before, "Complete" if wait else "started")],
        force_cmd=force_words(inv, scope.words, ("speakers", "nested", "per-clip")),
        extra=extra,
        warning="transcription can take minutes per clip; clips need audio",
        next=[hint("media", "list the clips"), hint("audio transcription show <clip>", "read it")],
    )
    kind = replace(ANALYSIS, label="transcribe")

    def apply() -> None:
        if per_clip:
            for each in targets:
                if not cast(Any, each.clip).TranscribeAudio(speakers, nested):
                    raise _clip_false(each.clip.GetName())
                poll_status(lambda: status_of(project), kind, timeout=timeout, interval=interval)
            return
        for target in scope.objects:
            if not target.TranscribeAudio(speakers, nested):
                if scope.folder is not None:
                    raise _folder_false(scope.folder[0], [f.clip for f in found])
                raise _clip_false(target.GetName())
        if wait:
            poll_status(lambda: status_of(project), kind, timeout=timeout, interval=interval)

    def verify() -> dict[str, object]:
        info = status_of(project) if wait else settle_start(lambda: status_of(project), kind)
        status = str(info.get("JobStatus") or "unknown")
        if not wait and status not in kind.idle and status not in kind.failed:
            status = "started"
        if wait:
            plan.extra["transcribed"] = [
                {"clip": f.clip.GetName(), "segments": segment_count(f.clip, nested)}
                for f in targets
            ]
        return {"status": status}

    return ai_mutation(inv, session, plan, apply, verify, package=LANGUAGES_PACK, advisory=True)


def run_clear(inv: Invocation) -> Result:
    nested = bool(inv.args.get("nested"))
    scope = pick_scope(inv, ["audio", "transcription", "clear"])
    rows = [(f"{f.folder_path}/{f.clip.GetName()}", f.clip) for f in scope.clips()]
    changes = [
        Change(f"{label} segments", n, 0) for label, c in rows if (n := segment_count(c, nested))
    ]
    plan = MutationPlan(
        command="audio transcription clear",
        target=scope.target,
        changes=changes,
        force_cmd=force_words(inv, scope.words, ("nested",)),
        irreversible=True,
        warning="removes the transcription; making it again takes a new transcription run",
        next=[hint("audio transcribe", "transcribe again")],
    )

    def apply() -> None:
        for target in scope.objects:
            ok = (
                target.ClearTranscription(nested)
                if scope.folder is None
                else target.ClearTranscription()
            )
            if not ok:
                raise not_available("ClearTranscription")

    def verify() -> dict[str, object]:
        return {f"{label} segments": segment_count(c, nested) for label, c in rows}

    return ai_mutation(inv, scope.ctx.session, plan, apply, verify)


def run_show(inv: Invocation) -> Result:
    ctx = open_pool(inv)
    found = resolve_clip(ctx, inv.positionals["clip"])
    nested, full = bool(inv.args.get("nested")), bool(inv.args.get("full"))
    name = found.clip.GetName()
    transcription = cast(AudioClip, found.clip).GetTranscription(nested) or {}
    segments = list(transcription.get("segments") or [])
    data: dict[str, object] = {"clip": name, "language": transcription.get("language")}
    if not segments:
        data["segments"] = empty_state("segments", f"in the transcription of clip {name}", {})
        return Result(data, [hint(f"audio transcribe {name}", "transcribe it (add --force)")])
    rows = [
        {
            "start": s.get("start"),
            "end": s.get("end"),
            "speaker": s.get("speaker"),
            "text": s.get("text"),
        }
        for s in segments
    ]
    help_lines: list[str] = []
    if len(segments) == 1 and not str(segments[0].get("text") or "").strip():
        data["note"] = "no speech detected"
        help_lines.append(hint(f"audio transcription clear {name}", "drop the empty transcription"))
    if not full:
        used, keep = 0, 0
        for row in rows:
            used += sum(len(str(v)) for v in row.values()) + 8
            if used > TRUNCATE_CHARS and keep:
                break
            keep += 1
        if keep < len(rows):
            full_cmd = f"audio transcription show {name} --full" + (" --nested" if nested else "")
            data["note"] = (
                f"showing {keep} of {len(rows)} segments; run `davinci-axi {full_cmd}` for all"
            )
            rows = rows[:keep]
    data["total"] = len(segments)
    data["segments"] = rows
    return Result(data, help_lines)


# ---- specs ----------------------------------------------------------------------------------

CLIP = Positional("clip", help="clip id or exact clip name")
NESTED = Flag("nested", "bool", False, help="the nested-clip transcription")


TRANSCRIBE_SPEC = verb(
    "transcribe",
    "Transcribe clips or a folder and wait for the job (progress on stderr)",
    run_transcribe,
    positionals=SCOPE_ARGS,
    flags=(
        FOLDER_FLAG,
        Flag("speakers", "bool", False, help="detect speakers"),
        Flag("nested", "bool", False, help="transcribe as a nested clip"),
        Flag(
            "per-clip",
            "bool",
            False,
            help="with --folder: call TranscribeAudio on each audio clip in turn instead of once "
            "on the folder (skips clips without audio and timeline clips); each clip gets the "
            "full --poll-timeout, and 60 clips take about 20 minutes",
        ),
        *POLL_FLAGS,
    ),
    examples=(
        "davinci-axi audio transcribe A001_beach",
        "davinci-axi audio transcribe --folder Master/Footage --speakers --force",
        "davinci-axi audio transcribe A001_beach --force --no-wait",
        "davinci-axi audio transcribe --folder Master --per-clip --force",
    ),
    methods=methods_of(
        MediaPoolItem="TranscribeAudio GetTranscription",
        Folder="TranscribeAudio",
        Project="GetTranscribeAudioStatus",
    )
    + MEDIA_READS,
)
SHOW_SPEC = CommandSpec(
    name="show",
    summary="Show the transcription of a clip as segments",
    run=run_show,
    positionals=(CLIP,),
    flags=(NESTED, Flag("full", "bool", False, help="show every segment")),
    examples=(
        "davinci-axi audio transcription show A001_beach",
        "davinci-axi audio transcription show A001_beach --full",
    ),
    methods=methods_of(MediaPoolItem="GetTranscription") + MEDIA_READS,
)
CLEAR_SPEC = verb(
    "clear",
    "Clear the transcription of clips or a folder (irreversible except through a snapshot)",
    run_clear,
    positionals=SCOPE_ARGS,
    flags=(FOLDER_FLAG, NESTED),
    examples=(
        "davinci-axi audio transcription clear A001_beach",
        "davinci-axi audio transcription clear --folder Master/Footage --force",
    ),
    methods=methods_of(
        MediaPoolItem="ClearTranscription GetTranscription", Folder="ClearTranscription"
    )
    + MEDIA_READS,
)
TRANSCRIPTION_SPEC = CommandSpec(
    name="transcription",
    summary="Read or clear clip transcriptions",
    subcommands={"show": SHOW_SPEC, "clear": CLEAR_SPEC},
    reads_only=False,
    methods=SHOW_SPEC.methods + CLEAR_SPEC.methods,
)

SPECS: dict[str, CommandSpec] = {
    "transcribe": TRANSCRIBE_SPEC,
    "transcription": TRANSCRIPTION_SPEC,
}
