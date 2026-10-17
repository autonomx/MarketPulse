from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from app.apps.rpg.narration.ai.grounding_patterns import (
    _AMBIGUOUS_DEBT_RESPONSE_PATTERNS,
    _CLEAR_DEBT_REFUSAL_PATTERNS,
    _COMBAT_PATTERNS,
    _DEBT_CONFIRMATION_PATTERNS,
    _LOCATION_MOVE_PATTERNS,
    _MONEY_PHRASE_PATTERN,
    _NEGATION_MARKERS,
    _OBJECTIVE_COMPLETION_PATTERNS,
    _PRICE_QUOTE_PATTERNS,
    _REWARD_PATTERNS,
    _UNSUPPORTED_DEBT_CLAIM_PATTERNS,
)
from app.apps.rpg.narration.ai.grounding_settings import normalize_grounding_settings
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, safe_str as _safe_str


@dataclass
class GroundingViolation:
    code: str
    message: str
    field: str = ""
    evidence: str = ""


@dataclass
class GroundingValidationResult:
    ok: bool
    violations: List[GroundingViolation] = field(default_factory=list)
    fallback_used: bool = False
    fallback_source: str = ""
    selected_candidate: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "fallback_used": bool(self.fallback_used),
            "fallback_source": self.fallback_source,
            "selected_candidate": self.selected_candidate,
            "violations": [
                {
                    "code": v.code,
                    "message": v.message,
                    "field": v.field,
                    "evidence": v.evidence,
                }
                for v in self.violations
            ],
        }


