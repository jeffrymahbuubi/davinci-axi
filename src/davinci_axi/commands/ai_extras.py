"""`ai extras`: which AI Extras packages are installed (F11). A filesystem check, no Resolve call.

Read-only and always exit 0, even when the Extras directory cannot be read (status `unknown`).
"""

from __future__ import annotations

from davinci_axi import extras
from davinci_axi.output import collapse_home
from davinci_axi.spec import CommandSpec, Invocation, Result

# (command, package, optional): an `optional` row still works without its package (built-in
# languages are the fallback), so a missing one is shown as `optional`, not `missing`.
FEATURES: tuple[tuple[str, str, bool], ...] = (
    ("ai intellisearch analyze", "AI IntelliSearch - Faster", False),
    ("ai intellisearch analyze --better", "AI IntelliSearch - Better", False),
    ("ai slate", "AI Slate ID", False),
    ("ai deblur", "AI Motion Deblur", False),
    ("audio speech", "AI Speech Generator", False),
    ("audio transcribe", "Extended AI Transcription Language Support 2", True),
    ("ai subtitles", "Extended AI Transcription Language Support 2", True),
)


def _directory_state(report: extras.Report) -> str:
    if report.readable:
        return "readable"
    return "unreadable" if report.dir is not None and report.dir.exists() else "not found"


def _feature_status(report: extras.Report, package: str, optional: bool) -> str:
    status = report.status(package)
    return "optional" if status == "missing" and optional else status


def run_extras(inv: Invocation) -> Result:
    report = extras.scan()
    names = report.names()
    data: dict[str, object] = {
        "dir": collapse_home(report.dir) if report.dir is not None else None,
        "status": _directory_state(report),
        "installed": len(names),
    }
    if report.unnamed():
        data["unnamed"] = report.unnamed()
    rows: list[dict[str, object]] = [
        {"name": name, "installed": name in names} for name in extras.PACKAGES
    ]
    if inv.opts.json:
        rows += [
            {"name": name, "installed": True, "known": False}
            for name in names
            if name not in extras.PACKAGES
        ]
    data["packages"] = rows
    data["features"] = [
        {"command": cmd, "package": pkg, "status": _feature_status(report, pkg, optional)}
        for cmd, pkg, optional in FEATURES
    ]
    help_lines = [f"Open {extras.INSTALL_MENU} to install a missing package"]
    if not report.readable:
        help_lines.append(
            "Set DAVINCI_AXI_EXTRAS_DIR to the Extras directory if Resolve keeps it elsewhere"
        )
    return Result(data, help_lines)


EXTRAS_SPEC = CommandSpec(
    name="extras",
    summary="Show which AI Extras packages are installed (filesystem check, no Resolve call)",
    run=run_extras,
    examples=("davinci-axi ai extras", "davinci-axi ai extras --json"),
    methods=(),
)
