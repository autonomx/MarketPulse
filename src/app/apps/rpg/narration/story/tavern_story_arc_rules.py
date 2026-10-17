from __future__ import annotations

from typing import List, Tuple

from app.apps.rpg.narration.story.story_arc_lifecycle import ArcFailureRule, ArcResolutionRule


def tavern_story_arc_rules() -> Tuple[List[ArcResolutionRule], List[ArcFailureRule]]:
    resolution_rules = [
        ArcResolutionRule(
            id="arc_rule:marked_coin_completed",
            arc_id="arc:marked_coin_investigation",
            outcome="completed_marked_coin_lead",
            requires_objectives=("objective:identify_marked_coin",),
            requires_items=("item:marked_coin",),
            requires_mechanics=("combat_resolved", "quest_progress"),
            reward_xp=10,
            set_flags=("arc:marked_coin_investigation.completed",),
            summary=(
                "The marked coin investigation is resolved after the party defeats the scouts "
                "and brings the proof back to Bran."
            ),
        ),
        ArcResolutionRule(
            id="arc_rule:road_threat_completed",
            arc_id="arc:mill_road_threat",
            outcome="completed_bandit_scout_threat",
            requires_flags=("encounter:mill_bandit_scouts.resolved",),
            requires_mechanics=("combat_resolved",),
            reward_xp=10,
            set_flags=("arc:mill_road_threat.completed",),
            summary="The immediate bandit threat on the mill road is resolved.",
        ),
    ]

    failure_rules = [
        ArcFailureRule(
            id="arc_fail:marked_coin_stalled",
            arc_id="arc:marked_coin_investigation",
            outcome="failed_investigation_stalled",
            fail_after_turns_without_progress=80,
            summary="The marked coin trail goes cold after too many turns without progress.",
        ),
        ArcFailureRule(
            id="arc_fail:road_threat_stalled",
            arc_id="arc:mill_road_threat",
            outcome="failed_bandits_scattered",
            fail_after_turns_without_progress=80,
            summary="The bandit scouts scatter before the party can resolve the road threat.",
        ),
    ]

    return resolution_rules, failure_rules