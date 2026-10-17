"""Pure projection of encountered NPC profiles into Campaign Bible canon."""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping, Sequence, cast

from app.apps.rpg.foundation.dynamic_npc_profiles import load_npc_profile
from app.apps.rpg.world.npc_biography_registry import get_npc_biography

from .campaign_lore_store import _mapping, _text, current_location_identity

_VISIBLE = {"public", "player_known", "learned", "partially_known", "disputed"}
_VISIBLE_STATUSES = {"public_at_campaign_start", "learned", "partially_known", "disputed"}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "unknown-npc"


def _npc_id(value: Any) -> str:
    text = _text(value)
    if not text or text == "player":
        return ""
    return text if text.casefold().startswith("npc:") else f"npc:{text}"


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value or () if isinstance(row, Mapping)]


def _list(value: Any) -> list[str]:
    if not isinstance(value, list | tuple | set):
        return []
    return [_text(item) for item in value if _text(item)]


def _first(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _tick(session: Mapping[str, Any]) -> int:
    state = _mapping(session.get("state"))
    simulation = _mapping(session.get("simulation_state"))
    runtime = _mapping(session.get("runtime_state"))
    for value in (
        simulation.get("tick"),
        simulation.get("turn"),
        runtime.get("tick"),
        runtime.get("turn"),
        state.get("tick"),
        state.get("turn"),
    ):
        try:
            return int(cast(Any, value))
        except (TypeError, ValueError):
            continue
    return 0


def _add(output: list[str], value: Any) -> None:
    npc_id = _npc_id(value)
    if npc_id and npc_id.casefold() not in {item.casefold() for item in output}:
        output.append(npc_id)


def encountered_npc_ids(
    session: Mapping[str, Any],
    *,
    explicit_npc_ids: Sequence[str] = (),
) -> tuple[str, ...]:
    """Return stable NPC IDs that are present, nearby, or speaking this turn."""

    output: list[str] = []
    for value in explicit_npc_ids:
        _add(output, value)
    state = _mapping(session.get("state"))
    simulation = _mapping(session.get("simulation_state"))
    runtime = _mapping(session.get("runtime_state"))
    location_id = _text((current_location_identity(session) or {}).get("id"))

    for row in _mapping(simulation.get("scene_population_state")).get("present_npcs") or ():
        value = cast(Any, row.get("npc_id") or row.get("id") if isinstance(row, Mapping) else row)
        _add(output, value)
    present = _mapping(simulation.get("present_npc_state"))
    for value in present.get(location_id) or ():
        _add(output, value)
    for row in _mapping(present.get("by_location")).values():
        for npc in _mapping(row).get("present_npcs") or ():
            value = cast(Any, npc.get("npc_id") or npc.get("id") if isinstance(npc, Mapping) else npc)
            _add(output, value)

    for scene in (
        _mapping(state.get("scene")),
        _mapping(runtime.get("current_scene")),
        _mapping(runtime.get("scene")),
        _mapping(simulation.get("scene")),
    ):
        for key in ("present_npc_ids", "nearby_npc_ids", "actor_ids"):
            for value in scene.get(key) or ():
                _add(output, value)
        for key in ("present_npcs", "nearby_npcs", "npcs"):
            for row in scene.get(key) or ():
                value = cast(Any, row.get("npc_id") or row.get("id") if isinstance(row, Mapping) else row)
                _add(output, value)
    for value in _mapping(simulation.get("player_state")).get("nearby_npc_ids") or ():
        _add(output, value)
    return tuple(output)


def _profile_containers(session: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    state = _mapping(session.get("state"))
    simulation = _mapping(session.get("simulation_state"))
    runtime = _mapping(session.get("runtime_state"))
    return (
        _mapping(state.get("npc_dossiers")),
        _mapping(simulation.get("npc_index")),
        _mapping(simulation.get("npcs")),
        _mapping(runtime.get("npc_index")),
        _mapping(_mapping(simulation.get("social_state")).get("profiles")),
        _mapping(_mapping(runtime.get("social_state")).get("profiles")),
    )


def _session_profile(session: Mapping[str, Any], npc_id: str) -> dict[str, Any]:
    target = npc_id.casefold()
    for container in _profile_containers(session):
        for key, value in container.items():
            if not isinstance(value, Mapping):
                continue
            row = {"npc_id": str(key), **dict(value)}
            aliases = {
                _npc_id(key).casefold(),
                _npc_id(row.get("npc_id") or row.get("id")).casefold(),
                _npc_id(row.get("name")).casefold(),
            }
            if target in aliases:
                return row
    return {}


def _personality(*profiles: Mapping[str, Any]) -> str:
    for profile in profiles:
        value = profile.get("personality")
        if isinstance(value, str) and _text(value):
            return _text(value)
        data = _mapping(value)
        traits = (
            _list(data.get("traits"))
            or _list(data.get("core_traits"))
            or _list(profile.get("personality_traits"))
        )
        temperament = _text(data.get("temperament"))
        parts = [part for part in (temperament, ", ".join(traits)) if part]
        if parts:
            return "; ".join(parts)
    return ""


def _speech(*profiles: Mapping[str, Any]) -> str:
    for profile in profiles:
        value = profile.get("speech_style") or profile.get("speaking_style")
        if isinstance(value, str) and _text(value):
            return _text(value)
        if isinstance(value, Mapping):
            parts = [
                part
                for part in (
                    _text(value.get("tone")),
                    ", ".join(_list(value.get("quirks"))),
                )
                if part
            ]
            if parts:
                return "; ".join(parts)
        nested = _mapping(profile.get("personality"))
        if _text(nested.get("speech_style")):
            return _text(nested.get("speech_style"))
        if _text(nested.get("social_style")):
            return _text(nested.get("social_style"))
    return ""


def _public_bio(
    name: str,
    role: str,
    location_name: str,
    profiles: Sequence[Mapping[str, Any]],
) -> str:
    for profile in profiles:
        biography = profile.get("biography")
        bio = _mapping(biography)
        text = _first(
            profile.get("public_bio"),
            bio.get("short_summary"),
            bio.get("public_reputation"),
            biography if isinstance(biography, str) else "",
            profile.get("short_bio"),
            profile.get("description"),
        )
        if text and "no detailed biography has been registered yet" not in text.casefold():
            return text
    where = f" at {location_name}" if location_name else ""
    manner = _personality(*profiles)
    suffix = f" Their visible manner is {manner}." if manner else ""
    return f"{name} is a {role or 'local person'} first encountered{where}.{suffix}".strip()


def _document_matches(row: Mapping[str, Any], entity_id: str, name: str) -> bool:
    refs = {
        _text(value).casefold()
        for value in list(row.get("entity_refs") or ()) + list(row.get("entities") or ())
        if _text(value)
    }
    return entity_id.casefold() in refs or (
        _text(row.get("topic_id")).casefold() in {"npc", "npcs", "characters"}
        and _text(row.get("title")).casefold() == name.casefold()
    )


def _rich_existing_dossier(entity: Mapping[str, Any]) -> bool:
    if _text(entity.get("kind")).casefold() != "npc":
        return False
    biography = entity.get("biography")
    biography_text = (
        _text(biography)
        if isinstance(biography, str)
        else _first(*_mapping(biography).values())
    )
    return bool(
        _text(entity.get("name"))
        and _first(
            entity.get("description"),
            entity.get("public_bio"),
            entity.get("backstory"),
            biography_text,
            entity.get("appearance"),
            entity.get("personality"),
        )
    )


def _dossier_is_player_visible(entity: Mapping[str, Any], status: str) -> bool:
    return _text(entity.get("visibility")).casefold() in _VISIBLE or status in _VISIBLE_STATUSES


def ensure_encountered_npc_lore(
    bible: Mapping[str, Any],
    session: Mapping[str, Any],
    *,
    explicit_npc_ids: Sequence[str] = (),
    canon_revision: int | None = None,
) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...], bool]:
    """Add one Campaign Bible biography entry per encountered NPC."""

    candidate = deepcopy(dict(bible))
    entities = deepcopy(_mapping(candidate.get("entities")))
    documents = _rows(candidate.get("documents"))
    discovery = deepcopy(_mapping(candidate.get("discovery_state")))
    pages = deepcopy(_mapping(discovery.get("pages")))
    entity_statuses = deepcopy(_mapping(discovery.get("entities")))
    discovery.setdefault("discoveries", [])
    location = current_location_identity(session) or {}
    location_id = _text(location.get("id"))
    location_name = _text(location.get("name"))
    revision = int(canon_revision or int(candidate.get("canon_revision") or 0) + 1)
    ensured: list[str] = []
    created: list[str] = []
    changed = False

    for runtime_id in encountered_npc_ids(session, explicit_npc_ids=explicit_npc_ids):
        entity_id = next(
            (key for key in entities if str(key).casefold() == runtime_id.casefold()),
            runtime_id,
        )
        existing = deepcopy(_mapping(entities.get(entity_id)))
        existing_status = _text(entity_statuses.get(entity_id))
        existing_document = next(
            (
                row
                for row in documents
                if _document_matches(row, entity_id, _text(existing.get("name")))
            ),
            None,
        )
        if (
            _rich_existing_dossier(existing)
            and _dossier_is_player_visible(existing, existing_status)
        ):
            ensured.append(entity_id)
            continue

        session_profile = _session_profile(session, runtime_id)
        dynamic = _mapping(load_npc_profile(runtime_id))
        registry = _mapping(get_npc_biography(runtime_id.split(":", 1)[-1]))
        profiles = (existing, session_profile, dynamic, registry)
        name = _first(
            existing.get("name"),
            session_profile.get("name"),
            dynamic.get("name"),
            registry.get("name"),
            runtime_id.split(":", 1)[-1].replace("_", " ").title(),
        )
        role = _first(
            existing.get("role"),
            session_profile.get("role"),
            _mapping(dynamic.get("evolution")).get("current_role"),
            registry.get("role"),
            "Local NPC",
        )
        public_bio = _public_bio(name, role, location_name, profiles)
        appearance = _first(*(profile.get("appearance") for profile in profiles))
        personality = _personality(*profiles)
        speech = _speech(*profiles)

        if not existing:
            refs = [
                value
                for value in dict.fromkeys(
                    [entity_id, runtime_id, _text(registry.get("npc_id"))]
                )
                if value
            ]
            biography = {
                "short_summary": public_bio,
                "public_reputation": public_bio,
            }
            entity = {
                "kind": "npc",
                "name": name,
                "role": role,
                "description": public_bio,
                "public_bio": public_bio,
                "biography": biography,
                "visibility": "player_known",
                "location_id": location_id,
                "entity_refs": refs,
                "dossier_status": "encountered",
                "profile_authority": "campaign_bible",
                "provenance": {
                    "last_source": "encountered_npc_profile_sync_v1",
                    "first_seen_tick": _tick(session),
                },
            }
            if appearance:
                entity["appearance"] = appearance
            if personality:
                entity["personality"] = personality
            if speech:
                entity["speech_style"] = speech
            for key in (
                "faction_ids",
                "values",
                "goals",
                "motives",
                "relationships",
                "knowledge_boundaries",
            ):
                for profile in profiles[1:]:
                    value = profile.get(key)
                    if value not in (None, "", [], {}):
                        entity[key] = deepcopy(value)
                        break
            entities[entity_id] = entity
            entity_statuses[entity_id] = "learned"
            created.append(entity_id)
            changed = True

        if existing_document is None:
            document_id = f"lore:npc:{_slug(name)}"
            occupied = {_text(row.get("document_id")) for row in documents}
            if document_id in occupied:
                document_id = f"{document_id}:{_slug(entity_id)}"
            details = [public_bio]
            if appearance:
                details.append(f"Appearance: {appearance}")
            if personality:
                details.append(f"Manner: {personality}")
            if speech:
                details.append(f"Speech: {speech}")
            full_text = "\n\n".join(details)
            documents.append(
                {
                    "document_id": document_id,
                    "topic_id": "npcs",
                    "title": name,
                    "full_text": full_text,
                    "summary_500": full_text[:500].rstrip(),
                    "summary_120": full_text[:120].rstrip(),
                    "keywords": [entity_id, runtime_id, name, role, location_id],
                    "entity_refs": [entity_id, runtime_id],
                    "entities": [entity_id],
                    "visibility": "player_known",
                    "canon_revision": revision,
                    "provenance": {
                        "source": "encountered_npc_profile_sync_v1",
                        "profile_authority": "campaign_bible",
                    },
                }
            )
            pages[document_id] = "learned"
            changed = True
        ensured.append(entity_id)

    if changed:
        candidate["canon_revision"] = revision
    candidate["entities"] = entities
    candidate["documents"] = documents
    discovery["pages"] = pages
    discovery["entities"] = entity_statuses
    candidate["discovery_state"] = discovery
    return candidate, tuple(ensured), tuple(created), changed
