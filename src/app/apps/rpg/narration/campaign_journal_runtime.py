from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

CAMPAIGN_CALENDAR_VERSION = "campaign_calendar_v1"
PLAYER_JOURNAL_VERSION = "player_journal_v2"

SEASONS = ("spring", "summer", "autumn", "winter")


def _safe_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


INTERNAL_JOURNAL_TOKENS = {
    "target_not_found",
    "no_supported_semantic_action_detected",
    "talk_handled_by_conversation_runtime",
    "service_not_available",
    "action_unhandled",
    "semantic_action_unsupported",
    "unsupported_action",
    "unknown_action",
    "no_effect",
    "no_op",
    "noop",
}


def _looks_internal_code(value: str) -> bool:
    text = _safe_str(value).strip()
    if not text:
        return False
    lower = text.lower()
    if lower in INTERNAL_JOURNAL_TOKENS:
        return True
    if " " not in lower and "_" in lower:
        return True
    if lower.endswith("_runtime") or lower.endswith("_detected") or lower.endswith("_unsupported"):
        return True
    if lower.startswith("target_") or lower.startswith("semantic_"):
        return True
    return False


def _looks_malformed_journal_fragment(value: str) -> bool:
    text = _safe_str(value).strip()
    if not text:
        return True
    lower = text.lower()

    # Fragments caused by prompt/action truncation or quote slicing.
    if text[0] in ".;,:!?)]}":
        return True
    if text.startswith(("\".", "'.", "“.", "‘.")):
        return True
    if ".and " in lower or "\n.and " in lower:
        return True
    if text.startswith(("…", "...")):
        return True
    if lower.startswith(("or trouble", "gold? or", "or gold", "and ask", "then ask")):
        return True

    # Too short to be useful unless it is a normal sentence-like command.
    words = [part for part in text.replace("…", " ").split() if part.strip()]
    if len(words) <= 2 and not text.endswith((".", "!", "?")):
        return True

    # Broken fragments often have no leading verb/subject and include ellipses.
    if "…" in text or "..." in text:
        if len(words) < 8:
            return True
        if text.count("…") + text.count("...") >= 2:
            return True

    return False


def _clean_journal_text(value: Any, *, max_len: int = 700) -> str:
    text = _safe_str(value).strip()
    if not text:
        return ""
    if _looks_internal_code(text):
        return ""
    if _looks_malformed_journal_fragment(text):
        return ""
    # Remove isolated internal tokens from otherwise readable text.
    parts = []
    for token in text.replace("\n", " ").split():
        cleaned = token.strip(".,;:()[]{}\"'")
        if _looks_internal_code(cleaned):
            continue
        parts.append(token)
    text = " ".join(parts).strip()
    if not text:
        return ""
    text = text.replace('".and ', '"and ')
    text = text.replace("'.and ", "'and ")
    text = text.replace("“.and ", "“and ")
    text = text.replace("‘.and ", "‘and ")
    text = text.replace(': ".', ": ")
    text = text.replace(": '.", ": ")
    text = text.replace('".what', "what")
    text = text.replace("'.what", "what")
    text = text.replace('".What', "What")
    text = text.replace("'.What", "What")
    text = text.replace('".', "")
    text = text.replace("'.", "")
    while "  " in text:
        text = text.replace("  ", " ")
    if _looks_malformed_journal_fragment(text):
        return ""
    if len(text) > max_len:
        # Prefer cutting at a sentence boundary when possible.
        candidate = text[: max_len - 1].rstrip()
        boundary = max(candidate.rfind("."), candidate.rfind("!"), candidate.rfind("?"))
        if boundary >= max(80, int(max_len * 0.45)):
            text = candidate[: boundary + 1].strip()
        else:
            text = candidate.rstrip(" ,;:") + "…"
    return text


def _normalize_sentence_punctuation(text: str) -> str:
    text = _safe_str(text).strip()
    if not text:
        return ""
    while "  " in text:
        text = text.replace("  ", " ")
    # Normalize common doubled punctuation from joined snippets.
    replacements = {
        "..": ".",
        "!.": "!",
        "?.": "?",
        ";.": ";",
        ",.": ".",
        ".;": ".",
    }
    changed = True
    while changed:
        changed = False
        for old, new in replacements.items():
            if old in text:
                text = text.replace(old, new)
                changed = True
    # Avoid trailing semicolon/comma fragments.
    text = text.rstrip(" ;,")
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _sentence_join(parts: List[str], *, max_items: int = 4) -> str:
    cleaned: List[str] = []
    seen = set()
    for part in _safe_list(parts):
        text = _normalize_sentence_punctuation(_clean_journal_text(part, max_len=240))
        if not text:
            continue
        if '".and ' in text.lower() or "'.and " in text.lower():
            continue
        marker = text.lower()
        if marker in seen:
            continue
        seen.add(marker)
        cleaned.append(text)
        if len(cleaned) >= max_items:
            break
    return " ".join(cleaned).strip()


