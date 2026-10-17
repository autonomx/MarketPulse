from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


ROLE_TO_PROVIDER: Dict[str, Dict[str, Any]] = {
    "innkeeper": {
        "provider_type": "service",
        "menu_ids": ["inn"],
    },
    "merchant": {
        "provider_type": "shop",
        "menu_ids": ["general_store"],
    },
    "shopkeeper": {
        "provider_type": "shop",
        "menu_ids": ["general_store"],
    },
    "weaponsmith": {
        "provider_type": "shop",
        "menu_ids": ["weaponsmith"],
    },
    "blacksmith": {
        "provider_type": "service_and_shop",
        "menu_ids": ["repair", "weaponsmith"],
    },
    "alchemist": {
        "provider_type": "shop",
        "menu_ids": ["alchemist"],
    },
    "caravan_master": {
        "provider_type": "service",
        "menu_ids": ["travel"],
    },
    "stablemaster": {
        "provider_type": "service",
        "menu_ids": ["travel"],
    },
}


PROFESSION_TO_PROVIDER: Dict[str, Dict[str, Any]] = {
    "innkeeper": {
        "provider_type": "service",
        "menu_ids": ["inn"],
    },
    "merchant": {
        "provider_type": "shop",
        "menu_ids": ["general_store"],
    },
    "weaponsmith": {
        "provider_type": "shop",
        "menu_ids": ["weaponsmith"],
    },
    "blacksmith": {
        "provider_type": "service_and_shop",
        "menu_ids": ["repair", "weaponsmith"],
    },
    "alchemist": {
        "provider_type": "shop",
        "menu_ids": ["alchemist"],
    },
    "caravan_master": {
        "provider_type": "service",
        "menu_ids": ["travel"],
    },
}


LOCATION_TO_PROVIDER: Dict[str, Dict[str, Any]] = {
    "inn": {
        "provider_type": "service",
        "menu_ids": ["inn"],
    },
    "tavern": {
        "provider_type": "service",
        "menu_ids": ["inn"],
    },
    "general_store": {
        "provider_type": "shop",
        "menu_ids": ["general_store"],
    },
    "weaponsmith": {
        "provider_type": "shop",
        "menu_ids": ["weaponsmith"],
    },
    "alchemy_shop": {
        "provider_type": "shop",
        "menu_ids": ["alchemist"],
    },
    "repair_shop": {
        "provider_type": "service",
        "menu_ids": ["repair"],
    },
    "stables": {
        "provider_type": "service",
        "menu_ids": ["travel"],
    },
}


def _provider_record(
    provider_id: str,
    provider_name: str,
    provider_kind: str,
    menu_ids: List[str],
    source: str,
) -> Dict[str, Any]:
    return {
        "provider_id": _safe_str(provider_id),
        "provider_name": _safe_str(provider_name),
        "provider_kind": _safe_str(provider_kind),
        "menu_ids": [str(x) for x in list(menu_ids or [])[:8]],
        "source": _safe_str(source),
    }


def derive_npc_transaction_providers(npcs: List[Any]) -> List[Dict[str, Any]]:
    providers: List[Dict[str, Any]] = []
    seen = set()

    for raw_npc in _safe_list(npcs)[:32]:
        npc = _safe_dict(raw_npc)
        npc_id = _safe_str(npc.get("id") or npc.get("npc_id"))
        npc_name = _safe_str(npc.get("name"))
        role = _safe_str(npc.get("role")).lower()
        profession = _safe_str(npc.get("profession")).lower()
        location_type = _safe_str(npc.get("location_type")).lower()

        matched = None
        source = ""

        if role and role in ROLE_TO_PROVIDER:
            matched = _safe_dict(ROLE_TO_PROVIDER.get(role))
            source = "role"
        elif profession and profession in PROFESSION_TO_PROVIDER:
            matched = _safe_dict(PROFESSION_TO_PROVIDER.get(profession))
            source = "profession"
        elif location_type and location_type in LOCATION_TO_PROVIDER:
            matched = _safe_dict(LOCATION_TO_PROVIDER.get(location_type))
            source = "location_type"

        if not matched:
            continue

        menu_ids = list(matched.get("menu_ids") or [])
        provider_kind = _safe_str(matched.get("provider_type"))
        dedupe_key = (npc_id, tuple(menu_ids))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        providers.append(_provider_record(
            provider_id=npc_id or npc_name.lower().replace(" ", "_"),
            provider_name=npc_name or "Unknown Provider",
            provider_kind=provider_kind,
            menu_ids=menu_ids,
            source=source,
        ))

    return providers[:24]


def derive_world_transaction_providers(world_entities: List[Any]) -> List[Dict[str, Any]]:
    providers: List[Dict[str, Any]] = []
    seen = set()

    for raw_entity in _safe_list(world_entities)[:32]:
        entity = _safe_dict(raw_entity)
        entity_id = _safe_str(entity.get("id") or entity.get("entity_id"))
        entity_name = _safe_str(entity.get("name"))
        entity_type = _safe_str(entity.get("entity_type") or entity.get("type")).lower()
        location_type = _safe_str(entity.get("location_type")).lower()

        matched = None
        source = ""

        if entity_type and entity_type in LOCATION_TO_PROVIDER:
            matched = _safe_dict(LOCATION_TO_PROVIDER.get(entity_type))
            source = "entity_type"
        elif location_type and location_type in LOCATION_TO_PROVIDER:
            matched = _safe_dict(LOCATION_TO_PROVIDER.get(location_type))
            source = "location_type"

        if not matched:
            continue

        menu_ids = list(matched.get("menu_ids") or [])
        provider_kind = _safe_str(matched.get("provider_type"))
        dedupe_key = (entity_id, tuple(menu_ids))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        providers.append(_provider_record(
            provider_id=entity_id or entity_name.lower().replace(" ", "_"),
            provider_name=entity_name or "Location",
            provider_kind=provider_kind,
            menu_ids=menu_ids,
            source=source,
        ))

    return providers[:24]
