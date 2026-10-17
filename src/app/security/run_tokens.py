"""Run-scoped signed tokens for agent processes (WP-4.6).

The Pi process of an agent run calls the broker and the model gateway with
``Authorization: OmnixRun <token>``. A token is::

    base64url(payload) "." base64url(HMAC-SHA256(key, payload))

with payload ``{"v", "run_id", "workspace_id", "owner", "caps_digest",
"exp", "nonce"}``. Tokens live 15 minutes; the holder renews them through
the broker while the run is active. A run id header alone is never enough.

Signing key: ``OMNIX_RUN_TOKEN_KEY``. The gateway entrypoint sets it from
``initialize_run_token_key`` (a protected key of its own, or the operator's
value), and its replicas and job worker inherit it. It is never derived from
``OMNIX_SERVICE_TOKEN``: model services hold that token and must not be able
to mint run tokens. Without the key (a single process started by hand), a
random per-process key is used.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass

from app.config.env import env_str

# Gateway paths an agent process calls with its run token (broker and model
# gateway). The middleware requires a run token on them, and the sandbox relay
# forwards nothing else (WP-4.6, WP-4.7).
AGENT_RUNTIME_PATTERNS = (
    r"/api/agent-model/v1/.+",
    r"/api/agent-runs/[^/]+/planning/[a-z_-]+",
    r"/api/agent-runs/[^/]+/run-change-set",
    r"/api/agent-runs/[^/]+/capabilities/[^/]+",
    r"/api/agent-runs/[^/]+/command-authorization",
    r"/api/agent-runs/[^/]+/workspace-authorization",
    r"/api/agent-runs/[^/]+/budget/tool",
    r"/api/agent-runs/[^/]+/run-token",
)
# Set by the sandbox relay on every request it forwards; the gateway refuses
# relayed requests outside AGENT_RUNTIME_PATTERNS.
SANDBOX_RELAY_HEADER = "x-omnix-sandbox-relay"
TOKEN_SCHEME = "OmnixRun"
TOKEN_ENVIRONMENT_KEY = "OMNIX_AGENT_RUN_TOKEN"
DEFAULT_TTL_SECONDS = 900
_VERSION = 1
_KEY_LOCK = threading.Lock()
_PROCESS_KEY: bytes | None = None


class RunTokenError(PermissionError):
    """The token is missing, malformed, forged, expired or for another run."""


@dataclass(frozen=True, slots=True)
class RunTokenClaims:
    run_id: str
    workspace_id: str
    owner: str
    caps_digest: str
    expires_at: int


def capabilities_digest(capabilities: Iterable[str], external_capabilities: Iterable[str]) -> str:
    """Digest of a run's issued capabilities; a token is void if they change."""
    document = json.dumps(
        {"local": sorted(set(capabilities)), "external": sorted(set(external_capabilities))},
        separators=(",", ":"),
    )
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def _key() -> bytes:
    configured = (env_str("OMNIX_RUN_TOKEN_KEY", "") or "").strip()
    if configured:
        if len(configured) < 32:
            raise RunTokenError("OMNIX_RUN_TOKEN_KEY must have at least 32 characters")
        return configured.encode("utf-8")
    global _PROCESS_KEY
    with _KEY_LOCK:
        if _PROCESS_KEY is None:
            _PROCESS_KEY = secrets.token_bytes(32)
        return _PROCESS_KEY


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_run_token(
    *,
    run_id: str,
    workspace_id: str,
    owner: str,
    caps_digest: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    if not all(str(value).strip() for value in (run_id, workspace_id, owner, caps_digest)):
        raise ValueError("run token claims are required")
    issued = int(now if now is not None else time.time())
    payload = json.dumps(
        {
            "v": _VERSION,
            "run_id": run_id,
            "workspace_id": workspace_id,
            "owner": owner,
            "caps_digest": caps_digest,
            "exp": issued + int(ttl_seconds),
            "nonce": secrets.token_urlsafe(12),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(_key(), payload, hashlib.sha256).digest()
    return f"{_encode(payload)}.{_encode(signature)}"


def verify_run_token(token: str, *, run_id: str, now: float | None = None) -> RunTokenClaims:
    """Check signature, expiry and run binding; raises ``RunTokenError``."""
    encoded_payload, separator, encoded_signature = str(token or "").partition(".")
    if not separator or not encoded_payload or not encoded_signature:
        raise RunTokenError("run_token_malformed")
    try:
        payload = _decode(encoded_payload)
        signature = _decode(encoded_signature)
    except (ValueError, TypeError) as exc:
        raise RunTokenError("run_token_malformed") from exc
    expected = hmac.new(_key(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise RunTokenError("run_token_invalid")
    try:
        claims = json.loads(payload)
        result = RunTokenClaims(
            run_id=str(claims["run_id"]),
            workspace_id=str(claims["workspace_id"]),
            owner=str(claims["owner"]),
            caps_digest=str(claims["caps_digest"]),
            expires_at=int(claims["exp"]),
        )
        version = int(claims["v"])
    except (ValueError, KeyError, TypeError) as exc:
        raise RunTokenError("run_token_malformed") from exc
    if version != _VERSION:
        raise RunTokenError("run_token_version_unsupported")
    if result.expires_at <= int(now if now is not None else time.time()):
        raise RunTokenError("run_token_expired")
    if not run_id or not hmac.compare_digest(result.run_id.encode("utf-8"), str(run_id).encode("utf-8")):
        raise RunTokenError("run_token_run_mismatch")
    return result


def token_from_authorization(values: list[str]) -> str | None:
    """The token from exactly one ``OmnixRun`` (or OpenAI-style ``Bearer``) header."""
    if len(values) != 1:
        return None
    scheme, _, token = values[0].strip().partition(" ")
    if scheme.lower() not in {TOKEN_SCHEME.lower(), "bearer"} or not token.strip():
        return None
    return token.strip()


__all__ = [
    "AGENT_RUNTIME_PATTERNS",
    "DEFAULT_TTL_SECONDS",
    "SANDBOX_RELAY_HEADER",
    "TOKEN_ENVIRONMENT_KEY",
    "TOKEN_SCHEME",
    "RunTokenClaims",
    "RunTokenError",
    "capabilities_digest",
    "issue_run_token",
    "token_from_authorization",
    "verify_run_token",
]
