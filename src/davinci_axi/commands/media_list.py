"""`media` read view (R24, never writes): lists the clips of one media pool folder."""

from __future__ import annotations

from datetime import datetime

from davinci_axi.commands.item_detail import str_arg
from davinci_axi.commands.media_folders import (
    Found,
    folder_hint,
    open_pool,
    props,
    resolve_folder,
    show_hint,
    walk_folders,
)
from davinci_axi.errors import AxiError
from davinci_axi.output import apply_limit, empty_state, hint, page_meta, select_fields
from davinci_axi.spec import DEFAULT_LIMIT, Flag, Invocation, Result

DEFAULT_FIELDS = ("id", "name", "type", "duration", "folder")
EXTRA_FIELDS = (
    "fps",
    "path",
    "start_tc",
    "color",
    "flags",
    "resolution",
    "video_codec",
    "frames",
    "date_created",
    "audio_ch",
)
PROPERTY_FIELDS = {
    "resolution": "Resolution",
    "video_codec": "Video Codec",
    "frames": "Frames",
    "date_created": "Date Created",
    "audio_ch": "Audio Ch",
}
SORT_CHOICES = ("name", "date_created", "duration", "pool")
SORT_FLAG = Flag(
    "sort",
    "str",
    "name",
    choices=SORT_CHOICES,
    help="row order: name (default), date_created, duration, or pool for Resolve's order",
)
LIST_VIEW_FLAGS: tuple[Flag, ...] = (
    Flag(
        "folder",
        "str",
        None,
        help="folder path to list, such as Master/Footage (default: the root folder)",
    ),
    Flag("recursive", "bool", True, help="include sub folders (--no-recursive for one folder)"),
    SORT_FLAG,
)

# Resolve 21.1.1 writes "Sun Nov 8 2026 11:42:37"; the ctime and ISO forms are fallbacks.
DATE_FORMATS = (
    "%a %b %d %Y %H:%M:%S",
    "%a %b %d %H:%M:%S %Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)


def parse_date_created(text: object) -> datetime | None:
    """Parse a `Date Created` property; `None` when empty or in no known format."""
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(str(text or "").strip(), fmt)
        except ValueError:
            continue
    return None


def _duration_key(text: object) -> tuple[int, tuple[int, ...]]:
    parts = str(text or "").split(":")
    if not text or not all(p.isdigit() for p in parts):
        return (1, ())
    return (0, tuple(int(p) for p in parts))


def sort_found(found: list[Found], sort: str) -> list[Found]:
    """Order `found` by `sort`; `sorted` is stable, so ties and bad values keep pool order."""
    if sort == "name":
        return sorted(found, key=lambda f: (f.clip.GetName().casefold(), f.clip.GetName()))
    if sort == "duration":
        return sorted(found, key=lambda f: _duration_key(props(f.clip).get("Duration")))
    if sort == "date_created":
        return sorted(
            found,
            key=lambda f: parse_date_created(props(f.clip).get("Date Created")) or datetime.max,
        )
    return found


def _row(found: Found, extras: set[str]) -> dict[str, object]:
    clip, live = found.clip, props(found.clip)
    row: dict[str, object] = {
        "id": clip.GetUniqueId(),
        "name": clip.GetName(),
        "type": live.get("Type"),
        "duration": live.get("Duration"),
        "folder": found.folder_path,
    }
    if "fps" in extras:
        row["fps"] = live.get("FPS")
    if "path" in extras:
        row["path"] = live.get("File Path") or None
    if "start_tc" in extras:
        row["start_tc"] = live.get("Start TC")
    if "color" in extras:
        row["color"] = clip.GetClipColor() or None
    if "flags" in extras:
        row["flags"] = list(clip.GetFlagList() or [])
    for field, key in PROPERTY_FIELDS.items():
        if field in extras:
            value = live.get(key)
            is_count = field == "frames" and str(value).isdigit()
            row[field] = int(str(value)) if is_count else value or None
    return row


def run(inv: Invocation) -> Result:
    raw = inv.args.get("fields")
    requested = [str(f) for f in raw] if isinstance(raw, list) else None
    limit = inv.args.get("limit", DEFAULT_LIMIT)
    limit = limit if isinstance(limit, int) else DEFAULT_LIMIT
    select_fields([dict.fromkeys((*DEFAULT_FIELDS, *EXTRA_FIELDS))], DEFAULT_FIELDS, requested)
    apply_limit([], limit)
    folder_ref, recursive = str_arg(inv, "folder"), bool(inv.args.get("recursive"))
    sort = str(inv.args.get("sort") or "name")
    if sort not in SORT_CHOICES:
        raise AxiError(
            "VALIDATION_ERROR",
            f"invalid value {sort} for --sort",
            help=[f"Valid values for --sort: {', '.join(SORT_CHOICES)}"],
        )
    extras = {f for f in requested or [] if f in EXTRA_FIELDS}
    ctx = open_pool(inv)
    path, folder = resolve_folder(ctx, folder_ref) if folder_ref else (ctx.root.GetName(), ctx.root)
    scope = walk_folders(folder, path) if recursive else iter([(path, folder)])
    found = [Found(p, c) for p, f in scope for c in f.GetClipList() or []]
    shown, total = apply_limit(sort_found(found, sort), limit)
    children = [
        {
            "path": f"{path}/{child.GetName()}",
            "clips": len(child.GetClipList() or []),
            "subfolders": len(child.GetSubFolderList() or []),
        }
        for child in folder.GetSubFolderList() or []
    ]
    data: dict[str, object] = {
        "folder": path,
        "folder_stale": bool(folder.GetIsFolderStale()),
        "folders": children,
        **page_meta(len(shown), total),
        "sort": sort,
    }
    if shown:
        data["clips"] = select_fields([_row(f, extras) for f in shown], DEFAULT_FIELDS, requested)
    else:
        scope_text = f"in folder {path}"
        data["clips"] = empty_state("clips", scope_text, {} if recursive else {"recursive": "no"})
    lines: list[str] = []
    if len(shown) < total:
        lines.append(hint(f"media --limit {total}", f"list all {total} clips"))
    if shown:
        lines.append(show_hint(shown[:1]))
    if children:
        lines.append(folder_hint(str(children[0]["path"]), "list one sub folder"))
    return Result(data, lines)
