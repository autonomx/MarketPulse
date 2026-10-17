"""Deterministic grounding guard for narration memory references."""
# mypy: disable-error-code=import-untyped
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set

from app.apps.rpg.narration.memory_prompt import build_relevant_memory_context_from_runtime
from app.prompts import prompt_template
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

_PROMPT_1 = prompt_template('rpg.ai_memory_narration_grounding.memory_narration_prompt_block', "1", "- You may say an NPC remembers, recalls, or refers to a prior event only when that fact appears in Relevant Memory or authoritative state.")
_PROMPT_2 = prompt_template('rpg.ai_memory_narration_grounding.memory_narration_prompt_block_2', "1", "- Do not invent remembered purchases, debts, promises, warnings, injuries, crimes, rumors, quest clues, or relationships.")
_PROMPT_3 = prompt_template('rpg.ai_memory_narration_grounding.memory_narration_prompt_block_3', "1", "- If no backing memory exists, answer from the current turn only and avoid words like remember, again, last time, or earlier.")

MEMORY_NARRATION_GROUNDING_SOURCE = "memory_narration_grounding_guard"
MEMORY_NARRATION_GROUNDING_VERSION = "rpg_memory_narration_grounding_v1"

_MEMORY_REFERENCE_MARKERS = (
    "remember",
    "remembers",
    "remembered",
    "recall",
    "recalls",
    "recalled",
    "last time",
    "again",
    "earlier",
    "as before",
    "from before",
    "same as before",
    "you asked",
    "you bought",
    "you paid",
    "you promised",
    "you warned",
    "you told",
    "i told you",
    "still short",
    "short on coin",
)

_TOKEN_STOPWORDS = {
    "about",
    "again",
    "before",
    "earlier",
    "from",
    "have",
    "last",
    "remember",
    "remembered",
    "remembers",
    "recall",
    "recalled",
    "recalls",
    "same",
    "that",
    "their",
    "them",
    "then",
    "there",
    "they",
    "this",
    "time",
    "told",
    "warned",
    "were",
    "what",
    "when",
    "with",
    "you",
    "your",
}


def _clean_text(value: Any, limit: int = 260) -> str:
    text = " ".join(_safe_str(value).strip().split())
    return text[:limit]


