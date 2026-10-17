from __future__ import annotations

from typing import Any

from app.apps.rpg.foundation.compat.character_cards import import_external_character_card
from app.apps.rpg.narration.presentation.visual_state import ensure_visual_state
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

_MAX_PACKS = 32
_MAX_PACK_CHARACTERS = 64


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _safe_str(value).strip()
        if text:
            return text
    return ""


def _normalize_pack_manifest(value: Any) -> dict[str, Any]:
    data = _safe_dict(value)
    return {
        "id": _safe_str(data.get("id")).strip(),
        "title": _safe_str(data.get("title")).strip(),
        "description": _safe_str(data.get("description")).strip(),
        "author": _safe_str(data.get("author")).strip(),
        "version": _first_non_empty(data.get("version"), "1.0"),
        "pack_type": _first_non_empty(data.get("pack_type"), "mixed"),
    }


def _normalize_content_pack(value: Any) -> dict[str, Any]:
    data = _safe_dict(value)
    return {
        "manifest": _normalize_pack_manifest(data.get("manifest")),
        "characters": [
            _safe_dict(item) for item in _safe_list(data.get("characters")) if isinstance(item, dict)
        ][:_MAX_PACK_CHARACTERS],
        "scenario": _safe_dict(data.get("scenario")),
        "world_seed": _safe_dict(data.get("world_seed")),
        "visual_defaults": _safe_dict(data.get("visual_defaults")),
    }


def ensure_content_pack_state(simulation_state: dict[str, Any]) -> dict[str, Any]:
    simulation_state = ensure_visual_state(_safe_dict(simulation_state))
    presentation_state = simulation_state.get("presentation_state")
    if not isinstance(presentation_state, dict):
        presentation_state = {}
        simulation_state["presentation_state"] = presentation_state

    modding_state = presentation_state.get("modding_state")
    if not isinstance(modding_state, dict):
        modding_state = {}
        presentation_state["modding_state"] = modding_state

    packs_in = _safe_list(modding_state.get("installed_packs"))
    packs_out = [
        _normalize_content_pack(item)
        for item in packs_in
        if isinstance(item, dict)
    ]
    packs_out = sorted(
        packs_out,
        key=lambda item: (
            _safe_str(_safe_dict(item.get("manifest")).get("title")).lower(),
            _safe_str(_safe_dict(item.get("manifest")).get("id")),
        ),
    )[:_MAX_PACKS]
    modding_state["installed_packs"] = packs_out
    return simulation_state


def add_content_pack(
    simulation_state: dict[str, Any],
    pack: dict[str, Any],
) -> dict[str, Any]:
    simulation_state = ensure_content_pack_state(simulation_state)
    presentation_state = simulation_state.get("presentation_state")
    if not isinstance(presentation_state, dict):
        presentation_state = {}
        simulation_state["presentation_state"] = presentation_state
    modding_state = presentation_state.get("modding_state")
    if not isinstance(modding_state, dict):
        modding_state = {}
        presentation_state["modding_state"] = modding_state

    packs = _safe_list(modding_state.get("installed_packs"))
    packs.append(_normalize_content_pack(pack))
    packs = sorted(
        [item for item in packs if isinstance(item, dict)],
        key=lambda item: (
            _safe_str(_safe_dict(item.get("manifest")).get("title")).lower(),
            _safe_str(_safe_dict(item.get("manifest")).get("id")),
        ),
    )[:_MAX_PACKS]
    modding_state["installed_packs"] = packs
    return simulation_state


def list_content_packs(simulation_state: dict[str, Any]) -> list[dict[str, Any]]:
    simulation_state = ensure_content_pack_state(simulation_state)
    presentation_state = simulation_state.get("presentation_state")
    if not isinstance(presentation_state, dict):
        return []
    modding_state = presentation_state.get("modding_state")
    if not isinstance(modding_state, dict):
        return []
    return _safe_list(modding_state.get("installed_packs"))


def build_pack_application_preview(pack: dict[str, Any]) -> dict[str, Any]:
    pack = _normalize_content_pack(pack)

    imported_characters = [
        import_external_character_card(card)
        for card in _safe_list(pack.get("characters"))
        if isinstance(card, dict)
    ][: _MAX_PACK_CHARACTERS]

    return {
        "manifest": _safe_dict(pack.get("manifest")),
        "character_count": len(imported_characters),
        "characters": imported_characters,
        "scenario": _safe_dict(pack.get("scenario")),
        "world_seed": _safe_dict(pack.get("world_seed")),
        "visual_defaults": _safe_dict(pack.get("visual_defaults")),
    }


def build_pack_bootstrap_payload(pack: dict[str, Any]) -> dict[str, Any]:
    """Build deterministic new-session bootstrap payload from a content pack."""
    preview = build_pack_application_preview(pack)
    manifest = _safe_dict(preview.get("manifest"))
    scenario = _safe_dict(preview.get("scenario"))
    world_seed = _safe_dict(preview.get("world_seed"))
    visual_defaults = _safe_dict(preview.get("visual_defaults"))
    characters = _safe_list(preview.get("characters"))
    # Normalize: sort by canonical name then format, cap at max
    characters = sorted(
        [item for item in characters if isinstance(item, dict)],
        key=lambda item: (
            _safe_str(_safe_dict(item.get("canonical_seed")).get("name")).lower(),
            _safe_str(_safe_dict(item.get("source_meta")).get("format")),
        ),
    )[:_MAX_PACK_CHARACTERS]

    setup = {
        "title": _first_non_empty(
            scenario.get("title"),
            manifest.get("title"),
            "New Adventure",
        ),
        "summary": _first_non_empty(
            scenario.get("summary"),
            manifest.get("description"),
        ),
        "opening": _safe_str(scenario.get("opening")).strip(),
        "world_seed": world_seed,
        "character_seeds": characters,
        "visual_defaults": visual_defaults,
        "source_pack": {
            "id": _safe_str(manifest.get("id")).strip(),
            "title": _safe_str(manifest.get("title")).strip(),
            "version": _safe_str(manifest.get("version")).strip(),
        },
    }

    return setup


def apply_content_pack(
    simulation_state: dict[str, Any],
    pack: dict[str, Any],
) -> dict[str, Any]:
    simulation_state = ensure_content_pack_state(simulation_state)
    pack = _normalize_content_pack(pack)

    simulation_state = add_content_pack(simulation_state, pack)

    presentation_state = simulation_state.get("presentation_state")
    if not isinstance(presentation_state, dict):
        presentation_state = {}
        simulation_state["presentation_state"] = presentation_state
    visual_state = presentation_state.get("visual_state")
    if not isinstance(visual_state, dict):
        visual_state = {}
        presentation_state["visual_state"] = visual_state
    defaults = _safe_dict(visual_state.get("defaults"))

    pack_visual_defaults = _safe_dict(pack.get("visual_defaults"))
    if pack_visual_defaults:
        defaults.update({
            key: value
            for key, value in pack_visual_defaults.items()
            if _safe_str(key).strip()
        })
        visual_state["defaults"] = defaults

    return simulation_state
