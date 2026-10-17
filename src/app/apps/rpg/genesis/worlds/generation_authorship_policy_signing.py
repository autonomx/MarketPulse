"""Bind authoritative profile policy to the signed World Forge authorship ledger."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .generation_authorship import AuthorshipValidationError
from .generation_authorship_signing import (
    sign_record,
    validate_signed_authorship,
    verify_record_signature,
)


def signed_authorship_policy(candidate: Mapping[str, Any]) -> dict[str, Any]:
    provenance = candidate.get("provenance")
    provenance = dict(provenance) if isinstance(provenance, Mapping) else {}
    authorship = provenance.get("authorship")
    authorship = dict(authorship) if isinstance(authorship, Mapping) else {}
    if not verify_record_signature(authorship):
        return {}
    policy = authorship.get("authorship_policy")
    return dict(policy) if isinstance(policy, Mapping) else {}


def bind_signed_authorship_policy(
    candidate: Mapping[str, Any],
    policy: Mapping[str, Any] | None,
) -> dict[str, Any]:
    payload = deepcopy(dict(candidate))
    if not policy:
        return payload
    provenance = payload.get("provenance")
    provenance = dict(provenance) if isinstance(provenance, Mapping) else {}
    authorship = provenance.get("authorship")
    authorship = dict(authorship) if isinstance(authorship, Mapping) else {}
    if not verify_record_signature(authorship):
        raise AuthorshipValidationError(
            {
                "schema_version": "rpg_world_publishable_authorship_report_v3",
                "publishable": False,
                "blocked_paths": [
                    {
                        "path": "/provenance/authorship",
                        "code": "server_authorship_signature_invalid",
                    }
                ],
            }
        )
    authorship["authorship_policy"] = deepcopy(dict(policy))
    provenance["authorship"] = sign_record(authorship)
    payload["provenance"] = provenance
    return payload


def validate_policy_bound_authorship(
    candidate: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    effective = dict(policy or {}) or signed_authorship_policy(candidate)
    return validate_signed_authorship(candidate, policy=effective)


def require_policy_bound_authorship(
    candidate: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report = validate_policy_bound_authorship(candidate, policy=policy)
    if not report["publishable"]:
        raise AuthorshipValidationError(report)
    return report


__all__ = [
    "bind_signed_authorship_policy",
    "require_policy_bound_authorship",
    "signed_authorship_policy",
    "validate_policy_bound_authorship",
]
