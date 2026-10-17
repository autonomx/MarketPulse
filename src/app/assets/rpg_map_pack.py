"""Read-only curated RPG map asset pack exposed through the shared asset API."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .models import AssetRecord, AssetType

_PACK_ROOT = Path(__file__).resolve().parent / "rpg_map_pack"

_ASSET_FILES: dict[str, str] = {
    "asset:rpg-map:frost-haven-base": "settlement-background.svg",
    "asset:rpg-map:northern-pass-base": "region-background.svg",
    "asset:rpg-map:frosted-flagon-interior-base": "interior-background.svg",
    "asset:rpg-map:timber-inn-01": "timber-building.svg",
    "asset:rpg-map:stone-building-01": "stone-building.svg",
    "asset:rpg-map:smithy-01": "stone-building.svg",
    "asset:rpg-map:market-hall-01": "timber-building.svg",
    "asset:rpg-map:shrine-01": "stone-building.svg",
    "asset:rpg-map:keep-01": "stone-building.svg",
    "asset:rpg-map:storehouse-01": "timber-building.svg",
    "asset:rpg-map:healer-01": "timber-building.svg",
    "asset:rpg-map:well-01": "landmark.svg",
    "asset:rpg-map:gate-wood-01": "gate.svg",
    "asset:rpg-map:gate-stone-01": "gate.svg",
    "asset:rpg-map:watchtower-01": "tower.svg",
    "asset:rpg-map:caravan-yard-01": "timber-building.svg",
    "asset:rpg-map:settlement-marker-01": "landmark.svg",
    "asset:rpg-map:ancient-arch-01": "gate.svg",
    "asset:rpg-map:quarry-01": "landmark.svg",
    "asset:rpg-map:hamlet-marker-01": "landmark.svg",
    "asset:rpg-map:interior-flagon-entry": "interior-prop.svg",
    "asset:rpg-map:interior-flagon-counter": "interior-prop.svg",
    "asset:rpg-map:interior-flagon-hearth": "interior-prop.svg",
    "asset:rpg-map:interior-flagon-tables": "interior-prop.svg",
    "asset:rpg-map:interior-flagon-stairs": "interior-prop.svg",
}


def curated_rpg_map_assets() -> tuple[AssetRecord, ...]:
    records = []
    for asset_id, filename in sorted(_ASSET_FILES.items()):
        path = _PACK_ROOT / filename
        records.append(
            AssetRecord(
                id=asset_id,
                module="rpg",
                type=AssetType.IMAGE,
                mime_type="image/svg+xml",
                storage_path=str(path),
                metadata={
                    "pack": "rpg-map-curated-v1",
                    "immutable": True,
                    "logical_role": _logical_role(filename),
                    "source": "repository_curated",
                    "trusted_svg": True,
                },
                created_at=_created_at(path),
                compat={"asset_pack_version": 1, "alias_file": filename},
            )
        )
    return tuple(records)


def curated_rpg_map_asset_ids() -> tuple[str, ...]:
    return tuple(sorted(_ASSET_FILES))


def _logical_role(filename: str) -> str:
    if "background" in filename:
        return "background"
    if "gate" in filename or "tower" in filename or "landmark" in filename:
        return "landmark"
    if "prop" in filename:
        return "interior_prop"
    return "structure"


def _created_at(path: Path) -> str:
    try:
        timestamp = path.stat().st_mtime
    except OSError:
        timestamp = 0
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
