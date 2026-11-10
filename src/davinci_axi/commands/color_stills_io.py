"""`color still export|import` and the gallery helpers they share with `color_gallery` (U6).

Split out of `color_gallery` to keep both under 500 lines. The album lookup helpers (`gallery_of`,
`find_album`, `parse_positions`, ...) live here so `color_gallery` imports from this module and no
import cycle appears. `still export` writes files only (an image plus a `.drx` grade file per
still, F31b), so it takes `--overwrite` instead of `--force` and never snapshots.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from davinci_axi.commands.color_common import AlbumApi, ColorProject, GalleryApi, project_write
from davinci_axi.commands.project_io import tree_bytes
from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.mutate import Change, check_snapshot_flags
from davinci_axi.output import collapse_home, hint
from davinci_axi.session import Session
from davinci_axi.spec import Invocation, Positional, Result

STILL_FORMATS = ("dpx", "cin", "tif", "jpg", "png", "ppm", "bmp", "xpm")
POSITIONS = Positional("positions", help="comma-separated still positions, counted from 1")


def gallery_of(session: Session) -> GalleryApi:
    gallery = cast(ColorProject, session.require_project()).GetGallery()
    if gallery is None:
        raise api_failed("Project.GetGallery", "this project has no gallery")
    return cast(GalleryApi, gallery)


def albums_of(gallery: GalleryApi) -> list[tuple[str, str, AlbumApi]]:
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
    found = [(n, a) for _, n, a in albums_of(gallery) if n == name]
    if not found:
        listing = ", ".join(n for _, n, _ in albums_of(gallery)) or "(none)"
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


def album_arg(inv: Invocation) -> str | None:
    value = inv.args.get("album")
    return value if isinstance(value, str) and value else None


def album_words(inv: Invocation) -> list[str]:
    name = album_arg(inv)
    return ["--album", name] if name else []


def still_setup(inv: Invocation) -> tuple[Session, str, AlbumApi, list[Any]]:
    check_snapshot_flags(inv)
    session = inv.connect()
    name, album = find_album(gallery_of(session), album_arg(inv))
    return session, name, album, album.GetStills() or []


def run_still_export(inv: Invocation) -> Result:
    fmt, prefix = str(inv.args.get("format") or "png"), str(inv.args.get("prefix") or "still")
    folder = Path(inv.positionals["dir"]).expanduser().resolve()
    shown_dir = collapse_home(folder)
    if folder.exists() and not folder.is_dir():
        raise AxiError(
            "VALIDATION_ERROR", f"{shown_dir} is not a directory", help=["Pass a directory"]
        )
    held = (
        sorted(p.name for p in (*folder.glob(f"{prefix}_*.{fmt}"), *folder.glob(f"{prefix}_*.drx")))
        if folder.is_dir()
        else []
    )
    if held and not inv.args.get("overwrite"):
        raise AxiError(
            "FILE_EXISTS",
            f"{shown_dir} already holds {', '.join(held[:3])}",
            help=["Pass --overwrite to replace them, or choose another --prefix or directory"],
        )
    _, name, album, stills = still_setup(inv)
    chosen = parse_positions(inv.positionals["positions"], len(stills), name)
    folder.mkdir(parents=True, exist_ok=True)
    if not album.ExportStills([stills[n - 1] for n in chosen], str(folder), prefix, fmt):
        raise api_failed("GalleryStillAlbum.ExportStills", f"could not export from album {name}")
    images = sorted(folder.glob(f"{prefix}*.{fmt}"))
    grades = sorted(folder.glob(f"{prefix}*.drx"))
    if not images:
        raise api_failed(
            "GalleryStillAlbum.ExportStills", "Resolve reported success, wrote no file"
        )
    files = [{"name": p.name, "bytes": tree_bytes(p)} for p in (*images, *grades)]
    shown = ", ".join(map(str, chosen))
    return Result(
        {
            "album": name,
            "stills": shown,
            "dir": shown_dir,
            "format": fmt,
            "drx": len(grades),
            "files": files,
        }
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
    session, name, album, stills = still_setup(inv)
    return project_write(
        inv,
        session,
        command="color still import",
        words=["color", "still", "import", inv.positionals["files"], *album_words(inv)],
        changes=[Change("stills", len(stills), len(stills) + len(paths))],
        call=lambda: album.ImportStills([str(p) for p in paths]),
        read=lambda: {"stills": len(album.GetStills() or [])},
        fail="GalleryStillAlbum.ImportStills",
        extra={"album": name},
        next_lines=[hint("color still list", "list the stills of the album")],
    )
