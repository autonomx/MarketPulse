"""Deterministic fallback narration helpers."""
from __future__ import annotations

import logging

from typing import Any, Dict, List, cast

from app.apps.rpg.narration.dialogue_state import get_dialogue_context, update_dialogue_state
from app.apps.rpg.narration.npc_dialogue.intelligence import (
    build_npc_intelligence_prompt,
    normalize_npc_intelligence_payload,
    npc_line_is_invalid,
)
from app.providers.service import get_provider

from .runtime_narration_common import NARRATION_FORMAT_VERSION, _norm, _safe_str
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

logger = logging.getLogger(__name__)

def _recent_npc_lines(simulation_state: Dict[str, Any], speaker: str, *, limit: int = 8) -> List[str]:
    simulation_state = _safe_dict(simulation_state)
    rows: List[Dict[str, Any]] = []
    for key in ("recent_turns", "turn_history", "transcript", "narration_history"):
        rows.extend([row for row in _safe_list(simulation_state.get(key)) if isinstance(row, dict)])
    lines: List[str] = []
    for row in rows[-max(1, int(limit or 8)):]:
        npc = _safe_dict(row.get("npc"))
        row_speaker = _safe_str(npc.get("speaker") or row.get("speaker"))
        line = _safe_str(npc.get("line") or row.get("npc_line") or row.get("line"))
        if speaker and row_speaker.lower() == speaker.lower() and line:
            lines.append(line)
    return lines[-limit:]




def _build_dialogue_state_update_payload(
    *,
    simulation_state: Dict[str, Any],
    speaker: str,
    player_action: str,
    npc_line: str,
) -> Dict[str, Any]:
    if not speaker or not npc_line:
        return {}
    state = _safe_dict(simulation_state)
    try:
        update_dialogue_state(
            state,
            npc_id=speaker,
            player_action=player_action,
            npc_line=npc_line,
            facts_revealed=_known_facts_for_npc_reply(state, player_action),
        )
        return _safe_dict(state.get("dialogue_state"))
    except Exception:
        return {}


def _dialogue_aware_bran_line(
    *,
    player_action: str,
    dialogue_context: Dict[str, Any],
    recent_lines: List[str],
) -> str:
    text = _norm(player_action)
    repeat_count = int(_safe_dict(dialogue_context).get("repeat_count") or 0)
    is_repeat = bool(_safe_dict(dialogue_context).get("is_repeat"))
    last_answer = _safe_str(_safe_dict(dialogue_context).get("last_npc_answer"))

    def choose(candidates: List[str]) -> str:
        for line in candidates:
            if not _line_was_recently_used(line, recent_lines):
                return line
        return candidates[-1] if candidates else ""

    if is_repeat and repeat_count >= 2:
        if "report" in text and ("trail" in text or "road" in text or "danger" in text):
            return choose(
                [
                    "Bran exhales through his nose. “You have told me that already. If the trail points to the road, stop reporting it to me and follow it before the mud dries.”",
                    "Bran’s patience thins. “Same answer: the road is the danger. You will learn more outside than by repeating the report.”",
                    "Bran glances toward the door. “I heard you. Road, trail, danger. Now act on it.”",
                ]
            )
        if "cloaked traveler" in text or "witness" in text or "side door" in text:
            return choose(

            [
                "Bran frowns. “I already told you the useful part: the cloaked traveler left by the side door. Stop asking me in circles and check the street before the trail goes cold.”",
                "Bran taps the bar once. “Same answer: side door, then the road. If you want proof, look for tracks instead of another reply from me.”",
                "Bran lowers his voice. “You keep asking the same thing. The lead is outside now: door, street, road.”",
            ]
        )
        return choose(
            [
                "Bran studies you for a moment. “You have asked that already. Ask me something sharper, or act on what you know.”",
                "Bran shakes his head. “Repeating the question will not change the answer.”",
            ]
        )

    if "what" in text and ("saw" in text or "personally saw" in text) and "cloaked traveler" in text:
        return choose(
            [
                "Bran leans closer. “I saw the traveler avoid the common room, keep their hood low, and slip out through the side door. If you want the truth, check the threshold and the street.”",
                "Bran’s jaw tightens. “They did not leave like a guest. They left like someone being followed. Side door first, road second.”",
            ]
        )

    if "where" in text and ("cloaked traveler" in text or "witness" in text or "side door" in text):
        return choose(
            [
                "The cloaked traveler left through the side door and angled toward the road.",
                "Check the side door first. If there are tracks, the street will tell you more than I can.",
                "They went out fast, toward the road. Follow that before the trail goes cold.",
            ]
        )

    if "report" in text and ("trail" in text or "road" in text or "witness" in text):
        return choose(
            [
                "Bran goes still. “Then it is the road. That is what I feared.”",
                "Bran nods once. “That fits. The road trouble is not rumor anymore.”",
                "Bran’s face hardens. “If the trail points to the road, you have your next step.”",
            ]
        )

    if "side door" in text or "boot prints" in text or "mud" in text or "torn cloth" in text:
        return choose(
            [
                "Look low near the threshold. Travelers hide faces, not footprints.",
                "The side door sticks in wet weather. Anyone leaving in a hurry may have left a mark.",
            ]
        )

    if last_answer:
        return choose(
            [
                f"Bran answers carefully. “Remember what I said: {last_answer} Now decide what you are doing with it.”",
                "Bran narrows his eyes. “I can help with facts, not fog. Ask about the traveler, the side door, or the road.”",
            ]
        )

    return choose(
        [
            "Bran watches you over the rim of a cup. “Ask plainly. Are you looking for the traveler, the road, or the person who frightened them?”",
            "Bran lowers his voice. “If this is about the trouble outside, ask the question you are avoiding.”",
        ]
    )


