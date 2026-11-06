"""`audio transcribe` and `audio transcription show|clear` (U18).

Transcription is the long job of the audio group: `TranscribeAudio` starts it on clips or a folder
and `poll.ANALYSIS` follows `Project.GetTranscribeAudioStatus` (progress on stderr, the result
block on stdout; `--no-wait` returns after the start call). A False from `TranscribeAudio` means
Studio or AI Extras is missing: `NOT_AVAILABLE`. The job is verified by its status (KTD23).
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

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
from davinci_axi.commands.media_folders import open_pool, resolve_clip
from davinci_axi.commands.render_common import force_words, methods_of
from davinci_axi.errors import AxiError
from davinci_axi.mutate import Change, MutationPlan, run_mutation
from davinci_axi.output import empty_state, hint
from davinci_axi.poll import ANALYSIS, POLL_FLAGS, poll_args, poll_status, settle_start
from davinci_axi.resolve_types import Info
from davinci_axi.spec import TRUNCATE_CHARS, CommandSpec, Flag, Invocation, Positional, Result

BUSY = frozenset({"Initializing", "Analyzing", "Cancelling"})


def status_of(project: AudioProject) -> Info:
    return dict(project.GetTranscribeAudioStatus() or {})


def run_transcribe(inv: Invocation) -> Result:
    wait, timeout, interval = poll_args(inv)
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
    plan = MutationPlan(
        command="audio transcribe",
        target=scope.target,
        changes=[Change("status", before, "Complete" if wait else "started")],
        force_cmd=force_words(inv, scope.words, ("speakers", "nested")),
        warning="transcription can take minutes per clip; it needs AI Extras",
        next=[hint("media", "list the clips"), hint("audio transcription show <clip>", "read it")],
    )
    kind = replace(ANALYSIS, label="transcribe")

    def apply() -> None:
        for target in scope.objects:
            if not target.TranscribeAudio(speakers, nested):
                raise not_available("TranscribeAudio")
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
                for f in scope.clips()
            ]
        return {"status": status}

    return run_mutation(inv, session, plan, apply, verify)


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

    return run_mutation(inv, scope.ctx.session, plan, apply, verify)


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
        *POLL_FLAGS,
    ),
    examples=(
        "davinci-axi audio transcribe A001_beach",
        "davinci-axi audio transcribe --folder Master/Footage --speakers --force",
        "davinci-axi audio transcribe A001_beach --force --no-wait",
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
