"""Paging over asset stores (WP-5.5)."""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from app.runtime.pagination import MAX_PAGE_SIZE, decode_cursor, encode_cursor, page_limit

from .models import AssetListResponse, AssetRecord

# Safety stop for callers that walk a whole collection.
MAX_ITERATED_PAGES = 10_000


def _type_value(asset: AssetRecord) -> str:
    return asset.type.value if hasattr(asset.type, "value") else str(asset.type)


def paginate_assets(
    assets: Iterable[AssetRecord],
    *,
    asset_type: str | None = None,
    modules: tuple[str, ...] | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> AssetListResponse:
    """The page an in-memory store returns, in the database store's order."""
    size = page_limit(limit)
    before = decode_cursor(cursor, arity=2)
    selected = sorted(
        (
            asset
            for asset in assets
            if (asset_type is None or _type_value(asset) == asset_type)
            and (not modules or asset.module in modules)
        ),
        key=lambda asset: (asset.created_at, asset.id),
        reverse=True,
    )
    if before is not None:
        selected = [asset for asset in selected if (asset.created_at, asset.id) < (before[0], before[1])]
    page = selected[:size]
    has_more = len(selected) > size
    return AssetListResponse(
        assets=page,
        next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if has_more else None,
        has_more=has_more,
    )


def iter_assets(
    store: Any,
    *,
    asset_type: str | None = None,
    modules: tuple[str, ...] | None = None,
) -> Iterator[AssetRecord]:
    """Every matching asset, newest first, one page at a time."""
    cursor: str | None = None
    for _ in range(MAX_ITERATED_PAGES):
        page = store.list_assets(asset_type=asset_type, modules=modules, limit=MAX_PAGE_SIZE, cursor=cursor)
        yield from page.assets
        if not page.has_more or not page.next_cursor:
            return
        cursor = page.next_cursor
    raise RuntimeError("asset iteration exceeded its page limit")


__all__ = ["iter_assets", "paginate_assets"]
