"""Low-latency LLM path for clearly non-stateful addressed NPC dialogue."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.session.turn_grounding import build_turn_grounding_packet, nearby_npc_profiles
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.ai_compact_dialogue.prompt', "1", 'Reply as the NPC in one or two concise spoken sentences. Output only the words they say. No label, narration, JSON, or markdown. Use only public context; do not invent facts. If unsupported, say you do not know. Preserve recent continuity without repeating lines.\nPLAYER: {v0}\nCONTEXT: ')
_PROMPT_2 = prompt_template('rpg.ai_compact_dialogue.choose_speaker', "1", 'The player is speaking to someone in the scene. Using the names, roles, descriptions and recent turns, decide which one present NPC the player addresses, then reply as that NPC in one or two concise spoken sentences. Use only public context; do not invent facts. If unsupported, say you do not know. If no present NPC fits, give an empty speaker_id. Return JSON only: {{"speaker_id": "<an id from present_npcs>", "line": "<the words they say>"}}.\nPLAYER: {v0}\nCONTEXT: ')

COMPACT_DIALOGUE_SOURCE = "compact_grounded_dialogue_v1"

_DIALOGUE_MARKERS = (
    "ask ",
    "say ",
    "tell ",
    "talk ",
    "speak ",
    "how are",
    "how is",
    "how's",
    "what do you think",
    "what have you heard",
    "what's your opinion",
    "what is your opinion",
)
_STATEFUL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:attack|fight|hit|kill|strike|defend|steal|threaten|intimidate)\b",
        r"\b(?:buy|sell|pay|price|cost|trade|hire|purchase|refund)\b",
        r"\b(?:food|meal|drink|ale|room|lodging|ration|supplies|menu|offer|available)\b",
        r"\b(?:give|take|use|equip|drop|pick up|cast)\b",
        r"\b(?:travel|leave|go to|head to|follow me|join me)\b",
        r"\b(?:quest|reward|inventory|discount|persuade|convince)\b",
        r"\b(?:secret|private|code phrase|warning phrase)\b",
    )
)
_ABSENCE_PATTERN = re.compile(
    r"\b(?:away|absent|not here|isn't here|is not here|call for|look for|find)\b",
    re.IGNORECASE,
)
_ADDRESSED_NAME_PATTERN = re.compile(
    r"\b(?:ask|tell|say to|talk to|speak to)\s+([A-Z][A-Za-z0-9_'-]{1,40})"
    r"(?:\s+and\s+([A-Z][A-Za-z0-9_'-]{1,40}))?\b",
    re.IGNORECASE,
)
_GENERIC_SPEAKERS = {
    "", "general npcs/scene", "narrator", "omnix", "scene", "system", "you", "player"
}


def _d(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _l(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _s(value: Any) -> str:
    return str(value) if value is not None else ""


def _clip(value: Any, limit: int) -> str:
    return _s(value).strip()[:limit]


def _clean_line(value: Any, speaker: str) -> str:
    text = _s(value).strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
    prefix = f"{speaker}:"
    if text.casefold().startswith(prefix.casefold()):
        text = text[len(prefix):].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return text[:700].strip()


def _inject_recent_dialogue_profile(
    packet: Dict[str, Any],
    *,
    player_input: str,
    runtime_state: Dict[str, Any],
    public_state: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Continue a short, untargeted question with the last concrete NPC speaker."""

    npc_context = _d(packet.get("npc_context"))
    if _l(npc_context.get("addressed_npcs")):
        return packet
    if _ADDRESSED_NAME_PATTERN.search(player_input) or _ABSENCE_PATTERN.search(player_input):
        return packet
    if any(pattern.search(player_input) for pattern in _STATEFUL_PATTERNS):
        return packet

    runtime = _d(runtime_state)
    recent = _l(runtime.get("recent_interactions"))
    last = _d(runtime.get("last_interaction")) or (_d(recent[-1]) if recent else {})
    if _s(last.get("kind")).casefold() != "npc_dialogue":
        return packet
    speaker = _clip(last.get("speaker"), 80)
    if speaker.casefold() in _GENERIC_SPEAKERS:
        return packet

    summary = _clip(_d(public_state).get("summary"), 1200)
    if summary and not re.search(rf"\b{re.escape(speaker)}\b", summary, re.IGNORECASE):
        return packet
    slug = re.sub(r"[^a-z0-9]+", "_", speaker.casefold()).strip("_")
    profile = {
        "id": f"npc:{slug}",
        "name": speaker,
        "visible_profile": {
            "short_description": summary,
            "public_biography": "",
            "visible_mood": "",
            "speech_style": "",
        },
        "personality_profile": {},
        "relationship_to_player": {},
        "knowledge_boundaries": {},
        "source": "recent_dialogue_continuity",
    }
    copied = deepcopy(packet)
    copied_npc_context = _d(copied.get("npc_context"))
    copied_npc_context["addressed_npcs"] = [profile]
    copied["npc_context"] = copied_npc_context
    priority = _d(copied.get("priority_context"))
    priority["addressed_npc_ids"] = [profile["id"]]
    recent_turns = _l(priority.get("recent_turns"))
    continuity_turn = {
        "player_input": _clip(last.get("player_input"), 180),
        "summary": _clip(last.get("npc_line") or last.get("narration"), 300),
        "speaker": speaker,
    }
    if continuity_turn["player_input"] or continuity_turn["summary"]:
        priority["recent_turns"] = [*recent_turns, continuity_turn][-6:]
    copied["priority_context"] = priority
    return copied


