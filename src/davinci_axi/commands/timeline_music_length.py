"""The length of a clip in its own frames, for `timeline music add` (F50 c).

Resolve reports `Frames` for a video clip but leaves it empty for an audio-only clip (a WAV); the
clip's `Duration` timecode and `FPS` still give its length.
"""

from __future__ import annotations

import re
from typing import Any

from davinci_axi.commands.ffmpeg_common import fps_of
from davinci_axi.errors import AxiError

_TC = re.compile(r"^(\d+)[:;](\d+)[:;](\d+)[:;](\d+)$")


def timecode_frames(tc: str, fps: float) -> int | None:
    """`HH:MM:SS:FF` (or `;` separators) as a frame count at `round(fps)`; None when unreadable."""
    base = round(fps)
    match = _TC.match(tc.strip())
    if match is None or base < 1:
        return None
    hours, minutes, seconds, frames = (int(g) for g in match.groups())
    return (hours * 3600 + minutes * 60 + seconds) * base + frames


def clip_length(clip: Any) -> tuple[int, str]:
    """`(frames, source)`: `Frames` when it is a number, else `Duration` x `FPS`."""
    frames = str(clip.GetClipProperty("Frames") or "").strip()
    if frames.isdigit() and int(frames) >= 1:
        return int(frames), "Frames"
    duration = str(clip.GetClipProperty("Duration") or "").strip()
    fps_text = str(clip.GetClipProperty("FPS") or "").strip()
    fps = fps_of(fps_text)
    counted = timecode_frames(duration, fps) if fps is not None else None
    if counted is not None and counted >= 1:
        return counted, "Duration x FPS"
    raise AxiError(
        "VALIDATION_ERROR",
        f"cannot read the length of {clip.GetName()} (Frames {frames or 'is empty'}, "
        f"Duration {duration or 'empty'}, FPS {fps_text or 'empty'})",
        help=[
            "Check `davinci-axi media show <clip>`; an audio clip needs Duration and FPS, "
            "a video clip Frames",
            "The clip may be offline: relink it in Resolve",
        ],
    )
