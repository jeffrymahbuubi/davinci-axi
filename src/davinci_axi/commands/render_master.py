"""`render master`: bring a rendered file to a loudness target with a two-pass ffmpeg loudnorm.

A pure file verb (no Resolve call unless `--job` finds the file): pass 1 measures the file, pass 2
applies the measured values linearly, copies the video and re-encodes the audio to AAC, then the
output is measured again exactly as `render verify` does. The input is never rewritten.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from davinci_axi.commands import ffmpeg_common
from davinci_axi.commands.render_verify import (
    DEFAULT_TOLERANCE,
    VERIFY_SPEC,
    checks_for,
    job_target,
    measure_file,
)
from davinci_axi.errors import AxiError, not_found
from davinci_axi.mutate import refuse_existing
from davinci_axi.output import collapse_home, hint
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

DEFAULT_TP, DEFAULT_LRA = -1.5, 11.0
AAC_TP_MARGIN, KBPS_FLOOR, KBPS_CEILING, DEFAULT_KBPS = 0.3, 128, 320, 256
FASTSTART_SUFFIXES = (".mp4", ".mov", ".m4a")
SAMPLE_RATE = 48000


@dataclass(frozen=True)
class Caps:
    """The loudnorm targets: integrated LUFS, true-peak ceiling (dBTP) and loudness range."""

    lufs: float
    true_peak: float = DEFAULT_TP
    lra: float = DEFAULT_LRA


@dataclass(frozen=True)
class Pass1:
    """What pass 1 measured on the input (`loudnorm` JSON keys `input_*` and `target_offset`)."""

    lufs: float
    true_peak: float
    lra: float
    thresh: float
    offset: float
    normalization_type: str = "dynamic"


def _bad(message: str, help_line: str) -> AxiError:
    return AxiError("VALIDATION_ERROR", message, help=[help_line])


def default_kbps(source_kbps: int | None) -> int:
    """The source's audio bit rate clamped to 128-320 kbps, or 256 when it is unknown."""
    if source_kbps is None:
        return DEFAULT_KBPS
    return max(KBPS_FLOOR, min(KBPS_CEILING, source_kbps))


def _sent_peak(target: Caps) -> float:
    return round(target.true_peak - AAC_TP_MARGIN, 2)


def _target_chain(target: Caps) -> str:
    """loudnorm asked for a true peak AAC_TP_MARGIN under the ceiling: encoding adds overshoot."""
    peak = _sent_peak(target)
    return f"loudnorm=I={target.lufs:g}:TP={peak:g}:LRA={target.lra:g}"


def _last_block(text: str) -> str | None:
    """The last balanced `{...}` block of `text` (loudnorm prints its JSON last), or None."""
    end = text.rfind("}")
    depth = 0
    for index in range(end, -1, -1):
        if text[index] == "}":
            depth += 1
        elif text[index] == "{":
            depth -= 1
            if depth == 0:
                return text[index : end + 1]
    return None


def parse_loudnorm(stderr: str) -> Pass1:
    """The measured values from pass 1's stderr; an unreadable or silent result is API_FAILED."""
    block = _last_block(stderr)
    try:
        data = json.loads(block) if block else {}
        values = [
            float(data[key])
            for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
        ]
    except (ValueError, KeyError, TypeError) as err:
        raise AxiError(
            "API_FAILED",
            "could not read the loudnorm measurement from ffmpeg pass 1",
            help=["Run pass 1 by hand: ffmpeg -i <file> -af loudnorm=print_format=json -f null -"],
        ) from err
    if any(v != v or abs(v) == float("inf") for v in values):  # nan or +-inf
        raise AxiError(
            "API_FAILED",
            "the file is silent or unreadable: loudnorm measured no loudness",
            help=["Pick a file with sound; `render verify <file> --lufs -14` shows its loudness"],
        )
    lufs, peak, lra, thresh, offset = values
    return Pass1(
        lufs,
        peak,
        lra,
        thresh,
        offset,
        normalization_type=str(data.get("normalization_type") or "dynamic"),
    )


def default_out(path: Path) -> Path:
    """`<stem>-master<suffix>` beside the input."""
    return path.with_name(f"{path.stem}-master{path.suffix}")


def pass1_command(path: Path, target: Caps) -> list[str]:
    return [
        ffmpeg_common.tool_path("ffmpeg"), "-hide_banner", "-nostats", "-vn", "-i", str(path),
        "-af", f"{_target_chain(target)}:print_format=json", "-f", "null", "-",
    ]  # fmt: skip


