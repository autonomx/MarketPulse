from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, List, Set

from app.apps.rpg.world.conversation_topics import conversation_topics_for_state
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v) if not isinstance(v, str) else v

PIVOT_STOPWORDS = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "will", "would", "can", "could", "should", "may", "might", "must", "shall", "about", "tell", "me", "what", "know", "heard", "do", "you", "hidden", "there", "some", "any", "this", "that", "these", "those", "here", "there", "where", "when", "why", "how", "all", "some", "many", "much", "few", "little", "first", "last", "next", "new", "old", "good", "bad", "big", "small", "long", "short", "high", "low", "right", "wrong", "true", "false"}

PIVOT_REQUEST_MARKERS = {
    "danger",
    "dragon",
    "trouble",
    "lair",
    "mountain",
    "mountains",
    "hidden",
}

def _tokens(value: str) -> Set[str]:
    raw = re.findall(r"[a-z0-9']+", _safe_str(value).lower())
    return {token for token in raw if len(token) > 2 and token not in PIVOT_STOPWORDS}


def _raw_tokens(value: str) -> Set[str]:
    return {token for token in re.findall(r"[a-z0-9']+", _safe_str(value).lower()) if len(token) > 2}


def _clean_hint(value: str) -> str:
    tokens = [token for token in re.findall(r"[a-z0-9']+", _safe_str(value).lower()) if len(token) > 2]
    kept = [token for token in tokens if token not in PIVOT_STOPWORDS]
    return " ".join(kept[:8])


def _explicit_topic_request_hint(player_input: str) -> str:
    """Extract an unbacked-topic hint from ordinary player questions.

    This intentionally runs before backed-topic matching.  If the hint does
    not match deterministic topics, the caller should return requested=true and
    accepted=false/no_backed_topic_found rather than pretending no pivot was
    requested.
    """
    text = _safe_str(player_input).strip().lower()
    patterns = [
        r"\btell\s+me\s+about\s+(.+?)[\?\.!]*$",
        r"\bwhat\s+(?:can\s+you\s+)?(?:tell|know)\s+(?:me\s+)?about\s+(.+?)[\?\.!]*$",
        r"\bwhat\s+do\s+you\s+know\s+about\s+(.+?)[\?\.!]*$",
        r"\bhave\s+you\s+heard\s+about\s+(.+?)[\?\.!]*$",
        r"\bdo\s+you\s+know\s+about\s+(.+?)[\?\.!]*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            hint = _clean_hint(match.group(1))
            if hint:
                return hint
    return ""


def _request_hint_tokens(player_input: str) -> Set[str]:
    """Return topic-match tokens from a genre-neutral player topic request.

    This intentionally detects the *shape* of a request ("tell me about X",
    "what do you know about X", etc.) rather than hard-coding fantasy,
    sci-fi, modern, or any other genre-specific nouns.
    """
    explicit_hint = _explicit_topic_request_hint(player_input)
    if explicit_hint:
        tokens = _tokens(explicit_hint)
        if tokens:
            return tokens
    return _tokens(player_input)


def _score_topic(topic: Dict[str, Any], request_tokens: Set[str]) -> int:
    """Score a topic based on token overlap with the request."""
    topic = _safe_dict(topic)
    raw = " ".join([
        _safe_str(topic.get("title")),
        _safe_str(topic.get("summary")),
        _safe_str(topic.get("source_id")),
        _safe_str(topic.get("topic_id")),
    ])
    topic_tokens = set(
        word
        for word in re.sub(r"[^a-z0-9 ]", " ", raw.lower()).split()
        if word and len(word) > 2 and word not in PIVOT_STOPWORDS
    )
    overlap = request_tokens & topic_tokens
    return len(overlap)

def detect_conversation_topic_pivot(
    simulation_state: Dict[str, Any],
    player_input: str,
    current_topic: Dict[str, Any],
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    request_text = _safe_str(player_input).strip()
    explicit_hint = _explicit_topic_request_hint(request_text)
    raw_request_tokens = _raw_tokens(request_text)
    request_tokens = _request_hint_tokens(request_text)
    current_topic = _safe_dict(current_topic)
    current_topic_id = _safe_str(current_topic.get("topic_id"))
    requested = bool(explicit_hint) or bool(raw_request_tokens.intersection(PIVOT_REQUEST_MARKERS)) or "?" in request_text
    requested_topic_hint = explicit_hint or " ".join(sorted(request_tokens)[:8])

    if not requested:
        return {
            "requested": False,
            "accepted": False,
            "reason": "no_topic_hint_in_reply",
            "pivot_rejected_reason": "no_topic_hint_in_reply",
            "requested_topic_hint": "",
            "selected_topic_id": current_topic_id,
            "selected_topic_type": _safe_str(current_topic.get("topic_type")),
            "selected_topic": {},
            "candidate_count": 0,
            "source": "deterministic_conversation_pivot_runtime",
        }

    candidates: List[Dict[str, Any]] = []
    for topic in conversation_topics_for_state(simulation_state, settings=settings):
        topic = _safe_dict(topic)
        score = _score_topic(topic, request_tokens)
        if score < 2:  # require at least 2 meaningful overlaps
            continue
        topic_type = _safe_str(topic.get("topic_type"))
        if topic_type in {"scene_activity", "recent_event"}:
            # require higher score for generic topics
            if score < 3:
                continue
        candidate = deepcopy(topic)
        candidate["pivot_score"] = score
        candidates.append(candidate)

    candidates.sort(
        key=lambda topic: (
            int(topic.get("pivot_score") or 0),
            int(topic.get("priority") or 0),
            _safe_str(topic.get("topic_id")),
        ),
        reverse=True,
    )
    selected = candidates[0] if candidates else {}
    selected_topic_id = _safe_str(selected.get("topic_id"))
    accepted = bool(selected_topic_id)
    if accepted and selected_topic_id == current_topic_id:
        reason = "requested_topic_already_current_and_backed"
    elif accepted:
        reason = "pivot_topic_backed_by_state"
    else:
        reason = "no_backed_topic_found"
    return {
        "requested": requested,
        "accepted": accepted,
        "reason": reason,
        "pivot_rejected_reason": "" if accepted else reason,
        "requested_topic_hint": requested_topic_hint,
        "selected_topic_id": selected_topic_id or "",
        "selected_topic_type": _safe_str(selected.get("topic_type") or current_topic.get("topic_type")),
        "selected_topic": deepcopy(selected) if accepted else {},
        "candidate_count": len(candidates),
        "source": "deterministic_conversation_pivot_runtime",
    }