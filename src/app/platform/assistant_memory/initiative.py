"""Deterministic proactive-memory and tool-enrichment policy for live companion turns."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict

from app.platform.assistant_tools.contracts import (
    AssistantCapabilityDashboard,
    build_assistant_capability_dashboard,
)
from app.conversation.live_profile import LiveConversationProfile

from app.conversation.memory_contracts import MemoryRecord, MemoryScopeContext
from .temporal_retrieval import TemporalRetrievalItem, TemporalRetrievalResult
from app.prompts import prompt_template


PROMPT_DIRECTIVE_TEMPLATE = prompt_template(
    'assistant_memory.initiative.prompt_directive', "1",
    'Companion initiative policy permits one gentle proactive reference this turn.',
)

PROMPT_DIRECTIVE_2_TEMPLATE = prompt_template(
    'assistant_memory.initiative.prompt_directive_2', "1",
    'Mention it naturally at most once and do not imply the user asked about it.',
)

PROMPT_DIRECTIVE_3_TEMPLATE = prompt_template(
    'assistant_memory.initiative.prompt_directive_3', "1",
    'A trusted {requested_tool} lookup is eligible, but no tool result is present yet.',
)

PROMPT_DIRECTIVE_4_TEMPLATE = prompt_template(
    'assistant_memory.initiative.prompt_directive_4', "1",
    'Do not invent current external data; ask permission or state that a lookup is needed.',
)

PROMPT_DIRECTIVE_5_TEMPLATE = prompt_template(
    'assistant_memory.initiative.prompt_directive_5', "1",
    'The {requested_tool} capability is unavailable; use an honest natural fallback.',
)


InitiativeAction = Literal[
    "suppress",
    "context_only",
    "surface",
    "surface_with_tool",
    "surface_without_tool",
]
CompanionTool = Literal["traffic", "weather", "calendar", "messages"]

_TERM_PATTERN = re.compile(r"[A-Za-z0-9_]{2,}")
_GREETING_TERMS = {
    "hello",
    "hey",
    "hi",
    "morning",
    "afternoon",
    "evening",
    "yo",
}
_TOOL_ENV = "OMNIX_COMPANION_TRUSTED_CAPABILITIES"
_SURFACE_LOCK = threading.RLock()
_LAST_SURFACED_TTL_SECONDS = 30 * 24 * 60 * 60
_MAX_LAST_SURFACED_ENTRIES = 4096
_LAST_SURFACED: dict[tuple[str, str, str], tuple[datetime, float]] = {}


class TrustedCapabilityManifest(BaseModel):
    """Trusted, content-free capability projection used by the planner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    available_tools: frozenset[CompanionTool] = frozenset()
    source: str = "runtime_dashboard"

    def available(self, tool: CompanionTool) -> bool:
        return tool in self.available_tools