def _contains_next_signal(text: str) -> bool:
    lower = _safe_str(text).lower()
    return any(
        token in lower
        for token in (
            "find",
            "report",
            "look for",
            "ask",
            "follow",
            "investigate",
            "search",
            "return",
            "next",
            "should",
            "need",
            "witness",
        )
    )


def _infer_next_lines(actions: List[str], results: List[str]) -> List[str]:
    candidates = list(_safe_list(results)[-3:]) + list(_safe_list(actions)[-3:])
    next_lines: List[str] = []
    for item in candidates:
        text = _clean_journal_text(item, max_len=220)
        if text and _contains_next_signal(text):
            next_lines.append(text)
    return next_lines


def _quest_progress_lines(runtime_state: Dict[str, Any]) -> List[str]:
    quest_state = _safe_dict(_safe_dict(runtime_state).get("quest_progress"))
    quests = _safe_dict(quest_state.get("quests"))
    lines: List[str] = []
    for quest in quests.values():
        quest = _safe_dict(quest)
        title = _safe_str(quest.get("title") or quest.get("quest_id"))
        objectives = _safe_list(quest.get("objectives"))
        active_objectives = []
        for objective in objectives:
            objective = _safe_dict(objective)
            if objective.get("completed") is True:
                continue
            status = _safe_str(objective.get("status")).lower()
            if status in {"completed", "done", "resolved"}:
                continue
            summary = _safe_str(objective.get("summary") or objective.get("title"))
            if summary:
                active_objectives.append(summary)
        if title and active_objectives:
            lines.append(f"{title}: " + "; ".join(active_objectives[:3]))
    return lines[:3]


def _first_clean_text(*values: Any, max_len: int = 700) -> str:
    for value in values:
        cleaned = _clean_journal_text(value, max_len=max_len)
        if cleaned:
            return cleaned
    return ""


