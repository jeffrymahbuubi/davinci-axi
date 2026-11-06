"""`dctl validate|encrypt`: check and encrypt DCTL colour transforms (U22, R35).

`validate` calls `Resolve.ValidateDCTL` (None means valid). `encrypt` writes an encrypted copy
with `Resolve.EncryptDCTL`. Neither changes the project, so neither takes `--force` or a snapshot;
`encrypt` writes a file and refuses to overwrite without `--overwrite`, like every disk-only export.
"""

from __future__ import annotations

import re
import shlex
import time
from datetime import date
from pathlib import Path
from typing import Any, Protocol, cast

from davinci_axi.commands.project_io import clean_name, text_arg
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import refuse_existing
from davinci_axi.output import collapse_home, hint
from davinci_axi.resolve_types import ResolveApp
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

# ASSUMED (live check): Resolve writes `<Name or input stem>.dctle` into OutputFolder.
ENCRYPTED_SUFFIX = ".dctle"
_ISO = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
)


class DctlResolve(ResolveApp, Protocol):
    def ValidateDCTL(self, dctlSource: str) -> str | None: ...
    def EncryptDCTL(
        self, inputPath: str, encryptDCTLOptions: dict[str, Any] | None = None
    ) -> bool: ...


def _file(inv: Invocation) -> Path:
    path = Path(inv.positionals["file"]).expanduser().resolve()
    if not path.is_file():
        raise not_found("file", collapse_home(path), "Check the path of the .dctl file")
    return path


def run_validate(inv: Invocation) -> Result:
    path = _file(inv)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot read {path.name} as UTF-8 text: {exc}",
            help=["Pass a plain-text .dctl source file"],
        ) from None
    resolve = cast(DctlResolve, inv.connect().resolve)
    message = resolve.ValidateDCTL(source)
    if message:
        raise AxiError(
            "DCTL_INVALID",
            f"{path.name} did not validate",
            help=["Fix the source, then run the same command again"],
            detail={"file": collapse_home(path), "message": message},
        )
    data: dict[str, object] = {"file": collapse_home(path), "valid": True, "bytes": len(source)}
    return Result(
        data,
        [hint(f"dctl encrypt {shlex.quote(str(path))}", "write an encrypted copy of this file")],
    )


def _expiry(inv: Invocation) -> str | None:
    raw = text_arg(inv, "expiry")
    if raw is None:
        return None
    match = _ISO.fullmatch(raw)
    valid = match is not None
    if match:
        try:
            date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            valid = False
    if not valid:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--expiry {raw} is not an ISO 8601 date or date-time",
            help=["Valid examples: --expiry 2027-12-31, --expiry 2027-12-31T23:59:59Z"],
        )
    return raw


def _output_name(inv: Invocation) -> str | None:
    raw = text_arg(inv, "name")
    if raw is None:
        return None
    name = clean_name(raw, "output name")
    if Path(name).name != name:
        raise AxiError(
            "VALIDATION_ERROR",
            f"--name {name} must be a file name without folders",
            help=["Use --out-dir to choose the folder"],
        )
    return name


def run_encrypt(inv: Invocation) -> Result:
    path = _file(inv)
    name, expiry = _output_name(inv), _expiry(inv)
    out_dir = Path(text_arg(inv, "out_dir") or path.parent).expanduser().resolve()
    expected = out_dir / f"{name or path.stem}{ENCRYPTED_SUFFIX}"
    refuse_existing(expected, bool(inv.args.get("overwrite")))
    resolve = cast(DctlResolve, inv.connect().resolve)
    options: dict[str, Any] = {"OutputFolder": str(out_dir)}
    if name:
        options["Name"] = name
    if expiry:
        options["Expiry"] = expiry
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time() - 1
    if not resolve.EncryptDCTL(str(path), options):
        raise api_failed("EncryptDCTL", f"could not encrypt {path.name}")
    written = expected
    if not written.is_file():
        fresh = [p for p in out_dir.glob(f"*{ENCRYPTED_SUFFIX}") if p.stat().st_mtime >= started]
        if not fresh:
            raise api_failed(
                "EncryptDCTL", f"Resolve reported success but wrote no {expected.name}"
            )
        written = max(fresh, key=lambda p: p.stat().st_mtime)
    data: dict[str, object] = {
        "file": collapse_home(path),
        "output": collapse_home(written),
        "bytes": written.stat().st_size,
        "expiry": expiry or "none",
    }
    return Result(data, [])


VALIDATE_SPEC = CommandSpec(
    name="validate",
    summary="Check a DCTL source file; exits 1 with the compiler message when it is invalid",
    run=run_validate,
    positionals=(Positional("file", help="path of the .dctl file"),),
    examples=(
        "davinci-axi dctl validate ~/LUT/grade.dctl",
        "davinci-axi dctl validate ok.dctl --json",
    ),
    reads_only=False,
    methods=("Resolve.ValidateDCTL",),
)

ENCRYPT_SPEC = CommandSpec(
    name="encrypt",
    summary="Write an encrypted copy of a DCTL file (no --force needed; never overwrites)",
    run=run_encrypt,
    positionals=(Positional("file", help="path of the .dctl file"),),
    flags=(
        Flag("name", "str", None, help="output file name without folders (default: input name)"),
        Flag("expiry", "str", None, help="ISO 8601 expiry date or date-time (default: none)"),
        Flag("out-dir", "str", None, help="output folder (default: the input file's folder)"),
        Flag("overwrite", "bool", False, help="replace the output file if it exists"),
    ),
    examples=(
        "davinci-axi dctl encrypt ~/LUT/grade.dctl",
        "davinci-axi dctl encrypt grade.dctl --out-dir ~/LUT/signed --expiry 2027-12-31",
        "davinci-axi dctl encrypt grade.dctl --name grade-v2 --overwrite",
    ),
    reads_only=False,
    methods=("Resolve.EncryptDCTL",),
)

SPEC = CommandSpec(
    name="dctl",
    summary="Validate and encrypt DCTL colour transform files",
    subcommands={"validate": VALIDATE_SPEC, "encrypt": ENCRYPT_SPEC},
    examples=("davinci-axi dctl validate grade.dctl", "davinci-axi dctl encrypt grade.dctl"),
    reads_only=False,
    methods=("Resolve.ValidateDCTL", "Resolve.EncryptDCTL"),
)
