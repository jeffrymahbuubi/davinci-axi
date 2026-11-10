"""`timeline`: create, duplicate, rename, delete and open timelines; settings, timecodes, tracks
and markers (R27-R30, R32, AE5).

Every verb is a dry run until `--force`; `--force` saves the project, snapshots it, writes and
reads the change back (`mutate.run_mutation`). `timeline_tracks.py` holds the `track` and
`marker` verbs and the helpers shared with this module.
"""

from __future__ import annotations

from typing import cast

from davinci_axi.commands.timeline_tracks import (
    MARKER_SPEC,
    TIMELINE_FLAG,
    TRACK_SPEC,
    check_timecode_fields,
    context_words,
    count_items,
    finish,
    is_custom,
    plural,
    same_value,
    target_timeline,
    text_arg,
    timecode_arg,
    timelines_hint,
    wire,
)
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    coerce_like,
    force_cmd,
    parse_assignments,
    run_mutation,
    with_current_timeline,
)
from davinci_axi.resolve_types_edit import MediaPoolEdit, ProjectEdit, TimelineEdit
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

CUSTOM = "useCustomSettings"
NAME_ARG = Positional("name", help="timeline name")
TO_FLAG = Flag("to", "str", None, required=True, help="the new name")


def _name(inv: Invocation, key: str = "name") -> str:
    raw = inv.positionals[key] if key in inv.positionals else text_arg(inv, key)
    name = (raw or "").strip()
    if not name:
        raise AxiError(
            "VALIDATION_ERROR",
            "the timeline name is empty",
            help=['Valid example: davinci-axi timeline create "Edit v3"'],
        )
    return name


def _new_name(inv: Invocation) -> str:
    return _name(inv, "to")


def _exists(name: str) -> AxiError:
    return AxiError(
        "TIMELINE_EXISTS",
        f"a timeline named {name} already exists",
        help=["Choose another name; this command never overwrites a timeline"],
    )


def _pool(session: Session) -> MediaPoolEdit:
    return cast(MediaPoolEdit, session.require_project().GetMediaPool())


def _target(session: Session, name: str) -> TimelineEdit:
    return cast(TimelineEdit, session.require_timeline(name))


def _present(session: Session, name: str) -> str | None:
    return name if session.find_timeline(name) is not None else None


def _current_name(session: Session) -> str | None:
    now = session.current_timeline()
    return now.GetName() if now is not None else None


