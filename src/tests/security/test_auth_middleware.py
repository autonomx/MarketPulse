from __future__ import annotations

import re
import secrets

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.security.auth import (
    AGENT_RUNTIME_PATTERNS,
    PUBLIC_PATHS,
    PUBLIC_PREFIXES,
    resolve_auth_settings,
)
from tests.support.auth import FakeAuthenticator

REMOTE = ("192.0.2.10", 50000)
LOOPBACK = ("127.0.0.1", 50000)
PROBE = "/api/__auth_probe__"


@pytest.fixture(scope="module")
def gateway():
    from app.composition.gateway.main import create_gateway_app

    authenticator = FakeAuthenticator()
    app = create_gateway_app(auth_service=authenticator)
    return app, authenticator


def _client(app, *, client=REMOTE, **headers) -> TestClient:
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        client=client,
        headers={"X-Omnix-Client": "test", **headers},
        raise_server_exceptions=False,
    )


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def _public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def test_every_non_public_route_requires_authentication(gateway) -> None:
    app, _ = gateway
    client = _client(app)
    checked = 0
    websockets: list[str] = []
    for context in iter_route_contexts(app.router.routes):
        route = context.original_route
        path = context.path or route.path
        if "WebSocket" in type(route).__name__:
            websockets.append(path)
            continue
        if _public(path):
            continue
        for method in sorted(getattr(route, "methods", None) or {"GET"}):
            if method == "HEAD":
                continue
            response = client.request(method, _concrete(path))
            assert response.status_code == 401, (method, path, response.status_code)
            agent_route = any(re.fullmatch(pattern, _concrete(path)) for pattern in AGENT_RUNTIME_PATTERNS)
            expected = "run_token_required" if agent_route else "authentication_required"
            assert response.json() == {"detail": expected}
            checked += 1
    # The gateway exposes hundreds of routes; a tiny count means enumeration broke.
    assert checked > 300
    assert websockets
    for path in websockets:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(_concrete(path)):
                pass
        assert closed.value.code == 1008, path


def test_public_paths_stay_reachable_without_credentials(gateway) -> None:
    app, _ = gateway
    client = _client(app)
    assert client.get("/health").status_code != 401
    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["authenticated"] is False
    assert session.json()["mode"] == "local"


def test_session_cookie_authenticates_and_unsafe_methods_require_csrf(gateway) -> None:
    app, authenticator = gateway
    token, csrf = authenticator.issue_session()
    client = _client(app)
    client.cookies.set("omnix_session", token)
    assert client.get(PROBE).status_code == 404
    missing = client.post(PROBE)
    assert missing.status_code == 403
    assert missing.json() == {"detail": "csrf_failed"}
    assert client.post(PROBE, headers={"X-Omnix-CSRF": "0" * 64}).status_code == 403
    assert client.post(PROBE, headers={"X-Omnix-CSRF": csrf}).status_code in {404, 405}

    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["user_id"] == "user:local"
    assert session.json()["roles"] == ["owner"]


def test_the_secure_session_cookie_authenticates_and_still_needs_csrf(gateway) -> None:
    """Over HTTPS the session cookie is named ``__Host-omnix_session`` (ASVS 3.4.4)."""
    app, authenticator = gateway
    token, csrf = authenticator.issue_session()
    client = _client(app, Cookie=f"__Host-omnix_session={token}")
    assert client.get(PROBE).status_code == 404
    assert client.post(PROBE).status_code == 403
    assert client.post(PROBE, headers={"X-Omnix-CSRF": csrf}).status_code in {404, 405}


def test_unknown_or_revoked_session_is_rejected(gateway) -> None:
    app, authenticator = gateway
    token, _ = authenticator.issue_session()
    client = _client(app)
    client.cookies.set("omnix_session", token)
    assert client.get(PROBE).status_code == 404
    del authenticator.sessions[token]
    assert client.get(PROBE).status_code == 401


def test_bearer_tokens_skip_csrf_but_must_be_valid(gateway) -> None:
    app, authenticator = gateway
    bearer = authenticator.issue_bearer()
    assert _client(app, Authorization=f"Bearer {bearer}").post(PROBE).status_code in {404, 405}
    assert _client(app, Authorization="Bearer wrong").get(PROBE).status_code == 401
    assert _client(app, Authorization=f"Basic {bearer}").get(PROBE).status_code == 401


def test_tokens_in_query_parameters_are_never_accepted(gateway) -> None:
    app, authenticator = gateway
    token, _ = authenticator.issue_session()
    bearer = authenticator.issue_bearer()
    client = _client(app)
    for name, value in (("omnix_session", token), ("session", token), ("access_token", bearer), ("token", bearer)):
        assert client.get(PROBE, params={name: value}).status_code == 401


def test_ambiguous_duplicate_session_cookies_are_rejected(gateway) -> None:
    app, authenticator = gateway
    token, _ = authenticator.issue_session()
    response = _client(app, Cookie=f"omnix_session={token}; omnix_session={token}").get(PROBE)
    assert response.status_code == 401


