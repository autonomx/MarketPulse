from __future__ import annotations
from app.config.env import env_str as _env_str

"""IBKR live-data and historical-repair provider.

This adapter deliberately exposes market data only. It has no order-placement
methods and its catalog bindings are LIVE_DATA purpose, never EXECUTION purpose.
"""

import hashlib
from collections.abc import Callable
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.apps.trading.binding_authority import MarketDataAuthorityDecision, MarketDataCapability
from app.apps.trading.catalog import POLICIES, bindings_for_instrument, instrument_by_id
from app.apps.trading.execution import (
    ExecutionEligibilityPolicy,
    ExecutionObservation,
    assess_execution_observation,
    execution_observation_from_quote,
)
from app.apps.trading.models import (
    AdjustmentMode,
    BarsResponse,
    DatasetProvenance,
    MarketBar,
    ProviderBinding,
)
from app.apps.trading.us_equity_calendar import us_equity_session

from .base import ProviderAdapter
from .errors import ProviderContractError, ProviderDataUnavailableError
from .ibkr_runtime import (
    IbkrContractAmbiguousError,
    IbkrContractIdentity,
    IbkrContractUnavailableError,
    IbkrQuoteSnapshot,
    IbkrRuntime,
    IbkrRuntimeError,
    default_ibkr_runtime,
    official_ibapi_available,
)
from app.apps.trading.us_equity_calendar import EASTERN as _ET


_EXTENDED_OPEN = time(4, 0)
_MAX_HISTORICAL_CHUNK = timedelta(days=1)


def ibkr_configured(runtime: IbkrRuntime | None = None) -> bool:
    active = runtime or default_ibkr_runtime()
    return bool(active.enabled and (active.transport is not None or official_ibapi_available()))


