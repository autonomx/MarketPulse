"""Market-data providers share pooled httpx clients, not requests (WP-7.2)."""
from __future__ import annotations

import ast
from pathlib import Path

import httpx

from app.apps.trading.providers.http_runtime import ProviderHttpRuntime

APP = Path(__file__).resolve().parents[2] / "app"


def test_the_default_session_is_a_pooled_client_that_follows_redirects() -> None:
    runtime = ProviderHttpRuntime("fixture", max_concurrency=3)
    try:
        assert isinstance(runtime.session, httpx.Client)
        assert runtime.session.follow_redirects is True
    finally:
        runtime.session.close()


def test_absent_query_values_are_left_out() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    runtime = ProviderHttpRuntime(
        "fixture",
        session=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    response = runtime.get("https://provider.test/bars", params={"symbol": "AAPL", "end": None})

    assert response.json() == {"ok": True}
    assert str(seen[0].url) == "https://provider.test/bars?symbol=AAPL"


def test_a_transport_failure_is_retried_then_reported_unavailable() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("refused", request=request)

    runtime = ProviderHttpRuntime(
        "fixture",
        session=httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=2,
        initial_backoff_seconds=0.0,
    )
    try:
        runtime.get("https://provider.test/quote")
    except Exception as exc:
        assert "transport failure" in str(exc)
    else:
        raise AssertionError("expected ProviderUnavailableError")
    assert len(attempts) == 2


def test_no_application_module_imports_requests() -> None:
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [alias.name for alias in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0
                else []
            )
            if any(name == "requests" or name.startswith("requests.") for name in names):
                offenders.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert offenders == []
