"""`media stills`: one small JPEG per pool clip (or per `--at` percentage) plus a manifest (U3).

Resolve exports a still only from a timeline, so the clips are first placed on a scratch timeline
`_axi-stills-<HHMMSS>`, made current, exported frame by frame, and deleted again. The project ends
unchanged, so no snapshot is taken (`snapshot="none"`); the previous timeline and page are
restored.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from davinci_axi.commands.audio_common import FOLDER_FLAG, Scope, pick_scope
from davinci_axi.commands.item_detail import walk_items
from davinci_axi.commands.media_folders import Found, clips_target
from davinci_axi.commands.render_common import methods_of, render_project
from davinci_axi.commands.stills_common import (
    STILL_FLAGS,
    StillRow,
    check_still_flags,
    compose_sheet,
    export_still,
    produced_name,
    still_path,
    stills_result,
    write_manifest,
)
from davinci_axi.commands.timeline_stills import _fps, _refuse_busy_dir
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    force_cmd,
    refuse_existing,
    run_mutation,
    with_page,
)
from davinci_axi.output import collapse_home
from davinci_axi.resolve_types import Info
from davinci_axi.resolve_types_edit import MediaPoolEdit, TimelineEdit
from davinci_axi.spec import CommandSpec, Invocation, Positional, Result


class _ScratchPool(MediaPoolEdit, Protocol):
    def CreateTimelineFromClips(self, name: str, clipInfos: list[Info]) -> TimelineEdit | None: ...


SCRATCH_PREFIX = "_axi-stills-"
_DEFAULTS = {"at": "50", "max_size": 480, "quality": 60, "columns": 6}


def _positionals(inv: Invocation) -> tuple[Invocation, str]:
    """Split the two optional positionals into `(inv with clips, dir text)`.

    The flag parser binds by position, so `--folder F ~/sb` arrives as one value in `clips`.
    """
    values = [inv.positionals[key] for key in ("clips", "dir") if key in inv.positionals]
    folder = inv.args.get("folder")
    if folder:
        if len(values) == 2:
            raise AxiError(
                "VALIDATION_ERROR",
                "pass clips or --folder, exactly one of them",
                help=["Valid example: davinci-axi media stills --folder Master/Footage ~/sb"],
            )
        if not values:
            raise AxiError(
                "VALIDATION_ERROR",
                "missing <dir> for `media stills`",
                help=["Valid example: davinci-axi media stills --folder Master/Footage ~/sb"],
            )
        return replace(inv, positionals={}), values[0]
    if len(values) < 2:
        raise AxiError(
            "VALIDATION_ERROR",
            "pass clips (or --folder) and an output folder",
            help=["Valid example: davinci-axi media stills A001_beach,A002_city ~/sb"],
        )
    return replace(inv, positionals={"clips": values[0]}), values[1]


def _flag_words(inv: Invocation) -> list[str]:
    """The still flags the caller changed, so the printed force command repeats them."""
    words: list[str] = []
    for key, default in _DEFAULTS.items():
        value = inv.args.get(key)
        if value is not None and str(value) != str(default):
            words += [f"--{key.replace('_', '-')}", str(value)]
    sheet = inv.args.get("sheet")
    if isinstance(sheet, str) and sheet:
        words += ["--sheet", sheet]
    if inv.args.get("overwrite"):
        words.append("--overwrite")
    return words


def _video_clips(scope: Scope) -> tuple[list[Found], list[str]]:
    """The clips whose pool `Type` says Video, and `name (Type)` for each one skipped."""
    video: list[Found] = []
    skipped: list[str] = []
    for found in scope.clips():
        kind = str(found.clip.GetClipProperty("Type") or "")
        if "Video" in kind:
            video.append(found)
        else:
            skipped.append(f"{found.clip.GetName()} ({kind or 'unknown'})")
    if not video:
        raise AxiError(
            "VALIDATION_ERROR",
            "none of these clips is a video clip",
            help=["Pick clips whose Type contains Video; `davinci-axi media` lists them"],
            detail={"skipped": ", ".join(skipped)},
        )
    return video, skipped


def run_media_stills(inv: Invocation) -> Result:
    ats, max_size, quality, columns = check_still_flags(inv.args)
    clips_inv, dir_text = _positionals(inv)
    target = Path(dir_text).expanduser().resolve()
    overwrite = bool(inv.args.get("overwrite"))
    _refuse_busy_dir(target, overwrite)
    sheet_text = inv.args.get("sheet")
    sheet = Path(sheet_text).expanduser().resolve() if isinstance(sheet_text, str) else None
    if sheet is not None:
        refuse_existing(sheet, overwrite)
    scope = pick_scope(clips_inv, ["media", "stills"])
    video, skipped = _video_clips(scope)
    session = scope.ctx.session
    project = render_project(session)
    pool = cast(_ScratchPool, scope.ctx.pool)
    name = f"{SCRATCH_PREFIX}{datetime.now():%H%M%S}"
    extra: dict[str, object] = {
        "dir": collapse_home(target),
        "clips": len(video),
        "at": ",".join(map(str, ats)),
    }
    if skipped:
        extra["skipped"] = ", ".join(skipped)
    planned = len(video) * len(ats)
    plan = MutationPlan(
        command="media stills",
        target=clips_target(video),
        changes=[Change("stills", 0, planned)],
        force_cmd=force_cmd(inv, *scope.words, dir_text, *_flag_words(inv)),
        snapshot="none",
        warning=(
            f"builds and deletes a scratch timeline {name}; "
            "the project ends unchanged, so no snapshot is taken"
        ),
        extra=extra,
    )
    rows: list[StillRow] = []

    def apply() -> None:
        previous = project.GetCurrentTimeline()
        scratch = pool.CreateTimelineFromClips(name, [{"mediaPoolItem": f.clip} for f in video])
        if scratch is None:
            raise api_failed("MediaPool.CreateTimelineFromClips", f"could not create {name}")
        try:
            if not project.SetCurrentTimeline(scratch):
                raise api_failed("SetCurrentTimeline", f"could not open timeline {name}")
            _export_all(scratch, video)
        finally:
            # Leave the scratch timeline before deleting it: Resolve may refuse to delete the
            # timeline that is open.
            if previous is not None:
                project.SetCurrentTimeline(previous)
            if not pool.DeleteTimelines([scratch]):
                extra["cleanup"] = f"could not delete {name}; run timeline delete {name} --force"

    def _export_all(scratch: TimelineEdit, clips: list[Found]) -> None:
        if ";" in scratch.GetStartTimecode():
            raise AxiError(
                "VALIDATION_ERROR",
                "stills cannot convert frames on a drop-frame timeline",
                help=["Use `render frame --tc HH:MM:SS;FF` for single frames"],
            )
        fps = _fps(session, scratch)
        placed = list(walk_items(scratch, ("video",), 1))
        with with_page(session, "color"):
            for index, located in enumerate(placed, start=1):
                start, length = int(located.item.GetStart()), int(located.item.GetDuration())
                clip = clips[index - 1].clip if len(placed) == len(clips) else located.item
                label = clip.GetName()
                for pct in ats:
                    frame = start + max(0, min(length - 1, round(length * pct / 100)))
                    dest = still_path(target, index, "V1", label, pct)
                    code = export_still(
                        session, project, scratch, frame, fps, dest, max_size=max_size,
                        quality=quality,
                    )  # fmt: skip
                    rows.append(
                        StillRow(
                            file=produced_name(dest),
                            item=label,
                            id=clip.GetUniqueId(),
                            name=label,
                            frame=frame,
                            timecode=code,
                            pct=pct,
                        )
                    )

    def verify() -> dict[str, object]:
        return {"stills": len(rows)}

    result = run_mutation(inv, session, plan, apply, verify)
    if not inv.args.get("force") or not plan.changes:
        return result
    if rows:
        write_manifest(target, rows)
        if sheet is not None:
            compose_sheet(rows, target, sheet, columns)
    shown = stills_result(target, rows, sheet, {})
    data = {**result.data, **shown.data}
    return Result(data, [*result.help, *shown.help])


MEDIA_STILLS_SPEC = CommandSpec(
    name="stills",
    summary="Export small JPEG stills of pool clips through a scratch timeline plus a manifest",
    run=run_media_stills,
    positionals=(
        Positional("clips", required=False, help="clip ids or names, comma-separated"),
        Positional("dir", required=False, help="output folder (created if missing)"),
    ),
    flags=(*STILL_FLAGS, FOLDER_FLAG, *MUTATION_FLAGS),
    examples=(
        "davinci-axi media stills A001_beach,A002_city ~/sb --force",
        "davinci-axi media stills --folder Master/Footage ~/sb --at 10,50,90 --force",
    ),
    reads_only=False,
    methods=methods_of(
        MediaPool="CreateTimelineFromClips DeleteTimelines GetRootFolder",
        Project=(
            "GetCurrentTimeline SetCurrentTimeline ExportCurrentFrameAsStill GetMediaPool "
            "GetSettings GetTimelineCount GetTimelineByIndex"
        ),
        Timeline=(
            "SetCurrentTimecode GetCurrentTimecode GetSettings GetStartTimecode "
            "GetItemListInTrack GetTrackCount GetName"
        ),
        TimelineItem="GetStart GetDuration GetName GetUniqueId",
        MediaPoolItem="GetName GetUniqueId GetClipProperty",
        Folder="GetClipList GetSubFolderList GetName",
        Resolve="GetCurrentPage OpenPage",
    ),
    notes=(
        "Resolve exports stills only from a timeline: the clips go on a scratch timeline that "
        f"{SCRATCH_PREFIX}<HHMMSS> names and that is deleted afterwards",
        "No snapshot is taken: the project ends unchanged; audio clips are skipped",
        "Pass clips or --folder (it includes sub folders), not both",
        "--sheet needs Pillow (pip install 'davinci-axi[sheet]'); without sips the stills stay PNG",
    ),
)
