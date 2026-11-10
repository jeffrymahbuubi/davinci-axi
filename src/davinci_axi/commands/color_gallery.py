"""`color still|album`: the gallery (U19, R35).

Stills are addressed by their 1-based position in `GalleryStillAlbum.GetStills()`; `--album` picks
an album by name (default: the current still album). `still grab` needs the Color page (KTD27,
OQ15): it opens it with `mutate.with_page` and reports `page_restored`; every other verb leaves
the page alone. `still export` and `still import` live in `color_stills_io` with the album helpers;
`still export` writes files only (an image and a `.drx` per still), so it takes `--overwrite`
instead of `--force` and never snapshots. Positions are creation order (a grab appends).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, cast

from davinci_axi.commands.color_common import parent, project_write, verb
from davinci_axi.commands.color_stills_io import (
    POSITIONS,
    STILL_FORMATS,
    album_words,
    albums_of,
    find_album,
    gallery_of,
    parse_positions,
    run_still_export,
    run_still_import,
    still_setup,
)
from davinci_axi.errors import AxiError
from davinci_axi.mutate import MUTATION_FLAGS, Change, check_snapshot_flags, with_page
from davinci_axi.output import empty_state, hint
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

GRAB_SOURCES = {"first": 1, "middle": 2}
ALBUM = Flag("album", "str", None, help="album name (default: the current still album)")
GALLERY_READS = "Project.GetGallery Gallery.GetAlbumName Gallery.GetCurrentStillAlbum"
ALBUM_READS = f"{GALLERY_READS} Gallery.GetGalleryStillAlbums Gallery.GetGalleryPowerGradeAlbums"
STILL_READS = f"{GALLERY_READS} GalleryStillAlbum.GetStills"
UNDO_NOTE = "the snapshot is the only way back"


class GalleryTimeline(TimelineApi, Protocol):
    def GrabStill(self) -> Any: ...
    def GrabAllStills(self, stillFrameSource: int) -> list[Any] | None: ...


def run_still_list(inv: Invocation) -> Result:
    _, name, album, stills = still_setup(inv)
    rows = [{"position": i, "label": album.GetLabel(s)} for i, s in enumerate(stills, start=1)]
    empty = empty_state("stills", f"in album {name}", {})
    return Result({"album": name, "count": len(rows), "stills": rows or empty})


def run_still_grab(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    word = inv.args.get("source")
    source = GRAB_SOURCES[str(word)] if word else None
    session = inv.connect()
    name, album = find_album(gallery_of(session), None)
    timeline = cast(GalleryTimeline, session.require_timeline(None))
    before = len(album.GetStills() or [])
    changes = [Change("stills", before, "more (one per video clip)" if source else before + 1)]
    extra: dict[str, object] = {"album": name}
    next_lines = [hint("color still list", "list the stills of the album")]
    if source is None:
        extra["position"] = before + 1  # ASSUMED appended last (the fake does; check live)
        next_lines.append(
            hint(
                f"color still delete {before + 1} --force", "remove the still from the album again"
            )
        )

    def call() -> bool:
        with with_page(session, "color") as switched:
            if switched:
                extra["page_restored"] = True
            if source is None:
                return timeline.GrabStill() is not None
            grabbed = timeline.GrabAllStills(source)
            if grabbed:
                changes[:] = [Change("stills", before, before + len(grabbed))]
            return bool(grabbed)

    return project_write(
        inv,
        session,
        command="color still grab",
        words=["color", "still", "grab", *(["--source", str(word)] if word else [])],
        changes=changes,
        call=call,
        read=lambda: {"stills": len(album.GetStills() or [])},
        fail="Timeline.GrabAllStills" if source else "Timeline.GrabStill",
        warning=(
            "grabs from the current clip at the playhead into album "
            f"{name} and leaves the still there (delete it with `color still delete`); "
            "the Color page opens and is restored"
        ),
        extra=extra,
        next_lines=next_lines,
    )


def run_still_delete(inv: Invocation) -> Result:
    session, name, album, stills = still_setup(inv)
    chosen = parse_positions(inv.positionals["positions"], len(stills), name)
    doomed = [stills[n - 1] for n in chosen]
    return project_write(
        inv,
        session,
        command="color still delete",
        words=["color", "still", "delete", inv.positionals["positions"], *album_words(inv)],
        changes=[Change("stills", len(stills), len(stills) - len(chosen))],
        call=lambda: album.DeleteStills(doomed),
        read=lambda: {"stills": len(album.GetStills() or [])},
        fail="GalleryStillAlbum.DeleteStills",
        irreversible=True,
        warning=f"deletes {len(chosen)} still(s) from album {name}; {UNDO_NOTE}",
        extra={"album": name, "positions": ", ".join(map(str, chosen))},
        next_lines=[hint("color still list", "list the remaining stills")],
    )


def run_still_label(inv: Invocation) -> Result:
    text = inv.positionals["text"]
    session, name, album, stills = still_setup(inv)
    chosen = parse_positions(inv.positionals["position"], len(stills), name)
    if len(chosen) != 1:
        raise AxiError("VALIDATION_ERROR", "label one still at a time", help=["Pass one position"])
    position = chosen[0]
    still = stills[position - 1]
    before = album.GetLabel(still)
    return project_write(
        inv,
        session,
        command="color still label",
        words=["color", "still", "label", str(position), text, *album_words(inv)],
        changes=[Change("label", before, text)] if before != text else [],
        call=lambda: album.SetLabel(still, text),
        read=lambda: {"label": album.GetLabel(still)},
        fail="GalleryStillAlbum.SetLabel",
        extra={"album": name, "position": position},
    )


def run_album_list(inv: Invocation) -> Result:
    gallery = gallery_of(inv.connect())
    current = gallery.GetCurrentStillAlbum()
    live = gallery.GetAlbumName(current) if current is not None else None
    rows = [
        {
            "name": n,
            "kind": k,
            "stills": len(a.GetStills() or []),
            "current": k == "still" and n == live,
        }
        for k, n, a in albums_of(gallery)
    ]
    empty = empty_state("albums", "in this gallery", {})
    return Result({"count": len(rows), "albums": rows or empty})


def run_album_create(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    power = bool(inv.args.get("powergrade"))
    kind = "powergrade" if power else "still"
    session = inv.connect()
    gallery = gallery_of(session)

    def count() -> int:
        return sum(1 for k, _, _ in albums_of(gallery) if k == kind)

    before = count()
    extra: dict[str, object] = {"kind": kind}

    def call() -> bool:
        make = gallery.CreateGalleryPowerGradeAlbum if power else gallery.CreateGalleryStillAlbum
        made = make()
        if made is not None:
            extra["new_album"] = gallery.GetAlbumName(made)
        return made is not None

    return project_write(
        inv,
        session,
        command="color album create",
        words=["color", "album", "create", *(["--powergrade"] if power else [])],
        changes=[Change("albums", before, before + 1)],
        call=call,
        read=lambda: {"albums": count()},
        fail="Gallery.CreateGalleryPowerGradeAlbum" if power else "Gallery.CreateGalleryStillAlbum",
        extra=extra,
        next_lines=[hint("color album rename", "name the new album (pass its name and --to)")],
    )


def run_album_rename(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    old, new = inv.positionals["name"], str(inv.args.get("to") or "").strip()
    if not new:
        raise AxiError("VALIDATION_ERROR", "--to needs a new name", help=["Valid: --to Hero"])
    session = inv.connect()
    gallery = gallery_of(session)
    _, album = find_album(gallery, old)
    return project_write(
        inv,
        session,
        command="color album rename",
        words=["color", "album", "rename", old, "--to", new],
        changes=[Change("album", old, new)] if old != new else [],
        call=lambda: gallery.SetAlbumName(album, new),
        read=lambda: {"album": gallery.GetAlbumName(album)},
        fail="Gallery.SetAlbumName",
    )


def run_album_use(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    name = inv.positionals["name"]
    session = inv.connect()
    gallery = gallery_of(session)
    _, album = find_album(gallery, name)
    current = gallery.GetCurrentStillAlbum()
    before = gallery.GetAlbumName(current) if current is not None else None

    def read() -> dict[str, object]:
        now = gallery.GetCurrentStillAlbum()
        return {"current_album": gallery.GetAlbumName(now) if now is not None else None}

    return project_write(
        inv,
        session,
        command="color album use",
        words=["color", "album", "use", name],
        changes=[Change("current_album", before, name)] if before != name else [],
        call=lambda: gallery.SetCurrentStillAlbum(album),
        read=read,
        fail="Gallery.SetCurrentStillAlbum",
        snapshot="none",
    )


def _leaf(
    name: str,
    summary: str,
    run: Callable[[Invocation], Result],
    positionals: tuple[Positional, ...],
    flags: tuple[Flag, ...],
    example: str,
    methods: str,
    kind: str = "write",
    notes: tuple[str, ...] = (),
) -> CommandSpec:
    return verb(
        name,
        summary,
        run,
        positionals,
        flags,
        example,
        methods,
        read=kind == "read",
        disk=kind == "disk",
        notes=notes,
    )


STILL_SPEC = parent(
    "still",
    "Grab, list, export, import, label or delete gallery stills",
    {
        "grab": _leaf(
            "grab",
            "Grab a still of the current clip, or of every clip with --source",
            run_still_grab,
            (),
            (
                *MUTATION_FLAGS,
                Flag(
                    "source",
                    "str",
                    None,
                    choices=tuple(GRAB_SOURCES),
                    help="grab every clip, at its first or middle frame",
                    renamed_from="all",
                ),
            ),
            "davinci-axi color still grab|davinci-axi color still grab --source middle --force",
            f"{STILL_READS} Timeline.GrabStill Timeline.GrabAllStills",
            notes=(
                "Every grab adds a still to the current album; export it with `color still "
                "export <position> <dir>` and delete it with `color still delete <position> "
                "--force` when it was only a means to a .drx",
            ),
        ),
        "list": _leaf(
            "list",
            "List the stills of an album (read-only)",
            run_still_list,
            (),
            (ALBUM,),
            "davinci-axi color still list|davinci-axi color still list --album 'Stills 2'",
            f"{STILL_READS} GalleryStillAlbum.GetLabel",
            "read",
        ),
        "export": _leaf(
            "export",
            "Export stills to a directory (files only, never overwrites)",
            run_still_export,
            (POSITIONS, Positional("dir", help="output directory")),
            (
                ALBUM,
                Flag("overwrite", "bool", False, help="replace files that already exist"),
                Flag("prefix", "str", "still", help="file name prefix"),
                Flag("format", "str", "png", choices=STILL_FORMATS, help="image format"),
            ),
            "davinci-axi color still export 1,2 ~/stills --prefix shot --format png",
            f"{STILL_READS} GalleryStillAlbum.ExportStills",
            "disk",
            (
                "Resolve writes a .drx grade file beside every exported image, whatever "
                "--format; there is no drx-only export",
                "The files are listed under files; the .drx is what `color grade apply-drx` "
                "and `color grade init --drx` take",
            ),
        ),
        "import": _leaf(
            "import",
            "Import still files into an album",
            run_still_import,
            (Positional("files", help="comma-separated still files"),),
            (*MUTATION_FLAGS, ALBUM),
            "davinci-axi color still import ~/stills/a.dpx",
            f"{STILL_READS} GalleryStillAlbum.ImportStills",
        ),
        "delete": _leaf(
            "delete",
            "Delete stills from an album (irreversible except through a snapshot)",
            run_still_delete,
            (POSITIONS,),
            (*MUTATION_FLAGS, ALBUM),
            "davinci-axi color still delete 2",
            f"{STILL_READS} GalleryStillAlbum.DeleteStills",
        ),
        "label": _leaf(
            "label",
            "Set the label of one still",
            run_still_label,
            (
                Positional("position", help="still position, from 1"),
                Positional("text", help="label"),
            ),
            (*MUTATION_FLAGS, ALBUM),
            "davinci-axi color still label 1 hero",
            f"{STILL_READS} GalleryStillAlbum.GetLabel GalleryStillAlbum.SetLabel",
        ),
    },
    "davinci-axi color still list|davinci-axi color still grab --force",
)
ALBUM_SPEC = parent(
    "album",
    "List, create, rename or select gallery albums",
    {
        "list": _leaf(
            "list",
            "List the still and PowerGrade albums (read-only)",
            run_album_list,
            (),
            (),
            "davinci-axi color album list",
            f"{ALBUM_READS} GalleryStillAlbum.GetStills",
            "read",
        ),
        "create": _leaf(
            "create",
            "Create a still album, or a PowerGrade album with --powergrade",
            run_album_create,
            (),
            (*MUTATION_FLAGS, Flag("powergrade", "bool", False, help="create a PowerGrade album")),
            "davinci-axi color album create|davinci-axi color album create --powergrade --force",
            f"{ALBUM_READS} Gallery.CreateGalleryStillAlbum Gallery.CreateGalleryPowerGradeAlbum",
        ),
        "rename": _leaf(
            "rename",
            "Rename an album",
            run_album_rename,
            (Positional("name", help="the album name"),),
            (*MUTATION_FLAGS, Flag("to", "str", None, required=True, help="the new name")),
            "davinci-axi color album rename 'Stills 2' --to Hero",
            f"{ALBUM_READS} Gallery.SetAlbumName",
        ),
        "use": _leaf(
            "use",
            "Make an album the current still album",
            run_album_use,
            (Positional("name", help="the album name"),),
            MUTATION_FLAGS,
            "davinci-axi color album use Hero",
            f"{ALBUM_READS} Gallery.SetCurrentStillAlbum",
        ),
    },
    "davinci-axi color album list|davinci-axi color album use Hero --force",
)

SUBCOMMANDS: dict[str, CommandSpec] = {"still": STILL_SPEC, "album": ALBUM_SPEC}
