"""Help notes that the README trim relies on (U1, U3): each fact lives in `--help`."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from davinci_axi.commands.media_list import EXTRA_FIELDS

Run = Callable[[list[str]], tuple[int, str, str]]

STALE_MARKS = "Timeline marks left by an earlier `render add --set MarkIn"


@pytest.mark.parametrize(
    ("path", "phrase"),
    [
        (["media"], "--fields also takes"),
        (["media", "property", "set"], "Numbers are sent typed (int or float)"),
        (["storage", "clone", "start"], "No snapshot: the clone writes outside the project"),
        (["item"], "A transition item (item <id> on a dissolve) shows the clips it joins"),
        (["fusion", "insert", "title"], STALE_MARKS),
        (["fusion", "insert"], STALE_MARKS),
        (["timeline", "insert", "title"], STALE_MARKS),
        (["timeline", "insert"], STALE_MARKS),
        (["snapshot", "diff"], "Markers and tracks are compared"),
        (["render", "deliver"], "overshoot"),
        (["render", "verify"], "Exit 1 when any check fails"),
        (["render", "start"], "--verify runs `render verify --job`"),
        (["render", "master"], "video stream is copied untouched"),
        (["render", "sheet"], "every --every seconds"),
        (["audio", "loudness"], "source range plus the clip volume"),
        (["audio", "gaps"], "marker rows"),
        (["timeline", "title", "add"], "Style: --size is the Text+ glyph scale"),
        (["timeline", "music", "add"], "The last piece is never shorter than --fade-out"),
        (["timeline", "marker", "add"], "custom (or custom_data)"),
        (["timeline", "marker", "list"], "custom is shown by default"),
        (["timeline", "check"], "Render cost is an info row"),
        (["item", "set"], "--edl"),
        (["media", "property", "set"], "--save-restore"),
        (["media", "analyze"], "--vfr-scan"),
        (["media", "analyze"], "the RGB of the mean colour"),
        (["media", "analyze"], "mean colour, unclipped"),
        (["color", "cdl", "set"], "--edl"),
    ],
)
def test_help_carries_the_note(run_cli: Run, path: list[str], phrase: str) -> None:
    code, out, err = run_cli([*path, "--help"])
    assert (code, err) == (0, "")
    assert phrase in out


def test_media_help_lists_every_extra_field(run_cli: Run) -> None:
    code, out, err = run_cli(["media", "--help"])
    assert (code, err) == (0, "")
    assert ", ".join(EXTRA_FIELDS) in out
    for name in ("resolution", "video_codec", "frames", "date_created", "audio_ch"):
        assert name in EXTRA_FIELDS
        assert name in out
