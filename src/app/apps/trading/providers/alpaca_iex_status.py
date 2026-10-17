from __future__ import annotations

from app.config.env import env_str, environment

import asyncio
import inspect
import json
import ssl
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any

import certifi
from app.runtime.features import FeatureContext
from app.apps.trading.monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from app.apps.trading.us_equity_calendar import EASTERN as _ET


ALPACA_IEX_STREAM_URL = "wss://stream.data.alpaca.markets/v2/iex"
_STATE_KEY = "_omnix_alpaca_iex_status_monitor"
_HALT_CODES = {"2", "H", "P"}
_RESUME_CODES = {"3", "Q", "T"}
_HISTORY_LIMIT_PER_SYMBOL = 256
_EXTENDED_SESSION_OPEN = time(4, 0)


def _stored_credentials() -> dict[str, str]:
    try:
        from app.security.provider_secret_store import load_trading_provider_secrets

        return dict(load_trading_provider_secrets().get("alpaca_iex") or {})
    except Exception:
        return {}


def _api_key() -> str:
    environment_value = (
        environment().get("OMNIX_ALPACA_API_KEY_ID")
        or environment().get("APCA_API_KEY_ID")
        or ""
    ).strip()
    return environment_value or _stored_credentials().get("api_key_id", "").strip()


def _api_secret() -> str:
    environment_value = (
        environment().get("OMNIX_ALPACA_API_SECRET_KEY")
        or environment().get("APCA_API_SECRET_KEY")
        or ""
    ).strip()
    return environment_value or _stored_credentials().get("secret_key", "").strip()


def _enabled() -> bool:
    value = environment().get("OMNIX_ALPACA_STATUS_STREAM", "1").strip().lower()
    return value in {"1", "true", "yes", "on"} and bool(_api_key() and _api_secret())


def alpaca_iex_status_monitor_enabled() -> bool:
    """Public readiness predicate shared by gateway operations telemetry."""

    return _enabled()


def _utc(value: datetime | None = None) -> datetime:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("Alpaca status timestamps must be timezone-aware")
    return observed.astimezone(timezone.utc)


@dataclass(frozen=True)
class AlpacaTradingStatus:
    symbol: str
    status_code: str
    reason_code: str | None
    message: str | None
    observed_at: datetime
    halted: bool


