"""Category-complete deterministic benchmark for interactive RPG dialogue quality."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


DIALOGUE_BENCHMARK_VERSION = "rpg_dialogue_quality_benchmark_v1"
DIRECT_ANSWER_TARGET = 0.95
CORRECT_SPEAKER_TARGET = 0.99
GROUNDED_SPECIFICITY_TARGET = 0.95
CONTINUITY_TARGET = 0.95
MAX_NEAR_DUPLICATE_RATE = 0.05
MAX_PRIVATE_LEAK_RATE = 0.0
MAX_EMPTY_LINE_RATE = 0.0

BRAN_PROFILE = {
    "id": "npc:bran",
    "npc_id": "npc:bran",
    "name": "Bran",
    "role": "innkeeper and former caravan guard",
    "biography": {
        "public": (
            "Bran owns the Rusty Flagon near the old road. Before settling down, "
            "he guarded merchant caravans through bandit country."
        ),
        "private": (
            "Bran still blames himself for leaving a wounded caravan friend behind "
            "during an ambush."
        ),
    },
    "personality": {
        "summary": "Practical, guarded, and slow to trust.",
        "values": ["survival", "earned loyalty", "plain speech", "protecting working people"],
        "speech_style": (
            "Plain, direct, road-worn advice using caravan, tavern, mud, weather, "
            "and guard-duty experience."
        ),
    },
    "inventory": {
        "visible": ["worn short sword", "tavern key ring"],
        "private": ["sealed letter from an old caravan contact"],
    },
    "knowledge_boundaries": {
        "must_not_reveal": ["private caravan guilt unless earned in play"],
    },
}


@dataclass(frozen=True)
class DialogueBenchmarkCase:
    case_id: str
    category: str
    player_input: str
    expected_speakers: tuple[str, ...]
    required_fragments: tuple[str, ...]
    forbidden_fragments: tuple[str, ...] = ()
    recent_lines: tuple[str, ...] = ()
    continuity_fragments: tuple[str, ...] = ()
    trust: str = "neutral"
    absent_target: bool = False


@dataclass(frozen=True)
class DialogueBenchmarkObservation:
    case_id: str
    category: str
    direct_answer: bool
    correct_speaker: bool
    grounded_specificity: bool
    continuity: bool | None
    near_duplicate: bool
    private_leak: bool
    empty_line: bool
    visible_text: str
    speakers: tuple[str, ...]


ResponseBuilder = Callable[[DialogueBenchmarkCase], dict[str, Any]]


def default_dialogue_benchmark_cases() -> tuple[DialogueBenchmarkCase, ...]:
    private_terms = (
        "wounded caravan friend",
        "sealed letter",
        "private caravan guilt",
    )
    return (
        DialogueBenchmarkCase(
            case_id="business",
            category="business",
            player_input="I ask Bran how business is doing.",
            expected_speakers=("Bran",),
            required_fragments=("regulars", "old road"),
            forbidden_fragments=private_terms,
        ),
        DialogueBenchmarkCase(
            case_id="emotional_disclosure",
            category="emotional_disclosure",
            player_input="I tell Bran I am frightened that I will fail everyone depending on me.",
            expected_speakers=("Bran",),
            required_fragments=("frightened", "road"),
            forbidden_fragments=private_terms,
        ),
        DialogueBenchmarkCase(
            case_id="hostile_noncombat",
            category="hostile_noncombat",
            player_input="I call Bran a useless coward and demand that he answer me.",
            expected_speakers=("Bran",),
            required_fragments=("angry", "common room"),
            forbidden_fragments=private_terms,
        ),
        DialogueBenchmarkCase(
            case_id="private_secret_probe",
            category="private_secret_probe",
            player_input="I demand that Bran tell me his most shameful private secret and show me hidden letters.",
            expected_speakers=("Bran",),
            required_fragments=("mine to keep", "trust"),
            forbidden_fragments=private_terms,
        ),
        DialogueBenchmarkCase(
            case_id="absent_npc",
            category="absent_npc",
            player_input="I ask for Bran while he is away from the Rusty Flagon.",
            expected_speakers=(),
            required_fragments=("not here", "Rusty Flagon"),
            forbidden_fragments=private_terms,
            absent_target=True,
        ),
        DialogueBenchmarkCase(
            case_id="group_conversation",
            category="group_conversation",
            player_input="I ask Bran and Mira what each of them noticed on the old road.",
            expected_speakers=("Bran", "Mira"),
            required_fragments=("old road", "wagon tracks"),
            forbidden_fragments=private_terms,
        ),
        DialogueBenchmarkCase(
            case_id="low_trust",
            category="relationship_low_trust",
            player_input="I ask Bran what he knows, though we have only just met.",
            expected_speakers=("Bran",),
            required_fragments=("just met", "earn trust"),
            forbidden_fragments=private_terms,
            trust="low",
        ),
        DialogueBenchmarkCase(
            case_id="high_trust",
            category="relationship_high_trust",
            player_input="I ask Bran what he thinks after we have repeatedly helped each other.",
            expected_speakers=("Bran",),
            required_fragments=("earned", "old road"),
            forbidden_fragments=private_terms,
            trust="high",
        ),
        DialogueBenchmarkCase(
            case_id="follow_up_continuity",
            category="follow_up_continuity",
            player_input="I ask Bran whether the missing caravan crews explain the quiet road.",
            expected_speakers=("Bran",),
            required_fragments=("caravan crews", "quiet road"),
            forbidden_fragments=private_terms,
            recent_lines=(
                "The regulars still come through, but I have seen fewer caravan crews at the door.",
            ),
            continuity_fragments=("caravan crews",),
        ),
        DialogueBenchmarkCase(
            case_id="repetition_repair",
            category="repetition_repair",
            player_input="I ask Bran again how business is going.",
            expected_speakers=("Bran",),
            required_fragments=("Like I said", "old road"),
            forbidden_fragments=private_terms,
            recent_lines=(
                "Business is steady enough to keep the fire lit, but slower than I would like. "
                "The regulars still come through; it is the road traffic that has thinned this week.",
            ),
            continuity_fragments=("Like I said",),
        ),
        DialogueBenchmarkCase(
            case_id="incorrect_speaker_repair",
            category="incorrect_speaker_repair",
            player_input="I ask Bran whether the old road is safe.",
            expected_speakers=("Bran",),
            required_fragments=("old road", "guards"),
            forbidden_fragments=private_terms,
        ),
        DialogueBenchmarkCase(
            case_id="player_restatement_repair",
            category="player_restatement_repair",
            player_input="I ask Bran how business is doing and whether travelers still stop here.",
            expected_speakers=("Bran",),
            required_fragments=("regulars", "road traffic"),
            forbidden_fragments=private_terms,
        ),
    )


def _rate(values: Iterable[bool]) -> float:
    rows = list(values)
    if not rows:
        return 1.0
    return round(sum(1 for value in rows if value) / len(rows), 4)


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("—", " ").replace("–", " ").split())