def _line_was_recently_used(line: str, recent_lines: List[str]) -> bool:
    wanted = _norm(line)
    return any(_norm(row) == wanted for row in recent_lines)


def _known_facts_for_npc_reply(
    simulation_state: Dict[str, Any],
    player_action: str = "",
) -> List[str]:
    facts = []
    for key in ("world_events", "recent_world_events", "quest_events", "journal_entries"):
        for row in _safe_list(_safe_dict(simulation_state).get(key)):
            if isinstance(row, dict):
                text = _safe_str(row.get("summary") or row.get("text") or row.get("description"))
            else:
                text = _safe_str(row)
            if text:
                facts.append(text)
    quest_state = _safe_dict(_safe_dict(simulation_state).get("quest_progress"))
    for quest in _safe_dict(quest_state.get("quests")).values():
        quest = _safe_dict(quest)
        title = _safe_str(quest.get("title"))
        if title:
            facts.append(f"Quest: {title}")
        for obj in _safe_list(quest.get("objectives")):
            obj = _safe_dict(obj)
            if not obj.get("completed"):
                facts.append(_safe_str(obj.get("summary") or obj.get("objective_text")))
    player_prompt = _safe_str(player_action).strip()
    if player_prompt:
        facts.append(f"Player asked: {player_prompt}")
    return [fact for fact in facts if fact][:12]


def is_echo_narration(*, player_action: str, narration: str) -> bool:
    action = _norm(player_action)
    text = _norm(narration)
    return bool(action and text and (text == action or text == action.rstrip(".") or text == action + "."))


def classify_player_action(player_action: str) -> str:
    text = _norm(player_action)
    if any(word in text for word in ["ask", "talk", "tell", "speak", "question", "report", "explain", "share", "approach", "convince", "persuade"]):
        return "social"
    if any(word in text for word in ["look", "inspect", "search", "observe", "scan", "examine", "listen"]):
        return "exploration"
    if any(word in text for word in ["walk", "travel", "leave", "follow", "pursue", "road", "outside"]):
        return "travel"
    if any(word in text for word in ["attack", "strike", "hit", "shoot", "cast", "defend"]):
        return "combat"
    if any(word in text for word in ["buy", "sell", "rent", "room", "drink", "meal", "service"]):
        return "service"
    return "general"


def infer_npc_speaker(player_action: str, simulation_state: Dict[str, Any] | None = None) -> str:
    text = _norm(player_action)
    aliases = {
        "bran": "Bran",
        "innkeeper": "Bran",
        "mira": "Mira",
        "cloaked traveler": "Cloaked Traveler",
        "traveler": "Cloaked Traveler",
        "patron": "Local Patron",
        "guard": "Guard",
        "merchant": "Merchant",
    }
    for key, value in aliases.items():
        if key in text:
            return value

    simulation_state = _safe_dict(simulation_state)
    profiles = _safe_dict(_safe_dict(simulation_state.get("npc_profile_state")).get("profiles"))
    for profile in profiles.values():
        profile = _safe_dict(profile)
        name = _safe_str(profile.get("name"))
        if name and _norm(name) in text:
            return name
    return ""