def is_compact_dialogue_candidate(
    *,
    player_input: str,
    grounding_packet: Dict[str, Any],
    candidate_action: Dict[str, Any] | None = None,
) -> bool:
    """Conservatively identify dialogue that cannot request a state mutation, to one known NPC."""

    if not _compact_dialogue_gate(player_input, grounding_packet, candidate_action):
        return False
    addressed = _l(_d(_d(grounding_packet).get("npc_context")).get("addressed_npcs"))
    return len(addressed) == 1


def _compact_dialogue_gate(
    player_input: str,
    grounding_packet: Dict[str, Any],
    candidate_action: Dict[str, Any] | None,
) -> bool:
    text = _s(player_input).strip()
    lowered = text.casefold()
    if not text or not ("?" in text or any(marker in lowered for marker in _DIALOGUE_MARKERS)):
        return False
    if any(pattern.search(text) for pattern in _STATEFUL_PATTERNS):
        return False

    action_type = _s(_d(candidate_action).get("action_type")).strip().lower()
    if action_type not in {"", "ask", "conversation", "dialogue", "observe", "social_activity", "talk"}:
        return False

    priority = _d(_d(grounding_packet).get("priority_context"))
    active_modes = _d(priority.get("active_modes"))
    return active_modes.get("combat_active") is not True


def _compact_context(packet: Dict[str, Any]) -> Dict[str, Any]:
    priority = _d(packet.get("priority_context"))
    profile = _d(_l(_d(packet.get("npc_context")).get("addressed_npcs"))[0])
    visible = _d(profile.get("visible_profile"))
    personality = _d(profile.get("personality_profile"))
    knowledge = _d(profile.get("knowledge_boundaries"))
    context: Dict[str, Any] = {
        "scene": {
            key: value
            for key, value in _d(priority.get("current_scene")).items()
            if value and key in {"location_name", "summary"}
        },
        "recent_turns": _l(priority.get("recent_turns"))[-3:],
        "npc": {
            "name": _clip(profile.get("name"), 80),
            "role": _clip(profile.get("role"), 100),
            "description": _clip(visible.get("short_description"), 220),
            "public_biography": _clip(visible.get("public_biography"), 280),
            "visible_mood": _clip(visible.get("visible_mood"), 100),
            "personality": _clip(personality.get("summary"), 300),
            "speech_style": _clip(visible.get("speech_style"), 220),
            "speech_examples": [_clip(row, 140) for row in _l(personality.get("speech_examples"))[:3]],
            "relationship_to_player": _d(profile.get("relationship_to_player")),
            "publicly_knows": [_clip(row, 140) for row in _l(knowledge.get("publicly_knows"))[:6]],
            "may_discuss": [_clip(row, 140) for row in _l(knowledge.get("may_discuss"))[:6]],
            "does_not_know": [_clip(row, 140) for row in _l(knowledge.get("does_not_know"))[:6]],
        },
        "relevant_memory": {
            key: value
            for key, value in _d(packet.get("relevant_memory")).items()
            if key in {"recent", "actors", "world"} and value
        },
    }
    context["npc"] = {key: value for key, value in context["npc"].items() if value}
    return {key: value for key, value in context.items() if value}


