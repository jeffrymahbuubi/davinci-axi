"""The H.264/H.265 encoder floor and the size overshoot near it (F43, F45), for `render deliver`.

Resolve's encoder holds a rate of about `900 + 900 kbps per megapixel` however low `VideoQuality`
goes, and renders overshoot `VideoQuality` by a margin that grows as the rate nears that floor.
Both fits come from live renders.
"""

from __future__ import annotations

import math

FLOOR_BASE_KBPS = 900  # Resolve's H.264 encoder rate is ~ base + slope * megapixels, fitted live
FLOOR_KBPS_PER_MPIXEL = 900
FLOOR_MARGIN_PCT = 10.0  # safety kept over the estimate when comparing and when suggesting
DRIFT_NEAR_FLOOR_PCT = 30.0  # overshoot at (or under) the floor
DRIFT_FAR_PCT = 2.0  # overshoot at R_FAR times the floor and beyond
R_FAR = 2.5  # rate / floor at which the overshoot has fallen to DRIFT_FAR_PCT


def encoder_floor_kbps(width: int, height: int) -> int:
    """The bit rate Resolve's H.264/H.265 encoder stayed near at `width`x`height` (0 if unknown)."""
    if width <= 0 or height <= 0:
        return 0
    return round(FLOOR_BASE_KBPS + width * height / 1e6 * FLOOR_KBPS_PER_MPIXEL)


def required_kbps(width: int, height: int) -> int:
    """The video rate deliver needs at this size: the floor estimate plus the safety margin."""
    floor = encoder_floor_kbps(width, height)
    return math.ceil(floor * (1 + FLOOR_MARGIN_PCT / 100)) if floor else 0


def drift_pct(kbps: int, floor: int) -> float:
    """How far (percent) a render overshoots `VideoQuality=kbps` when the encoder floor is `floor`.

    `DRIFT_NEAR_FLOOR_PCT` at or under the floor, falling linearly to `DRIFT_FAR_PCT` at `R_FAR`
    times the floor. An unknown floor (0) gives `DRIFT_FAR_PCT`."""
    if floor <= 0:
        return DRIFT_FAR_PCT
    ratio = kbps / floor
    if ratio <= 1:
        return DRIFT_NEAR_FLOOR_PCT
    if ratio >= R_FAR:
        return DRIFT_FAR_PCT
    span = DRIFT_NEAR_FLOOR_PCT - DRIFT_FAR_PCT
    return DRIFT_FAR_PCT + span * (R_FAR - ratio) / (R_FAR - 1)


def predicted_kbps(kbps: int, floor: int) -> int:
    """The video rate a render of `VideoQuality=kbps` is expected to come out at."""
    return round(kbps * (1 + drift_pct(kbps, floor) / 100))