def _fallback_npc_line(
    *,
    speaker: str,
    player_action: str,
    action_type: str,
    simulation_state: Dict[str, Any] | None = None,
) -> str:
    text = _norm(player_action)
    if not speaker:
        return ""
    recent = _recent_npc_lines(_safe_dict(simulation_state), speaker)
    dialogue_context = get_dialogue_context(
        _safe_dict(simulation_state),
        npc_id=speaker,
        player_action=player_action,
    )

    def choose(candidates: List[str]) -> str:
        for line in candidates:
            if not _line_was_recently_used(line, recent):
                return line
        return candidates[-1] if candidates else ""

    if speaker == "Bran":
        smart_line = _dialogue_aware_bran_line(
            player_action=player_action,
            dialogue_context=dialogue_context,
            recent_lines=recent,
        )
        if smart_line:
            return smart_line

        if "where" in text and ("witness" in text or "cloaked traveler" in text or "side door" in text):
            return choose([
                "The cloaked traveler left through the side door and kept their face turned from the room.",
                "Check the side door first. If there are tracks, the street will tell you more than I can.",
                "They went out fast, toward the road. Ask less broadly and follow that trail before it goes cold.",
            ])
        if "current objective" in text or "anything that can help" in text:
            return choose([
                "Be specific. Are you asking about the witness, the road, or the person who left by the side door?",
                "If you mean the witness, stop circling it. The side door and the road are your next leads.",
                "I can help with facts, not fog. Ask me about the traveler or check the exit.",
            ])
        if "report" in text and ("witness" in text or "trail" in text or "road" in text):
            return choose([
                "Then it is the road. If the traveler's trail points that way, this is bigger than tavern fear.",
                "That matches what I feared. The road has been wrong for days, and now we have a witness thread.",
                "Good. If you have the trail, follow it carefully. Bandits do not leave witnesses twice.",
            ])
        if "side door" in text or "boot prints" in text or "mud" in text or "torn cloth" in text:
            return choose([
                "The side door sticks in wet weather. Anyone leaving in a hurry would have left a mark there.",
                "Look low, near the threshold. Travelers hide faces, not footprints.",
                "If the street caught mud from their boots, you may still have a trail.",
            ])
        if "bandit" in text or "road" in text:
            return choose([
                "If the road is involved, then this is bigger than tavern gossip. Be careful how loudly you ask.",
                "The road has been wrong for days. People come in pale, then pretend they saw nothing.",
                "Bandits do not leave witnesses twice. If you go, go prepared.",
            ])
        if "witness" in text or "traveler" in text or "found" in text:
            return choose([
                "Slow down and tell me what you know. Around here, one witness can change the whole story.",
                "The cloaked traveler is the thread you want. They left fast and kept their face turned away.",
                "Find that traveler, then come back to me. I will know whether the story fits.",
            ])
        if "room" in text or "rent" in text:
            return choose([
                "I have rooms, but tonight I am more worried about what followed people here than where they sleep.",
                "A bed is easy. Explaining why travelers are afraid of the road is not.",
                "I can rent you a room, but if you are chasing this trouble, you will not stay in it long.",
            ])
        return choose([
            "Say what you need to say. I am listening, even if the rest of the room is pretending not to.",
            "Be specific. I can help with facts, not fog.",
            "If this is about the trouble outside, ask the question you are avoiding.",
        ])
    if speaker == "Mira":
        return choose([
            "I notice the things people try to hide. Ask plainly, and I will tell you what I saw.",
            "The side door matters. People used it tonight who did not want attention.",
            "If Bran is being careful, it is because someone in this room is listening.",
        ])
    if speaker == "Cloaked Traveler":
        return choose([
            "I did not want to be noticed. That should tell you enough about how dangerous this is.",
            "I saw the road trouble, and I left before it could follow me inside.",
            "Do not ask me loudly. The men on the road have friends who listen.",
        ])
    if speaker == "Local Patron":
        return choose([
            "People saw more than they are admitting. Fear has a way of keeping mugs close and mouths shut.",
            "The traveler came in like a hunted animal. That is all I will say with everyone listening.",
            "If you want answers, watch who refuses to look toward the door.",
        ])
    return "The reply comes cautiously, shaped by the pressure in the room."


