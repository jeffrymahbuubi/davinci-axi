"""The one place that spawns `ffprobe` and `ffmpeg` (U1; `render verify` imports it too).

Both tools are optional `PATH` programs, never Python dependencies. `FFPROBE` / `FFMPEG` in the
environment name a binary directly; otherwise `shutil.which` finds it. `require_tools` raises
`NOT_AVAILABLE` so a caller can check before it makes any Resolve call. Every subprocess goes
through `run`, so tests replace that one function.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from davinci_axi.errors import AxiError

ToolName = Literal["ffprobe", "ffmpeg"]
_ENV = {"ffprobe": "FFPROBE", "ffmpeg": "FFMPEG"}
_INF = ("-inf", "inf", "nan")
_NUMBER = r"(-?\d+(?:\.\d+)?|-?inf|nan)"


class ToolMissing(AxiError):
    """`NOT_AVAILABLE`: ffprobe or ffmpeg is not installed."""

    def __init__(self, name: str) -> None:
        super().__init__(
            "NOT_AVAILABLE",
            f"{name} not found on PATH",
            help=[
                "Install ffmpeg: brew install ffmpeg",
                f"Or set {_ENV.get(name, name.upper())} to the binary path",
            ],
        )


def tool_path(name: ToolName) -> str:
    """The binary for `name`: the `FFPROBE`/`FFMPEG` environment variable, else `PATH`."""
    chosen = os.environ.get(_ENV[name], "").strip()
    found = chosen or shutil.which(name)
    if not found:
        raise ToolMissing(name)
    return found


def require_tools(*names: str) -> None:
    """Raise `ToolMissing` unless every named tool resolves; call before connecting to Resolve."""
    for name in names:
        if name not in _ENV:
            raise ValueError(f"unknown tool {name}")
        tool_path(name)  # type: ignore[arg-type]


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    """The single `subprocess.run` call site (tests replace this function)."""
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _checked(tool: str, path: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
    done = run(command)
    if done.returncode != 0:
        lines = [ln.strip() for ln in (done.stderr or done.stdout or "").splitlines() if ln.strip()]
        raise AxiError(
            "API_FAILED",
            f"{tool} failed on {path.name}: {lines[0] if lines else f'exit {done.returncode}'}",
            help=[
                f"Run the command by hand to see the full error: {shlex.join(command)}",
                "Check the file plays in ffplay",
            ],
        )
    return done


# ---- parsing ----------------------------------------------------------------------------------


def fps_of(text: str) -> float | None:
    """`30000/1001` -> 29.97, `25` -> 25.0, `0/0` or junk -> None."""
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            if float(den) == 0:
                return None
            value = float(num) / float(den)
        else:
            value = float(text)
    except ValueError:
        return None
    return round(value, 2) if value > 0 else None


def _number(text: str) -> float | None:
    return None if text in _INF else float(text)


def parse_ebur128(stderr: str) -> tuple[float | None, float | None]:
    """`(integrated LUFS, true peak dBTP)` from the ebur128 summary; `-inf` and misses are None."""
    summary = stderr[stderr.rfind("Summary:") :] if "Summary:" in stderr else stderr
    loud = re.search(r"Integrated loudness:\s*\n?\s*I:\s*" + _NUMBER, summary)
    peak = re.search(r"True peak:\s*\n?\s*Peak:\s*" + _NUMBER, summary)
    return (
        _number(loud.group(1)) if loud else None,
        _number(peak.group(1)) if peak else None,
    )


def parse_silences(stderr: str) -> list[tuple[float, float]]:
    """`(start_s, end_s)` pairs from `silencedetect`.

    A silence that never ends is closed at the input `Duration:` when ffmpeg printed one,
    and dropped otherwise.
    """
    pairs: list[tuple[float, float]] = []
    start: float | None = None
    for line in stderr.splitlines():
        found_start = re.search(r"silence_start:\s*(-?\d+(?:\.\d+)?)", line)
        found_end = re.search(r"silence_end:\s*(-?\d+(?:\.\d+)?)", line)
        if found_start:
            start = max(0.0, float(found_start.group(1)))
        elif found_end and start is not None:
            pairs.append((start, float(found_end.group(1))))
            start = None
    if start is not None:
        total = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
        if total:
            end = int(total.group(1)) * 3600 + int(total.group(2)) * 60 + float(total.group(3))
            if end > start:
                pairs.append((start, end))
    return pairs


def parse_yavg(stdout: str) -> float | None:
    """The mean of every `lavfi.signalstats.YAVG=<n>` line, or None when there are none."""
    values = [
        float(m.group(1))
        for m in re.finditer(r"lavfi\.signalstats\.YAVG=(-?\d+(?:\.\d+)?)", stdout)
    ]
    return round(sum(values) / len(values), 2) if values else None


def parse_plane_avgs(stdout: str) -> dict[str, float | None]:
    """The mean of every `lavfi.signalstats.{Y,U,V}AVG=<n>` line, per letter (None when absent)."""
    found: dict[str, list[float]] = {"Y": [], "U": [], "V": []}
    for m in re.finditer(r"lavfi\.signalstats\.([YUV])AVG=(-?\d+(?:\.\d+)?)", stdout):
        found[m.group(1)].append(float(m.group(2)))
    return {k: round(sum(v) / len(v), 2) if v else None for k, v in found.items()}


# ---- ffprobe ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    duration_s: float | None
    size: int | None
    bit_rate_kbps: int | None
    video: dict[str, object] | None
    audio: dict[str, object] | None


def _float(value: object) -> float | None:
    try:
        return float(str(value))
    except ValueError:
        return None


def _int(value: object) -> int | None:
    number = _float(value)
    return int(number) if number is not None else None


def _kbps(value: object) -> int | None:
    bits = _int(value)
    return round(bits / 1000) if bits is not None else None


_SHOW = (
    "format=duration,size,bit_rate:"
    "stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,"
    "channels,bit_rate,color_space,color_range"
)


def probe(path: Path) -> Probe:
    """`ffprobe` the container and its first video and first audio stream."""
    command = [
        tool_path("ffprobe"),
        "-v",
        "error",
        "-show_entries",
        _SHOW,
        "-of",
        "json",
        str(path),
    ]
    done = _checked("ffprobe", path, command)
    try:
        data = json.loads(done.stdout or "{}")
    except json.JSONDecodeError as err:
        raise AxiError(
            "API_FAILED",
            f"ffprobe gave unreadable output for {path.name}",
            help=[
                "Check the file plays in ffplay",
                f"Run `ffprobe -i {path}` by hand to see what it reports",
            ],
        ) from err
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return Probe(
        duration_s=_float(fmt.get("duration")),
        size=_int(fmt.get("size")),
        bit_rate_kbps=_kbps(fmt.get("bit_rate")),
        video=None
        if video is None
        else {
            "codec": video.get("codec_name"),
            "width": _int(video.get("width")),
            "height": _int(video.get("height")),
            "fps": fps_of(str(video.get("r_frame_rate", ""))),
            "fps_avg": fps_of(str(video.get("avg_frame_rate", ""))),
            "frames": _int(video.get("nb_frames")),
            "kbps": _kbps(video.get("bit_rate")),
            "color_space": video.get("color_space"),
            "color_range": video.get("color_range"),
        },
        audio=None
        if audio is None
        else {
            "codec": audio.get("codec_name"),
            "channels": _int(audio.get("channels")),
            "kbps": _kbps(audio.get("bit_rate")),
        },
    )


# ffprobe `color_space` -> the word `scale=in_color_matrix=` takes.
MATRICES: dict[str, str] = {
    "bt709": "bt709", "bt470bg": "bt601", "smpte170m": "bt601", "bt601": "bt601",
    "bt470": "bt601", "fcc": "fcc", "smpte240m": "smpte240m",
    "bt2020nc": "bt2020", "bt2020c": "bt2020", "bt2020": "bt2020",
}  # fmt: skip
HD_HEIGHT = 720


@dataclass(frozen=True)
class Colour:
    matrix: str  # a scale in_color_matrix word
    range: str  # "tv" | "pc"
    assumed: bool  # True when matrix or range came from the default

    def label(self) -> str:
        return f"{self.matrix} {self.range}" + (" (assumed)" if self.assumed else "")


def colour_of(space: object, range_: object, height: object) -> Colour:
    """The stream's YUV-to-RGB matrix and range, defaulting what ffprobe leaves untagged.

    A known `space` maps through `MATRICES`; otherwise bt709 from `HD_HEIGHT` up (an unknown
    height counts as HD) and bt601 below. A `range_` of "tv" or "pc" is kept, else "tv".
    `assumed` is True when either half was defaulted. Never raises.
    """
    matrix = MATRICES.get(str(space).strip().lower())
    if matrix is None:
        try:
            tall = _int(height)
        except OverflowError:  # an "inf" height
            tall = None
        matrix = "bt601" if tall is not None and tall < HD_HEIGHT else "bt709"
    span = str(range_).strip().lower()
    known = span in ("tv", "pc")
    return Colour(matrix, span if known else "tv", assumed=space_missing(space) or not known)


def space_missing(space: object) -> bool:
    return str(space).strip().lower() not in MATRICES


# ffmpeg `scale` matrix word -> (Kr, Kb). Rec. 709, 601, FCC, SMPTE 240M, Rec. 2020.
KRKB: dict[str, tuple[float, float]] = {
    "bt709": (0.2126, 0.0722), "bt601": (0.299, 0.114), "fcc": (0.30, 0.11),
    "smpte240m": (0.212, 0.087), "bt2020": (0.2627, 0.0593),
}  # fmt: skip


def yuv_to_rgb(y: float, u: float, v: float, colour: Colour) -> tuple[float, float, float]:
    """R, G, B (0-255, two decimals, clamped) of one 8-bit Y, U, V triple under `colour`.

    tv range scales Y by 255/219 from 16 and U/V by 255/224 from 128; pc subtracts 128 only.
    R = Y' + 2(1-Kr)V', B = Y' + 2(1-Kb)U', G = Y' - (2Kb(1-Kb)U' + 2Kr(1-Kr)V') / Kg.
    An unknown matrix word uses bt709.
    """
    kr, kb = KRKB.get(colour.matrix, KRKB["bt709"])
    kg = 1.0 - kr - kb
    if colour.range == "pc":
        luma, blue, red = y, u - 128.0, v - 128.0
    else:
        luma, blue, red = (y - 16.0) * 255 / 219, (u - 128.0) * 255 / 224, (v - 128.0) * 255 / 224
    r = luma + 2 * (1 - kr) * red
    b = luma + 2 * (1 - kb) * blue
    g = luma - (2 * kb * (1 - kb) * blue + 2 * kr * (1 - kr) * red) / kg
    return _clamp(r), _clamp(g), _clamp(b)


def _clamp(value: float) -> float:
    return round(min(255.0, max(0.0, value)), 2)


# ---- ffmpeg passes ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Loudness:
    lufs: float | None
    true_peak: float | None
    silences: list[tuple[float, float]]


def loudness(
    path: Path,
    *,
    silence_db: float | None,
    silence_s: float,
    start_s: float | None = None,
    duration_s: float | None = None,
) -> Loudness:
    """Integrated loudness, true peak and (unless `silence_db` is None) silences of the audio.

    With `start_s` and a positive `duration_s`, only that window of the file is measured
    (`-ss`/`-t` before `-i`, so silence times count from the window start).
    """
    chain = "ebur128=peak=true"
    if silence_db is not None:
        chain += f",silencedetect=noise={silence_db:g}dB:d={silence_s:g}"
    window: list[str] = []
    if start_s is not None and duration_s is not None and duration_s > 0:
        window = ["-ss", f"{start_s:.3f}", "-t", f"{duration_s:.3f}"]
    command = [
        tool_path("ffmpeg"), "-hide_banner", "-nostats", "-vn", *window, "-i", str(path),
        "-af", chain, "-f", "null", "-",
    ]  # fmt: skip
    done = _checked("ffmpeg", path, command)
    lufs, peak = parse_ebur128(done.stderr or "")
    silences = parse_silences(done.stderr or "") if silence_db is not None else []
    return Loudness(lufs, peak, silences)


def _fps_prefix(sample_fps: float) -> str:
    return "" if sample_fps <= 0 else f"fps={sample_fps:g},"


def luma_mean(path: Path, *, sample_fps: float = 0.0) -> float | None:
    """Mean luma (0-255) over every frame, or over `sample_fps` frames per second when above 0."""
    command = [
        tool_path("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), "-an",
        "-vf",
        f"{_fps_prefix(sample_fps)}signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
        "-f", "null", "-",
    ]  # fmt: skip
    return parse_yavg(_checked("ffmpeg", path, command).stdout or "")


def rgb_means(
    path: Path, *, sample_fps: float = 0.0, colour: Colour | None = None
) -> tuple[float | None, float | None, float | None]:
    """R, G and B (0-255) of the clip's mean colour, from exact mean Y, U and V.

    `signalstats` measures Y, U and V over every frame (or `sample_fps` frames per second when
    above 0) on an 8-bit 4:4:4 picture, which also normalises 10-bit sources; the means are then
    converted with the stream's matrix and range in `yuv_to_rgb`. The explicit no-op `scale` pins
    the matrix and range so an RGB-coded source converts with the same matrix. Differs from the
    mean of per-pixel RGB only by clipping (under 1 on saturated test bars). `colour` None means
    bt709 tv; an untagged HD stream defaults to bt709 in `colour_of`.
    """
    colour = colour or Colour("bt709", "tv", True)
    pin = (
        f"scale=in_color_matrix={colour.matrix}:in_range={colour.range}:"
        f"out_color_matrix={colour.matrix}:out_range={colour.range}"
    )
    command = [
        tool_path("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), "-an",
        "-vf",
        f"{_fps_prefix(sample_fps)}{pin},format=yuv444p,signalstats,metadata=print:file=-",
        "-f", "null", "-",
    ]  # fmt: skip
    means = parse_plane_avgs(_checked("ffmpeg", path, command).stdout or "")
    y, u, v = means["Y"], means["U"], means["V"]
    if y is None or u is None or v is None:
        return None, None, None
    return yuv_to_rgb(y, u, v, colour)
