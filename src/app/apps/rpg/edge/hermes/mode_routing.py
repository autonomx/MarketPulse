"""Omnix modes for Hermes: which mode routes where, its policy and the route decision."""
from __future__ import annotations

from typing import Any, Literal, TypedDict, cast


OmnixMode = Literal["normal_chat", "live_chat", "agent_mode", "house_ai", "podcast", "rpg"]
HermesRole = Literal["disabled", "observe", "suggest", "critique", "plan", "request_execution"]
ExecutionOwner = Literal["provider", "voice_pipeline", "omnix", "audio_pipeline", "rpg_sim"]


class OmnixModeRoute(TypedDict):
    mode: OmnixMode
    label: str
    direct_provider_path: bool
    hermes_role: HermesRole
    execution_owner: ExecutionOwner
    requires_approval: bool
    summary: str


MODE_ROUTES: dict[OmnixMode, OmnixModeRoute] = {
    "normal_chat": {
        "mode": "normal_chat",
        "label": "Normal chat",
        "direct_provider_path": True,
        "hermes_role": "disabled",
        "execution_owner": "provider",
        "requires_approval": False,
        "summary": "Existing text chat path talks directly to the configured provider.",
    },
    "live_chat": {
        "mode": "live_chat",
        "label": "Live chat",
        "direct_provider_path": True,
        "hermes_role": "observe",
        "execution_owner": "voice_pipeline",
        "requires_approval": False,
        "summary": "Low-latency voice path remains optimized for live interaction.",
    },
    "agent_mode": {
        "mode": "agent_mode",
        "label": "Agent mode",
        "direct_provider_path": False,
        "hermes_role": "request_execution",
        "execution_owner": "omnix",
        "requires_approval": True,
        "summary": "Hermes may prepare work requests; Omnix owns approval and execution.",
    },
    "house_ai": {
        "mode": "house_ai",
        "label": "House AI",
        "direct_provider_path": False,
        "hermes_role": "plan",
        "execution_owner": "omnix",
        "requires_approval": True,
        "summary": "Hermes may plan house actions; Omnix remains the authorization boundary.",
    },
    "podcast": {
        "mode": "podcast",
        "label": "Podcast",
        "direct_provider_path": False,
        "hermes_role": "critique",
        "execution_owner": "audio_pipeline",
        "requires_approval": False,
        "summary": "Hermes can plan and critique, while Omnix owns the audio pipeline.",
    },
    "rpg": {
        "mode": "rpg",
        "label": "RPG",
        "direct_provider_path": False,
        "hermes_role": "suggest",
        "execution_owner": "rpg_sim",
        "requires_approval": False,
        "summary": "Hermes may suggest or critique; the RPG sim validates truth.",
    },
}


def omnix_mode_route(mode: str) -> OmnixModeRoute:
    route = MODE_ROUTES.get(cast(OmnixMode, mode))
    if route is None:
        raise KeyError(f"Unknown Omnix mode: {mode}")
    return dict(route)  # type: ignore[return-value]


def list_omnix_mode_routes() -> list[OmnixModeRoute]:
    return [cast(OmnixModeRoute, dict(MODE_ROUTES[mode])) for mode in ("normal_chat", "live_chat", "agent_mode", "house_ai", "podcast", "rpg")]


def omnix_mode_router_payload(mode: str | None = None) -> dict[str, Any]:
    if mode:
        try:
            return {"ok": True, "route": omnix_mode_route(mode)}
        except KeyError:
            return {"ok": False, "error": "unknown_mode", "mode": mode}
    return {"ok": True, "routes": list_omnix_mode_routes()}


ModeCapability = Literal["observe", "suggest", "critique", "plan", "request_execution"]


class OmnixModePolicy(TypedDict):
    mode: OmnixMode
    hermes_capabilities: list[ModeCapability]
    requires_review: bool
    owner: str
    notes: list[str]


MODE_CAPABILITIES: dict[OmnixMode, list[ModeCapability]] = {
    "normal_chat": [],
    "live_chat": ["observe"],
    "agent_mode": ["observe", "plan", "request_execution"],
    "house_ai": ["observe", "plan", "request_execution"],
    "podcast": ["observe", "critique", "plan"],
    "rpg": ["observe", "suggest", "critique"],
}


def omnix_mode_policy(mode: str) -> OmnixModePolicy:
    route = omnix_mode_route(mode)
    capabilities = MODE_CAPABILITIES[route["mode"]]
    notes = [
        f"Execution owner: {route['execution_owner']}",
        f"Hermes role: {route['hermes_role']}",
    ]
    if route["requires_approval"]:
        notes.append("Review is required before any owned system action is applied.")
    else:
        notes.append("No extra review is required for readout-only or suggestion-only use.")
    return {
        "mode": route["mode"],
        "hermes_capabilities": list(capabilities),
        "requires_review": route["requires_approval"],
        "owner": route["execution_owner"],
        "notes": notes,
    }


def list_omnix_mode_policies() -> list[OmnixModePolicy]:
    return [omnix_mode_policy(route["mode"]) for route in list_omnix_mode_routes()]


def omnix_mode_policy_payload(mode: str | None = None) -> dict[str, Any]:
    if mode:
        try:
            return {"ok": True, "policy": omnix_mode_policy(mode)}
        except KeyError:
            return {"ok": False, "error": "unknown_mode", "mode": mode}
    return {"ok": True, "policies": list_omnix_mode_policies()}


def omnix_route_decision_payload(mode: str | None = None) -> dict[str, Any]:
    selected_mode = (mode or "rpg").strip() or "rpg"
    try:
        route = omnix_mode_route(selected_mode)
        policy = omnix_mode_policy(selected_mode)
    except KeyError:
        return {"ok": False, "error": "unknown_mode", "mode": selected_mode, "source": "omnix_route_decision"}

    return {
        "ok": True,
        "source": "omnix_route_decision",
        "mode": route["mode"],
        "role": route["hermes_role"],
        "owner": route["execution_owner"],
        "review_required": route["requires_approval"],
        "capabilities": policy["hermes_capabilities"],
        "boundary": route["summary"],
    }
