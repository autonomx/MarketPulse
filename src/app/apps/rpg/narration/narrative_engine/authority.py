"""Authority, visibility, profile, and block taxonomies for RPG presentation."""
from __future__ import annotations

from enum import Enum


class AuthorityClass(str, Enum):
    CONFIRMED_TURN = "confirmed_turn"
    SCENE_OBSERVATION = "scene_observation"
    OBJECTIVE_CANON = "objective_canon"
    HISTORICAL_RECORD = "historical_record"
    PUBLIC_KNOWLEDGE = "public_knowledge"
    NPC_BELIEF = "npc_belief"
    FACTION_DOCTRINE = "faction_doctrine"
    RUMOR = "rumor"
    DISPUTED_CLAIM = "disputed_claim"
    SECRET_CANON = "secret_canon"
    GENERATED_PROPOSAL = "generated_proposal"


class VisibilityClass(str, Enum):
    PUBLIC = "public"
    PLAYER_KNOWN = "player_known"
    NPC_PRIVATE = "npc_private"
    FACTION_PRIVATE = "faction_private"
    NARRATOR_ONLY = "narrator_only"
    GAME_MASTER_ONLY = "game_master_only"


class EvidenceLifetime(str, Enum):
    TURN = "turn"
    SCENE = "scene"
    CAMPAIGN = "campaign"
    PERMANENT = "permanent"


class NarrativeSignificance(str, Enum):
    ROUTINE = "routine"
    NOTABLE = "notable"
    MAJOR = "major"


class PresentationProfile(str, Enum):
    FAST = "fast"
    IMMERSIVE = "immersive"
    CINEMATIC = "cinematic"


class DeliveryMode(str, Enum):
    BLOCKING = "blocking"
    DEFERRED = "deferred"


class BeatKind(str, Enum):
    NARRATION = "narration"
    DIALOGUE = "dialogue"
    ACTION = "action"
    RESULT = "result"
    CHOICE = "choice"
    CLARIFICATION = "clarification"
    STATE_CHANGE = "state_change"


class BeatPurpose(str, Enum):
    SCENE_ESTABLISHMENT = "scene_establishment"
    ENVIRONMENTAL_CHANGE = "meaningful_environmental_change"
    PHYSICAL_REACTION = "physical_reaction"
    DIRECT_ANSWER = "direct_answer"
    LORE_REVEAL = "lore_reveal"
    EMOTIONAL_ESCALATION = "emotional_escalation"
    MOVEMENT = "movement"
    RESOLVED_ACTION = "resolved_action"
    CONSEQUENCE = "consequence"
    CONTINUATION = "continuation"
    ULTIMATUM = "ultimatum"
    OFFERED_CHOICE = "offered_choice"
    CLARIFICATION = "clarification"