def pass2_command(path: Path, out: Path, target: Caps, p1: Pass1, kbps: int) -> list[str]:
    measured = (
        f"measured_I={p1.lufs:g}:measured_TP={p1.true_peak:g}:measured_LRA={p1.lra:g}"
        f":measured_thresh={p1.thresh:g}:offset={p1.offset:g}:linear=true:print_format=summary"
    )
    command = [
        ffmpeg_common.tool_path("ffmpeg"), "-hide_banner", "-nostats", "-y", "-i", str(path),
        "-map", "0:v?", "-map", "0:a", "-c:v", "copy", "-af", f"{_target_chain(target)}:{measured}",
        "-c:a", "aac", "-b:a", f"{kbps}k", "-ar", str(SAMPLE_RATE),
    ]  # fmt: skip
    if out.suffix.lower() in FASTSTART_SUFFIXES:
        command += ["-movflags", "+faststart"]
    return [*command, str(out)]


def _range(name: str, value: float, low: float, high: float, example: str) -> None:
    if not low <= value <= high:
        raise _bad(f"--{name} must be {low:g} to {high:g}, got {value:g}", f"Valid: {example}")


def _targets(inv: Invocation) -> tuple[Caps, float, int | None]:
    """The validated `(Caps, lufs tolerance, audio kbps or None)`; exit 2 before any subprocess."""
    args = inv.args
    lufs = args.get("lufs")
    if not isinstance(lufs, (int, float)) or isinstance(lufs, bool):
        raise _bad("--lufs is required", "davinci-axi render master ~/out/edit.mp4 --lufs -14")
    _range("lufs", float(lufs), -60, 0, "--lufs -14")
    peak = cast(float, args.get("true_peak", DEFAULT_TP))
    if peak > 0:
        raise _bad(f"--true-peak must be 0 or below, got {peak:g}", "Valid: --true-peak -1.5")
    lra = cast(float, args.get("lra", DEFAULT_LRA))
    _range("lra", lra, 1, 50, "--lra 11")
    tolerance = cast(float, args.get("lufs_tolerance", DEFAULT_TOLERANCE))
    if tolerance < 0:
        raise _bad("--lufs-tolerance must be 0 or above", "Valid: --lufs-tolerance 1")
    kbps = cast("int | None", args.get("audio_kbps"))
    if kbps is not None:
        _range("audio-kbps", kbps, 32, 512, "--audio-kbps 256")
    return Caps(float(lufs), peak, lra), tolerance, kbps


def _failed(command: list[str], name: str, label: str, stderr: str) -> AxiError:
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    return AxiError(
        "API_FAILED",
        f"ffmpeg {label} failed on {name}: {lines[0] if lines else 'no output'}",
        help=[f"Run the command by hand: {shlex.join(command)}"],
    )


def _input(inv: Invocation, given: object, job: str | None) -> Path:
    path = job_target(inv.connect(), job)[0] if job else Path(str(given)).expanduser().resolve()
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path; for a job use --job <id>")
    return path


def run_master(inv: Invocation) -> Result:
    target, tolerance, kbps_arg = _targets(inv)
    given = inv.positionals.get("file")
    job = str(inv.args["job"]) if inv.args.get("job") else None
    if given and job:  # before any tool or Resolve call
        raise _bad(
            "give a file or --job, not both", "davinci-axi render master --job <id> --lufs -14"
        )
    if not given and not job:
        raise _bad("name a file or --job", "davinci-axi render master ~/out/edit.mp4 --lufs -14")
    chosen = inv.args.get("out")
    out_arg = Path(str(chosen)).expanduser().resolve() if chosen else None
    if given and out_arg is not None and out_arg == Path(str(given)).expanduser().resolve():
        raise _bad("--out is the input file", "Pick another path; the input is never rewritten")
    ffmpeg_common.require_tools("ffmpeg", "ffprobe")
    path = _input(inv, given, job)
    out = out_arg or default_out(path)
    if out == path:
        raise _bad("--out is the input file", "Pick another path; the input is never rewritten")
    refuse_existing(out, bool(inv.args.get("overwrite")))
    audio = ffmpeg_common.probe(path).audio
    if audio is None:
        raise _bad(
            "no audio stream to master",
            "Pick a file with audio; `render verify <file>` lists the streams",
        )
    source_kbps = cast("int | None", audio.get("kbps"))
    kbps = kbps_arg or default_kbps(source_kbps)
    kbps_from = "flag" if kbps_arg else "source" if source_kbps is not None else "default"
    first = pass1_command(path, target)
    done = ffmpeg_common.run(first)
    if done.returncode != 0:
        raise _failed(first, path.name, "pass 1 (measure)", done.stderr or done.stdout or "")
    p1 = parse_loudnorm(done.stderr or "")
    second = pass2_command(path, out, target, p1, kbps)
    done = ffmpeg_common.run(second)
    if done.returncode != 0:
        out.unlink(missing_ok=True)
        raise _failed(second, path.name, "pass 2 (apply)", done.stderr or done.stdout or "")
    after = measure_file(out, loud=True)
    caps: dict[str, float | None] = {
        "lufs": target.lufs,
        "lufs_tolerance": tolerance,
        "true_peak": target.true_peak,
        "max_mb": None,
        "expected_s": None,
    }
    checks = checks_for(after, caps)
    ok = all(bool(row["ok"]) for row in checks)
    heard = f"source {source_kbps} kbps" if source_kbps is not None else "source bit rate unknown"
    mode = "linear" if p1.normalization_type == "linear" else "dynamic"
    data: dict[str, object] = {
        "file": collapse_home(path),
        "out": collapse_home(out),
        "before": {"lufs": p1.lufs, "true_peak": p1.true_peak, "lra": p1.lra},
        "after": {"lufs": after.get("lufs"), "true_peak": after.get("true_peak")},
        "bytes": after["bytes"],
        "mb": after["mb"],
        "duration_s": after["duration_s"],
        "video": "copied",
        "audio": f"aac {kbps} kbps 48 kHz ({heard})",
        "audio_kbps_from": kbps_from,
        "loudnorm": {"I": target.lufs, "TP": _sent_peak(target), "LRA": target.lra},
        "mode": mode,
        "checks": checks,
        "ok": ok,
    }
    return Result(data, _help(data, target, collapse_home(path)), exit_code=0 if ok else 1)


