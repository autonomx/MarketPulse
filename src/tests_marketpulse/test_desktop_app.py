"""The desktop gateway application: the gateway plus the built web app (roadmap MP-3.2)."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from marketpulse.desktop_app import CONTENT_SECURITY_POLICY, DesktopApplication


class FakeGateway:
    def __init__(self) -> None:
        self.lifespan_events: list[str] = []
        self.paths: list[str] = []

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                self.lifespan_events.append(message["type"])
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        self.paths.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"gateway": true}'})


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (tmp_path / "assets" / "app-1a2b.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    (tmp_path.parent / "secret.txt").write_text("outside", encoding="utf-8")
    return tmp_path


def test_lifespan_reaches_the_gateway(dist):
    gateway = FakeGateway()
    with TestClient(DesktopApplication(gateway, dist)):
        pass

    assert gateway.lifespan_events == ["lifespan.startup", "lifespan.shutdown"]


@pytest.mark.parametrize("path", ["/api/auth/session", "/events", "/health", "/ready", "/api"])
def test_gateway_paths_pass_through_with_security_headers(dist, path):
    gateway = FakeGateway()
    response = TestClient(DesktopApplication(gateway, dist)).get(path)

    assert response.json() == {"gateway": True} and gateway.paths == [path]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_app_routes_get_index_html_with_the_csp(dist):
    client = TestClient(DesktopApplication(FakeGateway(), dist))

    for path in ("/", "/trading", "/trading/chart?symbol=AAPL", "/login"):
        response = client.get(path)
        assert response.status_code == 200 and "id=root" in response.text, path
        assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert response.headers["cache-control"] == "no-cache"


def test_assets_are_served_and_cached(dist):
    client = TestClient(DesktopApplication(FakeGateway(), dist))

    response = client.get("/assets/app-1a2b.js")
    assert response.text == "console.log(1)"
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")


def test_missing_files_traversal_and_writes_are_refused(dist):
    client = TestClient(DesktopApplication(FakeGateway(), dist))

    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/..%2Fsecret.txt").status_code == 404
    assert "outside" not in client.get("/../secret.txt").text
    assert client.post("/trading").status_code == 405


def test_without_a_build_app_routes_are_404(tmp_path):
    response = TestClient(DesktopApplication(FakeGateway(), None)).get("/trading")

    assert response.status_code == 404


def test_the_module_app_wraps_the_production_application(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("MARKETPULSE_WEB_DIST", str(tmp_path))
    module = importlib.reload(importlib.import_module("marketpulse.desktop_app"))
    from app.composition.production import ProductionApplication

    assert isinstance(module.app.gateway, ProductionApplication)
    assert module.app.web_dist == tmp_path.resolve()
