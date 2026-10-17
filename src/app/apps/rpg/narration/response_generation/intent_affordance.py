from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

_TOKEN = re.compile(r"[a-z0-9']+")
_QUESTION_WORDS = {"who", "what", "where", "when", "why", "how"}
_MAGIC_WORDS = {"cast", "spell", "telepathy", "mind", "read", "magic", "ritual"}
_TECH_WORDS = {"telephone", "phone", "internet", "email", "camera", "computer", "radio"}
_TRAVEL_WORDS = {"travel", "go", "walk", "ride", "sail", "teleport", "journey"}
_SOCIAL_WORDS = {"convince", "persuade", "threaten", "intimidate", "understand", "order", "ask"}
_TRANSACTION_WORDS = {"buy", "pay", "purchase", "sell", "price", "room", "rent", "money", "coin"}
_COMBAT_WORDS = {"attack", "strike", "hit", "kill", "fight", "stab", "shoot"}
_ASSERTION_MARKERS = {"as", "remember", "again", "my", "our", "used", "before"}
_SENTENCE_LEADERS = {"I", "The", "A", "An", "Where", "What", "Who", "When", "Why", "How", "Ask", "Travel", "Do", "Make", "Go", "Tell", "Show", "Find", "Use", "Cast", "Convince", "Persuade", "Threaten", "Order"}


@dataclass(frozen=True)
class IntentHypothesis:
    intent: str
    affordance: str
    confidence: float
    underlying_goal: str
    required_evidence: tuple[str, ...] = ()
    ambiguity: str = "low"
    state_mutation_allowed: bool = False
    entities: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntentAnalysis:
    hypotheses: tuple[IntentHypothesis, ...]
    selected: IntentHypothesis
    tokens: tuple[str, ...]
    unresolved_references: tuple[str, ...] = ()


class NarrativeAffordanceClassifier:
    """Deterministic broad intent classification for graceful recovery routing."""

    def classify(self, player_input: str, *, known_entities: Mapping[str, Any] | None = None, known_locations: Mapping[str, Any] | None = None, supported_mechanics: tuple[str, ...] = ()) -> IntentAnalysis:
        text = str(player_input or "").strip()
        lowered = text.casefold()
        tokens = tuple(_TOKEN.findall(lowered))
        token_set = set(tokens)
        entities = _normalized_keys(known_entities)
        locations = _normalized_keys(known_locations)
        unresolved = tuple(sorted(phrase for phrase in _capitalized_phrases(text) if _normalize_name(phrase) not in entities | locations))
        hypotheses: list[IntentHypothesis] = []
        if token_set & _TECH_WORDS:
            hypotheses.append(IntentHypothesis("use_impossible_technology", "world_equivalent", 0.96, _technology_goal(token_set), ("world_rules", "available_services")))
        if token_set & _MAGIC_WORDS and not _mechanic_supported("magic", supported_mechanics):
            hypotheses.append(IntentHypothesis("use_unsupported_power", "analogous_skill", 0.9, "obtain information or influence through an unsupported power", ("skills", "magic_rules", "target_state")))
        if token_set & _TRANSACTION_WORDS:
            failed = any(phrase in lowered for phrase in ("no money", "no coin", "cannot afford", "can't afford", "even though i have no"))
            hypotheses.append(IntentHypothesis("transaction", "transaction_failure" if failed else "transaction", 0.97 if failed else 0.91, "obtain or pay for the requested good or service", ("offers", "price", "currency", "inventory")))
        if token_set & _COMBAT_WORDS:
            hypotheses.append(IntentHypothesis("combat_action", "combat_attempt", 0.94, "attempt the stated hostile action through combat rules", ("combat_state", "initiative", "target_state")))
        if token_set & _TRAVEL_WORDS:
            unknown_destination = bool(unresolved) or not locations
            hypotheses.append(IntentHypothesis("travel", "ask_directions" if unknown_destination else "travel_attempt", 0.82 if unknown_destination else 0.92, "reach the requested destination", ("known_locations", "routes", "travel_rules"), entities=unresolved))
        investigation_offer = (
            lowered.startswith("maybe ")
            and ("someone" in token_set or "knows" in token_set)
        ) or "investigate" in token_set
        if investigation_offer:
            hypotheses.append(IntentHypothesis("offer_investigation", "offer_investigation", 0.9, "identify a source or clue without automatically beginning the investigation", ("visible_entities", "known_sources", "clues")))
        if unresolved:
            hypotheses.append(IntentHypothesis("reference_unknown_entity", "entity_search", 0.84, "identify or learn about the referenced entity", ("entity_aliases", "npc_memory", "lorebook"), ambiguity="medium", entities=unresolved))
        if (tokens and tokens[0] in _QUESTION_WORDS) or text.endswith("?"):
            hypotheses.append(IntentHypothesis("ask_world_question", "lore_search", 0.88, "obtain an answer consistent with available knowledge", ("speaker_knowledge", "journal", "lorebook"), entities=unresolved))
        if token_set & _SOCIAL_WORDS:
            ambiguous = bool(unresolved) or "understand" in token_set or len(tokens) <= 4
            hypotheses.append(IntentHypothesis("social_action", "clarification" if ambiguous else "social_check", 0.7 if ambiguous else 0.86, "change another character's understanding or behavior", ("target", "relationship", "social_mechanics"), ambiguity="high" if ambiguous else "low", entities=unresolved))
        if token_set & _ASSERTION_MARKERS and any(word in lowered for word in ("agent", "sister", "friend", "promised", "remember")):
            hypotheses.append(IntentHypothesis("assert_prior_history", "unverified_player_claim", 0.98, "use an asserted relationship or prior event", ("campaign_history", "npc_memory", "relationships"), ambiguity="medium", entities=unresolved))
        if not hypotheses:
            hypotheses.append(IntentHypothesis("unresolved_action", "inspect_or_clarify", 0.45, "understand what outcome the player is seeking", ("scene", "visible_entities"), ambiguity="high", entities=unresolved))
        ordered = tuple(sorted(hypotheses, key=lambda row: row.confidence, reverse=True))
        return IntentAnalysis(hypotheses=ordered, selected=ordered[0], tokens=tokens, unresolved_references=unresolved)


def _capitalized_phrases(text: str) -> tuple[str, ...]:
    phrases = re.findall(r"\b[A-Z][a-zA-Z']+(?:\s+[A-Z][a-zA-Z']+)*", text)
    return tuple(phrase for phrase in phrases if phrase not in _SENTENCE_LEADERS)


def _normalized_keys(value: Mapping[str, Any] | None) -> set[str]:
    if not isinstance(value, Mapping):
        return set()
    result: set[str] = set()
    for key, row in value.items():
        result.add(_normalize_name(key))
        if isinstance(row, Mapping):
            for alias in row.get("aliases", ()):
                result.add(_normalize_name(alias))
            if row.get("name"):
                result.add(_normalize_name(row["name"]))
    return result


def _normalize_name(value: Any) -> str:
    return " ".join(_TOKEN.findall(str(value or "").casefold()))


def _mechanic_supported(name: str, supported_mechanics: tuple[str, ...]) -> bool:
    return name.casefold() in {str(value).casefold() for value in supported_mechanics}


def _technology_goal(tokens: set[str]) -> str:
    if tokens & {"telephone", "phone", "radio", "email"}:
        return "communicate with a distant person"
    if "camera" in tokens:
        return "preserve or prove what is visible"
    return "achieve the technology's underlying function through an in-world method"