def build_deterministic_narration_payload(
    *,
    player_action: str,
    simulation_state: Dict[str, Any] | None = None,
    turn_contract: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a safe presentation-only narration payload.

    This is a fallback, not a simulation authority. It describes only that the
    moment responds to the player's action. It does not award, complete, mutate,
    or invent authoritative facts.
    """
    simulation_state = _safe_dict(simulation_state)
    turn_contract = _safe_dict(turn_contract)
    action_type = classify_player_action(player_action)
    speaker = infer_npc_speaker(player_action, simulation_state)
    npc_line = ""
    npc_intel: Dict[str, Any] = {}

    if action_type == "social":
        speaker = speaker or "Local Patron"
        narration = f"{speaker} reacts to the question, and the surrounding noise seems to thin as the conversation draws attention."
        recent_lines = _recent_npc_lines(simulation_state, speaker)
        try:
            npc_messages = build_npc_intelligence_prompt(
                speaker=speaker,
                player_action=player_action,
                npc_profile=_safe_dict(_safe_dict(simulation_state).get("npc_profiles")).get(speaker, {}),
                social_state=_safe_dict(_safe_dict(simulation_state).get("social_state")).get(speaker, {}),
                known_facts=_known_facts_for_npc_reply(simulation_state),
                recent_lines=recent_lines,
                active_objectives=_safe_list(_safe_dict(simulation_state).get("active_objectives")),
            )
            raw = cast(Any, get_provider()).chat(messages=npc_messages, temperature=0.35, max_tokens=260)
            npc_intel = normalize_npc_intelligence_payload(raw)
            if not npc_line_is_invalid(npc_intel.get("line", ""), recent_lines):
                npc_line = npc_intel["line"]
        except Exception:
            logger.debug("suppressed error in %s", "build_deterministic_narration_payload", exc_info=True)

        if not npc_line:
            npc_line = _fallback_npc_line(
                speaker=speaker,
                player_action=player_action,
                action_type=action_type,
                simulation_state=simulation_state,
            )
    elif action_type == "exploration":
        narration = "The search draws out grounded details from the scene: small marks, watchful faces, and signs of recent tension."
        npc_line = ""
    elif action_type == "travel":
        narration = "The scene shifts with the movement, carrying the pressure of the current lead into the space ahead."
        npc_line = ""
    elif action_type == "combat":
        narration = "The hostile motion sharpens the moment, but the actual outcome remains bound to the combat result."
        npc_line = ""
    elif action_type == "service":
        speaker = speaker or "Bran"
        narration = "The practical request lands against the unease of the room, making ordinary business feel less ordinary."
        recent_lines = _recent_npc_lines(simulation_state, speaker)
        try:
            npc_messages = build_npc_intelligence_prompt(
                speaker=speaker,
                player_action=player_action,
                npc_profile=_safe_dict(_safe_dict(simulation_state).get("npc_profiles")).get(speaker, {}),
                social_state=_safe_dict(_safe_dict(simulation_state).get("social_state")).get(speaker, {}),
                known_facts=_known_facts_for_npc_reply(simulation_state),
                recent_lines=recent_lines,
                active_objectives=_safe_list(_safe_dict(simulation_state).get("active_objectives")),
            )
            raw = cast(Any, get_provider()).chat(messages=npc_messages, temperature=0.35, max_tokens=260)
            npc_intel = normalize_npc_intelligence_payload(raw)
            if not npc_line_is_invalid(npc_intel.get("line", ""), recent_lines):
                npc_line = npc_intel["line"]
        except Exception:
            logger.debug("suppressed error in %s", "build_deterministic_narration_payload", exc_info=True)

        if not npc_line:
            npc_line = _fallback_npc_line(
                speaker=speaker,
                player_action=player_action,
                action_type=action_type,
                simulation_state=simulation_state,
            )
    else:
        narration = "The moment responds without producing a major new consequence."
        npc_line = ""

    if is_echo_narration(player_action=player_action, narration=narration):
        narration = "The scene responds with a grounded beat rather than merely repeating the attempted action."

    if speaker and npc_line:
        try:
            update_dialogue_state(
                simulation_state,
                npc_id=speaker,
                player_action=player_action,
                npc_line=npc_line,
                facts_revealed=_known_facts_for_npc_reply(simulation_state, player_action),
            )
            # Also update recent_turns for global recent line tracking
            recent_turns = simulation_state.setdefault("recent_turns", [])
            recent_turns.append({
                "npc": {
                    "speaker": speaker,
                    "line": npc_line,
                }
            })
            simulation_state["recent_turns"] = recent_turns[-8:]
        except Exception:
            logger.debug("suppressed error in %s", "build_deterministic_narration_payload", exc_info=True)

    dialogue_state_update = _build_dialogue_state_update_payload(
        simulation_state=simulation_state,
        speaker=speaker,
        player_action=player_action,
        npc_line=npc_line,
    )

    return {
        "format_version": NARRATION_FORMAT_VERSION,
        "narration": narration,
        "action": _safe_str(turn_contract.get("summary") or turn_contract.get("action") or "The action is acknowledged by the scene."),
        "npc": {
            "speaker": speaker if npc_line else "",
            "line": npc_line,
        },
        "reward": "",
        "followup_hooks": [],
        "dialogue_state_update": dialogue_state_update,
        "source": "deterministic_runtime_narration_fallback",
        "authoritative_changes": False,
    }