def build_compact_dialogue_advisory(
    *,
    llm_gateway: Any,
    player_input: str,
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    candidate_action: Dict[str, Any] | None = None,
    public_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Generate one grounded NPC line, or return empty to use the full semantic path."""

    if llm_gateway is None or not hasattr(llm_gateway, "generate"):
        return {}
    packet = build_turn_grounding_packet(
        player_input=player_input,
        simulation_state=simulation_state,
        runtime_state=runtime_state,
        candidate_action=candidate_action,
    )
    packet = _inject_recent_dialogue_profile(
        packet,
        player_input=player_input,
        runtime_state=runtime_state,
        public_state=public_state,
    )
    addressed = _l(_d(packet.get("npc_context")).get("addressed_npcs"))
    if len(addressed) == 1 and is_compact_dialogue_candidate(
        player_input=player_input,
        grounding_packet=packet,
        candidate_action=candidate_action,
    ):
        profile = _d(addressed[0])
        speaker = _clip(profile.get("name") or profile.get("id"), 80)
        prompt = (
            _PROMPT_1.format(v0=(_clip(player_input, 500)))
            + json.dumps(_compact_context(packet), ensure_ascii=False, separators=(",", ":"))
        )
        raw = llm_gateway.generate(
            prompt,
            provider_options={
                "temperature": 0.55,
                "max_tokens": 80,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        line = _clean_line(raw, speaker)
    elif not addressed and _compact_dialogue_gate(player_input, packet, candidate_action):
        chosen = _choose_speaker(
            llm_gateway,
            packet,
            player_input=player_input,
            simulation_state=simulation_state,
            runtime_state=runtime_state,
        )
        if not chosen:
            return {}
        packet, profile, raw, line = chosen
        speaker = _clip(profile.get("name") or profile.get("id"), 80)
        line = _clean_line(line, speaker)
    else:
        return {}
    if not line:
        return {}
    visible_response = {"narration": "", "npc": {"speaker": speaker, "line": line}}
    return {
        "action_type": "social_activity",
        "semantic_family": "social",
        "interaction_mode": "direct",
        "target_id": _s(profile.get("id")),
        "target_name": speaker,
        "stateful": False,
        "needs_runtime_resolution": False,
        "literal_action_requested": False,
        "state_mutation_requested": False,
        "risk_domain": "none",
        "utterance_mode": "casual_conversation",
        "intent_summary": _clip(player_input, 180),
        "evidence_spans": [_clip(player_input, 180)],
        "direct_response_gate": {
            "safe_to_display_now": True,
            "reason": "deterministic compact-dialogue gate",
            "risk_flags": [],
        },
        "visible_response": visible_response,
        "first_call_grounding_diagnostics": {
            "source": COMPACT_DIALOGUE_SOURCE,
            "provider_called": True,
            "provider_status": "compact_dialogue_complete",
            "provider_parse_ok": True,
            "raw_text": _clip(raw, 700),
            "raw_text_length": len(_s(raw)),
            "turn_grounding_packet": deepcopy(packet),
            "format_version": "first_call_grounding_diagnostics_compact_v1",
        },
        "source": COMPACT_DIALOGUE_SOURCE,
    }


def _choose_speaker(
    llm_gateway: Any,
    packet: dict[str, Any],
    *,
    player_input: str,
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str, str] | None:
    """Let the model name the addressed NPC among those present and answer as them, in one call.

    The answer is used only when its speaker_id is a present NPC; otherwise the
    turn takes the full semantic path, whose router resolves the target.
    """
    nearby_ids = [_s(_d(row).get("id")) for row in _l(_d(packet.get("npc_context")).get("nearby_npcs"))]
    candidates = nearby_npc_profiles(
        simulation_state=simulation_state,
        runtime_state=runtime_state,
        npc_ids=[npc_id for npc_id in nearby_ids if npc_id][:6],
    )
    if not candidates:
        return None
    priority = _d(packet.get("priority_context"))
    context = {
        "scene": {
            key: value
            for key, value in _d(priority.get("current_scene")).items()
            if value and key in {"location_name", "summary"}
        },
        "recent_turns": _l(priority.get("recent_turns"))[-3:],
        "present_npcs": [
            {
                key: value
                for key, value in {
                    "id": _s(profile.get("id")),
                    "name": _clip(profile.get("name"), 80),
                    "role": _clip(profile.get("role"), 100),
                    "description": _clip(_d(profile.get("visible_profile")).get("short_description"), 200),
                    "personality": _clip(_d(profile.get("personality_profile")).get("summary"), 240),
                    "speech_style": _clip(_d(profile.get("visible_profile")).get("speech_style"), 160),
                }.items()
                if value
            }
            for profile in candidates
        ],
    }
    raw = llm_gateway.generate(
        _PROMPT_2.format(v0=_clip(player_input, 500))
        + json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        provider_options={
            "temperature": 0.55,
            "max_tokens": 160,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False},
        },
    )
    answer = _json_object(raw)
    by_id = {_s(profile.get("id")): profile for profile in candidates}
    profile = by_id.get(_s(answer.get("speaker_id")).strip())
    line = _s(answer.get("line")).strip()
    if profile is None or not line:
        return None
    chosen = deepcopy(packet)
    npc_context = _d(chosen.get("npc_context"))
    npc_context["addressed_npcs"] = [profile]
    chosen["npc_context"] = npc_context
    chosen_priority = _d(chosen.get("priority_context"))
    chosen_priority["addressed_npc_ids"] = [_s(profile.get("id"))]
    chosen["priority_context"] = chosen_priority
    return chosen, profile, _s(raw), line


def _json_object(raw: Any) -> dict[str, Any]:
    text = _s(raw).strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.casefold().startswith("json"):
            text = text[4:].strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
