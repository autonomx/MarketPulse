from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.apps.rpg.narration.narrative_engine import (
    HermesResearchPolicy,
    HermesResearchRequest,
    normalize_hermes_research,
    run_bounded_hermes_research,
)
from app.apps.rpg.session.service import SESSION_CAMPAIGN_SESSIONS


ResearchCallable = Callable[
    [HermesResearchRequest, HermesResearchPolicy],
    Mapping[str, Any],
]


class _CallableResearcher:
    def __init__(self, callback: ResearchCallable) -> None:
        self.callback = callback

    def research(
        self,
        request: HermesResearchRequest,
        policy: HermesResearchPolicy,
    ) -> Mapping[str, Any]:
        return self.callback(request, policy)


def _request(data: Mapping[str, Any]) -> HermesResearchRequest:
    entity_ids = data.get("entity_ids")
    return HermesResearchRequest(
        research_id=str(data.get("research_id") or "").strip(),
        campaign_id=str(
            data.get("campaign_id")
            or data.get("session_id")
            or ""
        ).strip(),
        query=str(data.get("query") or "").strip(),
        entity_ids=(
            tuple(
                str(value).strip()
                for value in entity_ids
                if str(value).strip()
            )[:16]
            if isinstance(entity_ids, list | tuple)
            else ()
        ),
        purpose=str(
            data.get("purpose") or "narrative_grounding"
        ).strip(),
        metadata=dict(data.get("metadata") or {}),
    )


def _policy(data: Mapping[str, Any]) -> HermesResearchPolicy:
    raw = (
        raw_policy if isinstance(raw_policy := data.get("policy"), Mapping) else {}
    )
    return HermesResearchPolicy(
        max_sources=int(raw.get("max_sources") or 5),
        max_findings=int(raw.get("max_findings") or 5),
        max_total_chars=int(raw.get("max_total_chars") or 8_000),
        max_excerpt_chars=int(raw.get("max_excerpt_chars") or 800),
        max_query_chars=int(raw.get("max_query_chars") or 500),
    ).bounded()


def _campaign_bible_result(
    data: Mapping[str, Any],
    request: HermesResearchRequest,
    policy: HermesResearchPolicy,
) -> dict[str, Any] | None:
    from app.apps.rpg.genesis.forge.hermes_campaign_research import (
        research_campaign_turn,
    )

    packet = research_campaign_turn(
        campaign_id=request.campaign_id,
        query=request.query,
        speaker_id=(
            str(data.get("speaker_id") or "").strip() or None
        ),
        actor_ids=tuple(
            str(value)
            for value in data.get("actor_ids") or ()
            if str(value)
        ),
        faction_ids=tuple(
            str(value)
            for value in data.get("faction_ids") or ()
            if str(value)
        ),
        entity_ids=request.entity_ids,
        research_id=request.research_id,
        max_topics=min(5, policy.max_sources),
        sessions=SESSION_CAMPAIGN_SESSIONS,
    )
    if packet is None:
        return None
    result = packet.result
    return {
        "ok": True,
        "source": "hermes_campaign_bible_research",
        "read_only": True,
        "state_changed": False,
        "campaign_bible_changed": False,
        "result": result.as_dict(),
        "evidence": [record.as_dict() for record in result.evidence()],
        "policy": policy.__dict__,
        "campaign_bible_revision": packet.snapshot.revision,
        "campaign_bible_hash": packet.snapshot.content_hash,
        "topic_titles": list(packet.topic_titles),
        "topic_count": len(packet.topic_titles),
    }


def hermes_rpg_narrative_research_payload(
    payload: Mapping[str, Any] | None,
    *,
    researcher: ResearchCallable | None = None,
) -> dict[str, Any]:
    """Run bounded research; default to the campaign's established Bible."""

    data = dict(payload or {})
    request = _request(data)
    policy = _policy(data)
    try:
        if researcher is not None:
            result = run_bounded_hermes_research(
                request,
                _CallableResearcher(researcher),
                policy=policy,
            )
        else:
            raw = data.get("research_result")
            if not isinstance(raw, Mapping):
                campaign_result = _campaign_bible_result(
                    data,
                    request,
                    policy,
                )
                if campaign_result is not None:
                    return campaign_result
                return {
                    "ok": False,
                    "error": "campaign_bible_not_available",
                    "source": "hermes_rpg_narrative_research",
                    "read_only": True,
                    "state_changed": False,
                    "policy": policy.__dict__,
                }
            result = normalize_hermes_research(
                request,
                raw,
                policy=policy,
            )
    except (TypeError, ValueError) as exc:
        return {
            "ok": False,
            "error": "invalid_hermes_narrative_research_request",
            "detail": str(exc),
            "source": "hermes_rpg_narrative_research",
            "read_only": True,
            "state_changed": False,
        }

    return {
        "ok": True,
        "source": "hermes_rpg_narrative_research",
        "read_only": True,
        "state_changed": False,
        "campaign_bible_changed": False,
        "result": result.as_dict(),
        "evidence": [record.as_dict() for record in result.evidence()],
        "policy": policy.__dict__,
    }
