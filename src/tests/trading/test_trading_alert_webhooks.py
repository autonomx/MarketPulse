"""Webhook delivery rules (TVP-0.5a): URL policy, pinning, signing, retries."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import socket
import threading
import time
from datetime import datetime, timezone

import httpx
import pytest

from app.apps.trading.alerts_delivery import (
    ClaimedDelivery,
    DeliveryResult,
    NotificationDeliveryMonitor,
    WebhookSender,
    post_within,
    retry_delay_seconds,
)
from app.security.url_policy import UrlPolicyError, check_outbound_url, outbound_addresses

HOOK = "https://hooks.example.com/services/T0/B0/token"
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


class Store:
    def __init__(self, entries: dict[str, dict[str, str]] | None = None, *, broken: bool = False) -> None:
        self.entries = entries or {}
        self.broken = broken

    def available(self) -> bool:
        return True

    def load(self, ref: str) -> dict[str, str] | None:
        if self.broken:
            raise OSError("store unreadable")
        return self.entries.get(ref)


def public(hostname: str, port: int) -> list[str]:
    return ["93.184.216.34"]


def delivery(message: str = '{"text": "BTC up"}', ref: str | None = "ref") -> ClaimedDelivery:
    return ClaimedDelivery("d1", "t1", "a1", "webhook", message, attempt=1, max_attempts=8, webhook_ref=ref)


def sender(handler, *, entries=None, resolver=public, broken=False) -> tuple[WebhookSender, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    store = Store(entries if entries is not None else {"ref": {"url": HOOK, "secret": "s3cret"}}, broken=broken)
    transport = httpx.MockTransport(record)
    return WebhookSender(store, exchange=lambda request, seconds: transport.handle_request(request), resolver=resolver, clock=lambda: NOW), seen


@pytest.fixture(autouse=True)
def no_private_networks(monkeypatch):
    monkeypatch.delenv("OMNIX_ALLOWED_PRIVATE_NETWORKS", raising=False)


def test_strict_policy_refuses_loopback_and_private_hosts_unless_configured(monkeypatch) -> None:
    for url in ("https://127.0.0.1/x", "https://10.1.2.3/x", "https://[::1]/x", "https://169.254.169.254/x"):
        with pytest.raises(UrlPolicyError):
            check_outbound_url(url, strict=True)
    with pytest.raises(UrlPolicyError, match="url_scheme_not_allowed"):
        check_outbound_url("http://hooks.example.com/x", strict=True, https_only=True)
    monkeypatch.setenv("OMNIX_ALLOWED_PRIVATE_NETWORKS", "10.1.0.0/16")
    assert check_outbound_url("https://10.1.2.3/x", strict=True) == "https://10.1.2.3/x"
    with pytest.raises(UrlPolicyError, match="loopback_address_not_allowed"):
        check_outbound_url("https://127.0.0.1/x", strict=True)
    # Shared address space (carrier NAT, Tailscale, cloud metadata at 100.100.100.200) is not global either.
    for url in ("https://100.100.100.200/latest/meta-data/", "https://100.64.0.1/"):
        with pytest.raises(UrlPolicyError, match="non_global_address_not_allowed"):
            check_outbound_url(url, strict=True)
    # Non-strict callers (local model servers) keep loopback.
    assert check_outbound_url("http://127.0.0.1:1234/v1") == "http://127.0.0.1:1234/v1"


def test_a_host_with_any_internal_address_is_refused() -> None:
    assert [str(address) for address in outbound_addresses(HOOK, strict=True, resolver=public)] == ["93.184.216.34"]
    with pytest.raises(UrlPolicyError, match="private_address_not_allowed"):
        outbound_addresses(HOOK, strict=True, resolver=lambda host, port: ["93.184.216.34", "192.168.1.10"])

    def unresolvable(host: str, port: int) -> list[str]:
        raise UrlPolicyError("hostname_resolution_failed")

    with pytest.raises(UrlPolicyError, match="hostname_resolution_failed"):
        outbound_addresses(HOOK, strict=True, resolver=unresolvable)


def test_a_delivery_goes_to_the_checked_address_signed_and_typed() -> None:
    webhook, seen = sender(lambda request: httpx.Response(204))
    assert webhook.send(delivery()) == DeliveryResult("delivered", status_code=204)
    [request] = seen
    # Pinned to the address that was checked; the hostname goes in Host and TLS SNI.
    assert request.url.host == "93.184.216.34"
    assert request.url.path == "/services/T0/B0/token"
    assert request.headers["host"] == "hooks.example.com"
    assert request.extensions["sni_hostname"] == "hooks.example.com"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["x-omnix-delivery"] == "d1"
    timestamp = request.headers["x-omnix-timestamp"]
    assert timestamp == str(int(NOW.timestamp()))
    # The delivery id is signed too, so receivers can trust it for de-duplication.
    expected = hmac.new(b"s3cret", f"{timestamp}.d1.".encode() + request.content, hashlib.sha256).hexdigest()
    assert request.headers["x-omnix-signature"] == f"sha256={expected}"
    assert request.content == b'{"text": "BTC up"}'


def test_plain_messages_are_text_and_unsigned_without_a_secret() -> None:
    webhook, seen = sender(lambda request: httpx.Response(200), entries={"ref": {"url": HOOK, "secret": ""}})
    assert webhook.send(delivery("BTC crossed 100")).outcome == "delivered"
    assert seen[0].headers["content-type"] == "text/plain; charset=utf-8"
    assert "x-omnix-signature" not in seen[0].headers


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(302, headers={"location": "https://127.0.0.1/"}), DeliveryResult("failed", "redirect_refused", 302)),
        (httpx.Response(404), DeliveryResult("failed", "http_404", 404)),
        (httpx.Response(500), DeliveryResult("retry", "http_500", 500)),
        (httpx.Response(429, headers={"retry-after": "120"}), DeliveryResult("retry", "http_429", 429, 120.0)),
    ],
)
def test_responses_map_to_outcomes(response, expected) -> None:
    webhook, seen = sender(lambda request: response)
    assert webhook.send(delivery()) == expected
    assert len(seen) == 1  # never follows a redirect


def test_network_errors_retry() -> None:
    def timeout(request):
        raise httpx.ConnectTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    assert sender(timeout)[0].send(delivery()) == DeliveryResult("retry", "timeout")
    assert sender(refused)[0].send(delivery()) == DeliveryResult("retry", "connection_failed")


def test_destinations_that_cannot_be_used() -> None:
    def unused(request):
        raise AssertionError("must not send")

    assert sender(unused)[0].send(delivery(ref=None)) == DeliveryResult("failed", "webhook_missing")
    assert sender(unused, entries={})[0].send(delivery()) == DeliveryResult("failed", "webhook_missing")
    # An unreadable store is not a removed webhook.
    assert sender(unused, broken=True)[0].send(delivery()) == DeliveryResult("retry", "webhook_store_unavailable")
    plain_http = {"ref": {"url": "http://hooks.example.com/x", "secret": ""}}
    assert sender(unused, entries=plain_http)[0].send(delivery()) == DeliveryResult("failed", "url_policy:url_scheme_not_allowed")
    # DNS now points the saved hostname at a private address (rebinding).
    rebound = sender(unused, resolver=lambda host, port: ["10.0.0.7"])[0]
    assert rebound.send(delivery()) == DeliveryResult("failed", "url_policy:private_address_not_allowed")

    def unresolvable(host: str, port: int) -> list[str]:
        raise UrlPolicyError("hostname_resolution_failed")

    assert sender(unused, resolver=unresolvable)[0].send(delivery()) == DeliveryResult("retry", "dns_failed")


def test_backoff_doubles_up_to_an_hour() -> None:
    assert [retry_delay_seconds(attempt) for attempt in range(1, 9)] == [30, 60, 120, 240, 480, 960, 1920, 3600]


def test_the_monitor_records_every_claimed_delivery() -> None:
    claimed = [delivery(), ClaimedDelivery("d2", "t2", "a2", "email", "hi", 1, 8, None), ClaimedDelivery("d3", "t3", "a3", "webhook", "x", 1, 8, "ref")]
    recorded: list[tuple[str, DeliveryResult]] = []

    class Repository:
        def claim_due(self, now, limit=1):
            # One row per claim, as the monitor asks.
            return [claimed.pop(0)] if claimed else []

        def record(self, item, result, now):
            recorded.append((item.delivery_id, result))
            return "delivered" if result.outcome == "delivered" else "pending"

    class Sender:
        def send(self, item):
            if item.delivery_id == "d3":
                raise RuntimeError("boom")
            return DeliveryResult("delivered", status_code=200)

    monitor = NotificationDeliveryMonitor(repository_factory=Repository, senders={"webhook": Sender()}, clock=lambda: NOW)
    assert asyncio.run(monitor.run_once()) == 1
    assert claimed == []
    assert recorded == [
        ("d1", DeliveryResult("delivered", status_code=200)),
        ("d2", DeliveryResult("failed", "channel_unavailable")),
        ("d3", DeliveryResult("retry", "sender_error:RuntimeError")),
    ]


def test_the_next_checked_address_is_tried_when_one_cannot_connect() -> None:
    # The first address (IPv6, say, on a host without an IPv6 route) cannot connect; the second answers.
    def first_fails(request):
        if request.url.host == "2606:4700::1111":
            raise httpx.ConnectError("no route", request=request)
        return httpx.Response(200)

    webhook, seen = sender(first_fails, resolver=lambda host, port: ["2606:4700::1111", "93.184.216.34"])
    assert webhook.send(delivery()) == DeliveryResult("delivered", status_code=200)
    assert [request.url.host for request in seen] == ["2606:4700::1111", "93.184.216.34"]


def test_nothing_logs_the_webhook_url(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    webhook, _ = sender(lambda request: httpx.Response(200))
    assert webhook.send(delivery()).outcome == "delivered"
    assert "token" not in caplog.text and "/services/" not in caplog.text


def test_internationalised_hostnames_are_sent_in_idna_form() -> None:
    webhook, seen = sender(lambda request: httpx.Response(200), entries={"ref": {"url": "https://bücher.example/hook", "secret": ""}})
    assert webhook.send(delivery()).outcome == "delivered"
    assert seen[0].headers["host"] == "xn--bcher-kva.example"
    assert seen[0].extensions["sni_hostname"] == "xn--bcher-kva.example"


def test_a_slow_lookup_is_retried_later() -> None:
    answered = threading.Event()

    def slow(host: str, port: int) -> list[str]:
        # Answers only after the sender has given up on it.
        answered.wait(5)
        return ["93.184.216.34"]

    webhook, _ = sender(lambda request: httpx.Response(200), resolver=slow)
    webhook.deadline = 0.2
    try:
        assert webhook.send(delivery()) == DeliveryResult("retry", "dns_timeout")
    finally:
        answered.set()


def test_one_deadline_bounds_a_peer_that_drips_its_headers() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    stop = threading.Event()

    def drip() -> None:
        connection, _ = server.accept()
        with connection:
            connection.recv(65536)
            for byte in b"HTTP/1.1 200 OK\r\nX-Slow: " + b"a" * 1000:
                if stop.is_set():
                    return
                try:
                    connection.sendall(bytes([byte]))
                except OSError:
                    return
                # A byte at a time, until the test ends.
                if stop.wait(0.05):
                    return

    threading.Thread(target=drip, daemon=True).start()
    request = httpx.Request("POST", f"http://127.0.0.1:{port}/", content=b"x", extensions={"timeout": httpx.Timeout(0.2).as_dict()})
    started = time.monotonic()
    try:
        with pytest.raises(httpx.TimeoutException):
            post_within(request, 1.0)
    finally:
        stop.set()
        server.close()
    # Each byte arrives inside the per-read timeout; only the overall deadline ends it.
    assert time.monotonic() - started < 3.0


def test_a_pass_sends_a_bounded_number_of_deliveries() -> None:
    claims = []

    class Repository:
        def claim_due(self, now, limit=1):
            claims.append(limit)
            return [delivery()]

        def record(self, item, result, now):
            return "delivered"

    class Sender:
        def send(self, item):
            return DeliveryResult("delivered", status_code=200)

    monitor = NotificationDeliveryMonitor(repository_factory=Repository, senders={"webhook": Sender()}, max_sends=3, clock=lambda: NOW)
    assert asyncio.run(monitor.run_once()) == 3
    assert claims == [1, 1, 1]


def test_a_response_is_returned_without_reading_its_body() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def answer() -> None:
        connection, _ = server.accept()
        with connection:
            connection.recv(65536)
            # Headers, then a body far bigger than anything the sender should read.
            connection.sendall(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
            # The connection stays open until the client has its answer.
            answered.wait(5)

    answered = threading.Event()
    threading.Thread(target=answer, daemon=True).start()
    request = httpx.Request("POST", f"http://127.0.0.1:{port}/hook", content=b"{}", extensions={"timeout": httpx.Timeout(2).as_dict()})
    try:
        response = post_within(request, 2.0)
    finally:
        answered.set()
        server.close()
    assert response.status_code == 204


def test_lookups_never_queue_behind_a_stuck_resolver() -> None:
    from app.apps.trading import alerts_delivery

    release = threading.Event()

    def stuck(host: str, port: int) -> list[str]:
        release.wait(5)
        return ["93.184.216.34"]

    resolve = alerts_delivery.bounded(stuck, 0.05)
    try:
        for _ in range(alerts_delivery._RESOLVER_THREADS):
            with pytest.raises(alerts_delivery._Deadline):
                resolve("hooks.example.com", 443)
        started = time.monotonic()
        with pytest.raises(alerts_delivery._Deadline):
            resolve("hooks.example.com", 443)
        # Every lookup thread is busy: gives up at once instead of queueing.
        assert time.monotonic() - started < 0.04
    finally:
        release.set()
