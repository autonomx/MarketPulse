from __future__ import annotations

import pytest
import importlib
import secrets
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.security.request_guard import RequestGuardMiddleware


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("OMNIX_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("OMNIX_ALLOWED_ORIGINS", raising=False)
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["POST"], allow_headers=["X-Omnix-Client"])
    app.add_middleware(RequestGuardMiddleware)

    @app.api_route("/api/action", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
    def action():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.websocket("/ws")
    async def websocket(socket: WebSocket):
        await socket.accept()
        await socket.send_text("ok")
        await socket.close()

    return TestClient(app, base_url="http://127.0.0.1")


@pytest.mark.parametrize("host", ["evil.test", "localhost.evil.test", "127.0.0.1.evil.test", "[::1]evil", "localhost:", "user@localhost", "localhost/bad", "localhost,evil.test", "localhost:99999", "localhost "])
def test_disallowed_host(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 421


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST:5173", "127.0.0.1:8101", "[::1]", "[::1]:8000"])
def test_allowed_host_with_any_port(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 200


def test_duplicate_hosts_rejected(client):
    response = client.get("/health", headers=[("Host", "localhost"), ("Host", "evil.test")])
    assert response.status_code == 421


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutation_requires_client_header(client, method):
    response = client.request(method, "/api/action")
    assert response.status_code == 403
    assert response.json() == {"detail": "missing_client_header"}


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_header_bearing_mutation_allowed(client, method):
    assert client.request(method, "/api/action", headers={"X-Omnix-Client": "gateway"}).status_code == 200


def test_cross_site_origin_rejected_even_with_client_header(client):
    response = client.post("/api/action", headers={"X-Omnix-Client": "web", "Origin": "https://evil.test"})
    assert response.status_code == 403
    assert response.json() == {"detail": "disallowed_origin"}


def test_allowed_browser_origin(client):
    assert client.post("/api/action", headers={"X-Omnix-Client": "web", "Origin": "http://localhost:5173"}).status_code == 200


def test_empty_client_header_rejected(client):
    assert client.post("/api/action", headers={"X-Omnix-Client": " "}).status_code == 403


def test_duplicate_origin_rejected(client):
    response = client.post("/api/action", headers=[("Origin", "http://localhost:5173"), ("Origin", "https://evil.test"), ("X-Omnix-Client", "web")])
    assert response.status_code == 403


def test_safe_reads_need_no_client_header(client):
    assert client.get("/api/action").status_code == 200
    assert client.head("/api/action").status_code == 200


def test_cors_preflight_allows_client_header(client):
    response = client.options("/api/action", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-Omnix-Client"})
    assert response.status_code == 200
    assert "x-omnix-client" in response.headers["access-control-allow-headers"].lower()


def test_websocket_rejects_cross_site_origin(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("ws://127.0.0.1/ws", headers={"Origin": "https://evil.test"}):
            pass
    assert exc.value.code == 1008


@pytest.mark.parametrize("headers", [{}, {"Origin": "http://localhost:5173"}])
def test_websocket_allows_local_and_nonbrowser_clients(client, headers):
    with client.websocket_connect("ws://127.0.0.1/ws", headers=headers) as socket:
        assert socket.receive_text() == "ok"


def test_configuration_can_restrict_a_public_host_port(monkeypatch):
    monkeypatch.setenv("OMNIX_ALLOWED_HOSTS", "omnix.example.com:443")
    app = FastAPI()
    app.add_middleware(RequestGuardMiddleware)

    @app.get("/")
    def index():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/", headers={"Host": "omnix.example.com:443"}).status_code == 200
    assert client.get("/", headers={"Host": "omnix.example.com:80"}).status_code == 421


@pytest.mark.parametrize("value", ["*", "*.example.com", "bad/host", "user@localhost"])
def test_invalid_host_configuration_fails_closed(monkeypatch, value):
    monkeypatch.setenv("OMNIX_ALLOWED_HOSTS", value)
    with pytest.raises(ValueError):
        RequestGuardMiddleware(FastAPI())


@pytest.mark.parametrize("module", ["services.tts.tts_server", "services.stt.nemotron_eou_stt_server", "services.image.image_service_runtime", "app.composition.launcher.control_app"])
def test_service_composition_protects_routes(monkeypatch, module):
    monkeypatch.delenv("OMNIX_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("OMNIX_ALLOWED_ORIGINS", raising=False)
    app = importlib.import_module(module).app
    token = secrets.token_urlsafe(32)
    monkeypatch.setenv("OMNIX_SERVICE_TOKEN", token)
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"X-Omnix-Service-Token": token},
        client=("127.0.0.1", 50000),
    )
    # An unknown route proves the guard runs before routing or model execution.
    assert client.post("/__guard_test__", headers={"Host": "evil.test"}).status_code == 421
    assert client.post("/__guard_test__").status_code == 403
    assert client.post("/__guard_test__", headers={"X-Omnix-Client": "test"}).status_code == 404


def test_launcher_control_app_refuses_non_loopback_peers():
    from app.composition.launcher.control_app import app

    remote = TestClient(app, base_url="http://127.0.0.1", client=("192.0.2.10", 50000))
    assert remote.get("/api/services").status_code == 403
    assert remote.get("/api/services").json() == {"detail": "loopback_only"}


def test_gateway_composition_protects_routes(monkeypatch):
    monkeypatch.delenv("OMNIX_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("OMNIX_ALLOWED_ORIGINS", raising=False)
    from app.composition.gateway.main import create_gateway_app

    client = TestClient(create_gateway_app(), base_url="http://127.0.0.1")
    assert client.post("/__guard_test__", headers={"Host": "evil.test"}).status_code == 421
    assert client.post("/__guard_test__").status_code == 403
    assert client.post("/__guard_test__", headers={"X-Omnix-Client": "test"}).status_code == 404
