"""Hermes assist diagnostics: sidecar status, schema and a dry-run planning test."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.platform.chat.assist.models import AssistantRequest
from app.platform.chat.assist.modes import ModeChatRequest, plan_mode_chat
from app.providers.hermes_status import hermes_runtime_config, hermes_status_payload


@dataclass
class HermesDiagnosticsTestRequest:
    """Input for a safe Hermes diagnostics round trip.

    Diagnostics tests are always dry-run. They may ask Hermes/local fallback for
    a plan, but Omnix must not mutate house, RPG, file, or workspace state from
    this helper.
    """

    content: str = "house status"
    session_id: str = "diagnostics"
    domain: str = "chat"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HermesDiagnosticsTestResult:
    ok: bool
    dry_run: bool
    status: dict[str, Any]
    request: dict[str, Any]
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def hermes_diagnostics_status_payload() -> dict[str, Any]:
    """Return the canonical Hermes diagnostics status payload.

    This is intentionally a thin wrapper around the existing status helper so
    Settings and future /api/hermes/status consumers share one source of truth.
    """

    payload = hermes_status_payload()
    config = hermes_runtime_config()
    payload.setdefault("enabled", config.enabled)
    payload.setdefault("base_url", config.base_url)
    payload["timeout_seconds"] = config.timeout_seconds
    payload["api_key_configured"] = config.api_key_configured
    payload["diagnostics"] = {
        "status_path": "/api/hermes/status",
        "test_path": "/api/hermes/test",
        "test_dry_run_only": True,
    }
    return payload


def run_hermes_diagnostics_test(request: HermesDiagnosticsTestRequest | None = None) -> HermesDiagnosticsTestResult:
    """Run a dry-run-only Agent Chat diagnostics pass.

    The helper deliberately forces dry_run=True even if a future caller adds a
    mutable request shape. This makes the upcoming diagnostics endpoint safe to
    expose before real tool execution is enabled.
    """

    test_request = request or HermesDiagnosticsTestRequest()
    status = hermes_diagnostics_status_payload()
    mode_request = ModeChatRequest(
        content=test_request.content,
        session_id=test_request.session_id,
        domain=test_request.domain,
        dry_run=True,
        metadata={**test_request.metadata, "source": "hermes_diagnostics"},
    )
    try:
        response = plan_mode_chat(mode_request)
        return HermesDiagnosticsTestResult(
            ok=response.ok,
            dry_run=True,
            status=status,
            request=asdict(test_request),
            result=asdict(response),
            error=response.error,
        )
    except Exception as exc:
        return HermesDiagnosticsTestResult(
            ok=False,
            dry_run=True,
            status=status,
            request=asdict(test_request),
            error=str(exc),
        )


def hermes_diagnostics_test_payload(request: HermesDiagnosticsTestRequest | None = None) -> dict[str, Any]:
    return asdict(run_hermes_diagnostics_test(request))


def hermes_diagnostics_schema() -> dict[str, Any]:
    """Small route contract for API/web consumers and tests."""

    return {
        "status": {
            "method": "GET",
            "path": "/api/hermes/status",
            "mutates": False,
        },
        "test": {
            "method": "POST",
            "path": "/api/hermes/test",
            "mutates": False,
            "dry_run_only": True,
            "default_request": asdict(HermesDiagnosticsTestRequest()),
        },
        "assistant_request_contract": asdict(
            AssistantRequest(message="house status", session_id="diagnostics", domain="house", dry_run=True)
        ),
    }
