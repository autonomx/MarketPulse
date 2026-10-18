"""IBKR Web API market data over first-party OAuth 1.0a: IBKR data without IB Gateway.

The user registers Omnix-generated public keys and a consumer key in IBKR's
self-service OAuth portal, which returns an access token and an encrypted
access token secret. Omnix keeps those and the private keys in the
OS-protected provider secret store; it never handles an IBKR username or
password. From them it derives a Diffie-Hellman live session token (valid
about 24 hours), opens a brokerage session and signs every request with it.

``IbkrWebApiTransport`` implements the same ``IbkrTransport`` contract as the
Gateway's official client, so the runtime, provider, live chart stream and
authority decisions work unchanged on either connection. Like that client it
is market data only: it calls no order endpoint.

Quotes come from one shared snapshot poll per second. Live 1m bars are built
from those ticks and, once a minute closes, replaced with IBKR's own bar for
it, so the chart keeps IBKR's history while it moves between closes.

Protocol: https://www.interactivebrokers.com/docs/web-api/authentication/oauth-1a/
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, quote_plus

from .ibkr_runtime import (
    IbkrBarStreamEvent,
    IbkrContractIdentity,
    IbkrHistoricalBar,
    IbkrQuoteSnapshot,
    IbkrRuntimeError,
)

logger = logging.getLogger(__name__)

IBKR_WEB_API_BASE_URL = "https://api.ibkr.com/v1/api"
# The self-service OAuth page; IBKR advises its US domain for setup.
IBKR_OAUTH_PORTAL_URL = "https://ndcdyn.interactivebrokers.com/sso/Login?action=OAUTH&RL=1&ip2loc=US"
IBKR_OAUTH_REALM = "limited_poa"
IBKR_DH_GENERATOR = 2
# RFC 3526 group 14: the 2048-bit MODP safe prime. Omnix uploads it as the
# Diffie-Hellman parameters instead of generating a group, which takes minutes.
RFC3526_GROUP14_PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF",
    16,
)
SECRET_PROVIDER = "ibkr_web_api"
_SNAPSHOT_FIELDS = "31,84,85,86,88,7059,70,71,7762,6509"
_AVAILABILITY_TYPES = {"R": "LIVE", "D": "DELAYED", "Z": "FROZEN", "Y": "DELAYED_FROZEN"}
_MINUTE = timedelta(minutes=1)
# A closed minute is fetched once IBKR has had a moment to publish it.
_FINALIZE_DELAY = timedelta(seconds=2)
# One 1d history request returns at most 1000 bars, so windows stay well under that.
_HISTORY_WINDOW = timedelta(hours=12)
# History bars report volume in hundreds of shares ("reported volume = actual volume/100").
_HISTORY_VOLUME_FACTOR = Decimal("100")
# The worker never waits long for history pacing: it also polls every quote.
_WORKER_RATE_WAIT_SECONDS = 1.0
# A failed live session token renewal is retried this often, not on every pass.
_RENEWAL_RETRY_SECONDS = 60.0


# --- Credentials and keys ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class IbkrWebApiCredentials:
    consumer_key: str
    # Secrets stay out of repr, so a log line or traceback never carries them.
    access_token: str = field(repr=False)
    access_token_secret: str = field(repr=False)
    signature_private_key: str = field(repr=False)
    encryption_private_key: str = field(repr=False)
    dh_prime: int = field(repr=False)
    realm: str = IBKR_OAUTH_REALM


@dataclass(frozen=True, slots=True)
class IbkrOAuthKeys:
    """Private keys Omnix keeps, and the public files the user uploads to IBKR."""

    signature_private_key: str = field(repr=False)
    encryption_private_key: str = field(repr=False)
    dh_prime: int

    @property
    def signature_public_key(self) -> str:
        return public_key_pem(self.signature_private_key)

    @property
    def encryption_public_key(self) -> str:
        return public_key_pem(self.encryption_private_key)

    @property
    def dh_parameters(self) -> str:
        return dh_parameters_pem(self.dh_prime)


def generate_oauth_keys() -> IbkrOAuthKeys:
    """Two RSA-2048 key pairs (signing, encryption) and the DH group to register with IBKR."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    def private_pem() -> str:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        return key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")

    return IbkrOAuthKeys(
        signature_private_key=private_pem(),
        encryption_private_key=private_pem(),
        dh_prime=RFC3526_GROUP14_PRIME,
    )


def _private_key(pem: str) -> Any:
    from cryptography.hazmat.primitives import serialization

    return serialization.load_pem_private_key(pem.encode("ascii"), password=None)


def public_key_pem(private_pem: str) -> str:
    from cryptography.hazmat.primitives import serialization

    return (
        _private_key(private_pem)
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )


def _der(tag: int, body: bytes) -> bytes:
    length = len(body)
    if length < 0x80:
        return bytes((tag, length)) + body
    size = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes((tag, 0x80 | len(size))) + size + body