def test_service_token_is_accepted_only_on_internal_routes(gateway, monkeypatch) -> None:
    app, _ = gateway
    service_token = secrets.token_urlsafe(32)
    monkeypatch.setenv("OMNIX_SERVICE_TOKEN", service_token)
    client = _client(app, **{"X-Omnix-Service-Token": service_token})
    assert client.post("/internal/jobs/__probe__").status_code in {404, 405}
    assert client.get(PROBE).status_code == 401
    wrong = _client(app, **{"X-Omnix-Service-Token": secrets.token_urlsafe(32)})
    assert wrong.post("/internal/jobs/__probe__").status_code == 401


def test_agent_runtime_routes_require_a_run_token_for_that_run(gateway) -> None:
    from app.security.run_tokens import issue_run_token

    app, _ = gateway
    path = "/api/agent-model/v1/__probe__"
    token = issue_run_token(run_id="run-1", workspace_id="workspace:local", owner="worker", caps_digest="d")
    authorization = {"Authorization": f"OmnixRun {token}"}
    # Loopback alone and a bare run id header no longer open the route.
    assert _client(app, client=LOOPBACK).get(path).status_code == 401
    assert _client(app, client=LOOPBACK, **{"X-Omnix-Agent-Run-Id": "run-1"}).get(path).status_code == 401
    # The token of the named run is accepted from any address (the probe route is absent).
    assert _client(app, **{"X-Omnix-Agent-Run-Id": "run-1"}, **authorization).get(path).status_code == 404
    # Another run's id, in the header or the path, is rejected.
    assert _client(app, **{"X-Omnix-Agent-Run-Id": "run-2"}, **authorization).get(path).status_code == 401
    assert _client(app, **authorization).post("/api/agent-runs/run-2/budget/tool", json={}).status_code == 401
    # A run token opens no other route.
    assert _client(app, **authorization).get(PROBE).status_code == 401


def test_relayed_sandbox_requests_reach_only_agent_runtime_routes() -> None:
    """With sign-in off, a sandboxed agent must not act as the owner (WP-4.7)."""
    from dataclasses import replace

    from app.composition.gateway.main import create_gateway_app
    from app.security.run_tokens import SANDBOX_RELAY_HEADER, issue_run_token
    from tests.support.auth import enforced_local_settings

    unenforced = FakeAuthenticator(replace(enforced_local_settings(), enforced=False))
    app = create_gateway_app(auth_service=unenforced)
    relayed = {SANDBOX_RELAY_HEADER: "1"}
    approve = {"command_type": "approve", "payload": {"approval_id": "a-1"}}
    # Relayed: refused before any handler, whatever the method or route.
    response = _client(app, **relayed).post("/api/agent-runs/run-1/commands", json=approve)
    assert (response.status_code, response.json()["detail"]) == (403, "sandbox_route_refused")
    assert _client(app, **relayed).get("/api/assistant/tools/config").status_code == 403
    # The agent's own routes still work through the relay with its run token.
    token = issue_run_token(run_id="run-1", workspace_id="workspace:local", owner="worker", caps_digest="d")
    agent = {**relayed, "Authorization": f"OmnixRun {token}", "X-Omnix-Agent-Run-Id": "run-1"}
    probe = _client(app, **agent).get("/api/agent-model/v1/__probe__")
    assert probe.status_code == 404


def test_cors_preflight_is_answered_before_authentication(gateway) -> None:
    app, _ = gateway
    origin = "http://127.0.0.1:5173"
    response = _client(app).options(
        PROBE,
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_agent_runtime_route_list_is_pinned() -> None:
    # Routes that accept run tokens instead of sessions (WP-4.6); must not grow silently.
    assert AGENT_RUNTIME_PATTERNS == (
        r"/api/agent-model/v1/.+",
        r"/api/agent-runs/[^/]+/planning/[a-z_-]+",
        r"/api/agent-runs/[^/]+/run-change-set",
        r"/api/agent-runs/[^/]+/capabilities/[^/]+",
        r"/api/agent-runs/[^/]+/command-authorization",
        r"/api/agent-runs/[^/]+/workspace-authorization",
        r"/api/agent-runs/[^/]+/budget/tool",
        r"/api/agent-runs/[^/]+/run-token",
    )


def test_authentication_backend_failure_fails_closed(gateway) -> None:
    app, authenticator = gateway
    token, _ = authenticator.issue_session()
    client = _client(app)
    client.cookies.set("omnix_session", token)
    authenticator.fail = True
    try:
        response = client.get(PROBE)
    finally:
        authenticator.fail = False
    assert response.status_code == 503
    assert response.json() == {"detail": "authentication_unavailable"}


def test_unenforced_mode_passes_requests_through() -> None:
    from app.composition.gateway.main import create_gateway_app

    app = create_gateway_app(auth_service=FakeAuthenticator(resolve_auth_settings({"OMNIX_AUTH_MODE": "disabled"})))
    client = _client(app)
    assert client.get(PROBE).status_code == 404
    assert client.get("/api/auth/session").json()["enforced"] is False
