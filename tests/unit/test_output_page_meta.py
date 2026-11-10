"""`page_meta`: `total` always, `truncated: true` only when a limit cut the list."""

from __future__ import annotations

import pytest

from davinci_axi.output import apply_limit, page_meta


@pytest.mark.parametrize(
    ("shown", "total", "expected"),
    [
        (2, 6, {"total": 6, "truncated": True}),
        (6, 6, {"total": 6}),
        (0, 0, {"total": 0}),
        (0, 3, {"total": 3, "truncated": True}),
    ],
)
def test_page_meta(shown: int, total: int, expected: dict[str, object]) -> None:
    assert page_meta(shown, total) == expected


def test_page_meta_follows_apply_limit() -> None:
    page, total = apply_limit(list(range(10)), 4)
    assert page_meta(len(page), total) == {"total": 10, "truncated": True}
    page, total = apply_limit(list(range(3)), 4)
    assert page_meta(len(page), total) == {"total": 3}
