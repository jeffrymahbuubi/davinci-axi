"""`storage clone start|status|stop`: the Media Storage clone tool (U21, KTD21, KTD23).

Cloning copies media on disk, outside the project, so the snapshot policy is `none`, but starting
or stopping a clone still needs `--force` (R27). `start` polls `GetCloneStatus` with
`poll.CLONE`: progress goes to stderr, the result block to stdout; `--no-wait` returns once the
clone has started. Checksum words map to `resolve.CLONE_CHECKSUM_TYPE_*` at run time (KTD24).
"""

from __future__ import annotations

import sys
import time

from davinci_axi.commands.media_folders import abs_path
from davinci_axi.commands.storage_common import STORAGE, split_paths, storage_of
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.output import hint
from davinci_axi.poll import CLONE, POLL_FLAGS, poll_args, poll_status
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

CHECKSUMS: dict[str, str] = {
    "none": "CLONE_CHECKSUM_TYPE_NONE",
    "filesize": "CLONE_CHECKSUM_TYPE_FILESIZE",
    "crc32": "CLONE_CHECKSUM_TYPE_CRC32",
    "md5": "CLONE_CHECKSUM_TYPE_MD5",
    "sha256": "CLONE_CHECKSUM_TYPE_SHA256",
    "sha512": "CLONE_CHECKSUM_TYPE_SHA512",
    "xxh64": "CLONE_CHECKSUM_TYPE_XXH_64",
}
CLONING = "Cloning"
STOP_WAIT_TRIES = 10
STOP_WAIT_SECONDS = 0.2
GET_STATUS = "Resolve.GetMediaStorage", STORAGE + "GetCloneStatus"


def _enum(session: Session, name: str) -> object:
    value = getattr(session.resolve, name, None)
    if value is None:
        raise AxiError(
            "API_FAILED",
            f"this Resolve has no {name}",
            help=["Choose another --checksum, or update Resolve to a version that offers it"],
        )
    return value


