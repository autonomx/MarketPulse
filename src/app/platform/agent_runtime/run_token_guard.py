"""Run-token checks that need the run's durable state (WP-4.6).

The authentication middleware has already verified the token's signature,
expiry and run binding. This dependency adds what only the database knows:
the run is still live, its issued capabilities are unchanged and the token
was issued by the owner that holds the run now.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.security.auth.middleware import run_token_claims
from app.security.run_tokens import RunTokenClaims, capabilities_digest

_FINISHED = frozenset({"cancelled", "completed", "failed"})


def _refuse(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "OmnixRun"})


def require_live_run_token(request: Request) -> RunTokenClaims:
    claims = run_token_claims(request.scope)
    if claims is None:
        raise _refuse("run_token_required")
    from .service import default_agent_run_service

    snapshot = default_agent_run_service().get(claims.run_id)
    if snapshot is None or snapshot.status in _FINISHED:
        raise _refuse("run_token_revoked")
    if capabilities_digest(snapshot.spec.capabilities, snapshot.spec.external_capabilities) != claims.caps_digest:
        raise _refuse("run_token_capabilities_changed")
    if snapshot.worker_id and snapshot.worker_id != claims.owner:
        raise _refuse("run_token_owner_changed")
    return claims


__all__ = ["require_live_run_token"]
