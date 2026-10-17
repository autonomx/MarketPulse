"""Shared memory provenance and sensitivity contracts and ordering rules."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

Sensitivity = Literal["normal", "sensitive", "secret"]
TrustLevel = Literal[
    "user_explicit",
    "system_trusted",
    "assistant_inference",
    "external_untrusted",
    "imported_unverified",
]

_SENSITIVITY_RANK: dict[Sensitivity, int] = {
    "normal": 0,
    "sensitive": 1,
    "secret": 2,
}
_TRUST_RANK: dict[TrustLevel, int] = {
    "external_untrusted": 0,
    "imported_unverified": 1,
    "assistant_inference": 2,
    "user_explicit": 3,
    "system_trusted": 3,
}


def sensitivity_allows(candidate: Sensitivity, maximum: Sensitivity) -> bool:
    return _SENSITIVITY_RANK[candidate] <= _SENSITIVITY_RANK[maximum]


def weakest_trust(
    values: Iterable[TrustLevel],
    *,
    cap_derived_at_assistant_inference: bool = False,
) -> TrustLevel:
    """Return the least trusted input under the canonical memory ordering."""
    candidates = tuple(values)
    if not candidates:
        return "assistant_inference"
    weakest = min(candidates, key=lambda item: _TRUST_RANK[item])
    if cap_derived_at_assistant_inference and _TRUST_RANK[weakest] > _TRUST_RANK["assistant_inference"]:
        return "assistant_inference"
    return weakest


def strongest_sensitivity(values: Iterable[Sensitivity]) -> Sensitivity:
    """Return the most restrictive sensitivity under the canonical ordering."""
    candidates = tuple(values)
    if not candidates:
        return "normal"
    return max(candidates, key=lambda item: _SENSITIVITY_RANK[item])
