"""Source metadata helpers for genesis-backed RPG sessions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

WIZARD_SOURCE_KIND = "wizard_v2"
LEGACY_SOURCE_KIND = "legacy_v1"
WIZARD_SOURCE_NAME = "rpg_create_campaign_wizard"
LEGACY_SOURCE_NAME = "legacy_new_game_payload"
DEFAULT_SOURCE_BUILD = "657"


class GenesisSourceInfo(BaseModel):
    """Stable metadata describing which source emitted a genesis contract."""

    source_kind: str = WIZARD_SOURCE_KIND
    source_name: str = WIZARD_SOURCE_NAME
    source_build: str = DEFAULT_SOURCE_BUILD


def genesis_source_info(
    *,
    source_kind: str = WIZARD_SOURCE_KIND,
    source_name: str = WIZARD_SOURCE_NAME,
    source_build: str = DEFAULT_SOURCE_BUILD,
) -> GenesisSourceInfo:
    return GenesisSourceInfo(
        source_kind=source_kind,
        source_name=source_name,
        source_build=source_build,
    )


def source_payload(info: GenesisSourceInfo) -> dict[str, Any]:
    return info.model_dump(mode="json")


def wizard_source_payload() -> dict[str, Any]:
    return source_payload(genesis_source_info())