def _help(data: dict[str, object], target: Caps, file: str) -> list[str]:
    lines = [
        hint(f"render verify {data['out']} --max-mb <mb>", "check the size cap too"),
    ]
    if data["mode"] == "dynamic":
        lines.append(
            hint(
                f"render master {file} --lufs {target.lufs:g} --lra {target.lra + 4:g}",
                "stay linear: a wider LRA lets loudnorm keep the dynamics",
            )
        )
    if not data["ok"]:
        lines.append(
            hint(
                f"render master {file} --lufs {target.lufs:g} "
                f"--true-peak {target.true_peak - 0.5:g}",
                "lower the ceiling and run again",
            )
        )
    return lines


MASTER_SPEC = CommandSpec(
    name="master",
    summary="Bring a rendered file to a loudness target (two-pass ffmpeg loudnorm, video copied)",
    run=run_master,
    positionals=(Positional("file", required=False, help="a rendered media file"),),
    flags=(
        Flag("job", "str", None, help="master the output of this render job instead of a file"),
        Flag("lufs", "float", None, required=True, help="target integrated loudness in LUFS"),
        Flag("true-peak", "float", DEFAULT_TP, help="true-peak ceiling in dBTP (0 or below)"),
        Flag("lra", "float", DEFAULT_LRA, help="target loudness range in LU (1 to 50)"),
        Flag(
            "lufs-tolerance", "float", DEFAULT_TOLERANCE, help="allowed LUFS distance (0 or more)"
        ),
        Flag(
            "audio-kbps",
            "int",
            None,
            help="AAC bit rate in kbps, 32 to 512 (default: the source's audio bit rate, "
            "128 to 320; 256 when unknown)",
        ),
        Flag("out", "str", None, help="output file (default: <name>-master beside the input)"),
        Flag("overwrite", "bool", False, help="replace the output file if it exists"),
    ),
    examples=(
        "davinci-axi render master ~/out/edit.mp4 --lufs -14",
        "davinci-axi render master --job job-1 --lufs -14 --out ~/out/edit-master.mp4",
        "davinci-axi render master ~/out/edit.mp4 --lufs -16 --overwrite",
    ),
    reads_only=False,
    methods=VERIFY_SPEC.methods,
    notes=(
        "Writes a new file and never touches the input; there is no --force. --job is the only "
        "Resolve call: it finds the job's output file",
        "The video stream is copied untouched; the audio is re-encoded to AAC (loudnorm changes "
        "the samples, so it cannot be copied) at --audio-kbps and 48 kHz (-ar 48000)",
        "Sizes are decimal MB (1,000,000 bytes), the unit render deliver --max-mb uses",
        "Pass 1 measures; pass 2 applies the measured values. mode is linear when loudnorm could "
        "apply one gain, else dynamic (it may squash dynamics: widen --lra to stay linear)",
        "loudnorm is asked for a true peak 0.3 dB under --true-peak because AAC encoding adds up "
        "to ~0.2 dB of overshoot; `loudnorm.TP` shows the value sent, `checks` test the ceiling "
        "you asked for",
        "The output is measured again like render verify; exit 1 when it misses --lufs "
        "(+/- --lufs-tolerance) or --true-peak, exit 0 when both hold",
        "ffmpeg and ffprobe are optional programs on PATH; without them this verb fails with "
        "NOT_AVAILABLE before it does anything else",
    ),
)
