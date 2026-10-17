"""Structured ordinary-life cultural coverage for World Forge generation."""
from __future__ import annotations

from typing import Any

_COMPONENTS = (
    "household_structure",
    "food_staple",
    "work_pattern",
    "leisure_practice",
    "care_practice",
    "education_path",
    "status_marker",
    "taboo_boundary",
)
_HOUSEHOLDS = (
    "extended_household",
    "small_household",
    "guild_household",
    "communal_household",
    "mobile_household",
    "chosen_household",
)
_FOODS = (
    "grain_and_stew",
    "river_fish_and_roots",
    "street_noodles",
    "fermented_greens",
    "flatbread_and_pulses",
    "preserved_meat_and_broth",
)
_WORK = (
    "seasonal_collective_labour",
    "shift_based_service_work",
    "household_craft_rotation",
    "apprentice_workshops",
    "mobile_contract_crews",
    "market_day_piecework",
)
_LEISURE = (
    "courtyard_storytelling",
    "public_board_games",
    "night_market_music",
    "community_sport",
    "shared_bath_gossip",
    "festival_processions",
)
_CARE = (
    "kin_care_rotation",
    "neighbourhood_care_circle",
    "guild_sick_fund",
    "temple_clinic",
    "mutual_aid_kitchen",
    "travelling_healer_network",
)
_EDUCATION = (
    "family_trade_teaching",
    "public_basic_school",
    "guild_apprenticeship",
    "oral_mentor_lineage",
    "religious_schooling",
    "community_archive_study",
)
_STATUS = (
    "hospitality_reputation",
    "craft_mastery",
    "public_service",
    "kinship_obligations_met",
    "scholarly_attainment",
    "successful_patronage",
)
_TABOOS = (
    "wasting_shared_food",
    "refusing_funeral_duty",
    "breaking_guest_safety",
    "concealing_public_debt",
    "profiting_from_sacred_water",
    "abandoning_dependants",
)


def ordinary_life_components() -> tuple[str, ...]:
    return _COMPONENTS


def deterministic_ordinary_life_signature(index: int) -> dict[str, Any]:
    """Return a bounded, categorical ordinary-life signature."""

    return {
        "household_structure": _HOUSEHOLDS[index % len(_HOUSEHOLDS)],
        "food_staple": _FOODS[(index * 5 + 1) % len(_FOODS)],
        "work_pattern": _WORK[(index * 3 + 2) % len(_WORK)],
        "leisure_practice": _LEISURE[(index * 5 + 3) % len(_LEISURE)],
        "care_practice": _CARE[(index * 3 + 4) % len(_CARE)],
        "education_path": _EDUCATION[(index * 5 + 5) % len(_EDUCATION)],
        "status_marker": _STATUS[(index * 3 + 1) % len(_STATUS)],
        "taboo_boundary": _TABOOS[(index * 5 + 2) % len(_TABOOS)],
    }


__all__ = [
    "deterministic_ordinary_life_signature",
    "ordinary_life_components",
]