def _narration_payload_candidates(turn_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    turn_result = _safe_dict(turn_result)
    result = _safe_dict(turn_result.get("result"))
    combined = _safe_dict(turn_result.get("combined_background_llm_result"))
    resolved = _safe_dict(turn_result.get("resolved_narration_payload"))
    return [
        _safe_dict(turn_result.get("narration_payload")),
        _safe_dict(result.get("narration_payload")),
        _safe_dict(combined.get("narration_payload")),
        resolved,
    ]


def _narration_text_candidates(turn_result: Dict[str, Any]) -> List[str]:
    turn_result = _safe_dict(turn_result)
    combined = _safe_dict(turn_result.get("combined_background_llm_result"))
    resolved = _safe_dict(turn_result.get("resolved_narration_payload"))
    candidates: List[str] = [
        _safe_str(turn_result.get("narration")),
        _safe_str(combined.get("narration")),
        _safe_str(resolved.get("narration")),
    ]
    for payload in _narration_payload_candidates(turn_result):
        npc = _safe_dict(payload.get("npc"))
        candidates.extend(
            [
                _safe_str(payload.get("narration")),
                _safe_str(payload.get("action")),
                _safe_str(npc.get("line")),
            ]
        )
    return [item for item in candidates if item]


def campaign_time_for_turn(
    *,
    turn_index: int,
    start_year: int = 1000,
    start_day: int = 1,
    minutes_per_turn: int = 30,
) -> Dict[str, Any]:
    """Deterministic lightweight fantasy calendar.

    This is metadata only. It does not alter travel, rests, services, combat,
    quests, or other authoritative simulation facts.
    """
    turn_index = max(1, int(turn_index or 1))
    minutes_per_turn = max(1, int(minutes_per_turn or 30))
    total_minutes = (turn_index - 1) * minutes_per_turn
    day_offset = total_minutes // (24 * 60)
    minute_of_day = total_minutes % (24 * 60)
    hour = minute_of_day // 60
    minute = minute_of_day % 60
    day_of_year = ((start_day - 1 + day_offset) % 360) + 1
    year = start_year + ((start_day - 1 + day_offset) // 360)
    season = SEASONS[(day_of_year - 1) // 90]
    day = ((day_of_year - 1) % 30) + 1
    month = ((day_of_year - 1) // 30) + 1
    if 5 <= hour < 12:
        phase = "morning"
    elif 12 <= hour < 17:
        phase = "afternoon"
    elif 17 <= hour < 21:
        phase = "evening"
    else:
        phase = "night"
    return {
        "format_version": CAMPAIGN_CALENDAR_VERSION,
        "year": year,
        "season": season,
        "month": month,
        "day": day,
        "day_of_year": day_of_year,
        "hour": hour,
        "minute": minute,
        "time_label": f"{hour:02d}:{minute:02d}",
        "day_phase": phase,
        "absolute_day": int(day_offset) + 1,
        "turn_index": turn_index,
        "minutes_per_turn": minutes_per_turn,
    }


def campaign_journal_runtime_state(runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    runtime_state = runtime_state if isinstance(runtime_state, dict) else {}
    runtime_state.setdefault(
        "campaign_calendar",
        {
            "format_version": CAMPAIGN_CALENDAR_VERSION,
            "minutes_per_turn": 30,
            "current": campaign_time_for_turn(turn_index=1),
            "history": [],
        },
    )
    runtime_state.setdefault(
        "player_journal",
        {
            "format_version": PLAYER_JOURNAL_VERSION,
            "entries": [],
            "pending_actions": [],
            "pending_results": [],
            "max_entries": 100,
        },
    )
    return runtime_state


def _authoritative_campaign_time(
    calendar_snapshot: Dict[str, Any] | None,
    *,
    fallback: Dict[str, Any],
) -> Dict[str, Any]:
    snapshot = _safe_dict(calendar_snapshot)
    calendar = _safe_dict(snapshot.get("calendar")) or snapshot
    if not calendar:
        return fallback

    year = max(1, int(calendar.get("year") or fallback.get("year") or 1))
    day_of_year = max(1, int(calendar.get("day_of_year") or fallback.get("day_of_year") or 1))
    days_per_year = max(1, int(calendar.get("days_per_year") or 360))
    absolute_minutes = int(
        snapshot.get("absolute_minutes")
        or calendar.get("absolute_minutes")
        or ((day_of_year - 1) * 24 * 60)
    )
    minute_of_day = absolute_minutes % (24 * 60)
    hour = minute_of_day // 60
    minute = minute_of_day % 60
    absolute_day = ((year - 1) * days_per_year) + day_of_year
    season = _safe_str(
        snapshot.get("season_id")
        or _safe_dict(snapshot.get("display")).get("season")
        or fallback.get("season")
    ) or SEASONS[min(3, ((day_of_year - 1) * 4) // days_per_year)]
    if 5 <= hour < 12:
        phase = "morning"
    elif 12 <= hour < 17:
        phase = "afternoon"
    elif 17 <= hour < 21:
        phase = "evening"
    else:
        phase = "night"
    return {
        **fallback,
        "year": year,
        "season": season,
        "day_of_year": day_of_year,
        "day": ((day_of_year - 1) % 30) + 1,
        "month": ((day_of_year - 1) // 30) + 1,
        "hour": hour,
        "minute": minute,
        "time_label": f"{hour:02d}:{minute:02d}",
        "day_phase": phase,
        "absolute_day": absolute_day,
        "calendar_source": "environment_runtime",
    }


def _player_voice_context(player_context: Dict[str, Any] | None) -> Dict[str, Any]:
    context = _safe_dict(player_context)
    profile = _safe_dict(context.get("personality_profile"))
    metadata = _safe_dict(context.get("metadata"))
    identity = _safe_dict(context.get("character_identity"))
    drivers = _safe_dict(context.get("drivers"))
    traits = [
        _safe_str(value).strip().lower()
        for value in _safe_list(profile.get("traits"))
        + _safe_list(metadata.get("values"))
        + _safe_list(drivers.get("values"))
        if _safe_str(value).strip()
    ]
    flaw = _safe_str(metadata.get("flaw") or drivers.get("flaw")).strip().lower()
    if flaw and flaw not in traits:
        traits.append(flaw)
    temperament = _safe_str(profile.get("temperament") or flaw).strip().lower()
    tone = _safe_str(profile.get("tone_hint") or profile.get("tone")).strip().lower()
    genre = _safe_str(
        context.get("genre")
        or metadata.get("genre")
        or identity.get("genre")
        or "fantasy"
    ).strip().lower()
    background = _safe_str(identity.get("background") or context.get("background")).strip().lower()
    playstyle = _safe_str(
        profile.get("playstyle")
        or metadata.get("primary_capability")
        or identity.get("primary_capability")
    ).strip().lower()
    return {
        "label": _safe_str(profile.get("label") or background or "Adventurer").strip().title(),
        "tone": tone or "neutral",
        "temperament": temperament,
        "traits": list(dict.fromkeys(traits))[:8],
        "genre": genre,
        "background": background,
        "playstyle": playstyle,
    }


def _personality_reflection(voice: Dict[str, Any]) -> str:
    markers = " ".join(
        [
            _safe_str(voice.get("tone")),
            _safe_str(voice.get("temperament")),
            *[_safe_str(value) for value in _safe_list(voice.get("traits"))],
        ]
    ).lower()
    if any(token in markers for token in ("cautious", "careful", "patient", "pragmatic")):
        return "I kept my eyes open and measured each choice; haste has never been the same thing as courage."
    if any(token in markers for token in ("heroic", "brave", "honorable", "protective", "merciful")):
        return "Whatever the cost, I meant to leave those around me safer than I found them."
    if any(token in markers for token in ("dark", "ruthless", "cold", "dominating")):
        return "I watched for weakness and leverage, because mercy is useful only when it buys something."
    if any(token in markers for token in ("cunning", "sly", "deceptive", "manipulative")):
        return "I showed only what served me and kept the sharper part of my purpose hidden."
    if any(token in markers for token in ("wild", "chaotic", "impulsive", "unpredictable")):
        return "The straight road looked dull, so I trusted instinct and let the day surprise me."
    return "I tried to read the day clearly and make each choice count."


def _daily_journal_text(
    actions: List[str],
    results: List[str],
    *,
    runtime_state: Dict[str, Any],
    voice: Dict[str, Any],
) -> str:
    clean_actions = [_clean_journal_text(value, max_len=220) for value in actions[-6:]]
    clean_results = [_clean_journal_text(value, max_len=340) for value in results[-6:]]
    clean_actions = [value for value in clean_actions if value]
    clean_results = [value for value in clean_results if value]
    paragraphs = [_personality_reflection(voice)]
    action_text = _sentence_join(clean_actions, max_items=4)
    if action_text:
        paragraphs.append(action_text)
    result_text = _sentence_join(clean_results, max_items=3)
    if result_text:
        paragraphs.append(result_text)
    next_text = _sentence_join(
        _quest_progress_lines(runtime_state) + _infer_next_lines(clean_actions, clean_results),
        max_items=2,
    )
    if next_text:
        genre = _safe_str(voice.get("genre")).lower()
        if any(token in genre for token in ("science", "sci-fi", "space", "cyber")):
            reminder = "Before the next cycle, I must remember this"
        elif any(token in genre for token in ("horror", "gothic")):
            reminder = "Before darkness closes in again, I must remember this"
        elif any(token in genre for token in ("western", "frontier")):
            reminder = "Before I ride on, I must remember this"
        else:
            reminder = "Before the next watch, I must remember this"
        paragraphs.append(f"{reminder}: {next_text}")
    return "\n\n".join(_normalize_sentence_punctuation(value) for value in paragraphs if value).strip()


def _daily_journal_title(voice: Dict[str, Any]) -> str:
    temperament = _safe_str(voice.get("temperament")).strip().title()
    label = _safe_str(voice.get("label") or "Adventurer").strip()
    if temperament and temperament.lower() not in label.lower():
        return f"A {temperament} {label}'s Journal"
    return f"{label}'s Journal"


def _extract_player_action(
    *,
    player_input: str = "",
    turn_contract: Dict[str, Any] | None = None,
    turn_result: Dict[str, Any] | None = None,
) -> str:
    turn_contract = _safe_dict(turn_contract)
    turn_result = _safe_dict(turn_result)
    return _first_clean_text(
        player_input,
        turn_contract.get("player_input"),
        turn_contract.get("action"),
        turn_result.get("player_action"),
        turn_result.get("player_input"),
        max_len=300,
    )


def _extract_result_summary(
    *,
    turn_contract: Dict[str, Any] | None = None,
    turn_result: Dict[str, Any] | None = None,
) -> str:
    turn_contract = _safe_dict(turn_contract)
    turn_result = _safe_dict(turn_result)
    resolved_result = _safe_dict(turn_contract.get("resolved_result"))
    resolved_action = _safe_dict(turn_contract.get("resolved_action"))

    # Prefer player-facing prose over deterministic internal reason codes.
    return _first_clean_text(
        *_narration_text_candidates(turn_result),
        resolved_result.get("summary"),
        resolved_result.get("description"),
        resolved_action.get("summary"),
        turn_contract.get("narration_brief"),
        turn_contract.get("result"),
        max_len=700,
    )


def advance_campaign_journal_for_turn(
    *,
    runtime_state: Dict[str, Any],
    turn_index: int,
    player_input: str = "",
    turn_contract: Dict[str, Any] | None = None,
    turn_result: Dict[str, Any] | None = None,
    minutes_per_turn: int | None = None,
    journal_every_turns: int | None = None,
    calendar_snapshot: Dict[str, Any] | None = None,
    player_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Advance the calendar and upsert one player-perspective entry per day.

    This mutates runtime_state only. It does not mutate authoritative
    simulation facts.
    """
    runtime_state = campaign_journal_runtime_state(runtime_state)
    calendar = _safe_dict(runtime_state.get("campaign_calendar"))
    journal = _safe_dict(runtime_state.get("player_journal"))
    journal["format_version"] = PLAYER_JOURNAL_VERSION

    if minutes_per_turn is None:
        minutes_per_turn = int(calendar.get("minutes_per_turn") or 30)
    fallback_time = campaign_time_for_turn(
        turn_index=int(turn_index or 1),
        minutes_per_turn=int(minutes_per_turn or 30),
    )
    time_info = _authoritative_campaign_time(calendar_snapshot, fallback=fallback_time)
    calendar["minutes_per_turn"] = int(minutes_per_turn or 30)
    calendar["current"] = time_info
    history = _safe_list(calendar.get("history"))
    if not history or _safe_dict(history[-1]).get("turn_index") != time_info["turn_index"]:
        history.append(time_info)
    calendar["history"] = history[-500:]

    action = _extract_player_action(
        player_input=player_input,
        turn_contract=_safe_dict(turn_contract),
        turn_result=_safe_dict(turn_result),
    )
    result = _extract_result_summary(
        turn_contract=_safe_dict(turn_contract),
        turn_result=_safe_dict(turn_result),
    )

    pending_actions = _safe_list(journal.get("pending_actions"))
    pending_results = _safe_list(journal.get("pending_results"))
    action = _clean_journal_text(action, max_len=300)
    result = _clean_journal_text(result, max_len=700)
    if action:
        pending_actions.append(action)
    if result:
        pending_results.append(result)

    entries = _safe_list(journal.get("entries"))
    absolute_day = int(time_info.get("absolute_day") or 1)
    entry_id = f"journal:day:{absolute_day}"
    existing_index = next(
        (
            index
            for index, value in enumerate(entries)
            if _safe_str(_safe_dict(value).get("entry_id")) == entry_id
        ),
        None,
    )
    entry = _safe_dict(entries[existing_index]) if existing_index is not None else {}
    source_turns = [int(value) for value in _safe_list(entry.get("source_turns")) if str(value).isdigit()]
    if int(turn_index or 1) not in source_turns:
        source_turns.append(int(turn_index or 1))
        entry_actions = _safe_list(entry.get("actions")) + ([action] if action else [])
        entry_results = _safe_list(entry.get("results")) + ([result] if result else [])
    else:
        entry_actions = _safe_list(entry.get("actions"))
        entry_results = _safe_list(entry.get("results"))
    voice = _player_voice_context(player_context)
    entry.update(
        {
            "format_version": PLAYER_JOURNAL_VERSION,
            "entry_id": entry_id,
            "day": absolute_day,
            "day_label": f"Day {absolute_day}",
            "title": _daily_journal_title(voice),
            "start_turn": min(source_turns) if source_turns else int(turn_index or 1),
            "end_turn": max(source_turns) if source_turns else int(turn_index or 1),
            "updated_through_turn": int(turn_index or 1),
            "source_turns": source_turns[-100:],
            "actions": entry_actions[-24:],
            "results": entry_results[-24:],
            "time": deepcopy(time_info),
            "perspective": "player",
            "voice": voice,
            "text": _daily_journal_text(
                entry_actions,
                entry_results,
                runtime_state=runtime_state,
                voice=voice,
            ),
            "source": "deterministic_daily_player_journal",
        }
    )
    if existing_index is None:
        entries.append(entry)
    else:
        entries[existing_index] = entry
    journal["pending_actions"] = []
    journal["pending_results"] = []
    journal.pop("journal_every_turns", None)

    max_entries = int(journal.get("max_entries") or 100)
    journal["entries"] = entries[-max_entries:]
    runtime_state["campaign_calendar"] = calendar
    runtime_state["player_journal"] = journal
    return runtime_state


