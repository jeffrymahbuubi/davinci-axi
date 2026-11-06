"""Coded errors shared by every command."""

from __future__ import annotations

CODE_EXIT: dict[str, int] = {
    "VALIDATION_ERROR": 2,
    "EXEC_NOT_ALLOWED": 2,
}
"""Exit code per error code. Unknown codes exit 1 (`CODE_EXIT.get(code, 1)`), so unit-local codes
need no edit here.

Codes in use: VALIDATION_ERROR EXEC_NOT_ALLOWED RESOLVE_NOT_INSTALLED RESOLVE_NOT_RUNNING
SCRIPTING_DISABLED FREE_EDITION RESOLVE_TIMEOUT NO_PROJECT NO_TIMELINE NOT_FOUND VERSION_TOO_OLD
API_FAILED SNAPSHOT_FAILED WRITE_REFUSED INTERNAL.
"""


class AxiError(Exception):
    """An error that is reported as `error`, `code` and `help[]` on stdout."""

    def __init__(
        self,
        code: str,
        message: str,
        help: list[str] | None = None,
        detail: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.help: list[str] = list(help) if help else []
        self.detail: dict[str, object] = dict(detail) if detail else {}

    @property
    def exit_code(self) -> int:
        return CODE_EXIT.get(self.code, 1)


def api_failed(method: str, detail: str = "") -> AxiError:
    """Map a None/False return from the Resolve API to a coded error."""
    message = f"Resolve call {method} failed"
    if detail:
        message = f"{message}: {detail}"
    return AxiError(
        "API_FAILED",
        message,
        help=["Check that Resolve is idle (no modal dialog) and that the project is still open"],
    )


def not_found(kind: str, ref: str, hint: str) -> AxiError:
    """A named thing does not exist; `hint` says what exists or how to list it."""
    return AxiError("NOT_FOUND", f"{kind} {ref} not found", help=[hint])
