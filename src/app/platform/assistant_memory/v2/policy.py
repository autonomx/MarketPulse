from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from app.conversation.memory_policy import sensitivity_allows, strongest_sensitivity, weakest_trust

from .contracts import (
    DerivedPolicyEnvelope,
    Observation,
    TrustLevel,
    VisibilityScope,
)

_SCOPE_RANK = {"global": 0, "workspace": 1, "project": 2, "session": 3}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def normalize_visibility(scopes: Iterable[VisibilityScope]) -> tuple[VisibilityScope, ...]:
    unique = {(scope.kind, scope.scope_id): scope for scope in scopes}
    return tuple(
        unique[key]
        for key in sorted(
            unique,
            key=lambda item: (_SCOPE_RANK[item[0]], item[1]),
        )
    )


def visibility_satisfied(
    required: tuple[VisibilityScope, ...],
    visible: tuple[VisibilityScope, ...],
) -> bool:
    """Conjunctive visibility: the reader must satisfy every inherited scope."""

    available = {(scope.kind, scope.scope_id) for scope in visible}
    return all((scope.kind, scope.scope_id) in available for scope in required)


def _effective_trust(
    observations: tuple[Observation, ...],
    inherited: tuple[DerivedPolicyEnvelope, ...],
) -> TrustLevel:
    candidates: list[TrustLevel] = [item.provenance.trust_level for item in observations]
    candidates.extend(item.trust_class for item in inherited)
    return weakest_trust(
        candidates,
        cap_derived_at_assistant_inference=True,
    )


def derive_policy_envelope(
    observations: Iterable[Observation],
    *,
    inherited: Iterable[DerivedPolicyEnvelope] = (),
    source_assertion_ids: Iterable[str] = (),
    declared_visibility: Iterable[VisibilityScope] = (),
    governance_revision: int,
    policy_version: str = "memory-v2-derived-policy@1",
) -> DerivedPolicyEnvelope:
    evidence = tuple(observations)
    inherited_policies = tuple(inherited)
    # Visibility requirements are conjunctive. Evidence requirements can only be kept or
    # narrowed by adding declared requirements; a derivation can never drop a backing scope.
    visibility = normalize_visibility(
        [item.visibility_scope for item in evidence]
        + [
            scope
            for policy in inherited_policies
            for scope in policy.effective_visibility
        ]
        + list(declared_visibility)
    )
    if not visibility:
        raise ValueError("derived policy requires visibility-bearing evidence")

    sensitivity_candidates = [item.sensitivity for item in evidence]
    sensitivity_candidates.extend(item.sensitivity for item in inherited_policies)
    sensitivity = strongest_sensitivity(sensitivity_candidates)
    observation_ids = tuple(
        sorted(
            {item.observation_id for item in evidence}
            | {
                source_id
                for policy in inherited_policies
                for source_id in policy.source_observation_ids
            }
        )
    )
    assertion_ids = tuple(
        sorted(
            set(source_assertion_ids)
            | {
                source_id
                for policy in inherited_policies
                for source_id in policy.source_assertion_ids
            }
        )
    )
    material: dict[str, Any] = {
        "sensitivity": sensitivity,
        "effective_visibility": [item.model_dump(mode="json") for item in visibility],
        "trust_class": _effective_trust(evidence, inherited_policies),
        "source_observation_ids": observation_ids,
        "source_assertion_ids": assertion_ids,
        "source_governance_revision": int(governance_revision),
        "policy_version": policy_version,
    }
    return DerivedPolicyEnvelope(
        **material,
        policy_digest=_digest(material),
    )


def policy_digest(policies: Iterable[DerivedPolicyEnvelope]) -> str:
    material = [
        policy.model_dump(mode="json")
        for policy in sorted(policies, key=lambda item: item.policy_digest)
    ]
    return _digest(material)


__all__ = [
    "derive_policy_envelope",
    "normalize_visibility",
    "policy_digest",
    "sensitivity_allows",
    "strongest_sensitivity",
    "visibility_satisfied",
    "weakest_trust",
]
