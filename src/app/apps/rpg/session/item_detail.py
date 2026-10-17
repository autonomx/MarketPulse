"""Read-only RPG inventory item details with persisted presentation-only LLM prose."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any, cast

from app.persistence.database import default_database
from app.security.tenant_context import current_tenant
from app.apps.rpg.foundation.persistence.rpg_item_description_repository import (
    PostgresRpgItemDescriptionRepository,
)
from app.apps.rpg.foundation.persistence.rpg_repository import canonical_json
from app.apps.rpg.foundation.llm_app_gateway import build_app_llm_gateway
from app.apps.rpg.session.inventory_items import (
    canonical_item_id,
    display_item_name,
    inventory_quantity,
    item_type,
    normalize_inventory_items,
)
from app.apps.rpg.session.item_descriptions import build_item_description_context
from app.prompts import prompt_template
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

_PROMPT_1 = prompt_template('rpg.session_item_detail.prompt', "1", (
    "Write a medium-detail lore description for this RPG inventory item in exactly three sentences, "
        "roughly 45 to 80 words. Ground its materials, construction, wear, visual character, and common cultural "
        "use in the supplied campaign genre and setting. You may add low-stakes setting-consistent descriptive "
        "fiction, but do not invent unique provenance, named makers, quest significance, hidden secrets, magical "
        "properties, effects, values, rarity, or mechanics. Do not mention inventory quantity, stackability, current "
        "equipment status, condition data, missing fields, UI, or game systems. Return immersive prose only."
))

ITEM_DETAIL_SOURCE = "rpg_item_detail_v1"
GENERIC_GENRES = {"", "deterministic_rpg_campaign", "rpg_campaign", "default"}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _title(value: Any, fallback: str) -> str:
    text = _text(value).replace("_", " ").replace("-", " ")
    return text.title() if text else fallback


def _inventory(state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    player = _safe_dict(state.get("player"))
    raw_inventory = player.get("inventory")
    if isinstance(raw_inventory, dict):
        raw_inventory = raw_inventory.get("items")
    if not isinstance(raw_inventory, list):
        raw_inventory = state.get("inventory")
    if isinstance(raw_inventory, dict):
        raw_inventory = raw_inventory.get("items")
    normalized, _trace = normalize_inventory_items(deepcopy(_safe_list(raw_inventory)))
    return player, normalized


def _find_item(items: list[dict[str, Any]], requested_name: str) -> dict[str, Any] | None:
    wanted = requested_name.casefold().strip()
    if not wanted:
        return None
    for item in items:
        names = {
            display_item_name(item).casefold(),
            canonical_item_id(item).casefold(),
            _text(item.get("id")).casefold(),
            _text(item.get("item_id")).casefold(),
        }
        if wanted in names:
            return item
    for item in items:
        if wanted in display_item_name(item).casefold():
            return item
    return None


def _equipment_references(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [reference for item in value for reference in _equipment_references(item)]
    if isinstance(value, dict):
        references: list[str] = []
        for key, item in value.items():
            if key in {"name", "label", "id", "item_id", "instance_id"}:
                references.append(_text(item))
            else:
                references.extend(_equipment_references(item))
        return references
    return []


def _item_status(player: dict[str, Any], item: dict[str, Any]) -> str:
    explicit = _text(item.get("status") or item.get("state"))
    if explicit:
        return _title(explicit, "Carried")
    if item.get("equipped") is True:
        return "Equipped"
    item_names = {
        display_item_name(item).casefold(),
        canonical_item_id(item).casefold(),
        _text(item.get("instance_id")).casefold(),
    }
    equipped = player.get("equipment") or player.get("equipped") or player.get("gear")
    if any(reference.casefold() in item_names for reference in _equipment_references(equipped)):
        return "Equipped"
    return "Carried"


def _item_condition(item: dict[str, Any]) -> str:
    condition = item.get("condition")
    if isinstance(condition, dict):
        explicit = _text(condition.get("label") or condition.get("status") or condition.get("state"))
    else:
        explicit = _text(condition)
    if explicit:
        return _title(explicit, "Not recorded")

    durability = item.get("durability")
    if isinstance(durability, dict):
        current = durability.get("current") if "current" in durability else durability.get("value")
        maximum = durability.get("max") if "max" in durability else durability.get("maximum")
    else:
        current = item.get("durability_current") if "durability_current" in item else durability
        maximum = item.get("durability_max") if "durability_max" in item else item.get("max_durability")
    if isinstance(current, (int, float)) and isinstance(maximum, (int, float)) and maximum > 0:
        percent = max(0, min(100, round(float(current) / float(maximum) * 100)))
        label = "Pristine" if percent >= 90 else "Good" if percent >= 70 else "Worn" if percent >= 40 else "Damaged" if percent > 0 else "Broken"
        return f"{label} ({percent}%)"
    if isinstance(current, (int, float)):
        return f"{current:g} durability"
    return "Not recorded"


def _item_tags(item: dict[str, Any]) -> list[str]:
    tags = [_text(tag) for tag in _safe_list(item.get("tags")) if _text(tag)]
    normalized_type = _title(item_type(item), "Item")
    return list(dict.fromkeys([normalized_type, *tags]))[:5]


def _session_genre(state: dict[str, Any], requested_genre: str | None) -> str:
    metadata = _safe_dict(state.get("metadata"))
    identity = _safe_dict(state.get("character_identity"))
    raw_genre = _text(
        requested_genre
        or metadata.get("genre")
        or identity.get("genre")
        or metadata.get("campaign_template"),
        "setting_defined_rpg",
    )
    if raw_genre.casefold() not in GENERIC_GENRES:
        return raw_genre

    clues = " ".join(
        [
            _text(state.get("location") or state.get("current_location")),
            _text(metadata.get("origin")),
            " ".join(_text(item) for item in _safe_list(metadata.get("starter_gear"))),
        ]
    ).casefold()
    if any(token in clues for token in ("tavern", "cloak", "dagger", "silver", "village", "torch")):
        return "classic_fantasy"
    if any(token in clues for token in ("starship", "space", "laser", "cyber", "station")):
        return "science_fiction"
    if any(token in clues for token in ("noir", "detective", "city", "firearm")):
        return "modern_mystery"
    return "setting_defined_rpg"


def _setting_context(state: dict[str, Any], genre: str) -> dict[str, Any]:
    metadata = _safe_dict(state.get("metadata"))
    identity = _safe_dict(state.get("character_identity"))
    world = _safe_dict(state.get("world"))
    environment = _safe_dict(world.get("environment"))
    genesis = _safe_dict(state.get("genesis_snapshot"))
    story_options = _safe_dict(genesis.get("story_options"))
    return {
        "genre": genre,
        "campaign_template": _text(metadata.get("campaign_template")),
        "tone": _text(metadata.get("tone") or identity.get("tone")),
        "location": _text(state.get("location") or state.get("current_location")),
        "origin": _text(metadata.get("origin") or identity.get("origin")),
        "character_background": _text(identity.get("background")),
        "power_source": _text(identity.get("power_source") or metadata.get("power_source")),
        "opening_hook": _text(metadata.get("opening_hook") or story_options.get("opening_hook")),
        "climate_profile": _text(environment.get("climate_profile_id")),
        "starter_gear": [_text(item) for item in _safe_list(metadata.get("starter_gear"))[:8] if _text(item)],
    }


def _lore_item_context(item: dict[str, Any], description_context: dict[str, Any]) -> dict[str, Any]:
    display = _safe_dict(description_context.get("current_display"))
    context: dict[str, Any] = {
        "name": display_item_name(item),
        "item_type": _title(item_type(item), "Item"),
        "tags": _item_tags(item),
    }
    for key in ("material", "quality", "rarity", "craftsmanship"):
        value = _text(item.get(key))
        if value:
            context[key] = value
    for key in ("description", "flavor_text"):
        value = _text(display.get(key))
        if value:
            context[f"existing_{key}"] = value
    flavor_tags = [_text(tag) for tag in _safe_list(display.get("flavor_tags")) if _text(tag)]
    if flavor_tags:
        context["flavor_tags"] = flavor_tags[:6]
    return context


def _detail_payload(item_name: str, item: dict[str, Any] | None, *, summary: str, source: str) -> dict[str, Any]:
    if not item:
        return {
            "item_name": item_name,
            "summary": summary,
            "status": "Not found",
            "condition": "Not recorded",
            "tags": [],
            "source": source,
        }
    return {
        "item_name": display_item_name(item),
        "summary": summary,
        "status": _text(item.get("status_label"), ""),
        "condition": _item_condition(item),
        "item_type": _title(item_type(item), "Item"),
        "quantity": inventory_quantity(item),
        "tags": _item_tags(item),
        "source": source,
    }


def _description_database_context() -> tuple[Any, Any]:
    database = default_database()
    context = current_tenant()
    return database, context


def _description_key(
    item: dict[str, Any],
    lore_item: dict[str, Any],
    setting: dict[str, Any],
) -> tuple[str, str, str]:
    item_key = canonical_item_id(item) or display_item_name(item).casefold().replace(" ", "_")
    encoded = canonical_json(
        {
            "prompt_version": ITEM_DETAIL_SOURCE,
            "item_key": item_key,
            "item": lore_item,
            "setting": setting,
        }
    )
    context_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return context_hash, context_hash, item_key


def _read_persisted_description(description_key: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        database, context = _description_database_context()
        with database.transaction() as connection:
            row = PostgresRpgItemDescriptionRepository(connection).get(
                context,
                description_key,
            )
        return row, None
    except Exception as exc:  # PostgreSQL availability must not hide the item UI.
        return None, type(exc).__name__


def _persist_description(
    *,
    description_key: str,
    context_hash: str,
    item_key: str,
    item_name: str,
    genre: str,
    summary: str,
    metadata: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        database, context = _description_database_context()
        with database.transaction() as connection:
            row = PostgresRpgItemDescriptionRepository(connection).put(
                context,
                description_key=description_key,
                item_key=item_key,
                item_name=item_name,
                genre=genre,
                context_hash=context_hash,
                summary=summary,
                source="llm",
                metadata=metadata,
            )
        return row, None
    except Exception as exc:  # Generation remains usable while persistence is repaired.
        return None, type(exc).__name__


def generate_item_detail(
    state: dict[str, Any],
    item_name: str,
    *,
    genre: str | None = None,
    llm_gateway: Any | None = None,
) -> dict[str, Any]:
    """Read a cached item description or generate and persist it once."""

    player, items = _inventory(_safe_dict(state))
    item = _find_item(items, item_name)
    if item is None:
        return {
            "ok": False,
            "error": "item_not_found",
            "item_detail": _detail_payload(
                item_name,
                None,
                summary="This item is not present in the selected session inventory.",
                source="unavailable",
            ),
            "mechanics_source": ITEM_DETAIL_SOURCE,
        }

    resolved_genre = _session_genre(state, genre)
    context = build_item_description_context(item, genre=resolved_genre)
    facts = {
        "name": display_item_name(item),
        "status": _item_status(player, item),
        "condition": _item_condition(item),
        "item_type": _title(item_type(item), "Item"),
        "quantity": inventory_quantity(item),
        "tags": _item_tags(item),
    }
    lore_item = _lore_item_context(item, context)
    setting = _setting_context(state, resolved_genre)
    description_key, context_hash, item_key = _description_key(item, lore_item, setting)
    cached, cache_error = _read_persisted_description(description_key)
    if cached is not None:
        detail = _detail_payload(
            item_name,
            item,
            summary=_text(cached.get("summary")),
            source="postgresql_cache",
        )
        detail["status"] = facts["status"]
        return {
            "ok": True,
            "item_detail": detail,
            "mechanics_source": ITEM_DETAIL_SOURCE,
            "description_persistence": {
                "backend": "postgresql",
                "status": "stored",
                "cache_hit": True,
                "description_key": description_key,
            },
        }

    gateway = llm_gateway or build_app_llm_gateway()
    if gateway is None:
        detail = _detail_payload(
            item_name,
            item,
            summary="An LLM provider is not available to describe this item.",
            source="unavailable",
        )
        detail["status"] = facts["status"]
        return {
            "ok": False,
            "error": "item_detail_llm_unavailable",
            "item_detail": detail,
            "mechanics_source": ITEM_DETAIL_SOURCE,
            "description_persistence": {
                "backend": "postgresql",
                "status": "cache_miss" if cache_error is None else "unavailable",
                "cache_hit": False,
                "error": cache_error,
            },
        }

    prompt = (
        _PROMPT_1.text
    )
    try:
        summary = _text(
            gateway.generate(
                prompt,
                context={"item": lore_item, "setting": setting},
                timeout_s=20.0,
            )
        )
    except Exception:
        summary = ""

    if not summary:
        detail = _detail_payload(
            item_name,
            item,
            summary="The configured LLM did not return an item description.",
            source="unavailable",
        )
        detail["status"] = facts["status"]
        return {
            "ok": False,
            "error": "item_detail_llm_failed",
            "item_detail": detail,
            "mechanics_source": ITEM_DETAIL_SOURCE,
            "description_persistence": {
                "backend": "postgresql",
                "status": "cache_miss" if cache_error is None else "unavailable",
                "cache_hit": False,
                "error": cache_error,
            },
        }

    persisted, persistence_error = _persist_description(
        description_key=description_key,
        context_hash=context_hash,
        item_key=item_key,
        item_name=cast(str, facts["name"]),
        genre=resolved_genre,
        summary=summary,
        metadata={
            "prompt_version": ITEM_DETAIL_SOURCE,
            "item_type": facts["item_type"],
            "tags": facts["tags"],
        },
    )
    detail = _detail_payload(item_name, item, summary=summary, source="llm")
    detail["status"] = facts["status"]
    return {
        "ok": True,
        "item_detail": detail,
        "mechanics_source": ITEM_DETAIL_SOURCE,
        "description_persistence": {
            "backend": "postgresql",
            "status": "stored" if persisted is not None else "unavailable",
            "cache_hit": False,
            "description_key": description_key,
            "error": persistence_error,
        },
    }