def run_create(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    name = _name(inv)
    session = inv.connect()
    project = session.require_project()
    if session.find_timeline(name) is not None:
        raise _exists(name)
    plan = MutationPlan(
        command="timeline create",
        target=f"project {project.GetName()}",
        changes=[Change("timeline", None, name)],
        force_cmd=force_cmd(inv, "timeline", "create", name, *context_words(inv)),
        warning="the new timeline may become the current timeline",
        next=timelines_hint(),
    )
    pool = _pool(session)

    def call() -> bool:
        return pool.CreateEmptyTimeline(name) is not None

    return finish(
        inv,
        session,
        plan,
        "CreateEmptyTimeline",
        call,
        lambda: {"timeline": _present(session, name)},
    )


def run_duplicate(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    name, new = _name(inv), _new_name(inv)
    session = inv.connect()
    source = _target(session, name)
    if session.find_timeline(new) is not None:
        raise _exists(new)
    plan = MutationPlan(
        command="timeline duplicate",
        target=f"timeline {name}",
        changes=[
            Change("duplicate", None, new),
            Change("source", name, name),
            Change("current", _current_name(session), new),
        ],
        force_cmd=force_cmd(inv, "timeline", "duplicate", name, "--to", new, *context_words(inv)),
        next=timelines_hint(),
    )

    def call() -> bool:
        return source.DuplicateTimeline(new) is not None

    return finish(
        inv,
        session,
        plan,
        "DuplicateTimeline",
        call,
        lambda: {
            "duplicate": _present(session, new),
            "source": _present(session, name),
            "current": _current_name(session),
        },
    )


def run_rename(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    name, new = _name(inv), _new_name(inv)
    session = inv.connect()
    timeline = _target(session, name)
    if new != name and session.find_timeline(new) is not None:
        raise _exists(new)
    plan = MutationPlan(
        command="timeline rename",
        target=f"timeline {name}",
        changes=[] if new == name else [Change("name", name, new)],
        force_cmd=force_cmd(inv, "timeline", "rename", name, "--to", new, *context_words(inv)),
        next=timelines_hint(),
    )
    return finish(
        inv,
        session,
        plan,
        "SetName",
        lambda: timeline.SetName(new),
        lambda: {"name": timeline.GetName()},
    )


def run_delete(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    name = _name(inv)
    session = inv.connect()
    timeline = _target(session, name)
    items = count_items(timeline)
    plan = MutationPlan(
        command="timeline delete",
        target=f"timeline {name}",
        changes=[Change("timeline", name, None)],
        force_cmd=force_cmd(inv, "timeline", "delete", name, *context_words(inv)),
        irreversible=True,
        warning=(
            f"deletes timeline {name} and its {plural(items, 'item')}; "
            "the snapshot holds the saved project"
        ),
        next=timelines_hint(),
    )
    pool = _pool(session)
    return finish(
        inv,
        session,
        plan,
        "DeleteTimelines",
        lambda: pool.DeleteTimelines([timeline]),
        lambda: {"timeline": _present(session, name)},
    )


def run_open(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    name = _name(inv)
    session = inv.connect()
    timeline = _target(session, name)
    current = session.current_timeline()
    current_name = current.GetName() if current is not None else None
    plan = MutationPlan(
        command="timeline open",
        target=f"timeline {name}",
        changes=[] if current_name == name else [Change("current", current_name, name)],
        force_cmd=force_cmd(inv, "timeline", "open", name, *context_words(inv)),
        snapshot="none",
        extra={"note": "already the current timeline"} if current_name == name else {},
        next=timelines_hint(),
    )
    project = cast(ProjectEdit, session.require_project())

    def verify() -> dict[str, object]:
        now = session.current_timeline()
        return {"current": now.GetName() if now is not None else None}

    return finish(
        inv,
        session,
        plan,
        "SetCurrentTimeline",
        lambda: project.SetCurrentTimeline(timeline),
        verify,
    )


def run_settings_set(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    wanted = parse_assignments(inv.positionals["assignments"])
    session = inv.connect()
    timeline = target_timeline(session, inv)
    live = timeline.GetSettings()
    unknown = [key for key in wanted if key not in live]
    if unknown:
        raise AxiError(
            "VALIDATION_ERROR",
            f"unknown timeline setting {', '.join(unknown)}",
            help=[f"Valid settings: {', '.join(sorted(live))}"],
        )
    custom = wanted.get(CUSTOM, live.get(CUSTOM))
    if any(key != CUSTOM for key in wanted) and not is_custom(custom):
        raise AxiError(
            "VALIDATION_ERROR",
            "this timeline follows the project settings (useCustomSettings is 0)",
            help=[
                "Run `davinci-axi timeline settings set useCustomSettings=1 --force` first, "
                "then set the other keys",
            ],
        )
    ordered = sorted(wanted, key=lambda key: key != CUSTOM)
    changes: list[Change] = []
    values: dict[str, object] = {}
    for key in ordered:
        after = coerce_like(wanted[key], live[key])
        if not same_value(live[key], after):
            changes.append(Change(key, live[key], after))
            values[key] = wire(after)
    plan = MutationPlan(
        command="timeline settings set",
        target=f"timeline {timeline.GetName()}",
        changes=changes,
        force_cmd=force_cmd(
            inv, "timeline", "settings", "set", inv.positionals["assignments"], *context_words(inv)
        ),
        warning="applies keys one by one; on failure earlier keys stay set",
    )

    def verify() -> dict[str, object]:
        now = timeline.GetSettings()
        return {
            c.field: c.after if same_value(c.after, now.get(c.field)) else now.get(c.field)
            for c in changes
        }

    return finish(inv, session, plan, "SetSettings", lambda: timeline.SetSettings(values), verify)


def run_timecode_set(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    code = timecode_arg(inv)
    session = inv.connect()
    timeline = target_timeline(session, inv)
    check_timecode_fields(code, timeline.GetSettings() or {})
    before = timeline.GetCurrentTimecode()
    plan = MutationPlan(
        command="timeline timecode set",
        target=f"timeline {timeline.GetName()}",
        changes=[] if before == code else [Change("current_timecode", before, code)],
        force_cmd=force_cmd(inv, "timeline", "timecode", "set", code, *context_words(inv)),
        snapshot="none",
    )
    read: dict[str, object] = {}

    def apply() -> None:
        with with_current_timeline(session, timeline) as switched:
            if not timeline.SetCurrentTimecode(code):
                raise api_failed("SetCurrentTimecode", f"could not move the playhead to {code}")
            read["current_timecode"] = timeline.GetCurrentTimecode()
        if switched:
            plan.extra["timeline_restored"] = True

    return run_mutation(inv, session, plan, apply, lambda: dict(read))


def run_timecode_start(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    code = timecode_arg(inv)
    session = inv.connect()
    timeline = target_timeline(session, inv)
    check_timecode_fields(code, timeline.GetSettings() or {})
    before = timeline.GetStartTimecode()
    plan = MutationPlan(
        command="timeline timecode start",
        target=f"timeline {timeline.GetName()}",
        changes=[] if before == code else [Change("start_timecode", before, code)],
        force_cmd=force_cmd(inv, "timeline", "timecode", "start", code, *context_words(inv)),
    )
    return finish(
        inv,
        session,
        plan,
        "SetStartTimecode",
        lambda: timeline.SetStartTimecode(code),
        lambda: {"start_timecode": timeline.GetStartTimecode()},
    )


SETTINGS_SPEC = CommandSpec(
    name="settings",
    summary="Change timeline settings",
    subcommands={
        "set": CommandSpec(
            name="set",
            summary="Set timeline settings from key=value pairs (needs useCustomSettings=1)",
            run=run_settings_set,
            positionals=(Positional("assignments", help="comma-separated key=value pairs"),),
            flags=(TIMELINE_FLAG, *MUTATION_FLAGS),
            examples=(
                "davinci-axi timeline settings set useCustomSettings=1 --force",
                "davinci-axi timeline settings set timelineFrameRate=25",
            ),
            reads_only=False,
            methods=("Timeline.SetSettings", "Timeline.GetSettings"),
        )
    },
    reads_only=False,
    methods=("Timeline.SetSettings",),
)

TIMECODE_SPEC = CommandSpec(
    name="timecode",
    summary="Move the playhead or change the start timecode",
    subcommands={
        "set": CommandSpec(
            name="set",
            summary="Move the playhead of a timeline to a timecode",
            run=run_timecode_set,
            positionals=(Positional("tc", help="timecode HH:MM:SS:FF"),),
            flags=(TIMELINE_FLAG, *MUTATION_FLAGS),
            examples=(
                "davinci-axi timeline timecode set 01:00:02:00",
                "davinci-axi timeline timecode set 01:00:02:00 --force",
            ),
            reads_only=False,
            methods=("Timeline.SetCurrentTimecode", "Timeline.GetCurrentTimecode"),
        ),
        "start": CommandSpec(
            name="start",
            summary="Change the start timecode of a timeline",
            run=run_timecode_start,
            positionals=(Positional("tc", help="timecode HH:MM:SS:FF"),),
            flags=(TIMELINE_FLAG, *MUTATION_FLAGS),
            examples=(
                "davinci-axi timeline timecode start 00:59:50:00",
                "davinci-axi timeline timecode start 01:00:00:00 --force",
            ),
            reads_only=False,
            methods=("Timeline.SetStartTimecode", "Timeline.GetStartTimecode"),
        ),
    },
    reads_only=False,
    methods=("Timeline.SetCurrentTimecode", "Timeline.SetStartTimecode"),
)

SUBCOMMANDS: dict[str, CommandSpec] = {
    "create": CommandSpec(
        name="create",
        summary="Create an empty timeline",
        run=run_create,
        positionals=(NAME_ARG,),
        flags=MUTATION_FLAGS,
        examples=(
            'davinci-axi timeline create "Edit v3"',
            'davinci-axi timeline create "Edit v3" --force',
        ),
        reads_only=False,
        methods=("MediaPool.CreateEmptyTimeline",),
    ),
    "duplicate": CommandSpec(
        name="duplicate",
        summary="Duplicate a timeline under a new name (the copy becomes current)",
        run=run_duplicate,
        positionals=(NAME_ARG,),
        flags=(TO_FLAG, *MUTATION_FLAGS),
        notes=(
            "--to <new name> is required: `timeline duplicate <name> <new>` is not accepted",
            "The duplicate becomes the current timeline; `timeline open <new>` after it is a no-op",
        ),
        examples=(
            'davinci-axi timeline duplicate "Edit v1" --to "Edit v1 backup"',
            'davinci-axi timeline duplicate "Edit v1" --to "Edit v1 backup" --force',
        ),
        reads_only=False,
        methods=("Timeline.DuplicateTimeline",),
    ),
    "rename": CommandSpec(
        name="rename",
        summary="Rename a timeline",
        run=run_rename,
        positionals=(NAME_ARG,),
        flags=(TO_FLAG, *MUTATION_FLAGS),
        examples=(
            'davinci-axi timeline rename "Edit v2" --to "Edit v3"',
            'davinci-axi timeline rename "Edit v2" --to "Edit v3" --force',
        ),
        reads_only=False,
        methods=("Timeline.SetName",),
    ),
    "delete": CommandSpec(
        name="delete",
        summary="Delete a timeline and its items (irreversible)",
        run=run_delete,
        positionals=(NAME_ARG,),
        flags=MUTATION_FLAGS,
        examples=(
            'davinci-axi timeline delete "Edit v1"',
            'davinci-axi timeline delete "Edit v1" --force',
        ),
        reads_only=False,
        methods=("MediaPool.DeleteTimelines",),
    ),
    "open": CommandSpec(
        name="open",
        summary="Make a timeline the current timeline",
        run=run_open,
        positionals=(NAME_ARG,),
        flags=MUTATION_FLAGS,
        examples=(
            'davinci-axi timeline open "Edit v2"',
            'davinci-axi timeline open "Edit v2" --force',
        ),
        reads_only=False,
        methods=("Project.SetCurrentTimeline",),
    ),
    "settings": SETTINGS_SPEC,
    "timecode": TIMECODE_SPEC,
    "track": TRACK_SPEC,
    "marker": MARKER_SPEC,
}


def _all_methods() -> tuple[str, ...]:
    found: dict[str, None] = {}
    stack = list(SUBCOMMANDS.values())
    while stack:
        spec = stack.pop(0)
        found.update(dict.fromkeys(spec.methods))
        stack.extend(spec.subcommands.values())
    return tuple(found)


SPEC = CommandSpec(
    name="timeline",
    summary="Create, rename, delete and open timelines; change tracks, markers, settings",
    subcommands=SUBCOMMANDS,
    examples=(
        'davinci-axi timeline rename "Edit v2" --to "Edit v3"',
        "davinci-axi timeline track add audio --force",
        "davinci-axi timeline marker add --frame 10 --color Green --name Pick --force",
    ),
    reads_only=False,
    methods=_all_methods(),
)