def _status(storage_status: object) -> dict[str, object]:
    return dict(storage_status) if isinstance(storage_status, dict) else {}


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def run_start(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    wait, timeout, interval = poll_args(inv)
    source_text, targets_text = inv.positionals["source"], inv.positionals["targets"]
    source = abs_path(source_text.strip())
    targets = split_paths(targets_text, "target folders")
    bad = [t for t in targets if _inside(t, source)]
    if bad:
        raise AxiError(
            "VALIDATION_ERROR",
            f"target {bad[0]} is the source folder or inside it",
            help=["Choose target folders outside the source folder"],
        )
    word = str(inv.args.get("checksum") or "") or None
    session = inv.connect()
    storage = storage_of(session)
    settings: dict[str, object] = {"PreserveFolderName": bool(inv.args.get("preserve_folder_name"))}
    if word:
        settings["ChecksumType"] = _enum(session, CHECKSUMS[word])
    before = _status(storage.GetCloneStatus())
    if before.get("JobStatus") == CLONING:
        raise AxiError(
            "CLONE_BUSY",
            "a clone is already running in Resolve",
            help=[hint("storage clone status", "check it"), hint("storage clone stop", "stop it")],
        )
    after = "Complete" if wait else "started"
    words = ["storage", "clone", "start", source_text, targets_text]
    if inv.args.get("preserve_folder_name"):
        words.append("--preserve-folder-name")
    if word:
        words += ["--checksum", word]
    if not wait:
        words.append("--no-wait")
    plan = MutationPlan(
        command="storage clone start",
        target=f"clone {source} to {len(targets)} folder(s)",
        changes=[Change("clone", "idle", after)],
        force_cmd=force_cmd(inv, *words),
        snapshot="none",
        warning="copies media files on disk outside the project; there is no snapshot to restore",
        extra={
            "source": source,
            "targets": ", ".join(targets),
            "checksum": word or "(unchanged)",
            "preserve_folder_name": bool(settings["PreserveFolderName"]),
        },
        next=[hint("storage clone status", "check the clone job")],
    )

    def read() -> dict[str, object]:
        return _status(storage.GetCloneStatus())

    def apply() -> None:
        if not storage.SetCloneToolSettings(settings):
            raise api_failed("MediaStorage.SetCloneToolSettings", "Resolve refused the settings")
        if not storage.StartCloneMedia(source, targets):
            raise api_failed(
                "MediaStorage.StartCloneMedia",
                "Resolve did not start the clone; check the source and target folders",
            )
        if wait:
            result = poll_status(read, CLONE, timeout=timeout, interval=interval, err=sys.stderr)
            plan.extra["percent"] = result.percent

    def verify() -> dict[str, object]:
        status = str(read().get("JobStatus") or "unknown")
        if not wait and status not in CLONE.failed:
            status = "started"
        return {"clone": status}

    return run_mutation(inv, session, plan, apply, verify)


def run_status(inv: Invocation) -> Result:
    info = _status(storage_of(inv.connect()).GetCloneStatus())
    status = str(info.get("JobStatus") or "unknown")
    data: dict[str, object] = {"status": status, "percent": info.get("CompletionPercentage")}
    if info.get("Error"):
        data["error"] = str(info["Error"])
    lines = [hint("storage clone stop", "stop the running clone")] if status == CLONING else []
    return Result(data, lines)


def run_stop(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    session = inv.connect()
    storage = storage_of(session)
    running = _status(storage.GetCloneStatus()).get("JobStatus") == CLONING
    plan = MutationPlan(
        command="storage clone stop",
        target="the running clone job",
        changes=[Change("cloning", True, False)] if running else [],
        force_cmd=force_cmd(inv, "storage", "clone", "stop"),
        snapshot="none",
        next=[hint("storage clone status", "check the final status")],
    )
    if not running:
        plan.extra["note"] = "no clone is running"

    def apply() -> None:
        if not storage.StopCloneMedia():
            raise api_failed("MediaStorage.StopCloneMedia", "Resolve did not stop the clone")

    def verify() -> dict[str, object]:
        for _ in range(STOP_WAIT_TRIES):
            if _status(storage.GetCloneStatus()).get("JobStatus") != CLONING:
                break
            time.sleep(STOP_WAIT_SECONDS)
        return {"cloning": _status(storage.GetCloneStatus()).get("JobStatus") == CLONING}

    return run_mutation(inv, session, plan, apply, verify)


START_SPEC = CommandSpec(
    name="start",
    summary="Clone a folder to one or more target folders and wait for it (dry run until --force)",
    run=run_start,
    positionals=(
        Positional("source", help="absolute source folder"),
        Positional("targets", help="absolute target folders, comma-separated"),
    ),
    flags=(
        Flag("preserve-folder-name", "bool", False, help="keep the source folder name in targets"),
        Flag("checksum", "str", None, choices=tuple(CHECKSUMS), help="checksum type to verify"),
        *POLL_FLAGS,
        *MUTATION_FLAGS,
    ),
    examples=(
        "davinci-axi storage clone start /Volumes/Card /Volumes/A,/Volumes/B --checksum sha256",
        "davinci-axi storage clone start /Volumes/Card /Volumes/A --force --no-wait",
    ),
    reads_only=False,
    methods=(
        "Resolve.GetMediaStorage",
        STORAGE + "SetCloneToolSettings",
        STORAGE + "StartCloneMedia",
        STORAGE + "GetCloneStatus",
    ),
)
STATUS_SPEC = CommandSpec(
    name="status",
    summary="Show the status of the current or last clone job",
    run=run_status,
    examples=("davinci-axi storage clone status", "davinci-axi storage clone status --json"),
    methods=GET_STATUS,
)
STOP_SPEC = CommandSpec(
    name="stop",
    summary="Stop the running clone job (no snapshot; dry run until --force)",
    run=run_stop,
    flags=MUTATION_FLAGS,
    examples=("davinci-axi storage clone stop", "davinci-axi storage clone stop --force"),
    reads_only=False,
    methods=("Resolve.GetMediaStorage", STORAGE + "StopCloneMedia", STORAGE + "GetCloneStatus"),
)
CLONE_SPEC = CommandSpec(
    name="clone",
    summary="Start, check or stop a Media Storage clone job",
    subcommands={"start": START_SPEC, "status": STATUS_SPEC, "stop": STOP_SPEC},
    examples=("davinci-axi storage clone status",),
    reads_only=False,
    methods=tuple(
        dict.fromkeys(m for s in (START_SPEC, STATUS_SPEC, STOP_SPEC) for m in s.methods)
    ),
)
