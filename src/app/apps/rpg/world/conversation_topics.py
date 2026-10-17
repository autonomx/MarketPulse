from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.location_registry import current_location_id, get_location
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _topic(
    *,
    topic_id: str,
    topic_type: str,
    title: str,
    summary: str,
    source_id: str,
    source_kind: str,
    location_id: str,
    priority: int,
    allowed_facts: List[str],
    allowed_signal_kinds: List[str],
) -> Dict[str, Any]:
    return {
        "topic_id": topic_id,
        "topic_type": topic_type,
        "title": title,
        "summary": summary,
        "source_id": source_id,
        "source_kind": source_kind,
        "location_id": location_id,
        "priority": int(priority or 0),
        "allowed_facts": [fact for fact in allowed_facts if _safe_str(fact)],
        "allowed_signal_kinds": allowed_signal_kinds,
        "forbidden_direct_effects": [
            "quest_started",
            "quest_completed",
            "reward_granted",
            "item_created",
            "currency_changed",
            "stock_changed",
            "journal_entry_created",
            "location_changed",
            "combat_started",
            "npc_moved",
        ],
        "source": "deterministic_conversation_topic_runtime",
    }


def _location_topic(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    location_id = current_location_id(simulation_state)
    location = get_location(location_id)
    name = _safe_str(location.get("name") or location_id)
    if location_id == "loc_tavern":
        return _topic(
            topic_id="topic:location:loc_tavern:mood",
            topic_type="location_smalltalk",
            title="The tavern's mood",
            summary="The tavern is busy with travelers, food, and low conversation.",
            source_id=location_id,
            source_kind="location",
            location_id=location_id,
            priority=1,
            allowed_facts=[
                "The tavern is busy with travelers.",
                "The room has been busier than usual.",
            ],
            allowed_signal_kinds=["rumor_interest", "ambient_interest"],
        )
    if location_id == "loc_market":
        return _topic(
            topic_id="topic:location:loc_market:trade",
            topic_type="location_smalltalk",
            title="Market traffic",
            summary="The market is busy with stalls, porters, and customers.",
            source_id=location_id,
            source_kind="location",
            location_id=location_id,
            priority=1,
            allowed_facts=[
                "The market crowd is moving quickly.",
                "Busy stalls can mean both opportunity and trouble.",
            ],
            allowed_signal_kinds=["market_pressure", "ambient_interest"],
        )
    return _topic(
        topic_id=f"topic:location:{location_id}:ambient",
        topic_type="location_smalltalk",
        title=f"Local activity near {name}",
        summary=f"NPCs nearby discuss what is happening around {name}.",
        source_id=location_id,
        source_kind="location",
        location_id=location_id,
        priority=1,
        allowed_facts=[f"NPCs are present near {name}."],
        allowed_signal_kinds=["ambient_interest"],
    )


def _world_event_topics(simulation_state: Dict[str, Any], settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not settings.get("allow_event_discussion", True):
        return []
    location_id = current_location_id(simulation_state)
    event_state = _safe_dict(simulation_state.get("world_event_state"))
    events = _safe_list(event_state.get("events"))
    topics: List[Dict[str, Any]] = []
    for event in events[-8:]:
        event = _safe_dict(event)
        event_id = _safe_str(event.get("event_id"))
        event_kind = _safe_str(event.get("kind"))
        # Avoid self-referential loops where NPCs discuss the previous
        # npc_conversation event, which then creates another npc_conversation
        # topic. Quest/event scenarios should seed non-conversation events.
        if event_kind == "npc_conversation":
            continue
        title = _safe_str(event.get("title") or event.get("kind") or "Recent event")
        summary = _safe_str(event.get("summary"))
        if not event_id or not summary:
            continue
        event_location = _safe_str(event.get("location_id") or location_id)
        topics.append(
            _topic(
                topic_id=f"topic:event:{event_id}",
                topic_type="recent_event",
                title=title,
                summary=summary,
                source_id=event_id,
                source_kind="world_event",
                location_id=event_location,
                priority=6,
                allowed_facts=[summary],
                allowed_signal_kinds=["event_attention", "ambient_interest"],
            )
        )
    return topics


def _journal_topics(simulation_state: Dict[str, Any], settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    location_id = current_location_id(simulation_state)
    journal_state = _safe_dict(simulation_state.get("journal_state"))
    entries = _safe_list(journal_state.get("entries"))
    topics: List[Dict[str, Any]] = []
    for entry in entries[-8:]:
        entry = _safe_dict(entry)
        entry_id = _safe_str(entry.get("entry_id"))
        kind = _safe_str(entry.get("kind"))
        title = _safe_str(entry.get("title") or entry_id)
        summary = _safe_str(entry.get("summary"))
        if not entry_id or not summary:
            continue
        if kind == "rumor" and not settings.get("allow_rumor_discussion", True):
            continue
        topics.append(
            _topic(
                topic_id=f"topic:journal:{entry_id}",
                topic_type="rumor" if kind == "rumor" else "journal",
                title=title,
                summary=summary,
                source_id=entry_id,
                source_kind="journal_entry",
                location_id=location_id,
                priority=7 if kind == "rumor" else 5,
                allowed_facts=[summary],
                allowed_signal_kinds=["rumor_pressure", "quest_interest", "event_attention"],
            )
        )
    return topics


def _quest_topics(simulation_state: Dict[str, Any], settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not settings.get("allow_quest_discussion", True):
        return []
    quest_state = _safe_dict(simulation_state.get("quest_state"))
    raw_quests = (
        _safe_list(quest_state.get("quests"))
        + _safe_list(quest_state.get("active_quests"))
        + _safe_list(quest_state.get("current_quests"))
    )
    seen_quest_ids = set()
    quests: List[Dict[str, Any]] = []
    for raw_quest in raw_quests:
        raw_quest = _safe_dict(raw_quest)
        quest_id = _safe_str(raw_quest.get("quest_id") or raw_quest.get("id"))
        if not quest_id or quest_id in seen_quest_ids:
            continue
        seen_quest_ids.add(quest_id)
        if "quest_id" not in raw_quest:
            raw_quest = dict(raw_quest)
            raw_quest["quest_id"] = quest_id
        quests.append(raw_quest)
    location_id = current_location_id(simulation_state)
    topics: List[Dict[str, Any]] = []
    for quest in quests[-8:]:
        quest = _safe_dict(quest)
        quest_id = _safe_str(quest.get("quest_id"))
        status = _safe_str(quest.get("status") or "active")
        title = _safe_str(quest.get("title") or quest_id)
        summary = _safe_str(quest.get("summary") or title)
        if not quest_id or status in {"completed", "failed"}:
            continue
        topics.append(
            _topic(
                topic_id=f"topic:quest:{quest_id}",
                topic_type="quest",
                title=title,
                summary=summary,
                source_id=quest_id,
                source_kind="quest",
                location_id=_safe_str(quest.get("location_id") or location_id),
                priority=8,
                allowed_facts=[summary, f"The quest '{title}' is {status}."],
                allowed_signal_kinds=["quest_interest", "danger_warning", "rumor_pressure"],
            )
        )
    return topics


def _is_synthetic_environment_memory(memory: Dict[str, Any]) -> bool:
    memory = _safe_dict(memory)
    haystack = " ".join(
        _safe_str(memory.get(key)).lower()
        for key in (
            "memory_id",
            "actor_id",
            "target_id",
            "target_name",
            "npc_id",
            "summary",
            "kind",
            "action_type",
            "semantic_action_type",
            "semantic_family",
            "activity_label",
        )
    )
    if "the room/environment" in haystack or "room/environment" in haystack:
        return True
    if "the tavern atmosphere" in haystack or "tavern atmosphere" in haystack:
        return True
    if "npc:the room/environment" in haystack or "target:environment" in haystack:
        return True
    if "environment/npcs" in haystack or "npcs (general)" in haystack:
        return True
    if "partial observe interaction" in haystack:
        return True
    if "observe" in haystack and (
        "room" in haystack
        or "environment" in haystack
        or "atmosphere" in haystack
        or "ambience" in haystack
        or "scene" in haystack
    ):
        return True
    return False


def _is_npc_backed_memory(memory: Dict[str, Any]) -> bool:
    memory = _safe_dict(memory)
    actor_id = _safe_str(memory.get("actor_id") or memory.get("npc_id") or memory.get("subject_id"))
    target_id = _safe_str(memory.get("target_id"))
    if actor_id.startswith("npc:"):
        return True
    if target_id.startswith("npc:") and "room/environment" not in target_id.lower():
        return True
    return False


def _memory_topics(simulation_state: Dict[str, Any], settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not settings.get("allow_memory_discussion", True):
        return []
    memory_state = _safe_dict(simulation_state.get("memory_state"))
    location_id = current_location_id(simulation_state)
    memories = _safe_list(memory_state.get("service_memories")) + _safe_list(memory_state.get("social_memories"))
    topics: List[Dict[str, Any]] = []
    for memory in memories[-8:]:
        memory = _safe_dict(memory)
        memory_id = _safe_str(memory.get("memory_id"))
        summary = _safe_str(memory.get("summary"))
        if not memory_id or not summary:
            continue
        if _is_synthetic_environment_memory(memory):
            continue
        if not _is_npc_backed_memory(memory):
            continue
        topics.append(
            _topic(
                topic_id=f"topic:memory:{memory_id}",
                topic_type="memory",
                title="Recent memory",
                summary=summary,
                source_id=memory_id,
                source_kind="memory",
                location_id=location_id,
                priority=4,
                allowed_facts=[summary],
                allowed_signal_kinds=["social_tension", "ambient_interest"],
            )
        )
    return topics


def conversation_topics_for_state(
    simulation_state: Dict[str, Any],
    *,
    settings: Dict[str, Any] | None = None,
    exclude_event_ids: List[str] | None = None,
) -> List[Dict[str, Any]]:
    settings = _safe_dict(settings)
    excluded_event_ids = set(exclude_event_ids or [])
    topics: List[Dict[str, Any]] = []
    topics.extend(_quest_topics(simulation_state, settings))
    topics.extend(_journal_topics(simulation_state, settings))
    event_topics = _world_event_topics(simulation_state, settings)
    if excluded_event_ids:
        event_topics = [
            topic
            for topic in event_topics
            if _safe_str(topic.get("source_id")) not in excluded_event_ids
        ]
    topics.extend(event_topics)
    topics.extend(_memory_topics(simulation_state, settings))
    topics.append(_location_topic(simulation_state))

    deduped: Dict[str, Dict[str, Any]] = {}
    for topic in topics:
        topic_id = _safe_str(topic.get("topic_id"))
        if not topic_id:
            continue
        existing = deduped.get(topic_id)
        if not existing or _safe_int(topic.get("priority")) > _safe_int(existing.get("priority")):
            deduped[topic_id] = topic

    out = list(deduped.values())
    out.sort(key=lambda topic: (_safe_int(topic.get("priority")), _safe_str(topic.get("topic_id"))), reverse=True)
    return deepcopy(out)


def select_conversation_topic(
    simulation_state: Dict[str, Any],
    *,
    settings: Dict[str, Any] | None = None,
    forced_topic_type: str = "",
    exclude_event_ids: List[str] | None = None,
) -> Dict[str, Any]:
    topics = conversation_topics_for_state(
        simulation_state,
        settings=settings,
        exclude_event_ids=exclude_event_ids,
    )
    if forced_topic_type:
        for topic in topics:
            if _safe_str(topic.get("topic_type")) == forced_topic_type:
                return deepcopy(topic)
    return deepcopy(topics[0]) if topics else {}


def topic_is_backed_by_state(topic: Dict[str, Any]) -> bool:
    topic = _safe_dict(topic)
    topic_type = _safe_str(topic.get("topic_type"))
    source_id = _safe_str(topic.get("source_id"))
    facts = _safe_list(topic.get("allowed_facts"))
    if topic_type == "location_smalltalk":
        return bool(source_id and facts)
    return bool(source_id and facts)


# ── Bundle H: topic pivot detection ─────────────────────────────────────────

_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "i", "you", "he",
    "she", "it", "we", "they", "what", "about", "that", "this", "of",
    "in", "on", "at", "to", "for", "with", "by", "from", "up", "and",
    "but", "or", "not", "no", "so", "me", "my", "your", "any", "all",
    "know", "tell", "say", "said", "told", "hear", "heard", "mean",
    "means", "meant", "please", "just", "still", "much", "more", "how",
    "its", "our", "their", "us", "him", "her", "them", "those", "these",
})


