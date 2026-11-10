"""`snapshot prune`: remove old snapshot directories, never a project's newest or a manual one.

Prune is the irreversible bulk verb of the `snapshot` group. A snapshot taken by `project snapshot`
is manual: it is kept unless `--all` is given (F34 g). Only directories inside the snapshot root
are ever removed. The snapshot root is shared by every project, so the default scope is the
current Resolve project; `--project <name>` (offline) or `--all-projects` change it (F36).
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from davinci_axi import registry
from davinci_axi.commands.project_io import next_steps, tree_bytes
from davinci_axi.errors import AxiError
from davinci_axi.mutate import Change, MutationPlan, force_cmd, run_mutation
from davinci_axi.output import collapse_home
from davinci_axi.snapshot import snapshot_root
from davinci_axi.snapshot_index import SnapshotMeta, list_snapshots
from davinci_axi.spec import CommandSpec, Flag, Invocation, Result


def inside_root(meta: SnapshotMeta, root: Path) -> bool:
    """True only for a real directory whose resolved path sits under the resolved root."""
    if meta.dir.is_symlink():
        return False
    return meta.dir.resolve().is_relative_to(root.resolve())


_DURATION = re.compile(r"(\d+)([mhdw])")
_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
PRUNE_SHOWN = 10
SCOPE_HELP = [
    "Pass --project <name> to prune one project's snapshots without Resolve",
    "Pass --all-projects to prune every project's snapshots (the pre-F36 behaviour)",
]


def parse_duration(text: str) -> timedelta:
    """`30m`, `12h`, `7d` or `2w` as a timedelta; anything else is VALIDATION_ERROR."""
    match = _DURATION.fullmatch(text.strip())
    if match is None or int(match.group(1)) == 0:
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot read --older-than {text!r} as a duration",
            help=["Valid examples: 30m, 12h, 7d, 2w (a whole number and one unit m, h, d or w)"],
        )
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})


def _created(meta: SnapshotMeta) -> datetime | None:
    try:
        when = datetime.fromisoformat(meta.created)
    except ValueError:
        return None
    return when if when.tzinfo else when.astimezone()


def _newest_per_project(snapshots: list[SnapshotMeta]) -> set[str]:
    """Ids of each project's newest snapshot (`snapshots` is newest first)."""
    newest: dict[str, str] = {}
    for meta in snapshots:
        newest.setdefault(meta.project, meta.id)
    return set(newest.values())


def is_manual(meta: SnapshotMeta) -> bool:
    """True for `project snapshot` snapshots.

    Legacy meta (no `manual` key): manual when the reason does not start with a command name.
    """
    if meta.manual is not None:
        return meta.manual
    words = meta.reason.split()
    return (words[0] if words else "") not in registry.COMMANDS


def prune_candidates(
    snapshots: list[SnapshotMeta],
    *,
    keep: int | None,
    older_than: timedelta | None,
    now: datetime,
    include_manual: bool = False,
) -> list[SnapshotMeta]:
    """Snapshots to remove, oldest first, never a project's newest.

    Manual snapshots are kept too unless `include_manual`. `snapshots` is newest first. A
    candidate is older than `older_than` (an unparsable `created` never is) or lies beyond the
    `keep` newest overall; manual snapshots still count toward `keep`.
    """
    protected = _newest_per_project(snapshots)
    chosen = {m.id for m in snapshots[keep:]} if keep is not None else set()
    if older_than is not None:
        cutoff = now - older_than
        chosen |= {m.id for m in snapshots if (w := _created(m)) is not None and w <= cutoff}
    if not include_manual:
        chosen -= {m.id for m in snapshots if is_manual(m)}
    return [m for m in reversed(snapshots) if m.id in chosen - protected]


def _prune_flags(inv: Invocation) -> tuple[int | None, timedelta | None]:
    keep, older = inv.args.get("keep"), inv.args.get("older_than")
    if keep is None and older is None:
        raise AxiError(
            "VALIDATION_ERROR",
            "snapshot prune needs --keep <n> or --older-than <duration>",
            help=["Valid examples: --keep 10, --older-than 7d (each project's newest is kept)"],
        )
    if keep is not None and (not isinstance(keep, int) or keep < 1):
        raise AxiError(
            "VALIDATION_ERROR",
            f"flag --keep needs a whole number of at least 1, got {keep}",
            help=["Valid example: --keep 10"],
        )
    return keep, parse_duration(older) if isinstance(older, str) else None


def prune_scope(inv: Invocation) -> str | None:
    """Project name to prune, or None for every project.

    `--project` wins and never connects; `--all-projects` is None; both is VALIDATION_ERROR;
    neither asks Resolve for the current project (connection errors get SCOPE_HELP prepended).
    """
    project = inv.args.get("project")
    named = project if isinstance(project, str) and project else None
    everything = bool(inv.args.get("all_projects"))
    if named and everything:
        raise AxiError(
            "VALIDATION_ERROR",
            "--project and --all-projects cannot be used together",
            help=["Pass one scope: --project <name> or --all-projects"],
        )
    if named:
        return named
    if everything:
        return None
    try:
        return str(inv.connect().require_project().GetName())
    except AxiError as err:
        raise AxiError(err.code, err.message, [*SCOPE_HELP, *err.help], err.detail) from err


