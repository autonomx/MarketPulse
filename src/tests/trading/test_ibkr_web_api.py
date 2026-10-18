"""IBKR Web API connection: OAuth 1.0a handshake, signed requests and IB Gateway-free market data.

The fake IBKR server implements its side of IBKR's documented protocol on its
own terms: it verifies the client's RSA signature, derives the live session
token from its own Diffie-Hellman half with Java-style integer bytes, and
rejects any request whose HMAC-SHA256 signature does not match.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote, quote_plus, unquote_plus

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading import market_data_api
from app.apps.trading.providers import ibkr_runtime as ibkr_runtime_module
from app.apps.trading.providers.ibkr import IbkrEquityProvider
from app.apps.trading.providers.ibkr_runtime import (
    IbkrBarStreamEvent,
    IbkrContractIdentity,
    IbkrRuntime,
    IbkrRuntimeError,
)
from app.apps.trading.providers.ibkr_web_api import (
    RFC3526_GROUP14_PRIME,
    IbkrWebApiCredentials,
    IbkrWebApiTransport,
    generate_oauth_keys,
)

BASE = "https://api.ibkr.com/v1/api"
CONSUMER_KEY = "OMNIXTEST"
ACCESS_TOKEN = "a1b2c3d4e5f6"
INSTRUMENT = "equity:NASDAQ:AAPL"
# Thursday 2026-09-17, 09:30 ET.
OPEN = datetime(2026, 9, 17, 13, 30, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)


def _probably_prime(value: int, rounds: int = 24) -> bool:
    if value % 2 == 0:
        return value == 2
    exponent, shift = value - 1, 0
    while exponent % 2 == 0:
        exponent //= 2
        shift += 1
    for _ in range(rounds):
        witness = secrets.randbelow(value - 3) + 2
        x = pow(witness, exponent, value)
        if x in (1, value - 1):
            continue
        for _ in range(shift - 1):
            x = pow(x, 2, value)
            if x == value - 1:
                break
        else:
            return False
    return True


def _der_integers(der: bytes) -> list[int]:
    def read(data: bytes, index: int) -> tuple[int, bytes, int]:
        tag, length = data[index], data[index + 1]
        index += 2
        if length & 0x80:
            size = length & 0x7F
            length = int.from_bytes(data[index:index + size], "big")
            index += size
        return tag, data[index:index + length], index + length

    tag, body, end = read(der, 0)
    assert (tag, end) == (0x30, len(der))
    values, index = [], 0
    while index < len(body):
        tag, value, index = read(body, index)
        assert tag == 0x02
        values.append(int.from_bytes(value, "big", signed=True))
    return values


def _oauth_params(header: str) -> dict[str, str]:
    assert header.startswith("OAuth ")
    params = {}
    for part in header[len("OAuth "):].split(", "):
        key, _, value = part.partition("=")
        params[key] = value.strip('"')
    return params


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"" if payload is None else b"{}"

    def json(self) -> Any:
        return self._payload


class FakeIbkr:
    """IBKR's side of the Web API, as its OAuth 1.0a documentation describes it."""

    def __init__(self, credentials: IbkrWebApiCredentials, secret_plaintext: bytes) -> None:
        self.credentials = credentials
        self.secret_plaintext = secret_plaintext
        self.live_session_token: str | None = None
        self.lst_signature_override: str | None = None
        self.init_payload: dict[str, Any] = {"authenticated": True, "competing": False, "connected": True}
        self.snapshot_rows: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, dict[str, Any] | None, Any]] = []
        self.unauthorized = False

    def _public(self, private_pem: str) -> Any:
        return serialization.load_pem_private_key(private_pem.encode(), password=None).public_key()

    def request(self, method, url, *, params=None, json=None, headers=None, timeout=None):
        path = url[len(BASE):]
        self.calls.append((method, path, params, json))
        oauth = _oauth_params(headers["Authorization"])
        if path == "/oauth/live_session_token":
            return self._live_session_token(method, url, oauth)
        if self.unauthorized or not self._signed(method, url, oauth):
            return FakeResponse(401, {"error": "invalid signature"})
        return self._route(path, params, json)

    def _live_session_token(self, method, url, oauth):
        signature = base64.b64decode(unquote_plus(oauth.pop("oauth_signature")))
        assert oauth.pop("realm") == "limited_poa"
        assert oauth["oauth_signature_method"] == "RSA-SHA256"
        params = "&".join(f"{key}={value}" for key, value in sorted(oauth.items()))
        base_string = f"{self.secret_plaintext.hex()}{method}&{quote_plus(url)}&{quote_plus(params)}"
        self._public(self.credentials.signature_private_key).verify(
            signature, base_string.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        server_secret = secrets.randbits(256)
        prime = self.credentials.dh_prime
        shared = pow(int(oauth["diffie_hellman_challenge"], 16), server_secret, prime)
        # Java's BigInteger.toByteArray: always room for the sign bit.
        shared_bytes = shared.to_bytes(shared.bit_length() // 8 + 1, "big")
        token = base64.b64encode(hmac.new(shared_bytes, self.secret_plaintext, hashlib.sha1).digest()).decode()
        self.live_session_token = token
        lst_signature = hmac.new(base64.b64decode(token), CONSUMER_KEY.encode(), hashlib.sha1).hexdigest()
        return FakeResponse(
            200,
            {
                "diffie_hellman_response": format(pow(2, server_secret, prime), "x"),
                "live_session_token_signature": self.lst_signature_override or lst_signature,
                "live_session_token_expiration": int((OPEN + timedelta(hours=24)).timestamp() * 1000),
            },
        )

    def _signed(self, method, url, oauth) -> bool:
        if self.live_session_token is None:
            return False
        signature = unquote_plus(oauth.pop("oauth_signature"))
        assert oauth.pop("realm") == "limited_poa"
        assert oauth["oauth_signature_method"] == "HMAC-SHA256"
        assert oauth["oauth_token"] == ACCESS_TOKEN
        params = "&".join(f"{key}={value}" for key, value in sorted(oauth.items()))
        base_string = f"{method}&{quote_plus(url)}&{quote(params)}"
        expected = hmac.new(base64.b64decode(self.live_session_token), base_string.encode(), hashlib.sha256).digest()
        return hmac.compare_digest(base64.b64encode(expected).decode(), signature)

    def _route(self, path, params, json):
        if path in ("/iserver/auth/ssodh/init", "/iserver/auth/status"):
            return FakeResponse(200, self.init_payload)
        if path == "/iserver/accounts":
            return FakeResponse(200, {"accounts": ["U1234567"]})
        if path == "/tickle":
            return FakeResponse(200, {"session": "s", "iserver": {"authStatus": {"authenticated": True}}})
        if path == "/trsrv/stocks":
            return FakeResponse(
                200,
                {
                    "AAPL": [
                        {
                            "name": "APPLE INC",
                            "assetClass": "STK",
                            "contracts": [
                                {"conid": 265598, "exchange": "NASDAQ", "isUS": True},
                                {"conid": 38708077, "exchange": "MEXI", "isUS": False},
                            ],
                        }
                    ]
                },
            )
        if path == "/iserver/marketdata/snapshot":
            return FakeResponse(200, list(self.snapshot_rows))
        if path == "/iserver/marketdata/unsubscribe":
            return FakeResponse(200, {"success": True})
        if path == "/iserver/marketdata/history":
            end = datetime.strptime(params["startTime"], "%Y%m%d-%H:%M:%S").replace(tzinfo=timezone.utc)
            span = {"1h": timedelta(hours=1), "1d": timedelta(days=1)}[params["period"]]
            # Bars run back from startTime, at most 1000 of them.
            rows = [row for row in self.history if end - span <= row["start"] < end][-1000:]
            return FakeResponse(
                200,
                {
                    "data": [
                        {
                            "o": row["o"],
                            "h": row["h"],
                            "l": row["l"],
                            "c": row["c"],
                            # Reported volume is actual volume / 100.
                            "v": row["shares"] / 100,
                            "t": int(row["start"].timestamp() * 1000),
                        }
                        for row in rows
                    ]
                },
            )
        return FakeResponse(404, {"error": path})


def _setup(clock: list[datetime] | None = None):
    keys = generate_oauth_keys()
    secret_plaintext = os.urandom(32)
    public_encryption = serialization.load_pem_private_key(keys.encryption_private_key.encode(), password=None).public_key()
    credentials = IbkrWebApiCredentials(
        consumer_key=CONSUMER_KEY,
        access_token=ACCESS_TOKEN,
        access_token_secret=base64.b64encode(public_encryption.encrypt(secret_plaintext, padding.PKCS1v15())).decode(),
        signature_private_key=keys.signature_private_key,
        encryption_private_key=keys.encryption_private_key,
        dh_prime=keys.dh_prime,
    )
    server = FakeIbkr(credentials, secret_plaintext)
    now = clock or [OPEN]
    transport = IbkrWebApiTransport(
        credentials_loader=lambda: credentials,
        client=server,
        base_url=BASE,
        clock=lambda: now[0],
        sleep=lambda _seconds: None,
        start_worker=False,
    )
    return transport, server, now


def _contract() -> IbkrContractIdentity:
    return IbkrContractIdentity(
        con_id=265598, symbol="AAPL", local_symbol="AAPL", sec_type="STK", currency="USD",
        exchange="SMART", primary_exchange="NASDAQ",
    )


def test_omnix_registers_the_rfc3526_2048_bit_safe_prime_group() -> None:
    prime = RFC3526_GROUP14_PRIME
    assert prime.bit_length() == 2048
    assert _probably_prime(prime)
    assert _probably_prime((prime - 1) // 2)


def test_generated_keys_give_the_three_files_ibkr_asks_for() -> None:
    keys = generate_oauth_keys()

    for public in (keys.signature_public_key, keys.encryption_public_key):
        assert public.startswith("-----BEGIN PUBLIC KEY-----")
        serialization.load_pem_public_key(public.encode())
    assert keys.signature_public_key != keys.encryption_public_key
    # PKCS#3, as `openssl dhparam` writes it: SEQUENCE { INTEGER prime, INTEGER generator }.
    lines = keys.dh_parameters.strip().split("\n")
    assert (lines[0], lines[-1]) == ("-----BEGIN DH PARAMETERS-----", "-----END DH PARAMETERS-----")
    assert all(len(line) <= 64 for line in lines[1:-1])
    assert _der_integers(base64.b64decode("".join(lines[1:-1]))) == [RFC3526_GROUP14_PRIME, 2]
    # IBKR's upload has rejected CRLF files.
    assert "\r" not in keys.dh_parameters + keys.signature_public_key + keys.encryption_public_key
    assert "PRIVATE" not in keys.dh_parameters + keys.signature_public_key + keys.encryption_public_key


def test_oauth_handshake_opens_a_brokerage_session_without_competing() -> None:
    transport, server, _ = _setup()

    transport.connect("ignored", 0, 0)

    assert transport.is_connected()
    # Every request after the token was signed with it; the server rejects anything else.
    assert [(method, path) for method, path, *_ in server.calls] == [
        ("POST", "/oauth/live_session_token"),
        ("POST", "/iserver/auth/ssodh/init"),
        ("GET", "/iserver/accounts"),
    ]
    assert server.calls[1][3] == {"publish": True, "compete": False}
    assert transport.diagnostics()["live_session_token_expires_at"] == (OPEN + timedelta(hours=24)).isoformat()


def test_a_live_session_token_ibkr_did_not_derive_is_never_used() -> None:
    transport, server, _ = _setup()
    server.lst_signature_override = "00" * 20

    with pytest.raises(IbkrRuntimeError, match="ibkr_web_api_live_session_token_mismatch"):
        transport.connect("ignored", 0, 0)
    assert not transport.is_connected()
    assert len(server.calls) == 1


def test_a_competing_session_is_reported_instead_of_logging_the_user_out() -> None:
    transport, server, _ = _setup()
    server.init_payload = {"authenticated": False, "competing": True, "connected": True}

    with pytest.raises(IbkrRuntimeError, match="ibkr_web_api_competing_session"):
        transport.connect("ignored", 0, 0)
    assert transport.diagnostics()["competing_session"] is True


def test_missing_credentials_fail_before_any_request() -> None:
    transport = IbkrWebApiTransport(credentials_loader=lambda: None, client=object(), start_worker=False)

    with pytest.raises(IbkrRuntimeError, match="ibkr_web_api_credentials_missing"):
        transport.connect("ignored", 0, 0)


def test_contracts_qualify_to_the_us_listing_through_the_runtime() -> None:
    transport, _, _ = _setup()
    runtime = IbkrRuntime(transport=transport, enabled=True)

    contract = runtime.qualify_contract(INSTRUMENT, symbol="AAPL", venue="NASDAQ")

    assert (contract.con_id, contract.currency, contract.primary_exchange) == (265598, "USD", "NASDAQ")


def test_history_is_scaled_to_shares_and_read_back_in_windows_under_ibkrs_bar_cap() -> None:
    transport, server, _ = _setup()
    transport.connect("ignored", 0, 0)
    start = OPEN - timedelta(hours=20)
    server.history = [
        {"start": start + offset * MINUTE, "o": 10, "h": 11, "l": 9, "c": 10.5, "shares": 12_300}
        for offset in range(20 * 60)
    ]

    rows = transport.historical_bars(
        _contract(), end=OPEN, duration_seconds=20 * 3600, bar_size="1 min", what_to_show="TRADES", use_rth=False
    )

    assert len(rows) == 20 * 60
    assert [row.start_time for row in rows] == [start + offset * MINUTE for offset in range(20 * 60)]
    assert rows[0].volume == Decimal("12300")
    assert rows[0].close == Decimal("10.5")
    history_calls = [params for _, path, params, _ in server.calls if path == "/iserver/marketdata/history"]
    assert [call["period"] for call in history_calls] == ["1d", "1d"]
    assert history_calls[0]["startTime"] == "20260917-13:30:00"
    assert history_calls[0]["outsideRth"] == "true"
    assert history_calls[0]["bar"] == "1min"

    short = transport.historical_bars(
        _contract(), end=OPEN, duration_seconds=600, bar_size="1 min", what_to_show="TRADES", use_rth=True
    )
    assert len(short) == 10
    last_call = [params for _, path, params, _ in server.calls if path == "/iserver/marketdata/history"][-1]
    assert (last_call["period"], last_call["outsideRth"]) == ("1h", "false")


def _quote_row(**fields: Any) -> dict[str, Any]:
    return {"conid": 265598, "_updated": int(fields.pop("at").timestamp() * 1000), **fields}


def test_snapshot_quotes_carry_ibkr_availability_and_skip_the_prior_close() -> None:
    transport, server, now = _setup()
    transport.connect("ignored", 0, 0)
    quotes = []
    token = transport.subscribe_quote(_contract(), quotes.append)

    # IBKR's first answer for a new conid is an empty pre-flight row.
    server.snapshot_rows = [{"conid": 265598}]
    transport.poll_once()
    assert quotes == []

    now[0] = OPEN + timedelta(seconds=3)
    server.snapshot_rows = [
        _quote_row(at=OPEN + timedelta(seconds=2), **{"31": "193.18", "84": "193.17", "86": "193.19", "88": "1,200", "85": "3.4K", "7762": "1500000", "6509": "RpB"})
    ]
    transport.poll_once()
    quote = quotes[-1]
    assert (quote.bid, quote.ask, quote.last) == (Decimal("193.17"), Decimal("193.19"), Decimal("193.18"))
    assert (quote.bid_size, quote.ask_size, quote.cumulative_volume) == (Decimal("1200"), Decimal("3400"), Decimal("1500000"))
    assert quote.market_data_type == "LIVE"
    assert quote.source_time == OPEN + timedelta(seconds=2)
    snapshot_calls = [params for _, path, params, _ in server.calls if path == "/iserver/marketdata/snapshot"]
    assert snapshot_calls[-1]["conids"] == "265598"

    # "C" marks yesterday's close: not a trade, so no last price.
    server.snapshot_rows = [_quote_row(at=OPEN + timedelta(seconds=4), **{"31": "C192.50", "6509": "DpB"})]
    transport.poll_once()
    assert quotes[-1].last is None
    assert quotes[-1].market_data_type == "DELAYED"

    server.snapshot_rows = [_quote_row(at=OPEN + timedelta(seconds=5), **{"84": "1", "6509": "N"})]
    transport.poll_once()
    assert transport.request_health(token)["entitlement_denied"] is True

    transport.unsubscribe_quote(token)
    assert ("POST", "/iserver/marketdata/unsubscribe", None, {"conid": 265598}) in server.calls


def test_live_bars_follow_trades_and_take_ibkrs_bar_when_the_minute_closes() -> None:
    transport, server, now = _setup([OPEN + timedelta(seconds=20)])
    transport.connect("ignored", 0, 0)
    server.history = [
        {"start": OPEN - MINUTE, "o": 99, "h": 100, "l": 98, "c": 99.5, "shares": 1_000},
        {"start": OPEN, "o": 100, "h": 101, "l": 99.8, "c": 100.4, "shares": 500},
    ]
    events: list[IbkrBarStreamEvent] = []
    transport.subscribe_bars(_contract(), events.append, duration_seconds=3_600, use_rth=False)

    transport.poll_once()
    assert events[0].kind == "history"
    assert [row.start_time for row in events[0].bars] == [OPEN - MINUTE, OPEN]

    def tick(seconds: float, price: str, day_volume: int) -> None:
        now[0] = OPEN + timedelta(seconds=seconds)
        server.snapshot_rows = [_quote_row(at=now[0], **{"31": price, "7762": str(day_volume), "6509": "RpB"})]
        transport.poll_once()

    # The first observation continues the minute the history already holds.
    tick(25, "100.6", 10_000)
    assert [(row.start_time, row.close, row.high, row.volume) for row in events[-1].bars] == [
        (OPEN, Decimal("100.6"), Decimal("101"), Decimal("500"))
    ]
    tick(30, "101.5", 10_300)
    assert [(row.close, row.high, row.volume) for row in events[-1].bars] == [(Decimal("101.5"), Decimal("101.5"), Decimal("800"))]

    # A repeated snapshot without new volume is not a trade.
    count = len(events)
    tick(35, "101.5", 10_300)
    assert len(events) == count

    # A trade in the next minute waits until IBKR has published the closed one.
    tick(61, "102", 10_400)
    assert len(events) == count
    server.history.append({"start": OPEN, "o": 100, "h": 101.6, "l": 99.8, "c": 101.5, "shares": 900})
    server.history = [row for row in server.history if not (row["start"] == OPEN and row["shares"] == 500)]
    now[0] = OPEN + MINUTE + timedelta(seconds=3)
    server.snapshot_rows = []
    transport.poll_once()
    closed, forming = events[-1].bars
    # IBKR's own bar replaces the tick-built one, then the new minute follows.
    assert (closed.start_time, closed.high, closed.volume) == (OPEN, Decimal("101.6"), Decimal("900"))
    assert (forming.start_time, forming.open, forming.volume) == (OPEN + MINUTE, Decimal("102"), Decimal("100"))


def test_an_unauthorized_answer_ends_the_session_and_its_streams() -> None:
    transport, server, _ = _setup()
    transport.connect("ignored", 0, 0)
    events: list[IbkrBarStreamEvent] = []
    transport.subscribe_bars(_contract(), events.append, duration_seconds=3_600, use_rth=False)
    server.unauthorized = True

    transport.poll_once()

    assert not transport.is_connected()
    assert [(event.kind, event.reason) for event in events] == [("closed", "ibkr_web_api_unauthorized")]


def test_the_ibkr_chart_feed_reads_web_api_history_like_gateway_history(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    transport, server, now = _setup([OPEN + timedelta(minutes=10, seconds=5)])
    server.history = [
        {"start": OPEN + offset * MINUTE, "o": 10, "h": 11, "l": 9, "c": 10, "shares": 100} for offset in range(11)
    ]
    provider = IbkrEquityProvider(runtime=IbkrRuntime(transport=transport, enabled=True), clock=lambda: now[0])

    response = provider.get_bars(INSTRUMENT, "1m", 5)

    # The forming minute is left out, exactly as with IB Gateway.
    assert [bar.start_time for bar in response.bars] == [OPEN + offset * MINUTE for offset in range(5, 10)]
    assert response.bars[-1].provider_event_id == f"ibkr:265598:{(OPEN + 9 * MINUTE).isoformat()}"


def _install_settings_double(monkeypatch) -> dict:
    from app.settings import access

    state: dict = {}
    monkeypatch.setattr(access, "load_settings", lambda *, allow_defaults_without_service=False: deepcopy(state))
    monkeypatch.setattr(access, "save_settings", lambda payload: (state.clear(), state.update(deepcopy(payload))))
    for name in ("OMNIX_IBKR_ENABLED", "OMNIX_IBKR_CONNECTION", "OMNIX_IBKR_HOST"):
        monkeypatch.delenv(name, raising=False)
    return state


def test_the_runtime_uses_the_connection_saved_in_settings(monkeypatch) -> None:
    from app.apps.trading.ibkr_settings import save_ibkr_settings

    _install_settings_double(monkeypatch)
    save_ibkr_settings({"enabled": True, "connection": "web_api"})
    runtime = IbkrRuntime()

    assert isinstance(runtime._ensure_transport(), IbkrWebApiTransport)
    assert runtime.diagnostics()["connection"] == "web_api"

    save_ibkr_settings({"connection": "gateway"})
    runtime.refresh_settings()
    assert runtime.transport is None
    assert runtime.connection == "gateway"


def _install_secret_store(monkeypatch) -> dict[str, dict[str, str]]:
    from app.security import provider_secret_store

    store: dict[str, dict[str, str]] = {}

    def load():
        return deepcopy(store)

    def save(provider, updates):
        current = store.setdefault(provider, {})
        for field, value in updates.items():
            if value:
                current[field] = value
            else:
                current.pop(field, None)

    def sources(provider):
        return {
            field: "os_protected_store" if store.get(provider, {}).get(field) else "missing"
            for field in ("consumer_key", "access_token", "access_token_secret", "signature_private_key", "encryption_private_key", "dh_prime")
        }

    monkeypatch.setattr(provider_secret_store, "load_trading_provider_secrets", load)
    monkeypatch.setattr(market_data_api, "load_trading_provider_secrets", load)
    monkeypatch.setattr(market_data_api, "save_trading_provider_secrets", save)
    monkeypatch.setattr(market_data_api, "trading_provider_credential_sources", sources)
    monkeypatch.setattr(market_data_api, "os_credential_store_available", lambda: True)
    monkeypatch.setattr(market_data_api, "credential_store_label", lambda: "test store")
    monkeypatch.setattr(ibkr_runtime_module, "_DEFAULT_RUNTIME", None)
    return store


def test_settings_walkthrough_generates_keys_and_saves_portal_tokens_without_exposing_secrets(monkeypatch) -> None:
    _install_settings_double(monkeypatch)
    store = _install_secret_store(monkeypatch)
    app = FastAPI()
    app.include_router(create_router := market_data_api.create_trading_market_data_router())
    del create_router
    client = TestClient(app)
    root = "/api/trading/market-data/providers/ibkr"

    assert client.get(f"{root}/web-api/keys").status_code == 404
    generated = client.post(f"{root}/web-api/keys", json={})
    assert generated.status_code == 200
    files = generated.json()
    assert set(files) == {"signature_public_key", "encryption_public_key", "dh_parameters"}
    assert "PRIVATE" not in "".join(files.values())
    assert "PRIVATE KEY" in store["ibkr_web_api"]["signature_private_key"]
    assert client.get(f"{root}/web-api/keys").json() == files
    # Keys are not replaced by accident: IBKR holds the registered ones.
    assert client.post(f"{root}/web-api/keys", json={}).status_code == 409

    assert client.put(f"{root}/settings", json={"enabled": True, "connection": "web_api"}).status_code == 200
    missing = client.get(f"{root}/settings").json()
    assert missing["connection_status"] == "credentials_missing"
    assert missing["web_api"]["keys_generated"] is True
    assert missing["web_api"]["ready"] is False

    bad = client.put(f"{root}/web-api/credentials", json={"consumer_key": "TOO_SHORT"})
    assert bad.status_code == 422
    saved = client.put(
        f"{root}/web-api/credentials",
        json={"consumer_key": "omnixtest", "access_token": ACCESS_TOKEN, "access_token_secret": base64.b64encode(b"x" * 256).decode()},
    )
    assert saved.status_code == 200
    status = saved.json()
    assert status["web_api"]["ready"] is True
    assert status["web_api"]["consumer_key"] == "OMNIXTEST"
    assert status["web_api"]["access_token_masked"].endswith(ACCESS_TOKEN[-4:])
    assert ACCESS_TOKEN not in str(status) and store["ibkr_web_api"]["access_token_secret"] not in str(status)
    assert status["connection_status"] == "disconnected"

    replaced = client.post(f"{root}/web-api/keys", json={"replace": True})
    assert replaced.json() != files
    # The token IBKR issued for the old keys is gone; the consumer key stays.
    assert "access_token" not in store["ibkr_web_api"]
    assert store["ibkr_web_api"]["consumer_key"] == "OMNIXTEST"


def test_failed_web_api_connections_back_off_a_minute_unless_the_user_retries() -> None:
    transport = IbkrWebApiTransport(credentials_loader=lambda: None, client=object(), start_worker=False)
    runtime = IbkrRuntime(transport=transport, enabled=True)
    runtime.connection = "web_api"

    with pytest.raises(IbkrRuntimeError, match="ibkr_web_api_credentials_missing"):
        runtime.connect()
    # The monitor's next pass waits instead of signing another request to IBKR.
    with pytest.raises(IbkrRuntimeError, match="ibkr_reconnect_backoff"):
        runtime.connect()
    # Pressing Connect in Settings tries again at once.
    with pytest.raises(IbkrRuntimeError, match="ibkr_web_api_credentials_missing"):
        runtime.connect(force=True)


def test_rejected_history_ends_that_stream_instead_of_repeating_every_second() -> None:
    transport, server, _ = _setup()
    transport.connect("ignored", 0, 0)
    events: list[IbkrBarStreamEvent] = []
    transport.subscribe_bars(_contract(), events.append, duration_seconds=600, use_rth=False)
    route = server._route

    def reject_history(path, params, json):
        if path == "/iserver/marketdata/history":
            return FakeResponse(500, {"error": "chart data unavailable"})
        return route(path, params, json)

    server._route = reject_history

    for _ in range(5):
        transport.poll_once()

    history_calls = [path for _, path, *_ in server.calls if path == "/iserver/marketdata/history"]
    # One attempt, then the consumer is told, backs off and reopens on its own schedule.
    assert len(history_calls) == 1
    assert [(event.kind, event.reason) for event in events] == [("closed", "ibkr_web_api_http_500:/iserver/marketdata/history")]
    assert transport.diagnostics()["active_bar_streams"] == 0
    assert ("POST", "/iserver/marketdata/unsubscribe", None, {"conid": 265598}) in server.calls


def test_closing_the_last_bar_stream_frees_ibkrs_market_data_line() -> None:
    transport, server, _ = _setup()
    transport.connect("ignored", 0, 0)
    first = transport.subscribe_bars(_contract(), lambda _event: None, duration_seconds=600, use_rth=False)
    second = transport.subscribe_bars(_contract(), lambda _event: None, duration_seconds=600, use_rth=False)
    unsubscribe = ("POST", "/iserver/marketdata/unsubscribe", None, {"conid": 265598})

    transport.unsubscribe_bars(first)
    assert unsubscribe not in server.calls  # the other chart still reads it
    transport.unsubscribe_bars(second)
    assert unsubscribe in server.calls


def test_a_quote_side_ibkr_stopped_sending_keeps_its_old_time() -> None:
    transport, server, now = _setup()
    transport.connect("ignored", 0, 0)
    quotes = []
    transport.subscribe_quote(_contract(), quotes.append)

    server.snapshot_rows = [_quote_row(at=OPEN, **{"84": "100.00", "86": "100.02", "31": "100.01", "6509": "RpB"})]
    transport.poll_once()
    later = OPEN + timedelta(seconds=30)
    now[0] = later
    server.snapshot_rows = [_quote_row(at=later, **{"31": "100.05"})]
    transport.poll_once()

    quote = quotes[-1]
    assert (quote.bid_observed_at, quote.ask_observed_at, quote.last_observed_at) == (OPEN, OPEN, later)
    # The book is as old as its oldest side, so a freshness check sees a 30-second-old quote.
    assert quote.source_time == OPEN


def test_a_failed_token_renewal_waits_a_minute_and_the_session_stays_tickled() -> None:
    clock = [OPEN]
    monotonic = [1_000.0]
    transport, server, now = _setup(clock)
    transport._monotonic = lambda: monotonic[0]
    transport.connect("ignored", 0, 0)
    # Inside the last ten minutes of the token's 24 hours, with IBKR refusing to renew.
    now[0] = OPEN + timedelta(hours=23, minutes=55)
    server._live_session_token = lambda method, url, oauth: FakeResponse(503, {"error": "maintenance"})

    for _ in range(5):
        monotonic[0] += 1
        transport.poll_once()
    renewals = [path for _, path, *_ in server.calls if path == "/oauth/live_session_token"]
    assert len(renewals) == 2  # the first connection, then one renewal attempt
    monotonic[0] += 60
    transport.poll_once()
    renewals = [path for _, path, *_ in server.calls if path == "/oauth/live_session_token"]
    assert len(renewals) == 3
    assert ("POST", "/tickle", None, {}) in server.calls


def test_reconnecting_quickly_gets_a_fresh_worker() -> None:
    transport, server, _ = _setup()
    transport._start_worker = True
    transport.poll_interval_seconds = 3600  # the worker only waits in this test
    transport.connect("ignored", 0, 0)
    first_worker, first_stop = transport._worker, transport._stop

    transport.disconnect()
    transport.connect("ignored", 0, 0)

    assert first_stop.is_set()
    assert transport._worker is not first_worker
    assert transport._worker.is_alive() and not transport._stop.is_set()
    transport.disconnect()


def test_credentials_never_show_their_secrets_in_a_repr() -> None:
    transport, _, _ = _setup()
    credentials = transport._credentials_loader()

    text = repr(credentials) + repr(generate_oauth_keys())

    assert CONSUMER_KEY in text
    for secret in (credentials.access_token, credentials.access_token_secret, "PRIVATE KEY"):
        assert secret not in text


def test_a_failed_web_api_attempt_does_not_make_ibkr_look_set_up(monkeypatch) -> None:
    from app.apps.trading.providers import ibkr_web_api

    monkeypatch.setattr(ibkr_web_api, "load_web_api_credentials", lambda: None)
    runtime = IbkrRuntime(enabled=True, host="127.0.0.1", port=4002, client_id=71)
    runtime.connection = "web_api"
    assert runtime.client_available() is False

    with pytest.raises(IbkrRuntimeError, match="credentials_missing"):
        runtime.connect()

    # The runtime built a transport for the attempt, but there are still no credentials.
    assert runtime.transport is not None
    assert runtime.client_available() is False
