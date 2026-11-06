"""`page show|set`: the Resolve page that is open (U23, R35).

`show` reads `GetCurrentPage`. `set` opens a page with `OpenPage` and reads the page back. The
page is UI state, not project state, so there is no snapshot; the change still needs `--force`
because it moves what the user sees. Opening the page that is already open is a no-op.
"""

from __future__ import annotations

from typing import cast

from davinci_axi.commands.project_io import next_steps
from davinci_axi.errors import AxiError, api_failed
from davinci_axi.mutate import (
    MUTATION_FLAGS,
    Change,
    MutationPlan,
    check_snapshot_flags,
    force_cmd,
    run_mutation,
)
from davinci_axi.resolve_types_edit import ResolveEdit
from davinci_axi.spec import CommandSpec, Invocation, Positional, Result

PAGES = ("media", "cut", "edit", "fusion", "color", "fairlight", "deliver")


def run_show(inv: Invocation) -> Result:
    page = inv.connect().resolve.GetCurrentPage()
    data: dict[str, object] = {"page": page or "unknown", "pages": ", ".join(PAGES)}
    return Result(data, next_steps(("page set", "open another page (needs --force)")))


def _page_arg(inv: Invocation) -> str:
    page = inv.positionals["page"].strip().lower()
    if page not in PAGES:
        raise AxiError(
            "VALIDATION_ERROR",
            f"{inv.positionals['page']} is not a Resolve page",
            help=[f"Valid pages: {', '.join(PAGES)}", "Valid example: page set color --force"],
        )
    return page


def run_set(inv: Invocation) -> Result:
    page = _page_arg(inv)
    check_snapshot_flags(inv)
    session = inv.connect()
    app = cast(ResolveEdit, session.resolve)
    before = app.GetCurrentPage()
    plan = MutationPlan(
        command="page set",
        target=f"the {page} page",
        changes=[] if before == page else [Change("page", before, page)],
        force_cmd=force_cmd(inv, "page", "set", page),
        warning="already on this page" if before == page else "",
        snapshot="none",
        next=next_steps(("page show", "read the open page")),
    )

    def apply() -> None:
        if not app.OpenPage(page):
            raise api_failed("OpenPage", f"could not switch to the {page} page")

    def verify() -> dict[str, object]:
        return {"page": app.GetCurrentPage()}

    return run_mutation(inv, session, plan, apply, verify)


SHOW_SPEC = CommandSpec(
    name="show",
    summary="Show the page Resolve has open",
    run=run_show,
    examples=("davinci-axi page show", "davinci-axi page show --json"),
    methods=("Resolve.GetCurrentPage",),
)

SET_SPEC = CommandSpec(
    name="set",
    summary="Open a Resolve page (dry run until --force; changes the UI only, no snapshot)",
    run=run_set,
    positionals=(Positional("page", help=f"one of {', '.join(PAGES)}"),),
    flags=MUTATION_FLAGS,
    examples=("davinci-axi page set color", "davinci-axi page set color --force"),
    reads_only=False,
    methods=("Resolve.OpenPage", "Resolve.GetCurrentPage"),
)

SPEC = CommandSpec(
    name="page",
    summary="Show or switch the open Resolve page (media cut edit fusion color fairlight deliver)",
    subcommands={"show": SHOW_SPEC, "set": SET_SPEC},
    examples=("davinci-axi page show", "davinci-axi page set deliver --force"),
    reads_only=False,
    methods=tuple(dict.fromkeys(m for s in (SHOW_SPEC, SET_SPEC) for m in s.methods)),
)
