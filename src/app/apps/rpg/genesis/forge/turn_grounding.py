"""Build one evidence packet from simulation, Campaign Bible, and Hermes research."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from app.apps.rpg.narration.narrative_engine import (
    CampaignBibleSnapshot,
    EvidenceRecord,
)
from app.apps.rpg.narration.narrative_engine.shadow import runtime_evidence

from .campaign_bible_runtime import load_campaign_bible_snapshot
from .hermes_campaign_research import CampaignResearchPacket, research_campaign_turn
from .npc_lore_sync import sync_encountered_npc_lore
from .runtime_lore_store import ensure_turn_scene_lore
from .turn_canon_snapshot import select_complete_turn_snapshot

if TYPE_CHECKING:
    from app.apps.rpg.genesis.contracts import CampaignSessions


_LORE_QUERY_PREFIXES = (
    "who ",
    "what ",
    "where ",
    "when ",
    "why ",
    "which ",
    "tell me ",
    "explain ",
    "describe ",
    "remind me ",
    "do you know ",
    "have you heard ",
)
_NON_LORE_FAST_QUESTIONS = (
    "how are you",
    "how is your day",
    "how was your day",
    "what are you doing",
    "are you okay",
    "are you all right",
    "can you help me",
)
_LORE_QUERY_TERMS = (
    "look around",
    "look at",
    "inspect",
    "examine",
    "observe",
    "survey",
    "study",
    "search the room",
    "search this place",
    "tell me about",
    "what do you know",
    "history of",
    "lore",
    "legend",
    "rumor",
    "remember",
    "recall",
    "where are we",
    "this place",
    "this area",
    "this region",
    "old road",
    "around here",
    "local area",
    "nearby town",
    "safe here",
)
_HOW_LORE_TERMS = (
    "how did ",
    "how does ",
    "how do i get",
    "how do we get",
    "how was ",
    "how were ",
    "how long has",
    "how long have",
)


@dataclass(frozen=True)
class TurnGroundingPacket:
    evidence: tuple[EvidenceRecord, ...]
    metadata: Mapping[str, Any]
    research: CampaignResearchPacket | None = None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _session(result: Mapping[str, Any], campaign_id: str, *, sessions: CampaignSessions) -> Mapping[str, Any]:
    existing = result.get("session")
    if isinstance(existing, Mapping):
        return existing
    try:
        loaded = sessions.load(campaign_id)
        return loaded if isinstance(loaded, Mapping) else {}
    except Exception:
        return {}


def _portable_materialized_snapshot(
    session: Mapping[str, Any],
    campaign_id: str,
) -> CampaignBibleSnapshot | None:
    projection = _mapping(session.get("campaign_bible_projection"))
    if not projection:
        return None
    return CampaignBibleSnapshot(
        campaign_id=campaign_id,
        revision=int(projection.get("canon_revision") or 0),
        content_hash=str(projection.get("content_hash") or ""),
        document=projection,
        provenance={"source": "portable_materialized_turn_authority"},
        consistency_report={},
        completeness=_mapping(projection.get("completeness")),
    )


def _faction_ids(session: Mapping[str, Any], speaker_id: str | None) -> tuple[str, ...]:
    if not speaker_id:
        return ()
    state = _mapping(session.get("state"))
    dossier = _mapping(_mapping(state.get("npc_dossiers")).get(speaker_id))
    values = dossier.get("faction_ids")
    if not isinstance(values, list | tuple | set):
        return ()
    return tuple(str(value) for value in values if str(value).strip())


def _entity_ids(
    result: Mapping[str, Any],
    session: Mapping[str, Any],
    speaker_id: str | None,
    actor_ids: Sequence[str],
) -> tuple[str, ...]:
    values: list[str] = [str(value) for value in actor_ids if str(value)]
    if speaker_id:
        values.append(speaker_id)
    state = _mapping(session.get("state"))
    scene = _mapping(result.get("scene") or state.get("scene"))
    for candidate in (
        scene.get("location_id"),
        scene.get("region_id"),
        _mapping(state.get("location")).get("id"),
        _mapping(state.get("current_location")).get("id"),
        state.get("current_location_id"),
        _mapping(state.get("world")).get("active_location_id"),
        _mapping(state.get("world")).get("current_location_id"),
    ):
        value = str(candidate or "").strip()
        if value:
            values.append(value)
    return tuple(dict.fromkeys(values))


def campaign_lore_research_required(
    player_input: str,
    result: Mapping[str, Any] | None = None,
) -> bool:
    """Return true when a fast turn still needs Campaign Bible retrieval."""

    text = re.sub(r"\s+", " ", str(player_input or "").strip().casefold())
    if not text:
        return False
    resolved = _mapping(
        _mapping(result or {}).get("resolved_result")
        or _mapping(result or {}).get("result")
    )
    mode = str(
        resolved.get("response_mode")
        or resolved.get("semantic_family")
        or resolved.get("action_type")
        or _mapping(result or {}).get("response_mode")
        or ""
    ).strip().casefold()
    if mode in {"observation", "investigation", "travel", "look", "inspect"}:
        return True
    if text.startswith(_NON_LORE_FAST_QUESTIONS):
        return False
    if text.startswith(_LORE_QUERY_PREFIXES):
        return True
    if text.startswith("how ") and any(term in text for term in _HOW_LORE_TERMS):
        return True
    return any(term in text for term in _LORE_QUERY_TERMS)


def build_turn_grounding_packet(
    result: Mapping[str, Any],
    *,
    sessions: CampaignSessions,
    campaign_id: str,
    player_input: str,
    speaker_id: str | None = None,
    actor_ids: tuple[str, ...] = (),
    max_topics: int = 5,
    runtime_only: bool = False,
) -> TurnGroundingPacket:
    """Assemble evidence after materializing current-scene canon without changing simulation."""

    lore_required = campaign_lore_research_required(player_input, result)
    requested_runtime_only = runtime_only
    runtime_only = runtime_only and not lore_required
    runtime = tuple(runtime_evidence(result))
    session = _session(result, campaign_id, sessions=sessions)
    explicit_npc_ids = tuple(
        value
        for value in (*actor_ids, speaker_id or "")
        if str(value).strip().casefold().startswith("npc:")
    )
    try:
        session, npc_lore_sync = sync_encountered_npc_lore(
            campaign_id,
            session,
            explicit_npc_ids=explicit_npc_ids,
            sessions=sessions,
        )
    except Exception as exc:
        npc_lore_sync = {
            "mode": "sync_error",
            "persisted": False,
            "encountered_npc_ids": list(explicit_npc_ids),
            "created_npc_ids": [],
            "changed": False,
            "error": type(exc).__name__,
        }

    explicit_entity_ids = tuple(
        dict.fromkeys(
            str(value).strip()
            for value in (*actor_ids, speaker_id or "")
            if str(value or "").strip()
        )
    )
    session, scene_lore = ensure_turn_scene_lore(
        campaign_id,
        session,
        result,
        explicit_entity_ids=explicit_entity_ids,
        sessions=sessions,
    )
    target_entity_ids = tuple(scene_lore.get("target_entity_ids") or ())
    portable_snapshot = _portable_materialized_snapshot(session, campaign_id)
    prefer_postgresql = scene_lore.get("postgresql_persisted") is not False
    loaded_snapshot = None
    if not runtime_only:
        loaded_snapshot = load_campaign_bible_snapshot(
            campaign_id,
            session=session,
            prefer_postgresql=prefer_postgresql,
            sessions=sessions,
        )
    snapshot, complete_bible_evidence, missing_materialized = (
        select_complete_turn_snapshot(
            loaded_snapshot,
            portable_snapshot,
            target_entity_ids,
        )
    )
    if target_entity_ids and (snapshot is None or missing_materialized):
        missing = ",".join(missing_materialized or target_entity_ids)
        raise RuntimeError(f"turn_lore_source_of_truth_unavailable:{missing}")
    if runtime_only:
        focused_entity_ids = {
            str(value).strip().casefold()
            for value in (*actor_ids, speaker_id or "", *target_entity_ids)
            if str(value).strip()
        }
        bible_evidence = tuple(
            record
            for record in complete_bible_evidence
            if focused_entity_ids.intersection(
                str(value).casefold() for value in record.entity_refs
            )
        )
    else:
        bible_evidence = complete_bible_evidence

    entity_ids = tuple(
        dict.fromkeys(
            (
                *_entity_ids(result, session, speaker_id, actor_ids),
                *target_entity_ids,
            )
        )
    )
    factions = _faction_ids(session, speaker_id)
    research = (
        None
        if runtime_only or snapshot is None
        else research_campaign_turn(
            campaign_id=campaign_id,
            query=player_input,
            session=session,
            speaker_id=speaker_id,
            actor_ids=actor_ids,
            faction_ids=factions,
            entity_ids=entity_ids,
            research_id=(
                f"research:{campaign_id}:"
                f"{result.get('turn_id') or result.get('tick') or 0}"
            ),
            max_topics=max_topics,
            snapshot=snapshot,
            sessions=sessions,
        )
    )
    hermes_evidence = research.result.evidence() if research else ()
    by_id: dict[str, EvidenceRecord] = {}
    for record in (*runtime, *bible_evidence, *hermes_evidence):
        previous = by_id.get(record.evidence_id)
        if previous is None or record.source_revision >= previous.source_revision:
            by_id[record.evidence_id] = record
    topic_titles = list(research.topic_titles) if research else []
    metadata: dict[str, Any] = {
        "hermes_used": research is not None and bool(research.result.sources),
        "hermes_research_id": research.result.research_id if research else "",
        "canon_topic_count": len(topic_titles),
        "canon_topic_titles": topic_titles,
        "lore_search_required": lore_required,
        "lore_search_performed": research is not None,
        "lore_topics_researched": len(topic_titles),
        "lore_topic_titles": topic_titles,
        "grounding_entity_ids": list(entity_ids),
        "hermes_source_ids": (
            [source.source_id for source in research.result.sources]
            if research
            else []
        ),
        "campaign_bible_revision": (
            research.snapshot.revision
            if research
            else snapshot.revision if snapshot else 0
        ),
        "campaign_bible_hash": (
            research.snapshot.content_hash
            if research
            else snapshot.content_hash if snapshot else ""
        ),
        "campaign_bible_evidence_count": len(bible_evidence),
        "runtime_evidence_count": len(runtime),
        "hermes_evidence_count": len(hermes_evidence),
        "faction_ids": list(factions),
        "grounding_passed": True,
        "research_read_only": True,
        "npc_lore_sync_mode": str(npc_lore_sync.get("mode") or ""),
        "npc_lore_persisted": npc_lore_sync.get("persisted") is True,
        "npc_lore_changed": npc_lore_sync.get("changed") is True,
        "encountered_npc_ids": list(
            npc_lore_sync.get("encountered_npc_ids") or ()
        ),
        "created_npc_lore_ids": list(
            npc_lore_sync.get("created_npc_ids") or ()
        ),
        "scene_lore_materialization_mode": str(scene_lore.get("mode") or ""),
        "scene_lore_persisted": scene_lore.get("persisted") is True,
        "scene_lore_postgresql_persisted": (
            scene_lore.get("postgresql_persisted") is not False
        ),
        "scene_lore_changed": scene_lore.get("changed") is True,
        "scene_lore_target_entity_ids": list(target_entity_ids),
        "scene_lore_created_entity_ids": list(
            scene_lore.get("created_entity_ids") or ()
        ),
        "scene_lore_created_document_ids": list(
            scene_lore.get("created_document_ids") or ()
        ),
        "scene_lore_source_of_truth_ready": not missing_materialized,
        "scene_lore_snapshot_source": str(
            _mapping(snapshot.provenance).get("source") if snapshot else ""
        ),
    }
    if npc_lore_sync.get("error"):
        metadata["npc_lore_sync_error"] = str(npc_lore_sync.get("error"))
    if scene_lore.get("database_error"):
        metadata["scene_lore_database_error"] = str(
            scene_lore.get("database_error")
        )
    if runtime_only:
        metadata["runtime_only"] = True
    elif requested_runtime_only:
        metadata["runtime_only_overridden_for_lore"] = True
    return TurnGroundingPacket(
        evidence=tuple(by_id.values()),
        metadata=metadata,
        research=research,
    )


def narrative_grounding_footer(
    metadata: Mapping[str, Any],
    *,
    block_count: int,
) -> dict[str, Any]:
    topics = int(metadata.get("canon_topic_count") or 0)
    passed = metadata.get("grounding_passed") is True
    searched = metadata.get("lore_search_performed") is True
    return {
        "canon_topics_used": topics,
        "topics_researched": int(metadata.get("lore_topics_researched") or topics),
        "lore_search_performed": searched,
        "narrative_blocks": int(block_count),
        "grounding_passed": passed,
        "label": (
            f"{topics} canon topics used · {int(block_count)} narrative blocks · "
            + ("grounding passed" if passed else "grounding failed")
        ),
        "research_label": (
            f"{topics} lore topics researched" if searched else "Runtime context only"
        ),
        "topic_titles": list(metadata.get("canon_topic_titles") or ()),
        "campaign_bible_revision": int(
            metadata.get("campaign_bible_revision") or 0
        ),
        "campaign_bible_hash": str(
            metadata.get("campaign_bible_hash") or ""
        ),
    }
