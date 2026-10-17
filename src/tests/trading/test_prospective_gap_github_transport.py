"""Research handoff fetch without the gh CLI, and its owner-only token (MarketPulse S-7)."""
from __future__ import annotations

import base64

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading import prospective_gap_api, prospective_gap_inputs as inputs


def _contents(text: str) -> dict:
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode(), "sha": "abc123"}


def test_without_gh_the_rest_api_is_used_with_the_stored_token(monkeypatch):
    monkeypatch.setattr(inputs.shutil, "which", lambda name: None)
    monkeypatch.setenv("OMNIX_TRADING_PROSPECTIVE_GAP_GITHUB_REPOSITORY", "autonomx/trading-research-data")
    monkeypatch.setattr(inputs, "github_token", lambda: "ghp_readonly")
    seen = {}

    def get(url, *, params, headers, timeout, follow_redirects):
        seen.update(url=url, params=params, headers=headers)
        return httpx.Response(200, json=_contents('{"ok": true}'))

    monkeypatch.setattr(httpx, "get", get)

    content, source = inputs.fetch_from_github("resources/trading/prospective_gap_inbox/2026-10-19.json")

    assert content == '{"ok": true}'
    assert source == "github:autonomx/trading-research-data@main:resources/trading/prospective_gap_inbox/2026-10-19.json:abc123"
    assert seen["url"] == "https://api.github.com/repos/autonomx/trading-research-data/contents/resources/trading/prospective_gap_inbox/2026-10-19.json"
    assert seen["params"] == {"ref": "main"}
    assert seen["headers"]["Authorization"] == "Bearer ghp_readonly"


def test_without_gh_or_token_a_private_repository_reads_as_not_published(monkeypatch):
    monkeypatch.setattr(inputs.shutil, "which", lambda name: None)
    monkeypatch.setattr(inputs, "github_token", lambda: "")
    sent = {}

    def get(url, *, params, headers, timeout, follow_redirects):
        sent.update(headers)
        return httpx.Response(404, json={"message": "Not Found"})

    monkeypatch.setattr(httpx, "get", get)

    assert inputs.fetch_from_github("resources/trading/prospective_gap_state/climatology.json") is None
    assert "Authorization" not in sent


def test_other_http_errors_are_reported(monkeypatch):
    monkeypatch.setattr(inputs.shutil, "which", lambda name: None)
    monkeypatch.setattr(inputs, "github_token", lambda: "bad")
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Response(401, json={}))

    try:
        inputs.fetch_from_github("x.json")
    except RuntimeError as exc:
        assert str(exc) == "prospective_gap_remote_inbox_fetch_failed:http_401"
    else:
        raise AssertionError("expected an error")


def test_gh_stays_first_when_installed(monkeypatch):
    monkeypatch.setattr(inputs.shutil, "which", lambda name: "C:/gh.exe")
    monkeypatch.setattr(inputs, "_fetch_with_gh", lambda gh, repository, ref, path: _contents("from gh"))
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("http used")))

    assert inputs.fetch_from_github("x.json")[0] == "from gh"


def test_the_credential_status_never_returns_the_token_and_needs_trading_control(monkeypatch):
    monkeypatch.setattr(inputs, "github_token", lambda: "ghp_secret_1234")
    monkeypatch.setattr(prospective_gap_api, "trading_provider_credential_sources", lambda provider: {"token": "os_protected_store"})
    app = FastAPI()
    router = prospective_gap_api.create_trading_prospective_gap_router()
    app.include_router(router)

    body = TestClient(app).get("/api/trading/prospective-gap/github-credentials").json()

    assert body["token_configured"] is True and body["token_masked"] == "***1234"
    assert "ghp_secret" not in str(body)
    routes = {route.path + ":" + ",".join(sorted(route.methods)): route for route in router.routes}
    for key in ("/api/trading/prospective-gap/github-credentials:GET", "/api/trading/prospective-gap/github-credentials:PUT"):
        assert routes[key].openapi_extra == {"x-omnix-permission": "trading:control"}