def _clean_id(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()[:120]
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", _safe_str(value).strip().casefold())


def _memory_reference_claimed(text: Any) -> bool:
    lower = _norm(text)
    return any(marker in lower for marker in _MEMORY_REFERENCE_MARKERS)


def _sentences(text: Any) -> List[str]:
    text = _safe_str(text).strip()
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    return parts or [text]


def _tokens(text: Any) -> Set[str]:
    out: Set[str] = set()
    for token in re.findall(r"[a-z0-9][a-z0-9'-]{2,}", _norm(text)):
        token = token.strip("'-")
        if len(token) < 4 or token in _TOKEN_STOPWORDS:
            continue
        out.add(token)
    return out


def _extract_actor_ids(context: Mapping[str, Any]) -> List[str]:
    context = _safe_dict(context)
    turn_contract = _safe_dict(context.get("turn_contract"))
    interpreted = _safe_dict(
        turn_contract.get("interpreted_action")
        or turn_contract.get("action")
        or context.get("action")
    )
    resolved = _safe_dict(
        context.get("resolved_result")
        or turn_contract.get("resolved_result")
        or turn_contract.get("resolved_action")
    )
    npc = _safe_dict(resolved.get("npc"))
    values = [
        interpreted.get("target_id"),
        interpreted.get("npc_id"),
        interpreted.get("target_name"),
        resolved.get("target_id"),
        resolved.get("npc_id"),
        resolved.get("target_name"),
        resolved.get("npc_name"),
        npc.get("id"),
        npc.get("speaker"),
        npc.get("name"),
    ]
    out: List[str] = []
    seen: Set[str] = set()
    for value in values:
        item = _clean_id(value)
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
        if len(out) >= 4:
            break
    return out


def _extract_location_id(context: Mapping[str, Any]) -> str:
    context = _safe_dict(context)
    scene = _safe_dict(context.get("scene") or context.get("current_scene"))
    grounded = _safe_dict(context.get("grounded"))
    resolved = _safe_dict(context.get("resolved_result"))
    runtime = _safe_dict(context.get("runtime_state"))
    current_scene = _safe_dict(runtime.get("current_scene"))
    player = _safe_dict(_safe_dict(context.get("simulation_state")).get("player_state"))
    return _clean_id(
        scene.get("location_id")
        or grounded.get("location_id")
        or current_scene.get("location_id")
        or resolved.get("location_id")
        or player.get("location_id")
    )


def _derive_relevant_memory(context: Mapping[str, Any]) -> Dict[str, Any]:
    context = _safe_dict(context)
    direct = _safe_dict(
        context.get("relevant_memory")
        or _safe_dict(context.get("turn_grounding_packet")).get("relevant_memory")
    )
    if direct:
        return direct
    runtime_state = _safe_dict(context.get("runtime_state"))
    if not runtime_state:
        return {}
    return build_relevant_memory_context_from_runtime(
        runtime_state,
        player_input=context.get("player_input") or context.get("player_action"),
        actor_ids=_extract_actor_ids(context),
        location_id=_extract_location_id(context),
    )


def _compact_memory_entry(entry: Any) -> Dict[str, Any]:
    entry = _safe_dict(entry)
    compact = {
        "id": _clean_id(entry.get("id")),
        "kind": _clean_id(entry.get("kind")),
        "text": _clean_text(entry.get("text")),
        "actor_id": _clean_id(entry.get("actor_id")),
        "subject_id": _clean_id(entry.get("subject_id")),
        "location_id": _clean_id(entry.get("location_id")),
        "visibility": _clean_id(entry.get("visibility")) or "public",
        "event_type": _clean_id(entry.get("event_type")),
        "tags": [_clean_id(tag).casefold() for tag in _safe_list(entry.get("tags"))[:6] if _clean_id(tag)],
    }
    return {key: value for key, value in compact.items() if value not in ("", [], None)}


def _memory_entries(memory_context: Mapping[str, Any]) -> List[Dict[str, Any]]:
    memory_context = _safe_dict(memory_context)
    entries: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for section in ("recent", "actors", "world"):
        for entry in _safe_list(memory_context.get(section)):
            compact = _compact_memory_entry(entry)
            entry_id = _clean_id(compact.get("id"))
            key = entry_id or repr(sorted(compact.items()))
            if compact and key not in seen:
                seen.add(key)
                compact["section"] = section
                entries.append(compact)
    return entries


def _weak_context_tokens(context: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]) -> Set[str]:
    weak: Set[str] = {"bran", "elara", "player"}
    for value in _extract_actor_ids(context):
        weak.update(_tokens(value.replace("npc:", "").replace("_", " ")))
    for entry in entries:
        for key in ("actor_id", "subject_id"):
            value = _safe_str(_safe_dict(entry).get(key))
            weak.update(_tokens(value.replace("npc:", "").replace("_", " ")))
    return weak