def run_prune(inv: Invocation) -> Result:
    keep, older_than = _prune_flags(inv)
    wanted = prune_scope(inv)
    root = snapshot_root()
    warnings: list[str] = []

    def scoped(into: list[str] | None) -> list[SnapshotMeta]:
        return [m for m in list_snapshots(root, into) if wanted is None or m.project == wanted]

    snapshots = scoped(warnings)
    now = datetime.now().astimezone()
    if older_than is not None:
        warnings.extend(
            f"{m.id} has an unreadable created time and was not aged"
            for m in snapshots
            if _created(m) is None
        )
    victims: list[SnapshotMeta] = []
    include_manual = bool(inv.args.get("all"))
    for meta in prune_candidates(
        snapshots, keep=keep, older_than=older_than, now=now, include_manual=include_manual
    ):
        if inside_root(meta, root):
            victims.append(meta)
        else:
            warnings.append(f"skipped {meta.id}: not a real directory inside the snapshot root")
    protected = sorted(_newest_per_project(snapshots))
    ids = [m.id for m in victims]
    extra: dict[str, object] = {
        "scope": "all projects" if wanted is None else wanted,
        "kept_newest": protected,
    }
    if not include_manual:
        kept_manual = [m.id for m in snapshots if is_manual(m)]
        if kept_manual:
            more = 0 if inv.opts.json else max(0, len(kept_manual) - PRUNE_SHOWN)
            shown = kept_manual[: len(kept_manual) - more]
            extra["kept_manual"] = [*shown, f"+{more} more"] if more else kept_manual
    if ids:
        more = 0 if inv.opts.json else max(0, len(ids) - PRUNE_SHOWN)
        extra["delete"] = [*ids[: len(ids) - more], f"+{more} more"] if more else ids
        extra["bytes"] = sum(tree_bytes(m.dir) for m in victims)
    else:
        extra["note"] = "nothing to prune"
    if warnings:
        extra["warnings"] = warnings
    given = {"--keep": keep, "--older-than": inv.args.get("older_than"), "--project": wanted}
    words = ["snapshot", "prune", *(w for k, v in given.items() if v for w in (k, str(v)))]
    if include_manual:
        words.append("--all")
    if inv.args.get("all_projects"):
        words.append("--all-projects")
    where = "across all projects" if wanted is None else f"of project {wanted}"
    plan = MutationPlan(
        command="snapshot prune",
        target=f"{len(victims)} of {len(snapshots)} snapshots {where} under {collapse_home(root)}",
        changes=[Change("snapshots", len(snapshots), len(snapshots) - len(victims))]
        if victims
        else [],
        force_cmd=force_cmd(inv, *words),
        irreversible=True,
        warning=f"removes {len(victims)} snapshot directories from disk; "
        "their .drp files cannot be recovered",
        snapshot="none",
        extra=extra,
        next=next_steps(("snapshot list", "list the remaining snapshots")),
    )

    def apply() -> None:
        removed: list[str] = []
        for meta in victims:
            try:
                shutil.rmtree(meta.dir)
            except OSError as exc:
                raise AxiError(
                    "DELETE_FAILED",
                    f"could not remove {collapse_home(meta.dir)}: {exc}",
                    help=["Check the directory permissions, then retry"],
                    detail={"removed": removed, "failed": meta.id},
                ) from exc
            removed.append(meta.id)

    def verify() -> dict[str, object]:
        return {"snapshots": len(scoped(None))}

    return run_mutation(inv, None, plan, apply, verify)


PRUNE_SPEC = CommandSpec(
    name="prune",
    summary="Delete old snapshot directories, keeping each project's newest and every manual "
    "snapshot (dry run; current project unless --project or --all-projects)",
    run=run_prune,
    flags=(
        Flag(
            "keep",
            "int",
            None,
            help="keep only this many newest snapshots overall (at least 1; manual ones count too)",
        ),
        Flag("older-than", "str", None, help="remove snapshots older than this: 30m, 12h, 7d, 2w"),
        Flag(
            "project",
            "str",
            None,
            help="only snapshots of this project name (offline; default: the current Resolve "
            "project)",
        ),
        Flag(
            "all-projects",
            "bool",
            False,
            help="consider every project's snapshots; without it (and without --project) only "
            "the current Resolve project's snapshots are pruned",
        ),
        Flag(
            "all",
            "bool",
            False,
            help="also prune snapshots taken by `project snapshot` (any --reason); "
            "without it they are always kept",
        ),
        Flag(
            "force",
            "bool",
            False,
            help="delete the snapshots (without it the command is a dry run)",
        ),
    ),
    examples=(
        "davinci-axi snapshot prune --keep 10",
        "davinci-axi snapshot prune --older-than 7d --force",
        "davinci-axi snapshot prune --keep 20 --all",
        "davinci-axi snapshot prune --older-than 7d --all-projects",
    ),
    reads_only=False,
)
