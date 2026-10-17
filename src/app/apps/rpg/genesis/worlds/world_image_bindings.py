"""Lightweight approved image binding lookup used during world publication."""
from __future__ import annotations

from typing import Any


def approved_world_asset_bindings(
    work: Any,
    context: Any,
    world_id: str,
) -> dict[str, Any]:
    """Return only approved active image assets without importing job services."""

    rows = work.connection.execute(
        "SELECT target_id, target_type, entity_id, role, source_content_hash, "
        "active_asset_id FROM omnix_rpg_world_image_targets WHERE workspace_id = %s "
        "AND world_id = %s AND review_state = 'approved' "
        "AND active_asset_id IS NOT NULL ORDER BY target_id",
        (context.workspace_id, world_id),
    ).fetchall()
    return {
        str(row[0]): {
            "target_type": str(row[1]),
            "entity_id": str(row[2]),
            "role": str(row[3]),
            "source_content_hash": str(row[4]),
            "asset_id": str(row[5]),
        }
        for row in rows
    }
