"""`app show`: what Resolve itself reports as current (U26, R36). Read-only, always exits 0.

It is a status read, not a project read (KTD33): with no project open it answers `null` for the
project, timeline, media pool and gallery album instead of failing, like `home`.
"""

from __future__ import annotations

from typing import Any, Protocol, cast

from davinci_axi import extras, registry
from davinci_axi.commands.color_common import GalleryApi
from davinci_axi.output import hint
from davinci_axi.resolve_types import ResolveApp
from davinci_axi.spec import CommandSpec, Invocation, Result


class ResolveReads(ResolveApp, Protocol):
    """`Resolve.GetGallery` is the one read here that `resolve_types` does not declare."""

    def GetGallery(self) -> Any: ...


def _album_name(resolve: ResolveReads) -> str | None:
    gallery = resolve.GetGallery()
    if gallery is None:
        return None
    api = cast(GalleryApi, gallery)
    album = api.GetCurrentStillAlbum()
    return api.GetAlbumName(album) if album is not None else None


def run_show(inv: Invocation) -> Result:
    session = inv.connect()
    resolve = cast(ResolveReads, session.resolve)
    project = resolve.GetCurrentProject()
    timeline = resolve.GetCurrentTimeline()
    pool = resolve.GetMediaPool()
    data: dict[str, object] = {
        "version": resolve.GetVersionString(),
        "product": session.product,
        "edition": "Studio" if session.is_studio else "Free",
        "ai_extras": extras.scan().summary(),
        "page": resolve.GetCurrentPage(),
        "project": project.GetName() if project is not None else None,
        "timeline": timeline.GetName() if timeline is not None else None,
        "media_pool_id": pool.GetUniqueId() if pool is not None else None,
        "gallery_album": _album_name(resolve) if project is not None else None,
    }
    lines: list[str] = []
    if "page" in registry.COMMANDS:
        lines.append(hint("page show", "see which pages exist and which one is open"))
    lines.append(hint("timelines", "list the timelines of the current project"))
    lines.append(hint("ai extras", "list the installed AI packages"))
    return Result(data, lines)


SHOW_SPEC = CommandSpec(
    name="show",
    summary=(
        "Show what Resolve reports as current: version, page, project, timeline, media pool, "
        "gallery album"
    ),
    run=run_show,
    examples=("davinci-axi app show", "davinci-axi app show --json"),
    methods=(
        "Resolve.GetVersionString",
        "Resolve.GetProductName",
        "Resolve.IsStudio",
        "Resolve.GetCurrentPage",
        "Resolve.GetCurrentProject",
        "Project.GetName",
        "Resolve.GetCurrentTimeline",
        "Timeline.GetName",
        "Resolve.GetMediaPool",
        "MediaPool.GetUniqueId",
        "Resolve.GetGallery",
        "Gallery.GetCurrentStillAlbum",
        "Gallery.GetAlbumName",
    ),
)