def _iter_authoritative_values(value: Any, *, depth: int = 0) -> Iterable[str]:
    if depth > 5:
        return
    if isinstance(value, str):
        text = _clean_text(value, 500)
        if text:
            yield text
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_authoritative_values(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value[:80]:
            yield from _iter_authoritative_values(item, depth=depth + 1)


def _authoritative_fact_texts(context: Mapping[str, Any]) -> List[str]:
    context = _safe_dict(context)
    turn_contract = _safe_dict(context.get("turn_contract"))
    resolved = _safe_dict(context.get("resolved_result"))
    texts: List[str] = []
    for value in _safe_list(context.get("recent_authoritative_facts")):
        text = _clean_text(value, 500)
        if text:
            texts.append(text)
    for source in (
        turn_contract.get("narration_brief"),
        turn_contract.get("state_delta"),
        turn_contract.get("allowed_facts"),
        turn_contract.get("new_facts"),
        turn_contract.get("result"),
        turn_contract.get("resolved_result"),
        resolved,
    ):
        texts.extend(_iter_authoritative_values(source))
    deduped: List[str] = []
    seen: Set[str] = set()
    for text in texts:
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(text)
        if len(deduped) >= 24:
            break
    return deduped


def build_memory_narration_evidence(context: Mapping[str, Any]) -> Dict[str, Any]:
    """Extract bounded memory evidence for narration validation."""
    context = _safe_dict(context)
    memory_context = _derive_relevant_memory(context)
    entries = _memory_entries(memory_context)
    authoritative_facts = _authoritative_fact_texts(context)
    evidence_texts = [entry.get("text", "") for entry in entries]
    evidence_texts.extend(authoritative_facts)
    evidence_tokens: Set[str] = set()
    for text in evidence_texts:
        evidence_tokens.update(_tokens(text))
    weak_tokens = _weak_context_tokens(context, entries)
    evidence_tokens.difference_update(weak_tokens)
    return {
        "format_version": MEMORY_NARRATION_GROUNDING_VERSION,
        "source": MEMORY_NARRATION_GROUNDING_SOURCE,
        "memory_context_version": _clean_id(memory_context.get("format_version")),
        "memory_ids": [_clean_id(entry.get("id")) for entry in entries if _clean_id(entry.get("id"))],
        "entries": entries[:12],
        "authoritative_facts": authoritative_facts[:12],
        "evidence_tokens": sorted(evidence_tokens)[:120],
        "weak_tokens": sorted(weak_tokens)[:40],
    }


def _sentence_backed(sentence: str, evidence: Mapping[str, Any]) -> bool:
    if not _memory_reference_claimed(sentence):
        return True
    entries = _safe_list(evidence.get("entries"))
    authoritative_facts = _safe_list(evidence.get("authoritative_facts"))
    if not entries and not authoritative_facts:
        return False
    claim_tokens = _tokens(sentence)
    claim_tokens.difference_update(set(_safe_list(evidence.get("weak_tokens"))))
    if not claim_tokens:
        return True
    evidence_tokens = set(_safe_list(evidence.get("evidence_tokens")))
    return bool(claim_tokens.intersection(evidence_tokens))


def validate_memory_narration_text(text: Any, context: Mapping[str, Any]) -> Dict[str, Any]:
    """Return memory-reference violations for narration/dialogue text."""
    evidence = build_memory_narration_evidence(context)
    violations: List[Dict[str, str]] = []
    for sentence in _sentences(text):
        if not _memory_reference_claimed(sentence):
            continue
        if _sentence_backed(sentence, evidence):
            continue
        violations.append(
            {
                "code": "unsupported_memory_reference",
                "reason": "memory reference is not backed by relevant_memory or authoritative state",
                "sentence": sentence[:260],
            }
        )
    return {
        "ok": not violations,
        "violations": violations,
        "evidence": evidence,
        "source": MEMORY_NARRATION_GROUNDING_SOURCE,
    }


def _sanitize_text(text: Any, context: Mapping[str, Any], *, fallback: str = "") -> str:
    raw = _safe_str(text).strip()
    if not raw:
        return ""
    evidence = build_memory_narration_evidence(context)
    kept: List[str] = []
    for sentence in _sentences(raw):
        if _sentence_backed(sentence, evidence):
            kept.append(sentence)
    if kept:
        return " ".join(kept).strip()
    return _safe_str(fallback).strip()


def sanitize_memory_narration_payload(payload: Mapping[str, Any], context: Mapping[str, Any]) -> Dict[str, Any]:
    """Strip unsupported memory-reference claims and attach validation metadata."""
    out = deepcopy(_safe_dict(payload))
    context = _safe_dict(context)
    original_text = " ".join(
        [
            _safe_str(out.get("narration")),
            _safe_str(out.get("action")),
            _safe_str(_safe_dict(out.get("npc")).get("line")),
        ]
    )
    original_validation = validate_memory_narration_text(original_text, context)
    out["narration"] = _sanitize_text(
        out.get("narration"),
        context,
        fallback=_safe_str(context.get("authoritative_fallback")),
    )
    out["action"] = _sanitize_text(out.get("action"), context)
    npc = _safe_dict(out.get("npc"))
    if npc:
        npc["line"] = _sanitize_text(
            npc.get("line"),
            context,
            fallback="I can only speak to what I know right now.",
        )
        out["npc"] = npc
    combined_text = " ".join(
        [
            _safe_str(out.get("narration")),
            _safe_str(out.get("action")),
            _safe_str(_safe_dict(out.get("npc")).get("line")),
        ]
    )
    final_validation = validate_memory_narration_text(combined_text, context)
    out["memory_grounding_validation"] = {
        "ok": final_validation.get("ok"),
        "violations": final_validation.get("violations"),
        "original_violations": original_validation.get("violations"),
        "evidence": final_validation.get("evidence"),
        "source": MEMORY_NARRATION_GROUNDING_SOURCE,
    }
    return out


def memory_narration_prompt_block(context: Mapping[str, Any]) -> str:
    evidence = build_memory_narration_evidence(context)
    memory_ids = _safe_list(evidence.get("memory_ids"))
    lines = [
        "Memory grounding guard:",
        _PROMPT_1.text,
        _PROMPT_2.text,
        _PROMPT_3.text,
        "- Backed memory ids: " + (", ".join(memory_ids[:12]) if memory_ids else "none"),
    ]
    return "\n".join(lines)