class AlpacaIexStatusCache:
    """Thread-safe live halt cache plus bounded prospective status history.

    ``halted()`` preserves the conservative execution contract: a resume is
    authoritative only while the stream is currently connected. The separate
    ``history_snapshot()`` method is evidence-only and reports whether the stream
    has been continuously connected since 04:00 ET before inferring that no halt
    was observed during the session.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: dict[str, AlpacaTradingStatus] = {}
        self._history: dict[str, deque[AlpacaTradingStatus]] = {}
        self._connected = False
        self._connected_since: datetime | None = None
        self._last_disconnect_at: datetime | None = None
        self._disconnect_count = 0

    def set_connected(self, connected: bool, *, observed_at: datetime | None = None) -> None:
        observed = _utc(observed_at)
        with self._lock:
            if connected and not self._connected:
                self._connected_since = observed
            elif not connected and self._connected:
                self._last_disconnect_at = observed
                self._disconnect_count += 1
                self._connected_since = None
            self._connected = connected

    def record(self, status: AlpacaTradingStatus) -> None:
        normalized = AlpacaTradingStatus(
            symbol=status.symbol.upper(),
            status_code=status.status_code,
            reason_code=status.reason_code,
            message=status.message,
            observed_at=_utc(status.observed_at),
            halted=status.halted,
        )
        with self._lock:
            self._values[normalized.symbol] = normalized
            history = self._history.setdefault(
                normalized.symbol,
                deque(maxlen=_HISTORY_LIMIT_PER_SYMBOL),
            )
            history.append(normalized)

    def halted(self, symbol: str) -> bool | None:
        with self._lock:
            value = self._values.get(symbol.upper())
            if value is None:
                return None
            if value.halted:
                return True
            return False if self._connected else None

    def confirmed_halt_interval(
        self,
        symbol: str,
        *,
        start: datetime,
        end: datetime,
    ) -> bool:
        """Return True only when the live status history proves the full interval halted.

        Absence of trades is never sufficient. The status stream must have been
        continuously connected since the extended-session open, the symbol must
        already be halted at interval start, and no resume may occur before end.
        """

        start_utc = _utc(start)
        end_utc = _utc(end)
        if end_utc <= start_utc:
            return False
        local = start_utc.astimezone(_ET)
        session_start = datetime.combine(
            local.date(),
            _EXTENDED_SESSION_OPEN,
            tzinfo=_ET,
        ).astimezone(timezone.utc)
        key = symbol.upper()
        with self._lock:
            if not (
                self._connected
                and self._connected_since is not None
                and self._connected_since <= session_start
            ):
                return False
            history = sorted(
                (
                    item
                    for item in self._history.get(key, ())
                    if session_start <= item.observed_at < end_utc
                ),
                key=lambda item: item.observed_at,
            )
            state_at_start: bool | None = False
            for item in history:
                if item.observed_at <= start_utc:
                    state_at_start = item.halted
                    continue
                if item.observed_at < end_utc and not item.halted:
                    return False
            return state_at_start is True

    def history_snapshot(self, symbol: str, *, as_of: datetime) -> dict[str, Any]:
        cutoff = _utc(as_of)
        local_cutoff = cutoff.astimezone(_ET)
        session_start = datetime.combine(
            local_cutoff.date(),
            _EXTENDED_SESSION_OPEN,
            tzinfo=_ET,
        ).astimezone(timezone.utc)
        key = symbol.upper()
        with self._lock:
            history = [
                item
                for item in self._history.get(key, ())
                if session_start <= item.observed_at <= cutoff
            ]
            history.sort(key=lambda item: item.observed_at)
            latest = history[-1] if history else None
            session_complete = bool(
                self._connected
                and self._connected_since is not None
                and self._connected_since <= session_start
            )
            halted_at_decision: bool | None
            if latest is not None:
                halted_at_decision = latest.halted
            elif session_complete:
                halted_at_decision = False
            else:
                halted_at_decision = None
            halts = [item for item in history if item.halted]
            resumes = [item for item in history if not item.halted]
            return {
                "symbol": key,
                "available": latest is not None or session_complete,
                "stream_connected": self._connected,
                "stream_connected_since": (
                    self._connected_since.isoformat() if self._connected_since is not None else None
                ),
                "last_disconnect_at": (
                    self._last_disconnect_at.isoformat() if self._last_disconnect_at is not None else None
                ),
                "disconnect_count": self._disconnect_count,
                "session_start": session_start.isoformat(),
                "session_history_complete": session_complete,
                "halted_at_decision": halted_at_decision,
                "halt_event_count": len(halts),
                "resume_event_count": len(resumes),
                "last_halt_at": halts[-1].observed_at.isoformat() if halts else None,
                "last_resume_at": resumes[-1].observed_at.isoformat() if resumes else None,
                "last_status_code": latest.status_code if latest is not None else None,
                "last_reason_code": latest.reason_code if latest is not None else None,
                "last_message": latest.message if latest is not None else None,
                "last_status_at": latest.observed_at.isoformat() if latest is not None else None,
                "error": None,
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": self._connected,
                "connected_since": (
                    self._connected_since.isoformat() if self._connected_since is not None else None
                ),
                "last_disconnect_at": (
                    self._last_disconnect_at.isoformat() if self._last_disconnect_at is not None else None
                ),
                "disconnect_count": self._disconnect_count,
                "symbols": len(self._values),
                "history_symbols": len(self._history),
                "known_halts": sum(1 for item in self._values.values() if item.halted),
            }


_default_cache = AlpacaIexStatusCache()


def default_alpaca_iex_status_cache() -> AlpacaIexStatusCache:
    return _default_cache


def _parse_time(value: Any) -> datetime:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _status_stream_connect_kwargs(connect: Any) -> dict[str, Any]:
    """Build compatible WebSocket options with direct transport by default."""

    connect_kwargs: dict[str, Any] = {
        "ping_interval": 20,
        "close_timeout": 5,
    }
    # websockets 15+ automatically discovers system proxy settings. A
    # partially speaking local proxy can fail the TLS handshake with
    # ASN1/NOT_ENOUGH_DATA before Alpaca receives the request. Keep the
    # status stream direct by default, while retaining an explicit proxy
    # escape hatch for deployments that require one. Older supported
    # websockets releases don't accept the ``proxy`` keyword.
    try:
        supports_proxy = "proxy" in inspect.signature(connect).parameters
    except (TypeError, ValueError):
        supports_proxy = False
    if supports_proxy:
        connect_kwargs["proxy"] = env_str("OMNIX_ALPACA_WS_PROXY") or None
    return connect_kwargs


def _status_stream_ssl_context() -> ssl.SSLContext:
    """Create a TLS context without loading the malformed Windows CA store."""

    return ssl.create_default_context(cafile=certifi.where())


class AlpacaIexStatusMonitor(ScheduledTradingMonitor):
    """Optional low-volume status stream used to reject known trading halts and capture research history."""

    def __init__(self, cache: AlpacaIexStatusCache | None = None) -> None:
        self.cache = cache or default_alpaca_iex_status_cache()
        self.last_error: str | None = None
        self.last_message_at: datetime | None = None
        self.reconnect_count = 0
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def run_once(self) -> None:
        """Keep the reconnecting status stream under its scheduler task lock.

        Async so the scheduler runs it on its event loop: the stream task needs
        a running loop, which a thread-executor cycle does not have.
        """
        if _enabled():
            self.start()

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.cache.set_connected(False)

    async def _receive_control(self, socket, expected: str) -> None:
        raw = await asyncio.wait_for(socket.recv(), timeout=10)
        payload = json.loads(raw)
        messages = payload if isinstance(payload, list) else [payload]
        if not any(
            isinstance(item, dict)
            and item.get("T") == "success"
            and item.get("msg") == expected
            for item in messages
        ):
            raise RuntimeError(f"Alpaca IEX status stream did not confirm {expected}")

    async def _session(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("Alpaca IEX status stream requires the websockets package") from exc

        url = environment().get("OMNIX_ALPACA_STREAM_URL", ALPACA_IEX_STREAM_URL).strip()
        connect_kwargs = _status_stream_connect_kwargs(websockets.connect)
        connect_kwargs["ssl"] = _status_stream_ssl_context()
        async with websockets.connect(url, **connect_kwargs) as socket:
            await self._receive_control(socket, "connected")
            await socket.send(
                json.dumps(
                    {"action": "auth", "key": _api_key(), "secret": _api_secret()},
                    separators=(",", ":"),
                )
            )
            await self._receive_control(socket, "authenticated")
            await socket.send(json.dumps({"action": "subscribe", "statuses": ["*"]}))
            subscription = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
            subscription_messages = subscription if isinstance(subscription, list) else [subscription]
            if not any(
                isinstance(item, dict)
                and item.get("T") == "subscription"
                and item.get("statuses")
                for item in subscription_messages
            ):
                raise RuntimeError("Alpaca IEX status subscription was not acknowledged")
            self.cache.set_connected(True)
            async for raw in socket:
                payload = json.loads(raw)
                messages = payload if isinstance(payload, list) else [payload]
                for item in messages:
                    if not isinstance(item, dict) or item.get("T") != "s":
                        continue
                    symbol = str(item.get("S") or "").upper()
                    code = str(item.get("sc") or "").upper()
                    if not symbol or code not in _HALT_CODES | _RESUME_CODES:
                        continue
                    status = AlpacaTradingStatus(
                        symbol=symbol,
                        status_code=code,
                        reason_code=str(item.get("rc")) if item.get("rc") is not None else None,
                        message=str(item.get("sm")) if item.get("sm") is not None else None,
                        observed_at=_parse_time(item.get("t")),
                        halted=code in _HALT_CODES,
                    )
                    self.cache.record(status)
                    self.last_message_at = datetime.now(timezone.utc)

    async def _loop(self) -> None:
        delay = 1.0
        while True:
            try:
                await self._session()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.reconnect_count += 1
            finally:
                self.cache.set_connected(False)
            await asyncio.sleep(delay)
            delay = min(30.0, delay * 2)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": _enabled(),
            "running": self._task is not None,
            "last_error": self.last_error,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
            "reconnect_count": self.reconnect_count,
            **self.cache.snapshot(),
        }


def create_alpaca_iex_status_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, AlpacaIexStatusMonitor):
        return None
    monitor = AlpacaIexStatusMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=_enabled, shutdown=(monitor.stop,))
