"""`color still|album`: the gallery (U19, R35).

Stills are addressed by their 1-based position in `GalleryStillAlbum.GetStills()`; `--album` picks
an album by name (default: the current still album). `still grab` needs the Color page (KTD27,
OQ15): it opens it with `mutate.with_page` and reports `page_restored`; every other verb leaves
the page alone. `still export` writes files only, so it takes `--overwrite` instead of `--force`
and never snapshots.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

from davinci_axi.commands.color_common import (
    AlbumApi,
    ColorProject,
    GalleryApi,
    parent,
    project_write,
    verb,
)
from davinci_axi.commands.project_io import tree_bytes
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import MUTATION_FLAGS, Change, check_snapshot_flags, with_page
from davinci_axi.output import collapse_home, empty_state, hint
from davinci_axi.resolve_types import TimelineApi
from davinci_axi.session import Session
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

STILL_FORMATS = ("dpx", "cin", "tif", "jpg", "png", "ppm", "bmp", "xpm")
GRAB_SOURCES = {"first": 1, "middle": 2}
ALBUM = Flag("album", "str", None, help="album name (default: the current still album)")
POSITIONS = Positional("positions", help="comma-separated still positions, counted from 1")
GALLERY_READS = "Project.GetGallery Gallery.GetAlbumName Gallery.GetCurrentStillAlbum"
ALBUM_READS = f"{GALLERY_READS} Gallery.GetGalleryStillAlbums Gallery.GetGalleryPowerGradeAlbums"
STILL_READS = f"{GALLERY_READS} GalleryStillAlbum.GetStills"
UNDO_NOTE = "the snapshot is the only way back"


class GalleryTimeline(TimelineApi, Protocol):
    def GrabStill(self) -> Any: ...
    def GrabAllStills(self, stillFrameSource: int) -> list[Any] | None: ...


def gallery_of(session: Session) -> GalleryApi:
    gallery = cast(ColorProject, session.require_project()).GetGallery()
    if gallery is None:
        raise api_failed("Project.GetGallery", "this project has no gallery")
    return cast(GalleryApi, gallery)


def _albums(gallery: GalleryApi) -> list[tuple[str, str, AlbumApi]]:
    still = gallery.GetGalleryStillAlbums() or []
    power = gallery.GetGalleryPowerGradeAlbums() or []
    return [("still", gallery.GetAlbumName(a), a) for a in still] + [
        ("powergrade", gallery.GetAlbumName(a), a) for a in power
    ]


def find_album(gallery: GalleryApi, name: str | None) -> tuple[str, AlbumApi]:
    """(album name, album): the one called `name`, or the current still album."""
    if name is None:
        current = gallery.GetCurrentStillAlbum()
        if current is None:
            hint_text = "Create one with `davinci-axi color album create`"
            raise not_found("album", "(current)", hint_text)
        return gallery.GetAlbumName(current), current
    found = [(n, a) for _, n, a in _albums(gallery) if n == name]
    if not found:
        listing = ", ".join(n for _, n, _ in _albums(gallery)) or "(none)"
        raise not_found("album", name, f"Albums in this gallery: {listing}")
    if len(found) > 1:
        raise AxiError(
            "VALIDATION_ERROR",
            f"{len(found)} albums are called {name}",
            help=["Rename one with `davinci-axi color album rename`, then retry"],
        )
    return found[0]


def parse_positions(text: str, count: int, album: str) -> list[int]:
    try:
        numbers = [int(part) for part in text.split(",") if part.strip()]
    except ValueError:
        numbers = []
    if not numbers or len(set(numbers)) != len(numbers):
        raise AxiError(
            "VALIDATION_ERROR",
            f"cannot read {text} as still positions",
            help=["Valid example: 1,3 (positions counted from 1, each once)"],
        )
    bad = [str(n) for n in numbers if not 1 <= n <= count]
    if bad:
        raise not_found("still", ", ".join(bad), f"Album {album} has {count} stills (from 1)")
    return numbers


def _album_arg(inv: Invocation) -> str | None:
    value = inv.args.get("album")
    return value if isinstance(value, str) and value else None


def _album_words(inv: Invocation) -> list[str]:
    name = _album_arg(inv)
    return ["--album", name] if name else []


def _still_setup(inv: Invocation) -> tuple[Session, str, AlbumApi, list[Any]]:
    check_snapshot_flags(inv)
    session = inv.connect()
    name, album = find_album(gallery_of(session), _album_arg(inv))
    return session, name, album, album.GetStills() or []


def run_still_list(inv: Invocation) -> Result:
    _, name, album, stills = _still_setup(inv)
    rows = [{"position": i, "label": album.GetLabel(s)} for i, s in enumerate(stills, start=1)]
    empty = empty_state("stills", f"in album {name}", {})
    return Result({"album": name, "count": len(rows), "stills": rows or empty})


def run_still_grab(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    word = inv.args.get("all")
    source = GRAB_SOURCES[str(word)] if word else None
    session = inv.connect()
    name, album = find_album(gallery_of(session), None)
    timeline = cast(GalleryTimeline, session.require_timeline(None))
    before = len(album.GetStills() or [])
    changes = [Change("stills", before, "more (one per video clip)" if source else before + 1)]
    extra: dict[str, object] = {"album": name}

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
        words=["color", "still", "grab", *(["--all", str(word)] if word else [])],
        changes=changes,
        call=call,
        read=lambda: {"stills": len(album.GetStills() or [])},
        fail="Timeline.GrabAllStills" if source else "Timeline.GrabStill",
        warning="grabs from the current clip at the playhead; the Color page opens and is restored",
        extra=extra,
        next_lines=[hint("color still list", "list the stills of the album")],
    )


def run_still_export(inv: Invocation) -> Result:
    fmt, prefix = str(inv.args.get("format") or "png"), str(inv.args.get("prefix") or "still")
    folder = Path(inv.positionals["dir"]).expanduser().resolve()
    shown_dir = collapse_home(folder)
    if folder.exists() and not folder.is_dir():
        raise AxiError(
            "VALIDATION_ERROR", f"{shown_dir} is not a directory", help=["Pass a directory"]
        )
    held = sorted(p.name for p in folder.glob(f"{prefix}_*.{fmt}")) if folder.is_dir() else []
    if held and not inv.args.get("overwrite"):
        raise AxiError(
            "FILE_EXISTS",
            f"{shown_dir} already holds {', '.join(held[:3])}",
            help=["Pass --overwrite to replace them, or choose another --prefix or directory"],
        )
    _, name, album, stills = _still_setup(inv)
    chosen = parse_positions(inv.positionals["positions"], len(stills), name)
    folder.mkdir(parents=True, exist_ok=True)
    if not album.ExportStills([stills[n - 1] for n in chosen], str(folder), prefix, fmt):
        raise api_failed("GalleryStillAlbum.ExportStills", f"could not export from album {name}")
    written = sorted(folder.glob(f"{prefix}*.{fmt}"))
    if not written:
        raise api_failed(
            "GalleryStillAlbum.ExportStills", "Resolve reported success, wrote no file"
        )
    files = [{"name": p.name, "bytes": tree_bytes(p)} for p in written]
    shown = ", ".join(map(str, chosen))
    return Result({"album": name, "stills": shown, "dir": shown_dir, "format": fmt, "files": files})


def run_still_delete(inv: Invocation) -> Result:
    session, name, album, stills = _still_setup(inv)
    chosen = parse_positions(inv.positionals["positions"], len(stills), name)
    doomed = [stills[n - 1] for n in chosen]
    return project_write(
        inv,
        session,
        command="color still delete",
        words=["color", "still", "delete", inv.positionals["positions"], *_album_words(inv)],
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
    session, name, album, stills = _still_setup(inv)
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
        words=["color", "still", "label", str(position), text, *_album_words(inv)],
        changes=[Change("label", before, text)] if before != text else [],
        call=lambda: album.SetLabel(still, text),
        read=lambda: {"label": album.GetLabel(still)},
        fail="GalleryStillAlbum.SetLabel",
        extra={"album": name, "position": position},
    )


def run_still_import(inv: Invocation) -> Result:
    check_snapshot_flags(inv)
    raw = [part.strip() for part in inv.positionals["files"].split(",") if part.strip()]
    paths = [Path(part).expanduser().resolve() for part in raw]
    if not paths:
        raise AxiError("VALIDATION_ERROR", "no files given", help=["Pass comma-separated files"])
    for path in paths:
        if not path.is_file():
            raise not_found("file", collapse_home(path), "Check the path of the still file")
    session, name, album, stills = _still_setup(inv)
    return project_write(
        inv,
        session,
        command="color still import",
        words=["color", "still", "import", inv.positionals["files"], *_album_words(inv)],
        changes=[Change("stills", len(stills), len(stills) + len(paths))],
        call=lambda: album.ImportStills([str(p) for p in paths]),
        read=lambda: {"stills": len(album.GetStills() or [])},
        fail="GalleryStillAlbum.ImportStills",
        extra={"album": name},
        next_lines=[hint("color still list", "list the stills of the album")],
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
        for k, n, a in _albums(gallery)
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
        return sum(1 for k, _, _ in _albums(gallery) if k == kind)

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
    )


STILL_SPEC = parent(
    "still",
    "Grab, list, export, import, label or delete gallery stills",
    {
        "grab": _leaf(
            "grab",
            "Grab a still of the current clip, or of every clip with --all",
            run_still_grab,
            (),
            (
                *MUTATION_FLAGS,
                Flag("all", "str", None, choices=tuple(GRAB_SOURCES), help="all clips"),
            ),
            "davinci-axi color still grab|davinci-axi color still grab --all middle --force",
            f"{STILL_READS} Timeline.GrabStill Timeline.GrabAllStills",
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
