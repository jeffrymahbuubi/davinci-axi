"""Connect to Resolve (real or fake) under a timeout and classify every failure (KTD2, KTD3)."""

from __future__ import annotations

import importlib
import os
import sys
import threading
from dataclasses import dataclass
from typing import cast

from davinci_axi import paths
from davinci_axi.errors import AxiError
from davinci_axi.probe import is_fake, process_running, studio_licence_present
from davinci_axi.resolve_types import ResolveApp
from davinci_axi.session import Session
from davinci_axi.spec import GlobalOpts

__all__ = ["Probe", "connect", "is_fake", "probe"]

SETTING_PATH = "Preferences > System > General > External scripting using"


@dataclass(frozen=True)
class Probe:
    ok: bool
    code: str | None
    product: str | None
    version: str | None
    is_studio: bool | None
    fix: str | None


def _prepare_sys_path() -> None:
    """Put the Resolve `Modules` folder on `sys.path` (`RESOLVE_SCRIPT_API`, else the default)."""
    api = os.environ.get("RESOLVE_SCRIPT_API")
    base = api if api else (str(paths.default_script_api()) if paths.default_script_api() else "")
    if base:
        modules = os.path.join(base, "Modules")
        if modules not in sys.path:
            sys.path.append(modules)


def _scriptapp() -> object | None:
    """Import the scripting module and ask it for the app. Runs in a worker thread."""
    if is_fake():
        from davinci_axi import fake

        if fake.current_world().connection == "not_installed":
            raise ImportError("DaVinciResolveScript is not installed (fake world)")
        return fake.scriptapp("Resolve")
    _prepare_sys_path()
    module = importlib.import_module("DaVinciResolveScript")
    app: object | None = module.scriptapp("Resolve")
    return app


def _run_with_timeout(timeout: float) -> object | None:
    outcome: dict[str, object] = {}

    def worker() -> None:
        try:
            outcome["app"] = _scriptapp()
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=worker, name="davinci-axi-connect", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise AxiError(
            "RESOLVE_TIMEOUT",
            f"Resolve did not answer within {timeout:g} seconds",
            help=[
                "Check that Resolve has finished starting and shows no modal dialog",
                "Retry with a longer wait, for example --timeout 10 or DAVINCI_AXI_TIMEOUT=10",
            ],
        )
    error = outcome.get("error")
    if isinstance(error, (ImportError, OSError)):
        raise _not_installed(error) from error
    if isinstance(error, BaseException):
        raise AxiError(
            "API_FAILED",
            f"Resolve connection failed: {type(error).__name__}: {error}",
            help=["Run with --debug for the traceback on stderr"],
        ) from error
    return outcome.get("app")


def _not_installed(error: BaseException) -> AxiError:
    default = paths.default_script_api()
    where = f" (default: {default})" if default else ""
    return AxiError(
        "RESOLVE_NOT_INSTALLED",
        f"DaVinciResolveScript could not be loaded: {error}",
        help=[
            "Install DaVinci Resolve Studio, or point RESOLVE_SCRIPT_API at its "
            f"Developer/Scripting folder{where}",
            "If Resolve is installed in a custom place, also set RESOLVE_SCRIPT_LIB to the "
            "fusionscript library",
        ],
    )


def _classify_unreachable() -> AxiError:
    """`scriptapp` returned None: tell the three causes apart without the API (KTD2)."""
    if not process_running():
        return AxiError(
            "RESOLVE_NOT_RUNNING",
            "DaVinci Resolve is not running",
            help=[
                "Open DaVinci Resolve Studio and wait until the project manager appears, then retry"
            ],
        )
    if not studio_licence_present():
        return AxiError(
            "FREE_EDITION",
            "DaVinci Resolve Free does not allow external scripting",
            help=[
                "External scripting is Studio-only: install or activate DaVinci Resolve Studio",
                "Restart Resolve after activating Studio, then retry",
            ],
        )
    help_lines = [
        f"In Resolve open {SETTING_PATH} and choose Local, then restart Resolve",
    ]
    if paths.license_dir() is None:
        help_lines.append(
            "If this is the Free edition, external scripting is not available (Studio only)"
        )
    return AxiError(
        "SCRIPTING_DISABLED",
        "Resolve is running but refused the scripting connection",
        help=help_lines,
    )


def _build_session(app: object) -> Session:
    resolve = cast(ResolveApp, app)
    raw = resolve.GetVersion() or []
    numbers: list[int] = []
    for part in raw[:3]:
        try:
            numbers.append(int(part))
        except (TypeError, ValueError):
            numbers.append(0)
    while len(numbers) < 3:
        numbers.append(0)
    product = resolve.GetProductName() or "DaVinci Resolve"
    return Session(
        resolve=resolve,
        version=(numbers[0], numbers[1], numbers[2]),
        product=str(product),
        is_studio=bool(resolve.IsStudio()),
    )


def connect(opts: GlobalOpts) -> Session:
    """Return a live session or raise an AxiError carrying one of the KTD2 codes."""
    app = _run_with_timeout(opts.timeout)
    if app is None:
        raise _classify_unreachable()
    return _build_session(app)


def probe(opts: GlobalOpts) -> Probe:
    """Like `connect` but never raises; the result says whether Resolve is reachable and why not."""
    try:
        session = connect(opts)
    except AxiError as err:
        return Probe(False, err.code, None, None, None, err.help[0] if err.help else err.message)
    version = ".".join(str(n) for n in session.version)
    return Probe(True, None, session.product, version, session.is_studio, None)
