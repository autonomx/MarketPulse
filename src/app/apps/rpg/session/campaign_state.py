"""The saved RPG campaign document: its typed envelope and the upcasters from older versions (WP-5.6).

A campaign's state (``omnix_rpg_campaigns.state_jsonb``, or a legacy session
file) is one document whose version is ``manifest.schema_version``. Loading
an older document runs the upcasters in order, each raising the version to
its target, then validates the envelope, so every saved session loads in the
current shape. The engine's sections (``state``, ``simulation_state``,
``runtime_state``, ``setup_payload``) stay free-form mappings: the envelope
types what every reader relies on, not the whole engine.

To change the shape: raise ``CURRENT_SCHEMA_VERSION`` and append an upcaster
whose target is the new version; never edit an existing one.
"""
from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict

CURRENT_SCHEMA_VERSION = 5


class CampaignManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    session_id: str = ""
    schema_version: int = Field(ge=1)
    title: str = ""


class CampaignState(BaseModel):
    """The envelope every saved campaign has once upcast."""

    model_config = ConfigDict(extra="allow")

    manifest: CampaignManifest
    state: dict[str, Any] = Field(default_factory=dict)
    simulation_state: dict[str, Any]
    runtime_state: dict[str, Any]
    setup_payload: dict[str, Any] = Field(default_factory=dict)


def schema_version(document: Any) -> int:
    """The document's ``manifest.schema_version``; 1 when it has none."""
    value = _safe_dict(_safe_dict(document).get("manifest")).get("schema_version")
    if value is None or isinstance(value, bool):
        return 1
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _to_v4_ambient_runtime(document: dict[str, Any]) -> dict[str, Any]:
    """Versions 1-3 predate ambient narration: give the runtime its queue and counters."""
    runtime = _safe_dict(document.get("runtime_state"))
    runtime.setdefault("ambient_queue", [])
    runtime.setdefault("ambient_seq", 0)
    runtime.setdefault("last_idle_tick_at", "")
    runtime.setdefault("last_player_turn_at", "")
    runtime.setdefault("idle_streak", 0)
    runtime.setdefault("ambient_cooldowns", {})
    runtime.setdefault("recent_ambient_ids", [])
    runtime.setdefault("pending_interrupt", None)
    runtime.setdefault("subscription_state", {"last_polled_seq": 0})
    runtime.setdefault("ambient_metrics", {"emitted": 0, "suppressed": 0, "coalesced": 0})
    document["runtime_state"] = runtime
    return document


# (target version, upcaster), in order. Version 5 marks the interaction
# timeline, which the normalization below keeps for every version.
UPCASTERS: tuple[tuple[int, Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    (4, _to_v4_ambient_runtime),
)


def _normalize(document: dict[str, Any]) -> dict[str, Any]:
    """What every load guarantees whatever the version: ids, state sections, the interaction timeline."""
    from app.apps.rpg.session.legacy_interaction_migration import migrate_legacy_interactions

    manifest = _safe_dict(document.get("manifest"))
    if not manifest.get("id"):
        manifest["id"] = str(manifest.get("session_id") or "session:unknown")
    if not manifest.get("session_id") and manifest.get("id") != "session:unknown":
        manifest["session_id"] = manifest.get("id")
    document["manifest"] = manifest
    simulation = _safe_dict(document.get("simulation_state"))
    simulation["presentation_state"] = _safe_dict(simulation.get("presentation_state"))
    simulation["memory_state"] = _safe_dict(simulation.get("memory_state"))
    document["simulation_state"] = simulation
    document["runtime_state"] = _safe_dict(document.get("runtime_state"))
    return migrate_legacy_interactions(document)


def upcast_campaign_document(document: Any) -> dict[str, Any]:
    """``document`` in the current shape, envelope validated (``pydantic.ValidationError`` if it cannot be)."""
    upcast = _safe_dict(document)
    version = schema_version(upcast)
    for target, upcaster in UPCASTERS:
        if version < target:
            upcast = upcaster(upcast)
            version = target
    upcast = _normalize(upcast)
    manifest = _safe_dict(upcast.get("manifest"))
    manifest["schema_version"] = max(version, CURRENT_SCHEMA_VERSION)
    upcast["manifest"] = manifest
    CampaignState.model_validate(upcast)
    return upcast


def upcast_if_outdated(document: dict[str, Any]) -> dict[str, Any]:
    """A stored document upcast only when it predates the current version; current documents load as stored."""
    if schema_version(document) >= CURRENT_SCHEMA_VERSION:
        return document
    return upcast_campaign_document(document)


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "CampaignManifest",
    "CampaignState",
    "UPCASTERS",
    "schema_version",
    "upcast_campaign_document",
    "upcast_if_outdated",
]
