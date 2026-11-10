"""Piece maths and the append loop of `timeline music add` (F50 d, F51 a).

A looped bed never ends in a stub: after the first piece, a remainder shorter than `stub_frames`
(the fade-out, or 30 frames) is left uncovered and reported as `tail_frames`, so the last piece is
long enough to carry the fade-out instead of being a blip of the song's start.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from davinci_axi.commands.timeline_extra_clips import PoolClips
from davinci_axi.errors import api_failed
from davinci_axi.resolve_types import Info, TimelineItemApi

MIN_TAIL_FRAMES = 30


def stub_frames(fade_out: int) -> int:
    """The shortest piece a looped bed may end on, in timeline frames."""
    return max(fade_out, MIN_TAIL_FRAMES)


def pieces(
    clip_frames: int, picture_frames: int, loop: bool, min_tail: int = 1
) -> list[tuple[int, int]]:
    """`(startFrame, endFrame)` per piece, in the clip's own frames; never a 0-length piece.

    Without `loop` one piece, the whole song or the part that fits. With `loop` the song repeats
    back to back until `picture_frames` are covered and the last piece is trimmed; a remainder
    under `min_tail` clip frames (after the first piece) is left uncovered.
    """
    if clip_frames < 1 or picture_frames < 1:
        return []
    if not loop:
        return [(0, min(clip_frames, picture_frames))]
    out: list[tuple[int, int]] = []
    left = picture_frames
    while left > 0:
        take = min(clip_frames, left)
        if out and take < min_tail:
            break
        out.append((0, take))
        left -= take
    return out


def next_piece(clip_frames: int, remaining_tl: int, ratio: float) -> tuple[int, int]:
    """`(0, endFrame)` for the piece placed at the current end: the song, or the part that fits."""
    return 0, min(clip_frames, max(1, round(remaining_tl / ratio)))


def holes_in(items: list[TimelineItemApi]) -> int:
    """Frames left uncovered between consecutive read-back pieces."""
    spans = sorted((int(i.GetStart()), int(i.GetEnd())) for i in items)
    return sum(max(0, nxt[0] - prev[1]) for prev, nxt in pairwise(spans))


def append_pieces(
    pool: PoolClips,
    clip: Any,
    track: int,
    span: tuple[int, int],
    song: tuple[int, float, bool],
    cap: int,
    stub: int,
) -> tuple[list[TimelineItemApi], int]:
    """Append the song piece by piece, each at the read-back end of the one before (no holes).

    Returns the pieces and the frames left before the picture end (`tail_frames`); once a piece is
    down, a remainder under `stub` frames is not appended.
    """
    (record, picture_end), (clip_frames, ratio, loop) = span, song
    base: Info = {"mediaPoolItem": clip, "trackIndex": track, "mediaType": 2}
    made: list[TimelineItemApi] = []
    last_end = record
    while picture_end - last_end > 0 and (loop or not made):
        if made and picture_end - last_end < stub:
            break
        if len(made) >= cap:
            raise api_failed(
                "MediaPool.AppendToTimeline",
                f"more pieces than planned ({cap}); stopped at {last_end}",
            )
        first, end = next_piece(clip_frames, picture_end - last_end, ratio)
        result = pool.AppendToTimeline(
            [{**base, "recordFrame": last_end, "startFrame": first, "endFrame": end}]
        )
        if not result:
            err = api_failed(
                "MediaPool.AppendToTimeline",
                f"Resolve appended nothing for piece {len(made) + 1} at {last_end}",
            )
            err.help.insert(
                0,
                f"Pieces already placed stay on A{track}; `snapshot restore <id>` brings the "
                "project back",
            )
            raise err
        made.extend(result)
        new_end = int(made[-1].GetEnd())
        if new_end <= last_end:
            raise api_failed("MediaPool.AppendToTimeline", f"the piece at {last_end} has no length")
        last_end = new_end
    return made, max(0, picture_end - last_end)