def _fingerprint(instrument_id: str, bars: list[MarketBar]) -> str:
    payload = "|".join(
        [
            instrument_id,
            *(
                f"{bar.start_time.isoformat()}:{bar.provider_event_id}:"
                f"{bar.open}:{bar.high}:{bar.low}:{bar.close}:{bar.volume}"
                for bar in bars
            ),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _quote_age(snapshot: IbkrQuoteSnapshot, now: datetime) -> Decimal:
    return max(
        Decimal("0"),
        Decimal(str((now.astimezone(timezone.utc) - snapshot.source_time.astimezone(timezone.utc)).total_seconds())),
    )


class IbkrEquityProvider(ProviderAdapter):
    provider_id = "ibkr"
    policy = POLICIES[provider_id]
    display_name = "IBKR Gateway"

    def configured(self) -> bool:
        return ibkr_configured(self.runtime)

    def runtime_status(self) -> tuple[str, dict[str, Any]]:
        diagnostics = dict(self.runtime.diagnostics())
        return ("ready" if diagnostics.get("connected") else "unavailable"), diagnostics

    def __init__(
        self,
        *,
        runtime: IbkrRuntime | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.runtime = runtime or default_ibkr_runtime()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def get_binding(self, instrument_id: str) -> ProviderBinding:
        binding = next(
            (
                item
                for item in bindings_for_instrument(instrument_id)
                if item.provider == self.provider_id
            ),
            None,
        )
        if binding is None:
            raise ValueError(f"IBKR does not support instrument: {instrument_id}")
        return binding

    def _contract(self, instrument_id: str) -> IbkrContractIdentity:
        instrument = instrument_by_id(instrument_id)
        if instrument is None or not instrument_id.startswith("equity:"):
            raise ValueError(f"IBKR requires an equity instrument: {instrument_id}")
        try:
            return self.runtime.qualify_contract(
                instrument_id,
                symbol=instrument.display_symbol,
                venue=instrument.venue,
                currency=instrument.quote_currency or "USD",
            )
        except IbkrContractAmbiguousError as exc:
            raise ProviderContractError(str(exc)) from exc
        except IbkrContractUnavailableError as exc:
            raise ProviderDataUnavailableError(str(exc)) from exc
        except IbkrRuntimeError as exc:
            raise ProviderDataUnavailableError(str(exc)) from exc

    def subscribe_quote(
        self,
        instrument_id: str,
        listener: Callable[[IbkrQuoteSnapshot], None],
    ) -> int:
        contract = self._contract(instrument_id)
        return self.runtime.subscribe_quote(
            instrument_id,
            contract=contract,
            listener=listener,
        )

    def unsubscribe_quote(
        self,
        instrument_id: str,
        *,
        listener: Callable[[IbkrQuoteSnapshot], None] | None = None,
    ) -> None:
        self.runtime.unsubscribe_quote(instrument_id, listener=listener)

    def latest_quote(self, instrument_id: str) -> IbkrQuoteSnapshot | None:
        return self.runtime.latest_quote(instrument_id)

    def authority_decision(
        self,
        instrument_id: str,
        *,
        max_age_seconds: Decimal = Decimal("5"),
    ) -> MarketDataAuthorityDecision:
        binding = self.get_binding(instrument_id)
        now = self.clock()
        if now.tzinfo is None:
            raise ProviderContractError("IBKR provider clock must be timezone-aware")
        now = now.astimezone(timezone.utc)
        capabilities: tuple[MarketDataCapability, ...] = (
            "QUOTE",
            "BID_ASK",
            "HISTORICAL_BARS",
            "EXACT_RANGE",
            "STREAMING_QUOTES",
        )

        if not self.runtime.enabled:
            return MarketDataAuthorityDecision(
                provider=self.provider_id,
                binding_id=binding.binding_id,
                instrument_id=instrument_id,
                capabilities=capabilities,
                health="CLIENT_UNAVAILABLE",
                observed_at=now,
                authoritative=False,
                reason_codes=("IBKR_DISABLED",),
            )
        if self.runtime.transport is None and not official_ibapi_available():
            return MarketDataAuthorityDecision(
                provider=self.provider_id,
                binding_id=binding.binding_id,
                instrument_id=instrument_id,
                capabilities=capabilities,
                health="CLIENT_UNAVAILABLE",
                observed_at=now,
                authoritative=False,
                reason_codes=("IBKR_OFFICIAL_CLIENT_UNAVAILABLE",),
            )
        if not self.runtime.is_connected():
            return MarketDataAuthorityDecision(
                provider=self.provider_id,
                binding_id=binding.binding_id,
                instrument_id=instrument_id,
                capabilities=capabilities,
                health="DISCONNECTED",
                observed_at=now,
                authoritative=False,
                reason_codes=("IBKR_DISCONNECTED",),
            )

        snapshot = self.runtime.latest_quote(instrument_id)
        if snapshot is None:
            request_health = self.runtime.subscription_health(instrument_id)
            if bool(request_health.get("entitlement_denied")):
                error = request_health.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                return MarketDataAuthorityDecision(
                    provider=self.provider_id,
                    binding_id=binding.binding_id,
                    instrument_id=instrument_id,
                    capabilities=capabilities,
                    health="ENTITLEMENT_MISSING",
                    market_data_type="UNKNOWN",
                    entitlement_live=False,
                    observed_at=now,
                    authoritative=False,
                    reason_codes=(
                        f"IBKR_LIVE_ENTITLEMENT_DENIED:{code}"
                        if code is not None
                        else "IBKR_LIVE_ENTITLEMENT_DENIED",
                    ),
                )
            return MarketDataAuthorityDecision(
                provider=self.provider_id,
                binding_id=binding.binding_id,
                instrument_id=instrument_id,
                capabilities=capabilities,
                health="UNKNOWN",
                observed_at=now,
                authoritative=False,
                reason_codes=("IBKR_QUOTE_MISSING",),
            )
        age = _quote_age(snapshot, now)
        data_type = snapshot.market_data_type
        if data_type == "DELAYED" or data_type == "DELAYED_FROZEN":
            return MarketDataAuthorityDecision(
                provider=self.provider_id,
                binding_id=binding.binding_id,
                instrument_id=instrument_id,
                capabilities=capabilities,
                health="DELAYED",
                market_data_type=data_type,
                entitlement_live=False,
                quote_age_seconds=age,
                observed_at=now,
                authoritative=False,
                reason_codes=("IBKR_MARKET_DATA_DELAYED",),
            )
        if data_type == "FROZEN":
            return MarketDataAuthorityDecision(
                provider=self.provider_id,
                binding_id=binding.binding_id,
                instrument_id=instrument_id,
                capabilities=capabilities,
                health="FROZEN",
                market_data_type=data_type,
                entitlement_live=False,
                quote_age_seconds=age,
                observed_at=now,
                authoritative=False,
                reason_codes=("IBKR_MARKET_DATA_FROZEN",),
            )
        if data_type != "LIVE":
            return MarketDataAuthorityDecision(
                provider=self.provider_id,
                binding_id=binding.binding_id,
                instrument_id=instrument_id,
                capabilities=capabilities,
                health="ENTITLEMENT_MISSING",
                market_data_type="UNKNOWN",
                entitlement_live=None,
                quote_age_seconds=age,
                observed_at=now,
                authoritative=False,
                reason_codes=("IBKR_LIVE_ENTITLEMENT_UNPROVEN",),
            )
        if age > max_age_seconds:
            return MarketDataAuthorityDecision(
                provider=self.provider_id,
                binding_id=binding.binding_id,
                instrument_id=instrument_id,
                capabilities=capabilities,
                health="STALE",
                market_data_type="LIVE",
                entitlement_live=True,
                quote_age_seconds=age,
                observed_at=now,
                authoritative=False,
                reason_codes=("IBKR_QUOTE_STALE",),
            )
        if snapshot.bid is None or snapshot.ask is None or snapshot.last is None:
            return MarketDataAuthorityDecision(
                provider=self.provider_id,
                binding_id=binding.binding_id,
                instrument_id=instrument_id,
                capabilities=capabilities,
                health="ENTITLEMENT_MISSING",
                market_data_type="LIVE",
                entitlement_live=True,
                quote_age_seconds=age,
                observed_at=now,
                authoritative=False,
                reason_codes=("IBKR_REQUIRED_QUOTE_FIELDS_MISSING",),
            )
        if (
            snapshot.bid <= 0
            or snapshot.ask <= 0
            or snapshot.bid > snapshot.ask
        ):
            return MarketDataAuthorityDecision(
                provider=self.provider_id,
                binding_id=binding.binding_id,
                instrument_id=instrument_id,
                capabilities=capabilities,
                health="ERROR",
                market_data_type="LIVE",
                entitlement_live=True,
                quote_age_seconds=age,
                observed_at=now,
                authoritative=False,
                reason_codes=("IBKR_CROSSED_OR_INVALID_BBO",),
            )
        if not self.runtime.live_authority_enabled:
            return MarketDataAuthorityDecision(
                provider=self.provider_id,
                binding_id=binding.binding_id,
                instrument_id=instrument_id,
                capabilities=capabilities,
                health="OBSERVATION_ONLY",
                market_data_type="LIVE",
                entitlement_live=True,
                quote_age_seconds=age,
                observed_at=now,
                authoritative=False,
                reason_codes=("IBKR_ZERO_AUTHORITY_OBSERVATION_MODE",),
            )
        return MarketDataAuthorityDecision(
            provider=self.provider_id,
            binding_id=binding.binding_id,
            instrument_id=instrument_id,
            capabilities=capabilities,
            health="READY",
            market_data_type="LIVE",
            entitlement_live=True,
            quote_age_seconds=age,
            observed_at=now,
            authoritative=True,
        )

    def get_quote(self, instrument_id: str, cancellation=None) -> dict[str, Any]:
        binding = self.get_binding(instrument_id)
        contract = self._contract(instrument_id)
        self.runtime.subscribe_quote(instrument_id, contract=contract)
        snapshot = self.runtime.wait_for_quote(
            instrument_id,
            timeout_seconds=float(_env_str("OMNIX_IBKR_QUOTE_TIMEOUT_SECONDS", "3")),
        )
        if snapshot is None or snapshot.last is None:
            request_health = self.runtime.subscription_health(instrument_id)
            if bool(request_health.get("entitlement_denied")):
                error = request_health.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                raise ProviderDataUnavailableError(
                    f"IBKR live market-data entitlement denied for {binding.provider_symbol}"
                    + (f" (error {code})" if code is not None else "")
                )
            raise ProviderDataUnavailableError(
                f"IBKR returned no quote for {binding.provider_symbol}"
            )
        freshness = "live" if snapshot.market_data_type == "LIVE" else "delayed"
        return {
            "instrument_id": instrument_id,
            "binding_id": binding.binding_id,
            "provider": self.provider_id,
            "bid": snapshot.bid,
            "ask": snapshot.ask,
            "bid_size": snapshot.bid_size,
            "ask_size": snapshot.ask_size,
            "last": snapshot.last,
            "high": snapshot.high,
            "low": snapshot.low,
            "cumulative_volume": snapshot.cumulative_volume,
            "source_time": snapshot.source_time,
            "received_at": snapshot.received_at,
            "last_trade_at": snapshot.last_trade_at,
            "session": us_equity_session(snapshot.source_time),
            "freshness_mode": freshness,
            "provider_sequence": snapshot.provider_sequence,
            "market_data_type": snapshot.market_data_type,
            "live_entitled": snapshot.live_entitled,
            "ibkr_con_id": contract.con_id,
            "ibkr_primary_exchange": contract.primary_exchange,
            "ibkr_local_symbol": contract.local_symbol,
        }

    def live_observation(
        self,
        instrument_id: str,
        *,
        policy: ExecutionEligibilityPolicy | None = None,
    ) -> ExecutionObservation:
        binding = self.get_binding(instrument_id)
        quote = self.get_quote(instrument_id)
        observation = execution_observation_from_quote(
            quote,
            binding_id=binding.binding_id,
            provider=self.provider_id,
            received_at=quote["received_at"],
        )
        return assess_execution_observation(
            observation,
            policy,
            binding_purpose="LIVE_DATA",
        )

    def _normalize_history(
        self,
        instrument_id: str,
        contract: IbkrContractIdentity,
        rows,
        *,
        start: datetime,
        end: datetime,
    ) -> list[MarketBar]:
        received = self.clock()
        if received.tzinfo is None:
            raise ProviderContractError("IBKR provider clock must be timezone-aware")
        received = received.astimezone(timezone.utc)
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        bars: list[MarketBar] = []
        for row in rows:
            bar_start = row.start_time.astimezone(timezone.utc)
            bar_end = bar_start + timedelta(minutes=1)
            if bar_start < start_utc or bar_start >= end_utc or bar_end > received:
                continue
            bars.append(
                MarketBar(
                    instrument_id=instrument_id,
                    interval="1m",
                    start_time=bar_start,
                    end_time=bar_end,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    is_final=True,
                    adjustment_mode=AdjustmentMode.RAW,
                    session=us_equity_session(bar_start),
                    provider=self.provider_id,
                    provider_event_id=f"ibkr:{contract.con_id}:{bar_start.isoformat()}",
                    received_at=received,
                )
            )
        by_start = {bar.start_time: bar for bar in bars}
        return [by_start[key] for key in sorted(by_start)]

    def get_intraday_bars_range(
        self,
        instrument_id: str,
        *,
        start: datetime,
        end: datetime,
        include_extended_hours: bool = False,
        cancellation=None,
    ) -> BarsResponse:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("IBKR bounded bars require timezone-aware start/end")
        if end <= start:
            raise ValueError("IBKR bounded bars end must follow start")
        binding = self.get_binding(instrument_id)
        contract = self._contract(instrument_id)

        rows = []
        cursor = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        while cursor < end_utc:
            chunk_end = min(end_utc, cursor + _MAX_HISTORICAL_CHUNK)
            rows.extend(
                self.runtime.historical_bars(
                    contract,
                    start=cursor,
                    end=chunk_end,
                    bar_size="1 min",
                    what_to_show="TRADES",
                    use_rth=not include_extended_hours,
                )
            )
            cursor = chunk_end

        bars = self._normalize_history(
            instrument_id,
            contract,
            rows,
            start=start,
            end=end,
        )
        if not bars:
            raise ProviderDataUnavailableError(
                f"IBKR returned no 1m TRADES bars for {binding.provider_symbol}"
            )
        instrument = instrument_by_id(instrument_id)
        if instrument is None:
            raise ProviderContractError(f"unknown instrument: {instrument_id}")
        received = max(bar.received_at for bar in bars)
        return BarsResponse(
            instrument=instrument,
            binding=binding,
            provenance=DatasetProvenance(
                instrument_id=instrument_id,
                requested_binding=binding.binding_id,
                resolved_binding=binding.binding_id,
                dataset_fingerprint=_fingerprint(instrument_id, bars),
                freshness_mode="polled",
                as_of=bars[-1].end_time,
                received_at=received,
                delay_seconds=0,
                cached=False,
                history_complete=True,
            ),
            interval="1m",
            bars=bars,
        )

    def indicator_bars_as_of(
        self,
        instrument_id: str,
        *,
        as_of: datetime,
        cancellation=None,
    ) -> list[MarketBar]:
        if as_of.tzinfo is None:
            raise ValueError("IBKR indicator as_of must be timezone-aware")
        local = as_of.astimezone(_ET)
        start = datetime.combine(local.date(), _EXTENDED_OPEN, tzinfo=_ET)
        if as_of <= start:
            return []
        response = self.get_intraday_bars_range(
            instrument_id,
            start=start,
            end=as_of,
            include_extended_hours=True,
            cancellation=cancellation,
        )
        return response.bars

    def get_bars(
        self,
        instrument_id: str,
        interval: str,
        limit: int,
        cancellation=None,
    ) -> BarsResponse:
        if interval != "1m":
            raise ValueError("IBKR provider exposes canonical 1m bars; aggregate in Omnix")
        if limit < 1:
            raise ValueError("IBKR bars limit must be positive")
        now = self.clock()
        if now.tzinfo is None:
            raise ProviderContractError("IBKR provider clock must be timezone-aware")
        start = now.astimezone(timezone.utc) - timedelta(minutes=min(limit, 2_000) + 2)
        response = self.get_intraday_bars_range(
            instrument_id,
            start=start,
            end=now,
            include_extended_hours=True,
            cancellation=cancellation,
        )
        if len(response.bars) <= limit:
            return response
        return response.model_copy(update={"bars": response.bars[-limit:]})

    def diagnostics(self) -> dict[str, Any]:
        return self.runtime.diagnostics()


__all__ = ["IbkrEquityProvider", "ibkr_configured"]
