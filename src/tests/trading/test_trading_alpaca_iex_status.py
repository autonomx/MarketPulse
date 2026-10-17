from __future__ import annotations

import ssl

from app.apps.trading.providers.alpaca_iex_status import (
    _status_stream_connect_kwargs,
    _status_stream_ssl_context,
)


def test_alpaca_status_stream_bypasses_automatic_proxy_by_default(monkeypatch) -> None:
    monkeypatch.delenv("OMNIX_ALPACA_WS_PROXY", raising=False)

    def connect(uri, *, proxy=True, ping_interval=20, close_timeout=5):
        return uri, proxy, ping_interval, close_timeout

    assert _status_stream_connect_kwargs(connect) == {
        "ping_interval": 20,
        "close_timeout": 5,
        "proxy": None,
    }


def test_alpaca_status_stream_accepts_explicit_proxy(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_ALPACA_WS_PROXY", "http://127.0.0.1:7890")

    def connect(uri, *, proxy=True, ping_interval=20, close_timeout=5):
        return uri, proxy, ping_interval, close_timeout

    assert _status_stream_connect_kwargs(connect)["proxy"] == "http://127.0.0.1:7890"


def test_alpaca_status_stream_supports_older_websockets_signature(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_ALPACA_WS_PROXY", "http://127.0.0.1:7890")

    def connect(uri, *, ping_interval=20, close_timeout=5):
        return uri, ping_interval, close_timeout

    assert _status_stream_connect_kwargs(connect) == {
        "ping_interval": 20,
        "close_timeout": 5,
    }


def test_alpaca_status_stream_uses_certifi_tls_context() -> None:
    context = _status_stream_ssl_context()

    assert isinstance(context, ssl.SSLContext)
    assert context.get_ca_certs()
