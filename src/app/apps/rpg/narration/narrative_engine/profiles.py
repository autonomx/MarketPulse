"""Adaptive word and beat budgets shared by every narrative mode."""
from __future__ import annotations

from dataclasses import dataclass

from .authority import NarrativeSignificance, PresentationProfile


@dataclass(frozen=True)
class NarrativeProfilePolicy:
    profile: PresentationProfile
    minimum_words: int
    maximum_words: int
    maximum_beats: int
    allow_lore_expansion: bool
    allow_forward_hook: bool


_PROFILE_POLICIES = {
    PresentationProfile.FAST: NarrativeProfilePolicy(
        profile=PresentationProfile.FAST,
        minimum_words=25,
        maximum_words=90,
        maximum_beats=3,
        allow_lore_expansion=False,
        allow_forward_hook=False,
    ),
    PresentationProfile.IMMERSIVE: NarrativeProfilePolicy(
        profile=PresentationProfile.IMMERSIVE,
        minimum_words=90,
        maximum_words=220,
        maximum_beats=5,
        allow_lore_expansion=True,
        allow_forward_hook=True,
    ),
    PresentationProfile.CINEMATIC: NarrativeProfilePolicy(
        profile=PresentationProfile.CINEMATIC,
        minimum_words=180,
        maximum_words=350,
        maximum_beats=8,
        allow_lore_expansion=True,
        allow_forward_hook=True,
    ),
}


def profile_policy(profile: PresentationProfile) -> NarrativeProfilePolicy:
    return _PROFILE_POLICIES[profile]


def adaptive_profile(
    requested: PresentationProfile,
    significance: NarrativeSignificance,
) -> PresentationProfile:
    """Raise presentation depth for important turns without changing architecture."""

    if significance is NarrativeSignificance.MAJOR:
        return PresentationProfile.CINEMATIC
    if significance is NarrativeSignificance.NOTABLE and requested is PresentationProfile.FAST:
        return PresentationProfile.IMMERSIVE
    return requested