class CompanionInitiativeDecision(BaseModel):
    """Deterministic decision; durable memory selection remains upstream."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: InitiativeAction
    reason: str
    mode: Literal["off", "gentle", "active"]
    selected_memory_id: str | None = None
    selected_kind: str | None = None
    activation_score: int = 0
    requested_tool: CompanionTool | None = None
    tool_available: bool = False
    cooldown_hours: int = 0
    proactive: bool = False

    def content_free_diagnostics(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "mode": self.mode,
            "selected_memory_id": self.selected_memory_id,
            "selected_kind": self.selected_kind,
            "activation_score": self.activation_score,
            "requested_tool": self.requested_tool,
            "tool_available": self.tool_available,
            "cooldown_hours": self.cooldown_hours,
            "proactive": self.proactive,
        }


def _terms(value: str) -> frozenset[str]:
    return frozenset(term.casefold() for term in _TERM_PATTERN.findall(value))


def _is_low_content_turn(query: str) -> bool:
    terms = _terms(query)
    return not terms or terms.issubset(_GREETING_TERMS)


def build_trusted_capability_manifest(
    dashboard: AssistantCapabilityDashboard | None = None,
) -> TrustedCapabilityManifest:
    """Build capability availability from trusted runtime state, never learned memory."""

    try:
        resolved = dashboard or build_assistant_capability_dashboard()
    except Exception:
        resolved = AssistantCapabilityDashboard()
    available: set[CompanionTool] = set()
    for status in resolved.tools:
        connected = status.connection_status in {"connected", "ready", "available"}
        if not status.enabled or not connected or status.enabled_action_count <= 0:
            continue
        if status.tool_id == "calendar":
            available.add("calendar")
        elif status.tool_id == "gmail":
            available.add("messages")
    configured = {
        item.strip().casefold()
        for item in (_env_str(_TOOL_ENV) or "").split(",")
        if item.strip()
    }
    available.update(
        cast(CompanionTool, item) for item in configured if item in {"traffic", "weather", "calendar", "messages"}
    )
    return TrustedCapabilityManifest(available_tools=frozenset(available))


def _surface_key(context: MemoryScopeContext, memory_id: str) -> tuple[str, str, str]:
    return context.owner_type, context.owner_id, memory_id


def reset_initiative_surface_history() -> None:
    with _SURFACE_LOCK:
        _LAST_SURFACED.clear()


def record_initiative_surface(
    context: MemoryScopeContext,
    memory_id: str,
    *,
    surfaced_at: datetime | None = None,
) -> None:
    at = surfaced_at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    with _SURFACE_LOCK:
        now = time.monotonic()
        _prune_surface_history_locked(now)
        key = _surface_key(context, memory_id)
        if key not in _LAST_SURFACED and len(_LAST_SURFACED) >= _MAX_LAST_SURFACED_ENTRIES:
            oldest_key = min(_LAST_SURFACED, key=lambda item: _LAST_SURFACED[item][1])
            _LAST_SURFACED.pop(oldest_key, None)
        _LAST_SURFACED[key] = (
            at.astimezone(timezone.utc),
            now + _LAST_SURFACED_TTL_SECONDS,
        )


def _prune_surface_history_locked(now: float) -> None:
    for key, (_surfaced_at, expires_at) in list(_LAST_SURFACED.items()):
        if expires_at <= now:
            _LAST_SURFACED.pop(key, None)


def _last_surfaced(context: MemoryScopeContext, record: MemoryRecord) -> datetime | None:
    payload_value = record.structured_payload.get("last_surfaced_at")
    payload_time: datetime | None = None
    if isinstance(payload_value, str) and payload_value.strip():
        try:
            payload_time = datetime.fromisoformat(payload_value.replace("Z", "+00:00"))
            if payload_time.tzinfo is None:
                payload_time = payload_time.replace(tzinfo=timezone.utc)
        except ValueError:
            payload_time = None
    with _SURFACE_LOCK:
        _prune_surface_history_locked(time.monotonic())
        runtime_entry = _LAST_SURFACED.get(_surface_key(context, record.id))
        runtime_time = runtime_entry[0] if runtime_entry is not None else None
    values = [value.astimezone(timezone.utc) for value in (payload_time, runtime_time) if value]
    return max(values) if values else None


def _tool_for_record(record: MemoryRecord) -> CompanionTool | None:
    terms = _terms(record.content)
    activity = str(record.structured_payload.get("activity") or "").casefold()
    combined = set(terms)
    combined.update(_terms(activity.replace("_", " ").replace("-", " ")))
    if record.kind == "routine" and combined & {
        "commute",
        "drive",
        "driving",
        "route",
        "traffic",
        "train",
        "transit",
    }:
        return "traffic"
    if combined & {"weather", "rain", "snow", "temperature", "forecast"}:
        return "weather"
    if record.kind in {"open_loop", "goal"} and combined & {
        "appointment",
        "calendar",
        "meeting",
        "schedule",
    }:
        return "calendar"
    if combined & {"email", "message", "reply", "inbox"}:
        return "messages"
    return None


def _candidate_item(result: TemporalRetrievalResult) -> TemporalRetrievalItem | None:
    proactive_kinds = {"routine", "open_loop", "goal", "temporal_fact"}
    return next((item for item in result.items if item.kind in proactive_kinds), None)


def _directly_relevant(item: TemporalRetrievalItem, query: str) -> bool:
    return bool(_terms(query) & _terms(item.record.content))


def _activation_score(item: TemporalRetrievalItem) -> int:
    """Add bounded confidence only after repeated evidence for a learned routine."""

    if item.kind != "routine":
        return item.score
    try:
        evidence_count = max(
            1,
            int(item.record.structured_payload.get("evidence_count") or 1),
        )
    except (TypeError, ValueError):
        evidence_count = 1
    evidence_bonus = min(280, max(0, evidence_count - 1) * 70)
    return item.score + evidence_bonus


def plan_companion_initiative(
    result: TemporalRetrievalResult,
    context: MemoryScopeContext,
    profile: LiveConversationProfile,
    query: str,
    *,
    privacy_mode: bool,
    capabilities: TrustedCapabilityManifest | None = None,
    now: datetime | None = None,
) -> CompanionInitiativeDecision:
    """Choose whether a selected memory may be surfaced proactively."""

    mode = profile.initiative_mode
    if privacy_mode:
        return CompanionInitiativeDecision(
            action="suppress",
            reason="private_session",
            mode=mode,
        )
    item = _candidate_item(result)
    if item is None:
        return CompanionInitiativeDecision(
            action="suppress",
            reason="no_proactive_candidate",
            mode=mode,
        )
    activation_score = _activation_score(item)
    direct = _directly_relevant(item, query)
    if mode == "off":
        return CompanionInitiativeDecision(
            action="context_only" if direct else "suppress",
            reason="direct_relevance" if direct else "initiative_disabled",
            mode=mode,
            selected_memory_id=item.memory_id if direct else None,
            selected_kind=item.kind if direct else None,
            activation_score=activation_score if direct else 0,
        )
    if direct:
        return CompanionInitiativeDecision(
            action="context_only",
            reason="direct_relevance",
            mode=mode,
            selected_memory_id=item.memory_id,
            selected_kind=item.kind,
            activation_score=activation_score,
        )
    if not _is_low_content_turn(query):
        return CompanionInitiativeDecision(
            action="suppress",
            reason="current_topic_has_priority",
            mode=mode,
        )
    threshold = 850 if mode == "gentle" else 650
    cooldown_hours = 36 if mode == "gentle" else 12
    if activation_score < threshold:
        return CompanionInitiativeDecision(
            action="suppress",
            reason="activation_below_threshold",
            mode=mode,
            selected_memory_id=item.memory_id,
            selected_kind=item.kind,
            activation_score=activation_score,
            cooldown_hours=cooldown_hours,
        )
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    prior = _last_surfaced(context, item.record)
    if prior is not None and current.astimezone(timezone.utc) - prior < timedelta(
        hours=cooldown_hours
    ):
        return CompanionInitiativeDecision(
            action="suppress",
            reason="repetition_cooldown",
            mode=mode,
            selected_memory_id=item.memory_id,
            selected_kind=item.kind,
            activation_score=activation_score,
            cooldown_hours=cooldown_hours,
        )
    manifest = capabilities or build_trusted_capability_manifest()
    requested_tool = _tool_for_record(item.record)
    if requested_tool is None:
        action: InitiativeAction = "surface"
        reason = "proactive_memory_allowed"
        available = False
    elif manifest.available(requested_tool):
        action = "surface_with_tool"
        reason = "tool_enrichment_allowed"
        available = True
    else:
        action = "surface_without_tool"
        reason = "tool_unavailable_fallback"
        available = False
    return CompanionInitiativeDecision(
        action=action,
        reason=reason,
        mode=mode,
        selected_memory_id=item.memory_id,
        selected_kind=item.kind,
        activation_score=activation_score,
        requested_tool=requested_tool,
        tool_available=available,
        cooldown_hours=cooldown_hours,
        proactive=True,
    )


def initiative_prompt_directive(
    decision: CompanionInitiativeDecision,
    result: TemporalRetrievalResult,
) -> str | None:
    """Render a bounded directive; tool data is never fabricated or persisted."""

    if not decision.proactive or decision.selected_memory_id is None:
        return None
    item = next(
        (candidate for candidate in result.items if candidate.memory_id == decision.selected_memory_id),
        None,
    )
    if item is None:
        return None
    lines = [
        PROMPT_DIRECTIVE_TEMPLATE.text,
        f"Relevant approved context: {item.record.content}",
        PROMPT_DIRECTIVE_2_TEMPLATE.text,
    ]
    if decision.action == "surface_with_tool" and decision.requested_tool:
        lines.extend(
            [
                PROMPT_DIRECTIVE_3_TEMPLATE.format(requested_tool=decision.requested_tool),
                PROMPT_DIRECTIVE_4_TEMPLATE.text,
            ]
        )
    elif decision.action == "surface_without_tool" and decision.requested_tool:
        lines.append(
            PROMPT_DIRECTIVE_5_TEMPLATE.format(requested_tool=decision.requested_tool)
        )
    return "\n".join(lines)


__all__ = [
    "CompanionInitiativeDecision",
    "TrustedCapabilityManifest",
    "build_trusted_capability_manifest",
    "initiative_prompt_directive",
    "plan_companion_initiative",
    "record_initiative_surface",
    "reset_initiative_surface_history",
]
