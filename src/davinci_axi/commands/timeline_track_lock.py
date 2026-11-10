"""`timeline track enable|disable|lock|unlock` (U2), split out of `timeline_tracks.py`.

On Resolve 21.1.1 `GetIsTrackLocked` has been seen to read False right after `SetTrackLock`
returned True. The lock verbs therefore read back twice (one settle in between) before they
report VERIFY_FAILED, and that error names the quirk and the manual check.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import cast

from davinci_axi.errors import AxiError
from davinci_axi.mutate import MUTATION_FLAGS, Change, MutationPlan, check_snapshot_flags, force_cmd
from davinci_axi.spec import CommandSpec, Invocation, Positional, Result

SETTLE_SECONDS = 0.3

LOCK_NOTE = (
    "On Resolve 21.1.1 GetIsTrackLocked has read False right after SetTrackLock returned True; "
    "check the lock icon in the Edit page track header: if the track is locked, only the "
    "read-back is wrong (the write stood)"
)


def switch_spec(verb: str, field_name: str, value: bool, setter: str, getter: str) -> CommandSpec:
    """`enable|disable` (`SetTrackEnable`) and `lock|unlock` (`SetTrackLock`)."""
    from davinci_axi.commands import timeline_tracks as tracks  # circular at import time

    is_lock = setter == "SetTrackLock"

    def run(inv: Invocation) -> Result:
        check_snapshot_flags(inv)
        kind, index = tracks.track_kind(inv), tracks.track_index(inv)
        session = inv.connect()
        timeline = tracks.target_timeline(session, inv)
        tracks.existing_track(timeline, kind, index)
        read = cast(Callable[[str, int], bool], getattr(timeline, getter))
        write = cast(Callable[[str, int, bool], bool], getattr(timeline, setter))
        before = read(kind, index)
        plan = MutationPlan(
            command=f"timeline track {verb}",
            target=f"timeline {timeline.GetName()}, {kind} track {index}",
            changes=[] if before == value else [Change(field_name, before, value)],
            force_cmd=force_cmd(
                inv, "timeline", "track", verb, kind, str(index), *tracks.context_words(inv)
            ),
            next=tracks.timelines_hint(),
        )

        def verify() -> dict[str, object]:
            live = read(kind, index)
            if is_lock and live != value:
                time.sleep(SETTLE_SECONDS)
                live = read(kind, index)
                plan.extra["read_back_retried"] = True
            return {field_name: live}

        try:
            return tracks.finish(
                inv, session, plan, setter, lambda: write(kind, index, value), verify
            )
        except AxiError as err:
            if is_lock and err.code == "VERIFY_FAILED" and err.detail.get("field") == field_name:
                err.help.append(LOCK_NOTE)
            raise

    return CommandSpec(
        name=verb,
        summary=f"{verb.capitalize()} a track of a timeline",
        run=run,
        positionals=(
            Positional("type", help="track type: video, audio or subtitle"),
            Positional("n", help="1-based track number"),
        ),
        flags=(tracks.TIMELINE_FLAG, *MUTATION_FLAGS),
        examples=(
            f"davinci-axi timeline track {verb} video 2",
            f"davinci-axi timeline track {verb} audio 1 --force",
        ),
        notes=(LOCK_NOTE,) if is_lock else (),
        reads_only=False,
        methods=(f"Timeline.{setter}", f"Timeline.{getter}"),
    )