def _der_integer(value: int) -> bytes:
    # Room for the sign bit keeps a positive integer positive.
    return _der(0x02, value.to_bytes(value.bit_length() // 8 + 1, "big"))


def dh_parameters_pem(prime: int, generator: int = IBKR_DH_GENERATOR) -> str:
    """The group as ``openssl dhparam`` writes it: PKCS#3 ``DHParameter ::= SEQUENCE { prime, base }``.

    Encoded here because ``cryptography`` writes X9.42 parameters and deprecates finite-field DH.
    Line endings are LF: IBKR's upload has rejected CRLF files.
    """
    body = base64.b64encode(_der(0x30, _der_integer(prime) + _der_integer(generator))).decode("ascii")
    lines = [body[index:index + 64] for index in range(0, len(body), 64)]
    return "-----BEGIN DH PARAMETERS-----\n" + "\n".join(lines) + "\n-----END DH PARAMETERS-----\n"


def load_web_api_credentials() -> IbkrWebApiCredentials | None:
    """The stored OAuth credentials, or None until keys and portal tokens are all saved."""
    from app.security.provider_secret_store import load_trading_provider_secrets

    stored = load_trading_provider_secrets().get(SECRET_PROVIDER) or {}
    values = {name: str(stored.get(name) or "").strip() for name in stored}
    required = (
        "consumer_key",
        "access_token",
        "access_token_secret",
        "signature_private_key",
        "encryption_private_key",
        "dh_prime",
    )
    if not all(values.get(name) for name in required):
        return None
    try:
        prime = int(values["dh_prime"], 16)
    except ValueError:
        return None
    return IbkrWebApiCredentials(
        consumer_key=values["consumer_key"],
        access_token=values["access_token"],
        access_token_secret=values["access_token_secret"],
        signature_private_key=values["signature_private_key"],
        encryption_private_key=values["encryption_private_key"],
        dh_prime=prime,
    )


def web_api_credentials_configured() -> bool:
    try:
        return load_web_api_credentials() is not None
    except Exception:
        logger.debug("IBKR Web API credentials could not be read", exc_info=True)
        return False


# --- OAuth 1.0a signing ------------------------------------------------------


def _nonce() -> str:
    return secrets.token_hex(16)


def _params_string(params: dict[str, str]) -> str:
    return "&".join(f"{key}={value}" for key, value in sorted(params.items()))


def _java_bytes(value: int) -> bytes:
    """Big-endian bytes with Java ``BigInteger.toByteArray`` sign padding, as IBKR computes them."""
    hex_value = format(value, "x")
    if len(hex_value) % 2:
        hex_value = "0" + hex_value
    raw = bytes.fromhex(hex_value)
    if value.bit_length() % 8 == 0:
        raw = b"\x00" + raw
    return raw


def decrypt_access_token_secret(credentials: IbkrWebApiCredentials) -> str:
    """The base-string prepend: the access token secret decrypted with the encryption key, as hex."""
    from cryptography.hazmat.primitives.asymmetric import padding

    try:
        ciphertext = base64.b64decode(credentials.access_token_secret)
        plaintext = _private_key(credentials.encryption_private_key).decrypt(ciphertext, padding.PKCS1v15())
    except Exception as exc:
        raise IbkrRuntimeError("ibkr_web_api_access_token_secret_unreadable") from exc
    return plaintext.hex()


@dataclass(frozen=True, slots=True)
class LiveSessionTokenRequest:
    url: str
    headers: dict[str, str]
    dh_random: int
    prepend: str


def live_session_token_request(
    credentials: IbkrWebApiCredentials,
    *,
    base_url: str = IBKR_WEB_API_BASE_URL,
    timestamp: int | None = None,
    nonce: str | None = None,
    dh_random: int | None = None,
) -> LiveSessionTokenRequest:
    """The RSA-SHA256-signed request whose answer yields the live session token."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    url = f"{base_url}/oauth/live_session_token"
    secret_exponent = dh_random if dh_random is not None else secrets.randbits(256)
    challenge = format(pow(IBKR_DH_GENERATOR, secret_exponent, credentials.dh_prime), "x")
    prepend = decrypt_access_token_secret(credentials)
    params = {
        "diffie_hellman_challenge": challenge,
        "oauth_consumer_key": credentials.consumer_key,
        "oauth_nonce": nonce or _nonce(),
        "oauth_signature_method": "RSA-SHA256",
        "oauth_timestamp": str(timestamp if timestamp is not None else int(time.time())),
        "oauth_token": credentials.access_token,
    }
    # The prepend is concatenated as is: not encoded, no separator.
    base_string = f"{prepend}POST&{quote_plus(url)}&{quote_plus(_params_string(params))}"
    signature = _private_key(credentials.signature_private_key).sign(
        base_string.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256()
    )
    params["oauth_signature"] = quote_plus(base64.b64encode(signature).decode("ascii"))
    header = f'OAuth realm="{credentials.realm}", ' + ", ".join(
        f'{key}="{value}"' for key, value in sorted(params.items())
    )
    return LiveSessionTokenRequest(url=url, headers={"Authorization": header}, dh_random=secret_exponent, prepend=prepend)


def compute_live_session_token(*, dh_response: str, dh_random: int, dh_prime: int, prepend: str) -> str:
    shared_secret = pow(int(dh_response, 16), dh_random, dh_prime)
    digest = hmac.new(_java_bytes(shared_secret), bytes.fromhex(prepend), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def live_session_token_is_valid(live_session_token: str, *, consumer_key: str, signature: str) -> bool:
    expected = hmac.new(base64.b64decode(live_session_token), consumer_key.encode("utf-8"), hashlib.sha1).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


def signed_request_headers(
    method: str,
    url: str,
    *,
    credentials: IbkrWebApiCredentials,
    live_session_token: str,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """HMAC-SHA256 OAuth headers for an API call; ``url`` excludes the query, which IBKR leaves unsigned."""
    params = {
        "oauth_consumer_key": credentials.consumer_key,
        "oauth_nonce": nonce or _nonce(),
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": str(timestamp if timestamp is not None else int(time.time())),
        "oauth_token": credentials.access_token,
    }
    base_string = f"{method.upper()}&{quote_plus(url)}&{quote(_params_string(params))}"
    digest = hmac.new(base64.b64decode(live_session_token), base_string.encode("utf-8"), hashlib.sha256).digest()
    params["oauth_signature"] = quote_plus(base64.b64encode(digest).decode("ascii"))
    params["realm"] = credentials.realm
    return {"Authorization": "OAuth " + ", ".join(f'{key}="{value}"' for key, value in sorted(params.items()))}


# --- Response parsing --------------------------------------------------------


def _number(value: Any) -> Decimal | None:
    """A snapshot number: "193.18", "1,200", "1.2K" or "3.4M"."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    multiplier = Decimal("1")
    if text[-1] in "KkMm":
        multiplier = Decimal("1000") if text[-1] in "Kk" else Decimal("1000000")
        text = text[:-1]
    try:
        number = Decimal(text) * multiplier
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _price(value: Any) -> Decimal | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _last_trade_price(value: Any) -> Decimal | None:
    """Field 31: "C" marks the prior close (no trade yet today), "H" a halted contract's last trade."""
    text = str(value or "").strip()
    if text.startswith("C"):
        return None
    return _price(text[1:] if text.startswith("H") else text)


def _epoch(value: Any) -> datetime | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # IBKR sends epoch milliseconds; tolerate seconds.
    seconds = number / 1000 if number > 100_000_000_000 else number
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _history_rows(payload: Any) -> list[IbkrHistoricalBar]:
    rows: list[IbkrHistoricalBar] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    for item in data if isinstance(data, list) else ():
        if not isinstance(item, dict):
            continue
        start = _epoch(item.get("t"))
        values = [_number(item.get(name)) for name in ("o", "h", "l", "c", "v")]
        if start is None or any(value is None for value in values):
            continue
        open_, high, low, close, volume = values
        rows.append(
            IbkrHistoricalBar(
                start_time=start,
                open=open_,  # type: ignore[arg-type]
                high=high,  # type: ignore[arg-type]
                low=low,  # type: ignore[arg-type]
                close=close,  # type: ignore[arg-type]
                volume=volume * _HISTORY_VOLUME_FACTOR,  # type: ignore[operator]
            )
        )
    return sorted(rows, key=lambda row: row.start_time)


def _minute_floor(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(second=0, microsecond=0)


# --- Transport ---------------------------------------------------------------


class _RateLimiter:
    """IBKR's history pacing: at most 10 requests a second and 50 a minute."""

    def __init__(self, *, clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, timeout_seconds: float) -> None:
        deadline = self._clock() + timeout_seconds
        while True:
            with self._lock:
                now = self._clock()
                while self._sent and now - self._sent[0] >= 60:
                    self._sent.popleft()
                last_second = sum(1 for sent in self._sent if now - sent < 1)
                if len(self._sent) < 50 and last_second < 10:
                    self._sent.append(now)
                    return
                wait = (self._sent[0] + 60 - now) if len(self._sent) >= 50 else 0.1
            if self._clock() + wait > deadline:
                raise IbkrRuntimeError("ibkr_web_api_history_rate_limited")
            self._sleep(wait)


@dataclass(slots=True)
class _QuoteSubscription:
    contract: IbkrContractIdentity
    listener: Callable[[IbkrQuoteSnapshot], None]


@dataclass(slots=True)
class _BarSubscription:
    contract: IbkrContractIdentity
    listener: Callable[[IbkrBarStreamEvent], None]
    duration_seconds: int
    use_rth: bool
    history_sent: bool = False


@dataclass(slots=True)
class _LiveMinute:
    """The minute forming from snapshot ticks for one contract."""

    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")
    # Day volume at this minute's start, so the minute's volume is the difference.
    volume_base: Decimal | None = None
    seeded_volume: Decimal = Decimal("0")

    def row(self) -> IbkrHistoricalBar:
        return IbkrHistoricalBar(
            start_time=self.start, open=self.open, high=self.high, low=self.low, close=self.close, volume=self.volume
        )


@dataclass(slots=True)
class _ContractState:
    values: dict[str, Any] = field(default_factory=dict)
    # When IBKR last sent each field.
    observed: dict[str, datetime] = field(default_factory=dict)
    market_data_type: str = "UNKNOWN"
    availability: str = ""
    minute: _LiveMinute | None = None
    # Closed minutes waiting for IBKR's own bars before the forming one is sent again.
    closing: list[datetime] = field(default_factory=list)
    # The day volume and last price at the latest trade seen, to tell a new trade from a repeat.
    trade_volume: Decimal | None = None
    trade_price: Decimal | None = None


class IbkrWebApiTransport:
    """IBKR market data over the Web API with OAuth 1.0a; see the module doc."""

    def __init__(
        self,
        *,
        credentials_loader: Callable[[], IbkrWebApiCredentials | None] | None = None,
        client: Any | None = None,
        base_url: str = IBKR_WEB_API_BASE_URL,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval_seconds: float = 1.0,
        tickle_interval_seconds: float = 60.0,
        start_worker: bool = True,
    ) -> None:
        # Read the store when connecting, so saved or replaced credentials are always current.
        self._credentials_loader = credentials_loader or (lambda: load_web_api_credentials())
        self._client = client
        self.base_url = base_url.rstrip("/")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._sleep = sleep
        self.poll_interval_seconds = poll_interval_seconds
        self.tickle_interval_seconds = tickle_interval_seconds
        self._start_worker = start_worker
        self._lock = threading.RLock()
        self._history_limiter = _RateLimiter(clock=monotonic, sleep=sleep)
        self._credentials: IbkrWebApiCredentials | None = None
        self._live_session_token: str | None = None
        self._live_session_token_expires_at: datetime | None = None
        self._connected = False
        self._competing = False
        self._last_tickle = 0.0
        self._next_renewal_attempt = 0.0
        self._next_token = 1
        self._quotes: dict[int, _QuoteSubscription] = {}
        self._bars: dict[int, _BarSubscription] = {}
        self._contracts: dict[int, _ContractState] = {}
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self.request_count = 0
        self.error_count = 0
        self.last_error: str | None = None

    # -- HTTP ----------------------------------------------------------------

    def _http(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=15.0, headers={"User-Agent": "Omnix", "Accept": "*/*"})
        return self._client

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout_seconds: float = 15.0,
    ) -> Any:
        with self._lock:
            credentials = self._credentials
            token = self._live_session_token
        if credentials is None or token is None:
            raise IbkrRuntimeError("ibkr_gateway_disconnected")
        url = f"{self.base_url}{path}"
        headers = signed_request_headers(method, url, credentials=credentials, live_session_token=token)
        headers.update({"Accept": "*/*", "Connection": "keep-alive", "User-Agent": "Omnix"})
        self.request_count += 1
        try:
            response = self._http().request(
                method, url, params=params, json=json, headers=headers, timeout=timeout_seconds
            )
        except Exception as exc:
            raise self._failure(f"ibkr_web_api_unreachable:{type(exc).__name__}") from exc
        if response.status_code == 401:
            failure = self._failure("ibkr_web_api_unauthorized")
            self._lose_session("ibkr_web_api_unauthorized")
            raise failure
        if response.status_code == 429:
            raise self._failure("ibkr_web_api_rate_limited")
        if response.status_code >= 400:
            raise self._failure(f"ibkr_web_api_http_{response.status_code}:{path}")
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise self._failure(f"ibkr_web_api_invalid_json:{path}") from exc

    def _failure(self, reason: str) -> IbkrRuntimeError:
        self.error_count += 1
        self.last_error = reason
        return IbkrRuntimeError(reason)

    # -- Session -------------------------------------------------------------

    def _open_live_session(self, credentials: IbkrWebApiCredentials) -> None:
        request = live_session_token_request(credentials, base_url=self.base_url)
        headers = {**request.headers, "Accept": "*/*", "User-Agent": "Omnix"}
        try:
            response = self._http().request("POST", request.url, headers=headers, timeout=15.0)
        except Exception as exc:
            raise self._failure(f"ibkr_web_api_unreachable:{type(exc).__name__}") from exc
        if response.status_code == 401:
            # Most often a consumer key that is not active yet (it activates after IBKR's nightly reset).
            raise self._failure("ibkr_web_api_invalid_consumer_or_token")
        if response.status_code >= 400:
            raise self._failure(f"ibkr_web_api_live_session_token_http_{response.status_code}")
        try:
            payload = response.json()
            dh_response = str(payload["diffie_hellman_response"])
            signature = str(payload["live_session_token_signature"])
        except (ValueError, KeyError, TypeError) as exc:
            raise self._failure("ibkr_web_api_live_session_token_malformed") from exc
        token = compute_live_session_token(
            dh_response=dh_response, dh_random=request.dh_random, dh_prime=credentials.dh_prime, prepend=request.prepend
        )
        if not live_session_token_is_valid(token, consumer_key=credentials.consumer_key, signature=signature):
            raise self._failure("ibkr_web_api_live_session_token_mismatch")
        expires = _epoch(payload.get("live_session_token_expiration")) or self._clock() + timedelta(hours=24)
        with self._lock:
            self._credentials = credentials
            self._live_session_token = token
            self._live_session_token_expires_at = expires

    def _open_brokerage_session(self, timeout_seconds: float) -> None:
        # compete=False: never log the user out of TWS or the mobile app.
        status = self._request("POST", "/iserver/auth/ssodh/init", json={"publish": True, "compete": False})
        deadline = self._monotonic() + timeout_seconds
        while not (isinstance(status, dict) and status.get("authenticated")):
            if isinstance(status, dict) and status.get("competing"):
                with self._lock:
                    self._competing = True
                raise self._failure("ibkr_web_api_competing_session")
            if self._monotonic() >= deadline:
                raise self._failure("ibkr_web_api_brokerage_session_not_authenticated")
            self._sleep(0.5)
            status = self._request("POST", "/iserver/auth/status", json={})
        with self._lock:
            self._competing = bool(status.get("competing"))
        # IBKR requires this before market-data snapshots.
        self._request("GET", "/iserver/accounts")

    def connect(self, host: str, port: int, client_id: int, timeout_seconds: float = 8.0) -> None:
        """Open an OAuth session; host, port and client id belong to the Gateway connection."""
        del host, port, client_id
        if self.is_connected():
            return
        credentials = self._credentials_loader()
        if credentials is None:
            raise self._failure("ibkr_web_api_credentials_missing")
        self._open_live_session(credentials)
        try:
            self._open_brokerage_session(timeout_seconds)
        except IbkrRuntimeError:
            with self._lock:
                self._live_session_token = None
            raise
        with self._lock:
            self._connected = True
            self._last_tickle = self._monotonic()
            self.last_error = None
        if self._start_worker and (self._worker is None or not self._worker.is_alive() or self._stop.is_set()):
            # A worker told to stop may still be finishing a request; it exits on its own
            # flag, and this session gets a fresh worker and flag.
            self._stop = threading.Event()
            self._worker = threading.Thread(
                target=self._run_worker, args=(self._stop,), name="omnix-ibkr-web-api", daemon=True
            )
            self._worker.start()

    def disconnect(self) -> None:
        self._stop.set()
        self._lose_session("ibkr_gateway_disconnected")

    def _lose_session(self, reason: str) -> None:
        """End everything scoped to this session, like a Gateway socket reset."""
        with self._lock:
            self._connected = False
            self._live_session_token = None
            bar_listeners = [item.listener for item in self._bars.values()]
            self._bars.clear()
            self._quotes.clear()
            self._contracts.clear()
        for listener in bar_listeners:
            self._deliver(listener, IbkrBarStreamEvent("closed", reason=reason))

    def is_connected(self) -> bool:
        with self._lock:
            if not self._connected or self._live_session_token_expires_at is None:
                return False
            return self._clock() < self._live_session_token_expires_at

    # -- Contracts and history ----------------------------------------------

    def qualify_stock(self, symbol: str, *, currency: str = "USD") -> list[IbkrContractIdentity]:
        clean = symbol.strip().upper()
        payload = self._request("GET", "/trsrv/stocks", params={"symbols": clean})
        rows: list[IbkrContractIdentity] = []
        entries = payload.get(clean) if isinstance(payload, dict) else None
        for entry in entries if isinstance(entries, list) else ():
            if not isinstance(entry, dict) or str(entry.get("assetClass") or "STK").upper() != "STK":
                continue
            for contract in entry.get("contracts") or ():
                if not isinstance(contract, dict):
                    continue
                # trsrv/stocks names no currency; a US listing trades in USD.
                contract_currency = "USD" if contract.get("isUS") else ""
                try:
                    con_id = int(contract.get("conid") or 0)
                except (TypeError, ValueError):
                    continue
                rows.append(
                    IbkrContractIdentity(
                        con_id=con_id,
                        symbol=clean,
                        local_symbol=clean,
                        sec_type="STK",
                        currency=contract_currency if currency.upper() == "USD" else "",
                        exchange="SMART",
                        primary_exchange=str(contract.get("exchange") or "").upper(),
                    )
                )
        return rows

    def _history(
        self,
        contract: IbkrContractIdentity,
        *,
        end: datetime,
        period: str,
        use_rth: bool,
        timeout_seconds: float,
        rate_wait_seconds: float,
    ) -> list[IbkrHistoricalBar]:
        self._history_limiter.acquire(rate_wait_seconds)
        payload = self._request(
            "GET",
            "/iserver/marketdata/history",
            params={
                "conid": contract.con_id,
                "period": period,
                "bar": "1min",
                # The window runs back from startTime.
                "startTime": end.astimezone(timezone.utc).strftime("%Y%m%d-%H:%M:%S"),
                "outsideRth": "false" if use_rth else "true",
            },
            timeout_seconds=timeout_seconds,
        )
        return _history_rows(payload)

    def historical_bars(
        self,
        contract: IbkrContractIdentity,
        *,
        end: datetime,
        duration_seconds: int,
        bar_size: str,
        what_to_show: str,
        use_rth: bool,
        timeout_seconds: float = 20.0,
        rate_wait_seconds: float | None = None,
    ) -> list[IbkrHistoricalBar]:
        """IBKR's 1m TRADES bars in ``[end - duration, end)``; ``rate_wait_seconds`` caps the wait for pacing."""
        if bar_size != "1 min" or what_to_show != "TRADES":
            raise IbkrRuntimeError(f"ibkr_web_api_unsupported_history:{bar_size}:{what_to_show}")
        if end.tzinfo is None:
            raise ValueError("ibkr historical end must be timezone-aware")
        end_utc = end.astimezone(timezone.utc)
        start = end_utc - timedelta(seconds=max(1, int(duration_seconds)))
        request = {
            "use_rth": use_rth,
            "timeout_seconds": timeout_seconds,
            "rate_wait_seconds": timeout_seconds if rate_wait_seconds is None else rate_wait_seconds,
        }
        if end_utc - start <= timedelta(hours=1):
            rows = self._history(contract, end=end_utc, period="1h", **request)
        else:
            rows = []
            window_end = end_utc
            while window_end > start:
                window = self._history(contract, end=window_end, period="1d", **request)
                rows.extend(row for row in window if window_end - _HISTORY_WINDOW <= row.start_time < window_end)
                window_end -= _HISTORY_WINDOW
        by_start = {row.start_time: row for row in rows if start <= row.start_time < end_utc}
        return [by_start[key] for key in sorted(by_start)]

    # -- Subscriptions -------------------------------------------------------

    def _token(self) -> int:
        with self._lock:
            token = self._next_token
            self._next_token += 1
            return token

    def subscribe_quote(self, contract: IbkrContractIdentity, listener: Callable[[IbkrQuoteSnapshot], None]) -> int:
        if not self.is_connected():
            raise IbkrRuntimeError("ibkr_gateway_disconnected")
        token = self._token()
        with self._lock:
            self._quotes[token] = _QuoteSubscription(contract, listener)
            self._contracts.setdefault(contract.con_id, _ContractState())
        return token

    def unsubscribe_quote(self, token: int) -> None:
        with self._lock:
            subscription = self._quotes.pop(int(token), None)
            if subscription is None:
                return
        self._release_contract(subscription.contract.con_id)

    def _release_contract(self, con_id: int) -> None:
        """Stop polling a contract nothing uses any more, and free IBKR's market-data line for it."""
        with self._lock:
            if self._contract_in_use(con_id) or self._contracts.pop(con_id, None) is None:
                return
        if self.is_connected():
            try:
                self._request("POST", "/iserver/marketdata/unsubscribe", json={"conid": con_id})
            except IbkrRuntimeError:
                logger.debug("IBKR Web API unsubscribe failed for %s", con_id, exc_info=True)

    def subscribe_bars(
        self,
        contract: IbkrContractIdentity,
        listener: Callable[[IbkrBarStreamEvent], None],
        *,
        duration_seconds: int,
        use_rth: bool,
    ) -> int:
        """The Gateway's keepUpToDate stream: history first (sent by the worker), then the forming bar."""
        if not self.is_connected():
            raise IbkrRuntimeError("ibkr_gateway_disconnected")
        token = self._token()
        with self._lock:
            self._bars[token] = _BarSubscription(contract, listener, int(duration_seconds), bool(use_rth))
            self._contracts.setdefault(contract.con_id, _ContractState())
        return token

    def unsubscribe_bars(self, token: int) -> None:
        with self._lock:
            subscription = self._bars.pop(int(token), None)
        if subscription is not None:
            self._release_contract(subscription.contract.con_id)

    def _contract_in_use(self, con_id: int) -> bool:
        return any(item.contract.con_id == con_id for item in (*self._quotes.values(), *self._bars.values()))

    def request_health(self, token: int) -> dict[str, Any]:
        with self._lock:
            subscription = self._quotes.get(int(token))
            state = self._contracts.get(subscription.contract.con_id) if subscription else None
        availability = state.availability if state else ""
        # N: no subscription for this contract; O: the yearly Market Data API acknowledgement is missing.
        denied = availability[:1] in {"N", "O"}
        return {
            "request_id": int(token),
            "market_data_type": state.market_data_type if state else "UNKNOWN",
            "error": {"code": availability[:1], "message": f"market data availability {availability}"} if denied else None,
            "entitlement_denied": denied,
        }

    # -- Worker --------------------------------------------------------------

    def _run_worker(self, stop: threading.Event) -> None:
        while not stop.wait(self.poll_interval_seconds):
            try:
                self.poll_once()
            except Exception:
                logger.debug("IBKR Web API poll failed", exc_info=True)

    def poll_once(self) -> None:
        """One worker pass: keep the session alive, send pending history, poll quotes, close minutes."""
        if not self.is_connected():
            if self._connected:
                self._lose_session("ibkr_web_api_live_session_token_expired")
            return
        # Each step runs on its own, so a failed history request never holds back quotes.
        for step in (self._keep_session_alive, self._send_pending_history, self._poll_snapshot, self._close_due_minutes):
            if not self.is_connected():
                return
            try:
                step()
            except IbkrRuntimeError as exc:
                logger.debug("IBKR Web API %s failed: %s", step.__name__, exc)

    def _keep_session_alive(self) -> None:
        with self._lock:
            credentials = self._credentials
            expires = self._live_session_token_expires_at
        renewal_due = credentials is not None and expires is not None and expires - self._clock() < timedelta(minutes=10)
        if renewal_due and self._monotonic() >= self._next_renewal_attempt:
            # Renew the live session token before it lapses; the brokerage session carries over.
            try:
                self._open_live_session(credentials)  # type: ignore[arg-type]
            except IbkrRuntimeError:
                # Try again in a minute, not on every pass, and keep the session tickled meanwhile.
                self._next_renewal_attempt = self._monotonic() + _RENEWAL_RETRY_SECONDS
        if self._monotonic() - self._last_tickle < self.tickle_interval_seconds:
            return
        self._last_tickle = self._monotonic()
        payload = self._request("POST", "/tickle", json={})
        auth = (payload.get("iserver") or {}).get("authStatus") if isinstance(payload, dict) else None
        if isinstance(auth, dict) and not auth.get("authenticated"):
            try:
                self._open_brokerage_session(timeout_seconds=5.0)
            except IbkrRuntimeError as exc:
                self._lose_session(str(exc))
                raise

    def _send_pending_history(self) -> None:
        with self._lock:
            pending = [(token, item) for token, item in self._bars.items() if not item.history_sent]
        for token, item in pending:
            now = self._clock()
            try:
                rows = self.historical_bars(
                    item.contract,
                    end=now + _MINUTE,  # include the forming minute
                    duration_seconds=item.duration_seconds,
                    bar_size="1 min",
                    what_to_show="TRADES",
                    use_rth=item.use_rth,
                    rate_wait_seconds=_WORKER_RATE_WAIT_SECONDS,
                )
            except IbkrRuntimeError as exc:
                # End this stream, like IBKR rejecting a Gateway keepUpToDate request: its consumer
                # backs off and reopens, instead of this request repeating on every pass.
                with self._lock:
                    if self._bars.get(token) is item:
                        del self._bars[token]
                    else:
                        continue
                self._release_contract(item.contract.con_id)
                self._deliver(item.listener, IbkrBarStreamEvent("closed", reason=str(exc)))
                continue
            with self._lock:
                if self._bars.get(token) is not item:
                    continue
                item.history_sent = True
                state = self._contracts.setdefault(item.contract.con_id, _ContractState())
                last = rows[-1] if rows else None
                if last is not None and state.minute is None and last.start_time == _minute_floor(now):
                    state.minute = _LiveMinute(
                        start=last.start_time,
                        open=last.open,
                        high=last.high,
                        low=last.low,
                        close=last.close,
                        volume=last.volume,
                        seeded_volume=last.volume,
                    )
            self._deliver(item.listener, IbkrBarStreamEvent("history", tuple(rows)))

    def _poll_snapshot(self) -> None:
        with self._lock:
            con_ids = sorted(self._contracts)
        if not con_ids:
            return
        payload = self._request(
            "GET",
            "/iserver/marketdata/snapshot",
            params={"conids": ",".join(str(con_id) for con_id in con_ids), "fields": _SNAPSHOT_FIELDS},
        )
        received = self._clock()
        for row in payload if isinstance(payload, list) else ():
            if isinstance(row, dict):
                self._apply_snapshot(row, received)

    def _apply_snapshot(self, row: dict[str, Any], received: datetime) -> None:
        try:
            con_id = int(row.get("conid") or 0)
        except (TypeError, ValueError):
            return
        source_time = _epoch(row.get("_updated")) or received
        quote_events: list[tuple[Callable[[IbkrQuoteSnapshot], None], IbkrQuoteSnapshot]] = []
        bar_events: list[tuple[Callable[[IbkrBarStreamEvent], None], IbkrBarStreamEvent]] = []
        with self._lock:
            state = self._contracts.get(con_id)
            if state is None:
                return
            for key, value in row.items():
                if key.isdigit():
                    state.values[key] = value
                    # A field keeps the time IBKR last sent it, so a value IBKR stopped
                    # updating never looks fresh.
                    state.observed[key] = source_time
            if "6509" in row:
                state.availability = str(row.get("6509") or "")
                state.market_data_type = _AVAILABILITY_TYPES.get(state.availability[:1], "UNKNOWN")
            values = state.values
            last = _last_trade_price(values.get("31"))
            day_volume = _number(values.get("7762"))
            quote_subscriptions = [item for item in self._quotes.values() if item.contract.con_id == con_id]
            bid, ask = _price(values.get("84")), _price(values.get("86"))
            # The first answer to a new conid is IBKR's empty pre-flight row.
            if quote_subscriptions and (bid is not None or ask is not None or last is not None):
                bid_at = state.observed.get("84") if bid is not None else None
                ask_at = state.observed.get("86") if ask is not None else None
                last_at = state.observed.get("31") if last is not None else None
                # Like the Gateway's quotes: the older side of the book dates the quote.
                quote_time = (
                    min(bid_at, ask_at)
                    if bid_at is not None and ask_at is not None
                    else max((moment for moment in (bid_at, ask_at, last_at) if moment is not None), default=source_time)
                )
                for item in quote_subscriptions:
                    quote_events.append(
                        (
                            item.listener,
                            IbkrQuoteSnapshot(
                                contract=item.contract,
                                bid=bid,
                                ask=ask,
                                bid_size=_number(values.get("88")),
                                ask_size=_number(values.get("85")),
                                last=last,
                                last_size=_number(values.get("7059")),
                                high=_price(values.get("70")),
                                low=_price(values.get("71")),
                                cumulative_volume=day_volume,
                                bid_observed_at=bid_at,
                                ask_observed_at=ask_at,
                                last_observed_at=last_at,
                                source_time=quote_time,
                                received_at=received,
                                market_data_type=state.market_data_type,
                                provider_sequence=self.request_count,
                            ),
                        )
                    )
            if last is not None:
                update = self._fold_trade(state, last, day_volume, source_time)
                if update is not None:
                    for item in self._bars.values():
                        if item.contract.con_id == con_id and item.history_sent:
                            bar_events.append((item.listener, IbkrBarStreamEvent("update", (update,))))
        for quote_listener, snapshot in quote_events:
            self._deliver(quote_listener, snapshot)
        for bar_listener, event in bar_events:
            self._deliver(bar_listener, event)

    @staticmethod
    def _fold_trade(
        state: _ContractState, price: Decimal, day_volume: Decimal | None, moment: datetime
    ) -> IbkrHistoricalBar | None:
        """Fold a new trade into the forming minute; the bar to send now, or None.

        A snapshot repeats the last trade until the next one, so a trade is new
        only when the day volume rises (or, without volume, the price changes).
        The first observation is a baseline, not a trade, unless it continues
        the minute the opening history already holds.
        """
        start = _minute_floor(moment)
        previous_volume, previous_price = state.trade_volume, state.trade_price
        if day_volume is not None and previous_volume is not None and day_volume < previous_volume:
            previous_volume = None  # the day's volume restarted
        state.trade_volume, state.trade_price = day_volume, price
        minute = state.minute
        first_observation = previous_volume is None and previous_price is None
        if first_observation:
            if minute is None or start != minute.start:
                return None
        elif day_volume is not None and previous_volume is not None:
            if day_volume <= previous_volume:
                return None
        elif price == previous_price:
            return None
        if minute is not None and start < minute.start:
            return None
        if minute is None or start > minute.start:
            if minute is not None:
                state.closing.append(minute.start)
            state.minute = _LiveMinute(
                start=start,
                open=price,
                high=price,
                low=price,
                close=price,
                # The base before this trade, so its own size counts toward the minute.
                volume_base=previous_volume if previous_volume is not None else day_volume,
            )
            minute = state.minute
        else:
            minute.high = max(minute.high, price)
            minute.low = min(minute.low, price)
            minute.close = price
        if day_volume is not None:
            if minute.volume_base is None:
                # A minute seeded from history: count on from the volume it already holds.
                minute.volume_base = day_volume - minute.seeded_volume
            minute.volume = max(Decimal("0"), day_volume - minute.volume_base)
        return None if state.closing else minute.row()

    def _close_due_minutes(self) -> None:
        now = self._clock()
        with self._lock:
            due = [
                (con_id, list(state.closing))
                for con_id, state in self._contracts.items()
                if state.closing and now >= state.closing[-1] + _MINUTE + _FINALIZE_DELAY
            ]
        for con_id, closing in due:
            with self._lock:
                listeners = [item for item in self._bars.values() if item.contract.con_id == con_id and item.history_sent]
            official: list[IbkrHistoricalBar] = []
            if listeners:
                try:
                    rows = self.historical_bars(
                        listeners[0].contract,
                        end=closing[-1] + _MINUTE,
                        duration_seconds=int((closing[-1] + _MINUTE - closing[0]).total_seconds()),
                        bar_size="1 min",
                        what_to_show="TRADES",
                        use_rth=listeners[0].use_rth,
                        rate_wait_seconds=_WORKER_RATE_WAIT_SECONDS,
                    )
                    official = [row for row in rows if row.start_time in set(closing)]
                except IbkrRuntimeError:
                    # The tick-built bars stand for those minutes.
                    logger.debug("IBKR Web API could not fetch closed minutes %s", closing, exc_info=True)
            with self._lock:
                state = self._contracts.get(con_id)
                if state is None or state.closing[: len(closing)] != closing:
                    continue
                del state.closing[: len(closing)]
                forming = state.minute.row() if state.minute is not None and not state.closing else None
            # IBKR's bars for the closed minutes, oldest first, then the minute now forming.
            bars = (*official, *((forming,) if forming is not None else ()))
            if bars:
                for item in listeners:
                    self._deliver(item.listener, IbkrBarStreamEvent("update", bars))

    @staticmethod
    def _deliver(listener: Callable[[Any], None], event: Any) -> None:
        try:
            listener(event)
        except Exception:
            # A consumer failure must not stop the worker.
            logger.debug("suppressed error in %s", "IbkrWebApiTransport._deliver", exc_info=True)

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            expires = self._live_session_token_expires_at
            return {
                "transport": "ibkr_web_api",
                "connected": self.is_connected(),
                "competing_session": self._competing,
                "live_session_token_expires_at": expires.isoformat() if expires else None,
                "active_quote_subscriptions": len(self._quotes),
                "active_bar_streams": len(self._bars),
                "request_count": self.request_count,
                "error_count": self.error_count,
                "last_error": self.last_error,
            }


__all__ = [
    "IBKR_OAUTH_PORTAL_URL",
    "IBKR_WEB_API_BASE_URL",
    "RFC3526_GROUP14_PRIME",
    "SECRET_PROVIDER",
    "IbkrOAuthKeys",
    "IbkrWebApiCredentials",
    "IbkrWebApiTransport",
    "compute_live_session_token",
    "dh_parameters_pem",
    "generate_oauth_keys",
    "live_session_token_is_valid",
    "live_session_token_request",
    "load_web_api_credentials",
    "public_key_pem",
    "signed_request_headers",
    "web_api_credentials_configured",
]