def _safe_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _extract_player_action_text(turn_contract: Mapping[str, Any]) -> str:
    contract = _safe_dict(turn_contract)
    result = _safe_dict(
        contract.get("result")
        or contract.get("resolved_result")
        or contract.get("resolved_action")
    )
    action = _safe_dict(contract.get("action"))
    action_metadata = _safe_dict(action.get("metadata"))
    semantic_action = _safe_dict(action_metadata.get("semantic_action"))

    candidates = [
        contract.get("player_action"),
        contract.get("player_input"),
        contract.get("input"),
        contract.get("command"),
        result.get("player_action"),
        result.get("player_input"),
        action.get("player_action"),
        action.get("player_input"),
        semantic_action.get("player_action"),
        semantic_action.get("player_input"),
    ]

    for candidate in candidates:
        text = _safe_str(candidate)
        if text:
            return text

    return ""


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(_flatten_text(v) for v in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return " ".join(_flatten_text(v) for v in value)
    return _safe_str(value)


def _contains_pattern(text: str, patterns: Iterable[str]) -> Optional[str]:
    text = _safe_str(text)
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            prefix = f" {_safe_str(text[max(0, match.start() - 32) : match.start()]).lower()} "
            if any(marker in prefix for marker in _NEGATION_MARKERS):
                continue
            return pattern
    return None


def _contains_price_quote(text: str) -> bool:
    text = _safe_str(text)
    for pattern in _PRICE_QUOTE_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True
    return False


def _ids_from_entries(entries: Any) -> Set[str]:
    ids: Set[str] = set()
    for entry in _safe_list(entries):
        if isinstance(entry, str):
            ids.add(entry.strip())
        elif isinstance(entry, Mapping):
            for key in (
                "id",
                "name",
                "label",
                "npc",
                "speaker",
                "location",
                "item",
                "objective",
                "quest",
                "node_id",
            ):
                value = entry.get(key)
                if value:
                    ids.add(_safe_str(value).strip())
    return {value for value in ids if value}


def _texts_from_entries(entries: Any) -> List[str]:
    texts: List[str] = []
    for entry in _safe_list(entries):
        if isinstance(entry, str):
            texts.append(entry)
        elif isinstance(entry, Mapping):
            for key in ("text", "summary", "title", "name", "label", "description", "command"):
                value = entry.get(key)
                if value:
                    texts.append(_safe_str(value))
    return [text for text in texts if text]


def _extract_allowed_fact_texts(turn_contract: Mapping[str, Any]) -> List[str]:
    contract = _safe_dict(turn_contract)
    result = _safe_dict(
        contract.get("result")
        or contract.get("resolved_result")
        or contract.get("resolved_action")
    )
    texts: List[str] = []

    for source in (contract, result):
        for key in (
            "new_facts",
            "facts",
            "allowed_facts",
            "known_facts",
            "unlocked_facts",
            "new_leads",
            "allowed_leads",
            "suggested_actions",
            "allowed_next_actions",
        ):
            value = source.get(key)
            if isinstance(value, Mapping):
                texts.extend(_safe_str(k) for k in value.keys())
                texts.extend(_texts_from_entries(value.values()))
            else:
                texts.extend(_texts_from_entries(value))

    narration_brief = _safe_dict(contract.get("narration_brief"))
    texts.extend(_texts_from_entries([narration_brief]))
    return [text for text in texts if text]


def _extract_allowed_ids(turn_contract: Mapping[str, Any], *keys: str) -> Set[str]:
    contract = _safe_dict(turn_contract)
    result = _safe_dict(
        contract.get("result")
        or contract.get("resolved_result")
        or contract.get("resolved_action")
    )
    ids: Set[str] = set()

    for source in (contract, result):
        for key in keys:
            value = source.get(key)
            if isinstance(value, Mapping):
                ids.update(_safe_str(k).strip() for k in value.keys())
                ids.update(_ids_from_entries(value.values()))
            else:
                ids.update(_ids_from_entries(value))
    return {value for value in ids if value}


def _delta_exists(turn_contract: Mapping[str, Any], keys: Sequence[str]) -> bool:
    contract = _safe_dict(turn_contract)
    result = _safe_dict(
        contract.get("result")
        or contract.get("resolved_result")
        or contract.get("resolved_action")
    )
    state_delta = _safe_dict(contract.get("state_delta"))

    for source in (contract, result, state_delta):
        for key in keys:
            value = source.get(key)
            if value not in (None, "", {}, []):
                return True
    return False


def _currency_or_inventory_delta_exists(turn_contract: Mapping[str, Any]) -> bool:
    return _delta_exists(
        turn_contract,
        (
            "currency_delta",
            "currency",
            "money_delta",
            "reward",
            "inventory_delta",
            "items_added",
            "items_removed",
            "xp_result",
            "skill_xp_result",
            "level_up",
        ),
    )


def _combat_delta_exists(turn_contract: Mapping[str, Any]) -> bool:
    return _delta_exists(
        turn_contract,
        (
            "combat_delta",
            "damage_delta",
            "health_delta",
            "defeat",
            "combat",
            "combat_result",
            "npc_combat_result",
        ),
    )


def _quest_completion_exists(turn_contract: Mapping[str, Any]) -> bool:
    return _delta_exists(
        turn_contract,
        (
            "completed_quests",
            "quest_completed",
            "complete_quest",
            "completed_objectives",
            "objective_completed",
            "quest_log_delta",
            "quest_result",
        ),
    )


def _payment_or_debt_authorized(turn_contract: Mapping[str, Any]) -> bool:
    contract = _safe_dict(turn_contract)
    result = _safe_dict(
        contract.get("result")
        or contract.get("resolved_result")
        or contract.get("resolved_action")
    )

    candidates = [
        contract,
        result,
        _safe_dict(contract.get("service_result")),
        _safe_dict(contract.get("interaction_result")),
        _safe_dict(contract.get("conversation_result")),
        _safe_dict(contract.get("npc_backbone_decision")),
        _safe_dict(contract.get("state_delta")),
        _safe_dict(result.get("service_result")),
        _safe_dict(result.get("interaction_result")),
        _safe_dict(result.get("conversation_result")),
        _safe_dict(result.get("npc_backbone_decision")),
        _safe_dict(result.get("state_delta")),
    ]

    for source in candidates:
        if not source:
            continue

        for key in (
            "currency_delta",
            "money_delta",
            "reward",
            "payment",
            "payment_due",
            "debt",
            "debt_confirmed",
            "owed_amount",
            "inventory_delta",
            "items_added",
        ):
            if source.get(key) not in (None, "", {}, []):
                return True

        if source.get("accepted") is True and (
            source.get("service_id")
            or source.get("service")
            or source.get("price")
            or source.get("cost")
        ):
            return True

    return False


def _debt_reference_is_not_reward_grant(
    text: str,
    reward_pattern: Optional[str],
    turn_contract: Mapping[str, Any],
) -> bool:
    """Allow currency words when they describe an unsupported player debt claim."""
    if not reward_pattern:
        return False

    text = _safe_str(text)
    contract = _safe_dict(turn_contract)

    player_action_text = _extract_player_action_text(contract)

    player_made_debt_claim = bool(
        _contains_pattern(player_action_text, _UNSUPPORTED_DEBT_CLAIM_PATTERNS)
    )
    if not player_made_debt_claim:
        return False

    if _payment_or_debt_authorized(contract):
        return False

    explicit_grant_patterns = [
        rf"\b(?:hands?|handed|gives?|gave|pays?|paid)\s+(?:you\s+)?{_MONEY_PHRASE_PATTERN}\b",
        rf"\byou\s+(?:gain|gained|receive|received|get|got|are\s+given|were\s+given)\s+{_MONEY_PHRASE_PATTERN}\b",
        rf"\b{_MONEY_PHRASE_PATTERN}\s+(?:is|are|was|were)\s+(?:added|placed|put)\b.*\b(?:inventory|purse|hand|pocket|pack)\b",
        r"\bpayment\s+changes\s+hands\b",
        r"\badds?\b.*\bto your inventory\b",
    ]
    if _contains_pattern(text, explicit_grant_patterns):
        return False

    non_grant_debt_reference_patterns = [
        rf"\b(?:demand|demands|demanded|request|requests|requested|claim|claims|claimed)\b.*\b{_MONEY_PHRASE_PATTERN}\b",
        rf"\b{_MONEY_PHRASE_PATTERN}\b.*\b(?:demand|claim|unsupported\s+claim|unsupported\s+debt)\b",
        rf"\bdebt\s+of\s+{_MONEY_PHRASE_PATTERN}\b",
        rf"\b{_MONEY_PHRASE_PATTERN}\b.*\bdebt\b",
        r"\bunsupported\s+debt\s+claim\b",
        r"\bunsupported\s+payment\s+claim\b",
        r"\bdoes\s+not\s+owe\b",
        r"\bdo\s+not\s+owe\b",
        r"\bdon't\s+owe\b",
        r"\bno\s+coin\s+changes\s+hands\b",
        r"\bdoes\s+not\s+hand\s+over\s+any\s+coin\b",
        r"\bno\s+payment\b",
        r"\bpayment\s+demand\b",
        r"\bdemand\s+payment\b",
        r"\bclaim\s+against\b",
    ]

    return bool(_contains_pattern(text, non_grant_debt_reference_patterns))


def _allowed_speakers(
    turn_contract: Mapping[str, Any],
    state_snapshot: Optional[Mapping[str, Any]] = None,
) -> Set[str]:
    state = _safe_dict(state_snapshot)
    allowed: Set[str] = set()

    allowed.update(
        _extract_allowed_ids(turn_contract, "allowed_npcs", "present_npcs", "known_npcs", "unlocked_npcs", "npcs")
    )
    allowed.update(
        _extract_allowed_ids(state, "present_npcs", "known_npcs", "unlocked_npcs", "npcs")
    )

    for source in (_safe_dict(turn_contract), state):
        for key in ("speaker", "npc_speaker", "target_id", "target_name", "npc_name", "speaker_name"):
            value = source.get(key)
            if value:
                allowed.add(_safe_str(value).strip())

    return {value for value in allowed if value}


def _speaker_allowed(speaker: str, allowed: Set[str]) -> bool:
    speaker = _safe_str(speaker).strip()
    if not speaker:
        return True
    speaker_aliases = _speaker_aliases(speaker)
    allowed_aliases = {
        alias
        for value in allowed
        for alias in _speaker_aliases(value)
    }
    if speaker_aliases & allowed_aliases:
        return True
    return any(
        _speaker_alias_contains(allowed_alias, speaker_alias)
        or _speaker_alias_contains(speaker_alias, allowed_alias)
        for speaker_alias in speaker_aliases
        for allowed_alias in allowed_aliases
    )


def _speaker_aliases(value: str) -> Set[str]:
    raw = _safe_str(value).strip().lower()
    if not raw:
        return set()

    aliases = {_normalize_speaker_alias(raw)}
    if ":" in raw:
        aliases.add(_normalize_speaker_alias(raw.rsplit(":", 1)[-1]))
    if "_" in raw:
        aliases.add(_normalize_speaker_alias(raw.replace("_", " ")))

    return {alias for alias in aliases if alias}


def _normalize_speaker_alias(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _speaker_alias_contains(container: str, candidate: str) -> bool:
    container = _normalize_speaker_alias(container)
    candidate = _normalize_speaker_alias(candidate)
    if not container or not candidate:
        return False
    if container == candidate:
        return True
    container_tokens = container.split()
    candidate_tokens = candidate.split()
    if len(candidate_tokens) == 1:
        token = candidate_tokens[0]
        return len(token) >= 3 and token in container_tokens
    width = len(candidate_tokens)
    return any(
        container_tokens[index:index + width] == candidate_tokens
        for index in range(0, len(container_tokens) - width + 1)
    )


def _looks_like_location_speaker(value: str) -> bool:
    raw = _safe_str(value).strip().lower()
    normalized = _normalize_speaker_alias(raw)
    return bool(
        raw.startswith(("loc_", "location:"))
        or normalized.startswith("loc ")
        or normalized.startswith("location ")
    )


def _player_action_is_personal_day_disclosure(value: str) -> bool:
    text = _safe_str(value).strip().lower()
    if not text:
        return False
    has_personal_anchor = bool(
        re.search(r"\b(?:i|ive|i've|i had|i have|my|me)\b", text)
    )
    has_day_term = bool(re.search(r"\b(?:day|days|week|weeks|time)\b", text))
    has_burden_term = any(
        term in text
        for term in (
            "rough",
            "tough",
            "hard",
            "bad",
            "long",
            "heavy",
            "awful",
            "terrible",
            "few day",
            "few rough",
        )
    )
    return has_personal_anchor and has_day_term and has_burden_term


def _npc_line_repeats_prior_day_prompt(value: str) -> bool:
    text = _safe_str(value).strip().lower()
    if not text:
        return False
    stale_prompt_terms = (
        "how about yourself",
        "what kind of day have you",
        "what kind of day did you",
        "how was your day",
        "how has your day",
        "how's your day",
        "hows your day",
    )
    stale_answer_terms = (
        "it's been busy",
        "its been busy",
        "it has been busy",
        "usual mix",
        "rowdy adventurers",
        "bard practicing",
        "elara fussing",
        "moon-berries",
        "moon berries",
    )
    return any(term in text for term in stale_prompt_terms) or sum(
        1 for term in stale_answer_terms if term in text
    ) >= 2


def _allowed_locations(
    turn_contract: Mapping[str, Any],
    state_snapshot: Optional[Mapping[str, Any]] = None,
) -> Set[str]:
    contract = _safe_dict(turn_contract)
    state = _safe_dict(state_snapshot)
    allowed: Set[str] = set()

    allowed.update(_extract_allowed_ids(contract, "allowed_locations", "unlocked_locations", "locations"))
    allowed.update(_extract_allowed_ids(state, "allowed_locations", "unlocked_locations", "locations"))

    for source in (contract, state):
        for key in (
            "location",
            "current_location",
            "current_location_id",
            "new_location",
            "set_location",
            "location_name",
        ):
            value = source.get(key)
            if isinstance(value, Mapping):
                allowed.update(_ids_from_entries([value]))
            elif value:
                allowed.add(_safe_str(value).strip())
    return {value for value in allowed if value}


def validate_narration_grounding(
    narration_payload: Mapping[str, Any],
    turn_contract: Mapping[str, Any],
    *,
    state_snapshot: Optional[Mapping[str, Any]] = None,
    strict_named_fact_check: bool = False,
) -> GroundingValidationResult:
    payload = _safe_dict(narration_payload)
    contract = _safe_dict(turn_contract)
    state = _safe_dict(state_snapshot)

    violations: List[GroundingViolation] = []
    resolved_claim_text = _flatten_text(
        {
            "narration": payload.get("narration"),
            "action": payload.get("action"),
            "npc": payload.get("npc"),
            "reward": payload.get("reward"),
        }
    )

    if payload.get("reward") not in (None, "", {}, []):
        if not _currency_or_inventory_delta_exists(contract):
            violations.append(
                GroundingViolation(
                    code="unsupported_reward",
                    field="reward",
                    message="Narration included a reward, but the turn contract has no reward/currency/inventory delta.",
                    evidence=_flatten_text(payload.get("reward"))[:240],
                )
            )

    reward_pattern = _contains_pattern(resolved_claim_text, _REWARD_PATTERNS)
    if (
        reward_pattern
        and not _reward_pattern_is_only_price_quote(resolved_claim_text, reward_pattern)
        and not _debt_reference_is_not_reward_grant(resolved_claim_text, reward_pattern, contract)
        and not _currency_or_inventory_delta_exists(contract)
    ):
        violations.append(
            GroundingViolation(
                code="unsupported_reward_claim",
                field="narration",
                message="Narration mentions reward/currency/item gain, but the turn contract does not authorize it.",
                evidence=reward_pattern,
            )
        )

    combat_pattern = _contains_pattern(resolved_claim_text, _COMBAT_PATTERNS)
    if combat_pattern and not _combat_delta_exists(contract):
        violations.append(
            GroundingViolation(
                code="unsupported_combat_claim",
                field="narration",
                message="Narration mentions combat/death/injury/blood/damage, but the turn contract has no combat delta.",
                evidence=combat_pattern,
            )
        )

    objective_pattern = _contains_pattern(resolved_claim_text, _OBJECTIVE_COMPLETION_PATTERNS)
    if objective_pattern and not _quest_completion_exists(contract):
        violations.append(
            GroundingViolation(
                code="unsupported_objective_or_quest_completion",
                field="narration",
                message="Narration claims objective/quest completion without matching turn-contract completion.",
                evidence=objective_pattern,
            )
        )

    npc = _safe_dict(payload.get("npc"))
    speaker = _safe_str(npc.get("speaker") or npc.get("name") or npc.get("id")).strip()
    if speaker:
        if _looks_like_location_speaker(speaker):
            violations.append(
                GroundingViolation(
                    code="location_used_as_npc_speaker",
                    field="npc.speaker",
                    message="Dialogue speaker looks like a location id, not a character.",
                    evidence=speaker,
                )
            )
        allowed = _allowed_speakers(contract, state)
        if allowed and not _speaker_allowed(speaker, allowed):
            violations.append(
                GroundingViolation(
                    code="unsupported_npc_speaker",
                    field="npc.speaker",
                    message="NPC speaker is not present/known/allowed by the turn contract.",
                    evidence=speaker,
                )
            )

    npc_line = _safe_str(npc.get("line")).strip()
    player_action_text = _extract_player_action_text(contract)
    if (
        npc_line
        and _player_action_is_personal_day_disclosure(player_action_text)
        and _npc_line_repeats_prior_day_prompt(npc_line)
    ):
        violations.append(
            GroundingViolation(
                code="stale_or_irrelevant_dialogue",
                field="npc.line",
                message="NPC dialogue repeats the previous day-inquiry answer instead of responding to the player's disclosure.",
                evidence=npc_line[:240],
            )
        )

    move_pattern = _contains_pattern(resolved_claim_text, _LOCATION_MOVE_PATTERNS)
    if move_pattern:
        allowed_locations = _allowed_locations(contract, state)
        lower_text = resolved_claim_text.lower()
        if allowed_locations and not any(_safe_str(loc).lower() in lower_text for loc in allowed_locations):
            violations.append(
                GroundingViolation(
                    code="unsupported_location_move",
                    field="narration",
                    message="Narration appears to move the player to a location not authorized by the turn contract.",
                    evidence=move_pattern,
                )
            )

    if strict_named_fact_check:
        allowed_blob = " ".join(_extract_allowed_fact_texts(contract)).lower()
        for candidate in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4}\b", _safe_str(payload.get("narration"))):
            if candidate.lower() not in allowed_blob:
                violations.append(
                    GroundingViolation(
                        code="possible_unsupported_named_fact",
                        field="narration",
                        message="Narration contains a named phrase not found in allowed facts/leads/actions.",
                        evidence=candidate,
                    )
                )
                break

    player_made_debt_claim = bool(
        _contains_pattern(player_action_text, _UNSUPPORTED_DEBT_CLAIM_PATTERNS)
    )

    text_mentions_payment_or_debt = bool(
        _contains_pattern(resolved_claim_text, _UNSUPPORTED_DEBT_CLAIM_PATTERNS)
        or _contains_pattern(resolved_claim_text, _REWARD_PATTERNS)
    )

    if player_made_debt_claim and not _payment_or_debt_authorized(contract):
        clear_refusal = _contains_pattern(resolved_claim_text, _CLEAR_DEBT_REFUSAL_PATTERNS)
        ambiguous_debt_response = _contains_pattern(resolved_claim_text, _AMBIGUOUS_DEBT_RESPONSE_PATTERNS)
        debt_confirmation = _contains_pattern(resolved_claim_text, _DEBT_CONFIRMATION_PATTERNS)
        explicit_grant = _contains_pattern(
            resolved_claim_text,
            [
                rf"\b(?:hands?|handed|gives?|gave|pays?|paid)\s+(?:you\s+)?{_MONEY_PHRASE_PATTERN}\b",
                rf"\byou\s+(?:gain|receive|get|are\s+given)\s+{_MONEY_PHRASE_PATTERN}\b",
                r"\bpayment\s+changes\s+hands\b",
            ],
        )

        if explicit_grant:
            violations.append(
                GroundingViolation(
                    code="unsupported_debt_payment_claim",
                    field="narration",
                    message="Narration grants or confirms payment for an unsupported debt claim.",
                    evidence=explicit_grant,
                )
            )
        elif debt_confirmation:
            violations.append(
                GroundingViolation(
                    code="unsupported_debt_confirmed",
                    field="narration",
                    message="Narration confirms, acknowledges, or treats an unsupported debt as real.",
                    evidence=debt_confirmation,
                )
            )
        elif ambiguous_debt_response and not clear_refusal:
            violations.append(
                GroundingViolation(
                    code="unsupported_debt_claim_not_refused",
                    field="narration",
                    message="Player made an unsupported debt/payment claim, but narration answered ambiguously instead of clearly refusing.",
                    evidence=ambiguous_debt_response,
                )
            )
        elif text_mentions_payment_or_debt and not clear_refusal:
            violations.append(
                GroundingViolation(
                    code="unsupported_debt_claim_not_refused",
                    field="narration",
                    message="Player made an unsupported debt/payment claim, but narration did not clearly refuse or challenge it.",
                    evidence=player_action_text[:240],
                )
            )

    return GroundingValidationResult(ok=not violations, violations=violations)


def _first_allowed_speaker(
    turn_contract: Mapping[str, Any],
    state_snapshot: Optional[Mapping[str, Any]] = None,
) -> str:
    allowed = sorted(_allowed_speakers(turn_contract, state_snapshot))
    for value in allowed:
        if value and not value.lower().startswith("npc:"):
            return value
    for value in allowed:
        if value:
            return value.replace("npc:", "").replace("_", " ").title()
    return ""


def build_deterministic_fallback_narration(
    turn_contract: Mapping[str, Any],
    *,
    violations: Optional[Sequence[GroundingViolation]] = None,
    state_snapshot: Optional[Mapping[str, Any]] = None,
    reason: str = "grounding_validation_failed",
) -> Dict[str, Any]:
    contract = _safe_dict(turn_contract)
    result = _safe_dict(
        contract.get("result")
        or contract.get("resolved_result")
        or contract.get("resolved_action")
    )
    violations = list(violations or [])

    codes = {violation.code for violation in violations}
    speaker = _first_allowed_speaker(contract, state_snapshot)

    player_action_text = _extract_player_action_text(contract)
    player_made_debt_claim = bool(
        _contains_pattern(player_action_text, _UNSUPPORTED_DEBT_CLAIM_PATTERNS)
    )

    narration = ""
    action = ""
    npc_line = ""

    if (
        player_made_debt_claim
        or "unsupported_debt_payment_claim" in codes
        or "unsupported_debt_claim_not_refused" in codes
    ):
        display_speaker = speaker or "The NPC"
        narration = f"{display_speaker} does not hand over any coin."
        action = "The unsupported debt claim is refused; no payment or reward is resolved."
        npc_line = "No. I do not owe you coin."
    elif "unsupported_reward_claim" in codes or "unsupported_reward" in codes:
        narration = f"{speaker or 'The NPC'} does not hand over any coin."
        action = "No payment, reward, or inventory change is resolved by the turn contract."
        npc_line = "No coin changes hands. I can't agree to that."
    elif "unsupported_combat_claim" in codes:
        narration = "The confrontation remains tense, but no injury is resolved."
        action = "No combat, damage, death, or injury is resolved by the turn contract."
        npc_line = "Careful. This does not need to become violence."
    elif "unsupported_location_move" in codes:
        current_location = (
            contract.get("current_location")
            or contract.get("location")
            or result.get("current_location")
            or result.get("location")
            or "the current location"
        )
        narration = f"You remain at {current_location}."
        action = "No location change is resolved by the turn contract."
    elif "unsupported_objective_or_quest_completion" in codes:
        narration = "The lead remains unresolved."
        action = "No quest or objective completion is resolved by the turn contract."
        npc_line = "Not yet. We need something solid before calling this finished."
    elif "unsupported_npc_speaker" in codes:
        narration = "No unsupported speaker enters the exchange."
        action = "Only present and allowed characters may speak."
    else:
        brief = _safe_dict(contract.get("narration_brief"))
        facts = _extract_allowed_fact_texts(contract)
        narration = (
            _safe_str(brief.get("summary")).strip()
            or (facts[0] if facts else "")
            or _safe_str(result.get("narrative_brief") or result.get("message") or result.get("summary")).strip()
            or "The action resolves according to the current turn contract."
        )
        action = narration

    npc = None
    if npc_line:
        npc = {"speaker": speaker or "NPC", "line": npc_line}

    return {
        "format_version": "rpg_narration_v2",
        "narration": narration,
        "action": action,
        "npc": npc,
        "reward": None,
        "followup_hooks": [],
        "grounding_fallback": True,
        "grounding_fallback_reason": reason,
    }


def _reward_pattern_is_only_price_quote(text: str, reward_pattern: Optional[str]) -> bool:
    if not reward_pattern:
        return False

    text = _safe_str(text)

    # Explicit grant/receive verbs still mean reward claim, not price quote.
    explicit_grant_patterns = [
        rf"\byou\s+(?:gain|gained|receive|received|get|got|take|took|earn|earned|are\s+given|were\s+given)\s+{_MONEY_PHRASE_PATTERN}\b",
        rf"\b(?:gain|gained|receive|received|receives|rewarded|earns?|earned)\b.*\b{_MONEY_PHRASE_PATTERN}\b",
        rf"\b(?:hands?|handed|gives?|gave|offers?|offered|pays?|paid)\s+(?:you\s+)?{_MONEY_PHRASE_PATTERN}\b",
        rf"\b{_MONEY_PHRASE_PATTERN}\s+(?:is|are|was|were)\s+(?:added|placed|put)\b.*\b(?:inventory|purse|hand|pocket|pack)\b",
        r"\badds?\b.*\bto your inventory\b",
        r"\bpayment changes hands\b",
    ]
    if _contains_pattern(text, explicit_grant_patterns):
        return False

    # If there is a price/cost/service context, treat currency mention as a quote.
    return bool(_contains_price_quote(text))


def _candidate_payload(value: Any) -> Dict[str, Any]:
    value = _safe_dict(value)
    npc = _safe_dict(value.get("npc"))
    return {
        "format_version": "rpg_narration_v2",
        "narration": _safe_str(value.get("narration")).strip(),
        "action": _safe_str(value.get("action")).strip(),
        "npc": {
            "speaker": _safe_str(npc.get("speaker")).strip(),
            "line": _safe_str(npc.get("line")).strip(),
        }
        if npc
        else None,
        "reward": value.get("reward") if value.get("reward") not in ("", {}, []) else None,
        "followup_hooks": _safe_list(value.get("followup_hooks")),
    }


def select_grounded_narration_candidate(
    parsed_payload: Mapping[str, Any],
    turn_contract: Mapping[str, Any],
    *,
    state_snapshot: Optional[Mapping[str, Any]] = None,
    grounding_settings: Optional[Mapping[str, Any]] = None,
    strict_named_fact_check: bool = False,
) -> Dict[str, Any]:
    settings = normalize_grounding_settings(grounding_settings or {})
    parsed = _safe_dict(parsed_payload)

    if not settings.get("enabled", True):
        payload = _candidate_payload(parsed)
        payload["grounding_validation"] = {
            "ok": True,
            "selected_candidate": "ungrounded_disabled",
            "fallback_used": False,
            "violations": [],
        }
        return payload

    if "primary" in parsed or "safe_fallback" in parsed:
        primary = _candidate_payload(parsed.get("primary"))
        safe_fallback = _candidate_payload(parsed.get("safe_fallback"))
    else:
        primary = _candidate_payload(parsed)
        safe_fallback = {}

    primary_result = validate_narration_grounding(
        primary,
        turn_contract,
        state_snapshot=state_snapshot,
        strict_named_fact_check=strict_named_fact_check,
    )

    if primary_result.ok:
        primary_result.selected_candidate = "primary"
        primary["grounding_validation"] = primary_result.to_dict()
        return primary

    if settings.get("llm_safe_fallback_candidate", True) and safe_fallback:
        fallback_result = validate_narration_grounding(
            safe_fallback,
            turn_contract,
            state_snapshot=state_snapshot,
            strict_named_fact_check=strict_named_fact_check,
        )
        if fallback_result.ok:
            fallback_result.selected_candidate = "safe_fallback"
            fallback_result.fallback_used = True
            fallback_result.fallback_source = "llm_safe_fallback"
            validation = fallback_result.to_dict()
            validation["primary_rejected"] = True
            validation["primary_violations"] = primary_result.to_dict().get("violations", [])
            safe_fallback["grounding_validation"] = validation
            return safe_fallback

    deterministic = build_deterministic_fallback_narration(
        turn_contract,
        violations=primary_result.violations,
        state_snapshot=state_snapshot,
    )

    # Include violations from both primary and safe_fallback attempts
    validation = primary_result.to_dict()
    validation["selected_candidate"] = "deterministic_fallback"
    validation["fallback_used"] = True
    validation["fallback_source"] = "deterministic_fallback"

    if settings.get("llm_safe_fallback_candidate", True) and safe_fallback:
        fallback_result = validate_narration_grounding(
            safe_fallback,
            turn_contract,
            state_snapshot=state_snapshot,
            strict_named_fact_check=strict_named_fact_check,
        )
        if not fallback_result.ok:
            validation["safe_fallback_rejected"] = True
            validation["safe_fallback_violations"] = fallback_result.to_dict().get("violations", [])

    deterministic["grounding_validation"] = validation
    return deterministic


