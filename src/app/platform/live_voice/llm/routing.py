"""Durable provider routing for Chat and live voice LLM turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.platform.chat.contracts import (
    SendChatMessageRequest,
    live_call_provider_affinity,
    provider_key,
    resolve_effective_provider_id,
)
from app.conversation.contracts import LIVE_VOICE_ROUTE_METADATA_KEY
from app.providers.catalog import RUNTIME_STATS, provider_supports
from app.observability.tts_stream_diagnostics import stream_log
from app.runtime.live_voice_config import resolve_live_voice_chat_route

ROUTE_METADATA_KEY = LIVE_VOICE_ROUTE_METADATA_KEY


@dataclass(frozen=True)
class ProviderRoute:
    provider_id: str | None
    model_id: str | None
    provider_explicit: bool
    model_explicit: bool
    execution_lane: str = "session"

    def to_metadata(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "provider_explicit": self.provider_explicit,
            "model_explicit": self.model_explicit,
            "execution_lane": self.execution_lane,
        }


def _normalized(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _is_live_voice_request(request: SendChatMessageRequest) -> bool:
    user_turn_id = _normalized(request.user_turn_id)
    speech_segment_id = _normalized(request.speech_segment_id)
    return bool(
        speech_segment_id
        or (user_turn_id and user_turn_id.startswith("voice-user-turn:"))
    )


def resolve_provider_route(provider_id: str | None) -> tuple[str | None, Any]:
    """Return the current concrete provider and its configured provider instance."""
    from app.providers.service import get_provider

    effective_provider_id = resolve_effective_provider_id(provider_id)
    provider = get_provider(provider_key(effective_provider_id))
    return effective_provider_id, provider


def route_chat_request(
    request: SendChatMessageRequest,
    *,
    implicit_provider_id: str | None = None,
    implicit_model_id: str | None = None,
) -> tuple[SendChatMessageRequest, ProviderRoute]:
    """Resolve the default route and optional dedicated live-voice model lane."""
    provider_explicit = _normalized(request.provider_id) is not None
    model_explicit = _normalized(request.model_id) is not None
    provider_id = resolve_effective_provider_id(
        request.provider_id if provider_explicit else implicit_provider_id
    )
    model_id = (
        request.model_id
        if model_explicit
        else (_normalized(implicit_model_id) if not provider_explicit else None)
    )
    execution_lane = "session"
    if _is_live_voice_request(request):
        provider_id, model_id, execution_lane = resolve_live_voice_chat_route(
            provider_id,
            model_id,
        )
    route = ProviderRoute(
        provider_id=provider_id,
        model_id=model_id,
        provider_explicit=provider_explicit,
        model_explicit=model_explicit,
        execution_lane=execution_lane,
    )
    return request.model_copy(
        update={"provider_id": route.provider_id, "model_id": route.model_id}
    ), route


def _live_voice_affinity_for_current_provider(
    session_id: str,
) -> tuple[str | None, str | None] | None:
    """Use prewarm affinity only while it matches the current Settings provider."""
    affinity = live_call_provider_affinity(session_id)
    if affinity is None:
        return None
    affinity_provider_id, affinity_model_id = affinity
    configured_provider_id = resolve_effective_provider_id(None)
    if provider_key(affinity_provider_id) == provider_key(configured_provider_id):
        return affinity_provider_id, affinity_model_id

    stream_log(
        "gateway-live-chat-first-token",
        "runtime",
        "live_chat_provider_affinity_stale",
        session_id=session_id,
        affinity_provider_id=affinity_provider_id,
        configured_provider_id=configured_provider_id,
        reason="settings_provider_changed",
    )
    return None


def _route_from_message(user_message: Any) -> ProviderRoute | None:
    metadata = getattr(user_message, "metadata", None)
    raw = metadata.get(ROUTE_METADATA_KEY) if isinstance(metadata, dict) else None
    if not isinstance(raw, dict) or "provider_id" not in raw:
        return None
    return ProviderRoute(
        provider_id=_normalized(raw.get("provider_id")),
        model_id=_normalized(raw.get("model_id")),
        provider_explicit=bool(raw.get("provider_explicit")),
        model_explicit=bool(raw.get("model_explicit")),
        execution_lane=_normalized(raw.get("execution_lane")) or "session",
    )


def begin_routed_user_message(
    store: Any,
    session_id: str,
    request: SendChatMessageRequest,
    *,
    persist: Callable[[SendChatMessageRequest, dict[str, Any] | None], Any],
) -> Any:
    """Persist the chosen provider route with the user turn for worker recovery."""
    implicit_provider_id = None
    implicit_model_id = None
    if _is_live_voice_request(request) and _normalized(request.provider_id) is None:
        affinity = _live_voice_affinity_for_current_provider(session_id)
        if affinity is not None:
            implicit_provider_id, implicit_model_id = affinity
    routed_request, route = route_chat_request(
        request,
        implicit_provider_id=implicit_provider_id,
        implicit_model_id=implicit_model_id,
    )
    route_metadata = (
        route.to_metadata()
        if not (route.provider_explicit and route.model_explicit)
        or route.execution_lane != "session"
        else None
    )
    appended = persist(routed_request, route_metadata)
    if appended is None:
        return None
    session, user_message = appended
    if route_metadata is None:
        return appended
    if _route_from_message(user_message) is not None:
        return appended

    user_message.metadata[ROUTE_METADATA_KEY] = route_metadata
    session.provider_id = route.provider_id
    session.model_id = route.model_id
    update_metadata = getattr(store, "update_user_message_metadata", None)
    if callable(update_metadata):
        persisted = update_metadata(
            session_id=session.id,
            message_id=user_message.id,
            metadata={ROUTE_METADATA_KEY: route_metadata},
        )
        if not persisted:
            raise RuntimeError("provider route metadata could not be persisted")
    else:
        store._save_session(session)
    return session, user_message


def resolve_generation_route(
    user_message: Any,
    *,
    provider_id: str | None,
    model_id: str | None,
    request: SendChatMessageRequest,
) -> ProviderRoute:
    route = _route_from_message(user_message)
    if route is not None:
        return route
    routed_request, route = route_chat_request(request)
    if routed_request.provider_id or routed_request.model_id:
        return route
    return ProviderRoute(
        provider_id=resolve_effective_provider_id(provider_id),
        model_id=model_id,
        provider_explicit=False,
        model_explicit=False,
    )


def resolve_stream_route(
    user_message: Any,
    *,
    provider_id: str | None,
    model_id: str | None,
) -> ProviderRoute:
    route = _route_from_message(user_message)
    if route is not None:
        return route
    return ProviderRoute(
        provider_id=resolve_effective_provider_id(provider_id),
        model_id=model_id,
        provider_explicit=_normalized(provider_id) is not None,
        model_explicit=_normalized(model_id) is not None,
    )


def log_provider_route(
    *,
    requested_provider_id: str | None,
    route: ProviderRoute,
    stream: bool,
    provider: Any = None,
) -> None:
    effective_provider_name = _normalized(getattr(provider, "provider_name", None))
    if effective_provider_name:
        effective_provider_name = effective_provider_name.lower()
    else:
        effective_provider_name = _normalized(provider_key(route.provider_id))
    stream_log(
        "gateway-live-chat-first-token",
        "runtime",
        "live_chat_provider_route_resolved",
        requested_provider_id=requested_provider_id,
        effective_provider_id=route.provider_id,
        effective_provider_name=effective_provider_name,
        provider_class=(
            f"{provider.__class__.__module__}.{provider.__class__.__name__}"
            if provider is not None
            else None
        ),
        provider_explicit=route.provider_explicit,
        model_explicit=route.model_explicit,
        execution_lane=route.execution_lane,
        effective_model_id=route.model_id,
        session_provider_overridden=(
            provider_key(requested_provider_id) != provider_key(route.provider_id)
        ),
        lmstudio_metrics_path_expected=provider_supports(effective_provider_name, RUNTIME_STATS),
        stream=stream,
    )


__all__ = [
    "ProviderRoute",
    "ROUTE_METADATA_KEY",
    "begin_routed_user_message",
    "log_provider_route",
    "resolve_effective_provider_id",
    "resolve_generation_route",
    "resolve_provider_route",
    "resolve_stream_route",
    "route_chat_request",
]
