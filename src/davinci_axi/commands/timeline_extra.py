"""`timeline` edit extras: append, build, compound, insert, import-into, mark in/out, blanking,
stereo, Dolby Vision, selection and custom marker data (U25, R30, R36).

`SPEC` wraps `timeline_io.SPEC` with `replace()` (KTD15); the registry points `timeline` here.
The verbs live in `timeline_extra_clips.py` and `timeline_extra_state.py`.
"""

from __future__ import annotations

from dataclasses import replace

from davinci_axi.commands import timeline, timeline_io
from davinci_axi.commands.diff_cmds import TIMELINE_DIFF_SPEC
from davinci_axi.commands.settings_show import TIMELINE_SETTINGS_SHOW_SPEC
from davinci_axi.commands.timeline_append import APPEND_SPEC
from davinci_axi.commands.timeline_batch import FADES_SPEC, TRANSITIONS_SPEC
from davinci_axi.commands.timeline_check import CHECK_SPEC
from davinci_axi.commands.timeline_clear import CLEAR_SPEC
from davinci_axi.commands.timeline_extra_clips import (
    BUILD_SPEC,
    COMPOUND_SPEC,
    IMPORT_INTO_SPEC,
    INSERT_SPEC,
)
from davinci_axi.commands.timeline_extra_state import (
    BLANKING_SPEC,
    CUSTOM_SPEC,
    DOLBY_SPEC,
    MARK_SPEC,
    SELECTION_SPEC,
    STEREO_SPEC,
)
from davinci_axi.commands.timeline_marker_edl import MARKER_ADD_EDL_SPEC
from davinci_axi.commands.timeline_marker_list import MARKER_LIST_SPEC
from davinci_axi.commands.timeline_music import MUSIC_SPEC
from davinci_axi.commands.timeline_reads import SHOW_SPEC
from davinci_axi.commands.timeline_stills import TIMELINE_STILLS_SPEC
from davinci_axi.commands.timeline_title import TITLE_SPEC
from davinci_axi.commands.timeline_tracks import MARKER_SPEC
from davinci_axi.spec import CommandSpec

WRAPPED_MARKER = replace(
    MARKER_SPEC,
    summary="Add, list or delete timeline markers, or read and write their custom data",
    subcommands={
        **MARKER_SPEC.subcommands,
        "add": MARKER_ADD_EDL_SPEC,
        "list": MARKER_LIST_SPEC,
        "custom": CUSTOM_SPEC,
    },
    methods=tuple(
        dict.fromkeys((*MARKER_SPEC.methods, *MARKER_LIST_SPEC.methods, *CUSTOM_SPEC.methods))
    ),
)
WRAPPED_SETTINGS = replace(
    timeline.SETTINGS_SPEC,
    summary="Show or change timeline settings",
    subcommands={**timeline.SETTINGS_SPEC.subcommands, "show": TIMELINE_SETTINGS_SHOW_SPEC},
    methods=tuple(
        dict.fromkeys((*timeline.SETTINGS_SPEC.methods, *TIMELINE_SETTINGS_SHOW_SPEC.methods))
    ),
)

NEW: dict[str, CommandSpec] = {
    "show": SHOW_SPEC,
    "append": APPEND_SPEC,
    "check": CHECK_SPEC,
    "diff": TIMELINE_DIFF_SPEC,
    "clear": CLEAR_SPEC,
    "stills": TIMELINE_STILLS_SPEC,
    "transitions": TRANSITIONS_SPEC,
    "fades": FADES_SPEC,
    "settings": WRAPPED_SETTINGS,
    "build": BUILD_SPEC,
    "compound": COMPOUND_SPEC,
    "insert": INSERT_SPEC,
    "title": TITLE_SPEC,
    "music": MUSIC_SPEC,
    "import-into": IMPORT_INTO_SPEC,
    "mark": MARK_SPEC,
    "blanking": BLANKING_SPEC,
    "stereo": STEREO_SPEC,
    "dolby-vision": DOLBY_SPEC,
    "selection": SELECTION_SPEC,
    "marker": WRAPPED_MARKER,
}


def _all_methods() -> tuple[str, ...]:
    found: dict[str, None] = dict.fromkeys(timeline_io.SPEC.methods)
    stack = list(NEW.values())
    while stack:
        spec = stack.pop(0)
        found.update(dict.fromkeys(spec.methods))
        stack.extend(spec.subcommands.values())
    return tuple(found)


SPEC = replace(
    timeline_io.SPEC,
    subcommands={**timeline_io.SPEC.subcommands, **NEW},
    examples=(
        *timeline_io.SPEC.examples,
        "davinci-axi timeline show",
        "davinci-axi timeline check --target 2m-5m",
        "davinci-axi timeline diff 'edit v1' probe-x",
        "davinci-axi timeline append A003_dusk --track 2 --force",
        "davinci-axi timeline mark set --in 10 --out 100 --force",
        "davinci-axi timeline title add 'The Indoor Garden' --at 01:00:32:00 --track 2 --force",
        "davinci-axi timeline music add song.wav --db -18 --loop --force",
    ),
    methods=_all_methods(),
)
