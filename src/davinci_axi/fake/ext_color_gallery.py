"""Fake gallery for U19: stills, albums, thumbnails, LUT export and the LUT list refresh.

Split from `ext_color` for the 500-line rule; `ext_color.extend()` calls this module's `extend()`.
Imports only from `davinci_axi.fake.core|timeline|world`, never from the `davinci_axi.fake`
package (KTD17). ASSUMED (OQ15, live check decides): `GrabStill`, `GrabAllStills` and
`GetCurrentClipThumbnailImage` need the Color page, so the fake refuses them on any other page;
this proves the command switches pages around them. The `EXPORT_LUT_*` numbers are made up, only
distinct. `extend()` is idempotent.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from davinci_axi.fake.core import FakeProject, FakeResolve
from davinci_axi.fake.timeline import FakeTimeline, FakeTimelineItem
from davinci_axi.fake.world import FakeBase, FakeWorld

PNG_1X1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5E"
    "rkJggg=="
)

STILL_FORMATS = ("dpx", "cin", "tif", "jpg", "png", "ppm", "bmp", "xpm")

LUT_ENUMS: dict[str, float] = {
    "17PTCUBE": 1701.0,
    "33PTCUBE": 1733.0,
    "65PTCUBE": 1765.0,
    "PANASONICVLUT": 1799.0,
}


class FakeStill:
    """An opaque gallery still: only its label is readable (through the album)."""

    def __init__(self, label: str) -> None:
        self.label = label


class FakeAlbum(FakeBase):
    CLASS = "GalleryStillAlbum"

    def __init__(self, world: FakeWorld, name: str) -> None:
        super().__init__(world)
        self.name = name
        self.stills: list[FakeStill] = []

    def GetStills(self) -> list[FakeStill]:
        return self._ret("GetStills", list(self.stills))

    def GetLabel(self, galleryStill: FakeStill) -> str:
        return self._ret("GetLabel", galleryStill.label, galleryStill)

    def SetLabel(self, galleryStill: FakeStill, label: str) -> bool:
        injected, value = self._rec("SetLabel", galleryStill, label)
        if injected:
            return bool(value)
        if galleryStill not in self.stills:
            return False
        galleryStill.label = label
        return True

    def ImportStills(self, filePaths: list[str]) -> bool:
        injected, value = self._rec("ImportStills", filePaths)
        if injected:
            return bool(value)
        if not filePaths or not all(Path(p).is_file() for p in filePaths):
            return False
        self.stills.extend(FakeStill(Path(p).stem) for p in filePaths)
        return True

    def ExportStills(
        self, galleryStill: list[FakeStill], folderPath: str, filePrefix: str, format: str
    ) -> bool:
        injected, value = self._rec("ExportStills", galleryStill, folderPath, filePrefix, format)
        if injected:
            return bool(value)
        folder = Path(folderPath)
        if not galleryStill or not folder.is_dir() or format not in STILL_FORMATS:
            return False
        if any(still not in self.stills for still in galleryStill):
            return False
        for still in galleryStill:
            number = self.stills.index(still) + 1
            target = folder / f"{filePrefix}_{number}.{format}"
            target.write_text(f"fake still: {still.label}\n", encoding="utf-8")
        return True

    def DeleteStills(self, galleryStill: list[FakeStill]) -> bool:
        injected, value = self._rec("DeleteStills", galleryStill)
        if injected:
            return bool(value)
        if not galleryStill or any(still not in self.stills for still in galleryStill):
            return False
        for still in galleryStill:
            self.stills.remove(still)
        return True


class FakeGallery(FakeBase):
    CLASS = "Gallery"

    def __init__(self, world: FakeWorld) -> None:
        super().__init__(world)
        self.still_albums = [FakeAlbum(world, "Stills 1")]
        self.power_albums = [FakeAlbum(world, "PowerGrade 1")]
        self.current = self.still_albums[0]

    def _owns(self, album: FakeAlbum) -> bool:
        return album in self.still_albums or album in self.power_albums

    def GetAlbumName(self, galleryStillAlbum: FakeAlbum) -> str:
        return self._ret("GetAlbumName", galleryStillAlbum.name, galleryStillAlbum)

    def SetAlbumName(self, galleryStillAlbum: FakeAlbum, albumName: str) -> bool:
        injected, value = self._rec("SetAlbumName", galleryStillAlbum, albumName)
        if injected:
            return bool(value)
        if not albumName or not self._owns(galleryStillAlbum):
            return False
        galleryStillAlbum.name = albumName
        return True

    def GetCurrentStillAlbum(self) -> FakeAlbum:
        return self._ret("GetCurrentStillAlbum", self.current)

    def SetCurrentStillAlbum(self, galleryStillAlbum: FakeAlbum) -> bool:
        injected, value = self._rec("SetCurrentStillAlbum", galleryStillAlbum)
        if injected:
            return bool(value)
        if not self._owns(galleryStillAlbum):
            return False
        self.current = galleryStillAlbum
        return True

    def GetGalleryStillAlbums(self) -> list[FakeAlbum]:
        return self._ret("GetGalleryStillAlbums", list(self.still_albums))

    def GetGalleryPowerGradeAlbums(self) -> list[FakeAlbum]:
        return self._ret("GetGalleryPowerGradeAlbums", list(self.power_albums))

    def CreateGalleryStillAlbum(self) -> FakeAlbum | None:
        injected, value = self._rec("CreateGalleryStillAlbum")
        if injected:
            return value if isinstance(value, FakeAlbum) else None
        album = FakeAlbum(self._world, f"Stills {len(self.still_albums) + 1}")
        self.still_albums.append(album)
        return album

    def CreateGalleryPowerGradeAlbum(self) -> FakeAlbum | None:
        injected, value = self._rec("CreateGalleryPowerGradeAlbum")
        if injected:
            return value if isinstance(value, FakeAlbum) else None
        album = FakeAlbum(self._world, f"PowerGrade {len(self.power_albums) + 1}")
        self.power_albums.append(album)
        return album


def _gallery(project: FakeProject | None) -> FakeGallery | None:
    if project is None:
        return None
    if "gallery" not in project.__dict__:
        project.__dict__["gallery"] = FakeGallery(project._world)
    gallery: FakeGallery = project.__dict__["gallery"]
    return gallery


def _project_get_gallery(self: FakeProject) -> FakeGallery | None:
    return self._ret("GetGallery", _gallery(self))


def _resolve_get_gallery(self: FakeResolve) -> FakeGallery | None:
    return self._ret("GetGallery", _gallery(self.pm.current_project))


def _on_color_page(self: FakeTimeline) -> bool:
    return bool(self._world.resolve.page == "color")


def _grab_still(self: FakeTimeline) -> FakeStill | None:
    injected, value = self._rec("GrabStill")
    if injected:
        return value if isinstance(value, FakeStill) else None
    gallery = _gallery(self._world.resolve.pm.current_project)
    item = self.current_video_item
    if gallery is None or item is None or not _on_color_page(self):
        return None
    still = FakeStill(item.name)
    gallery.current.stills.append(still)
    return still


def _grab_all_stills(self: FakeTimeline, stillFrameSource: int) -> list[FakeStill] | None:
    injected, value = self._rec("GrabAllStills", stillFrameSource)
    if injected:
        return value if isinstance(value, list) else None
    gallery = _gallery(self._world.resolve.pm.current_project)
    if gallery is None or stillFrameSource not in (1, 2) or not _on_color_page(self):
        return None
    where = "first" if stillFrameSource == 1 else "middle"
    grabbed = [FakeStill(f"{i.name} {where}") for t in self.tracks["video"] for i in t.items]
    gallery.current.stills.extend(grabbed)
    return grabbed


def set_thumbnail_settle(world: FakeWorld, reads: int) -> None:
    """Make the first `reads` Color-page thumbnail reads return nothing (B1 live lag)."""
    world.__dict__["thumbnail_settle"] = reads


def _thumbnail(self: FakeTimeline) -> dict[str, Any] | None:
    injected, value = self._rec("GetCurrentClipThumbnailImage")
    if injected:
        return value if isinstance(value, dict) else None
    if not _on_color_page(self):
        return None
    lag = int(self._world.__dict__.get("thumbnail_settle", 0))
    if lag > 0:
        self._world.__dict__["thumbnail_settle"] = lag - 1
        return None
    return {"width": 1, "height": 1, "format": "RGB 8 bit", "data": PNG_1X1}


def _export_lut(self: FakeTimelineItem, exportType: Any, path: str) -> bool:
    injected, value = self._rec("ExportLUT", exportType, path)
    if injected:
        return bool(value)
    known = {getattr(self._world.resolve, f"EXPORT_LUT_{n}") for n in LUT_ENUMS}
    if exportType not in known or not Path(path).parent.is_dir():
        return False
    Path(path).write_text(f"fake lut: {self.name}\n", encoding="utf-8")
    return True


def _refresh_lut_list(self: FakeProject) -> bool:
    injected, value = self._rec("RefreshLUTList")
    return bool(value) if injected else True


_METHODS: dict[tuple[type[Any], str], Callable[..., Any]] = {
    (FakeResolve, "GetGallery"): _resolve_get_gallery,
    (FakeProject, "GetGallery"): _project_get_gallery,
    (FakeProject, "RefreshLUTList"): _refresh_lut_list,
    (FakeTimeline, "GrabStill"): _grab_still,
    (FakeTimeline, "GrabAllStills"): _grab_all_stills,
    (FakeTimeline, "GetCurrentClipThumbnailImage"): _thumbnail,
    (FakeTimelineItem, "ExportLUT"): _export_lut,
}


def extend() -> None:
    """Add the gallery, still, thumbnail and LUT methods and the `EXPORT_LUT_*` constants."""
    for (cls, name), method in _METHODS.items():
        setattr(cls, name, method)
    for suffix, number in LUT_ENUMS.items():
        setattr(FakeResolve, f"EXPORT_LUT_{suffix}", number)


__all__ = [
    "LUT_ENUMS",
    "PNG_1X1",
    "STILL_FORMATS",
    "FakeAlbum",
    "FakeGallery",
    "FakeStill",
    "extend",
]
