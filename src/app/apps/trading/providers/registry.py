from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from app.apps.trading.binding_authority import MarketDataAuthorityDecision, MarketDataCapability
from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.catalog import POLICIES, all_bindings, binding_by_id, default_binding
from app.apps.trading.execution import (
    ExecutionEligibilityPolicy,
    ExecutionObservation,
    assess_execution_observation,
    execution_observation_from_quote,
)
from app.apps.trading.models import BarsResponse, MarketBar, ProviderBinding

from .additional_crypto import AdditionalCryptoProvider
from .aggregation import (
    aggregate_market_bars,
    aggregated_dataset_fingerprint,
    aggregation_plan,
)
from app.apps.trading.market_session_status import us_equity_rules_apply

from .alpaca_iex import AlpacaIexExecutionProvider, alpaca_iex_configured
from .base import MarketDataProvider
from .binance import BinanceMarketDataProvider
from .ibkr import IbkrEquityProvider
from .coinmarketcap import CoinMarketCapProvider
from .equity import StooqEquityProvider, YahooEquityProvider
from .equity_execution import yahoo_execution_observation
from .errors import ProviderContractError, ProviderFallbackEligibleError


class ProviderRegistry:
    def __init__(
        self,
        *,
        cache: TradingMarketDataCache | None = None,
        factories: dict[str, Callable[[], Any]] | None = None,
    ) -> None:
        from .fred import FredSeriesProvider

        self.cache = cache or TradingMarketDataCache()
        self._factories = factories or {
            "binance": lambda: BinanceMarketDataProvider(cache=self.cache),
            "yahoo": lambda: YahooEquityProvider(cache=self.cache),
            "ibkr": lambda: IbkrEquityProvider(),
            "alpaca_iex": lambda: AlpacaIexExecutionProvider(),
            "stooq": lambda: StooqEquityProvider(cache=self.cache),
            "coinbase": lambda: AdditionalCryptoProvider("coinbase", cache=self.cache),
            "kraken": lambda: AdditionalCryptoProvider("kraken", cache=self.cache),
            "hyperliquid": lambda: AdditionalCryptoProvider("hyperliquid", cache=self.cache),
            "coinmarketcap": lambda: CoinMarketCapProvider(cache=self.cache),
            "fred": lambda: FredSeriesProvider(cache=self.cache),
        }
        self._providers: dict[str, Any] = {}

    def provider(self, provider_id: str):
        if provider_id not in self._factories:
            raise ValueError(f"unknown Trading provider: {provider_id}")
        return self._providers.setdefault(provider_id, self._factories[provider_id]())

    def resolve_binding(
        self,
        instrument_id: str,
        binding_id: str | None = None,
    ) -> ProviderBinding:
        binding = binding_by_id(binding_id) if binding_id else default_binding(instrument_id)
        if binding is None or binding.instrument_id != instrument_id:
            raise ValueError(f"invalid provider binding for {instrument_id}: {binding_id}")
        return binding

    def resolve_execution_binding(
        self,
        instrument_id: str,
        binding_id: str | None = None,
    ) -> ProviderBinding:
        """Resolve the authoritative paper-execution feed without changing chart history.

        Equity charts and strategy bars continue to use Yahoo/Stooq bindings. Any
        equity execution request is overlaid onto the official Alpaca IEX binding.
        Missing Alpaca credentials then fail closed at quote fetch time rather than
        silently falling back to Yahoo or a caller supplied price.
        """
        requested = self.resolve_binding(instrument_id, binding_id)
        if requested.provider == "fred" or instrument_id.startswith("economic:"):
            raise ValueError(f"{instrument_id} is an economic series: research data, never traded")
        if instrument_id.startswith("equity:") and requested.provider in {
            "yahoo",
            "stooq",
            "ibkr",
            "alpaca_iex",
        }:
            alpaca = next(
                (
                    item
                    for item in all_bindings()
                    if item.instrument_id == instrument_id and item.provider == "alpaca_iex"
                ),
                None,
            )
            if alpaca is not None:
                return alpaca
        return requested

    def resolve_live_data_authority(
        self,
        instrument_id: str,
        binding_id: str | None = None,
        *,
        required_capabilities: tuple[MarketDataCapability, ...] = ("QUOTE",),
        allow_partial_market: bool = False,
    ) -> MarketDataAuthorityDecision:
        """Resolve provider-neutral LIVE_DATA authority without granting order authority.

        IBKR is preferred only when its per-contract decision proves a healthy
        Gateway connection, unique contract, LIVE market-data type, entitlement,
        freshness and the explicit rollout gate. Alpaca IEX remains an
        independently attributed partial-market fallback.
        """

        requested = self.resolve_binding(instrument_id, binding_id)
        now = datetime.now(timezone.utc)
        if not instrument_id.startswith("equity:"):
            return MarketDataAuthorityDecision(
                provider=requested.provider,
                binding_id=requested.binding_id,
                instrument_id=instrument_id,
                capabilities=("QUOTE",),
                health="UNKNOWN",
                observed_at=now,
                authoritative=False,
                reason_codes=("LIVE_DATA_AUTHORITY_UNSUPPORTED_ASSET",),
            )

        ibkr_binding = next(
            (
                item
                for item in all_bindings()
                if item.instrument_id == instrument_id and item.provider == "ibkr"
            ),
            None,
        )
        ibkr_decision: MarketDataAuthorityDecision | None = None
        if ibkr_binding is not None:
            try:
                provider = self.provider("ibkr")
                ibkr_decision = provider.authority_decision(instrument_id)
            except Exception as exc:
                ibkr_decision = MarketDataAuthorityDecision(
                    provider="ibkr",
                    binding_id=ibkr_binding.binding_id,
                    instrument_id=instrument_id,
                    capabilities=("QUOTE", "BID_ASK", "HISTORICAL_BARS", "EXACT_RANGE", "STREAMING_QUOTES"),
                    health="ERROR",
                    observed_at=now,
                    authoritative=False,
                    reason_codes=(f"IBKR_AUTHORITY_ERROR:{type(exc).__name__}",),
                )
            if ibkr_decision.authoritative:
                missing = tuple(
                    capability
                    for capability in required_capabilities
                    if capability not in ibkr_decision.capabilities
                )
                if not missing:
                    return ibkr_decision
                ibkr_decision = MarketDataAuthorityDecision(
                    provider=ibkr_decision.provider,
                    binding_id=ibkr_decision.binding_id,
                    instrument_id=instrument_id,
                    capabilities=ibkr_decision.capabilities,
                    health="UNKNOWN",
                    market_data_type=ibkr_decision.market_data_type,
                    entitlement_live=ibkr_decision.entitlement_live,
                    quote_age_seconds=ibkr_decision.quote_age_seconds,
                    observed_at=now,
                    authoritative=False,
                    reason_codes=tuple(
                        f"REQUIRED_CAPABILITY_MISSING:{item}" for item in missing
                    ),
                )

        alpaca_binding = next(
            (
                item
                for item in all_bindings()
                if item.instrument_id == instrument_id and item.provider == "alpaca_iex"
            ),
            None,
        )
        if alpaca_binding is not None and alpaca_iex_configured():
            try:
                observation = self.provider("alpaca_iex").execution_observation(instrument_id)
                age = observation.age_seconds
                ready = observation.market_data_eligible
                capabilities: tuple[MarketDataCapability, ...] = (
                    "QUOTE",
                    "BID_ASK",
                    "HISTORICAL_BARS",
                    "PARTIAL_MARKET",
                )
                missing = tuple(
                    capability
                    for capability in required_capabilities
                    if capability not in capabilities
                )
                semantic_reasons: list[str] = []
                if not allow_partial_market:
                    semantic_reasons.append("PARTIAL_MARKET_NOT_AUTHORIZED")
                semantic_reasons.extend(
                    f"REQUIRED_CAPABILITY_MISSING:{item}" for item in missing
                )
                semantic_reasons.extend(observation.rejection_reasons if not ready else ())
                authoritative = ready and not semantic_reasons
                return MarketDataAuthorityDecision(
                    provider="alpaca_iex",
                    binding_id=alpaca_binding.binding_id,
                    instrument_id=instrument_id,
                    capabilities=capabilities,
                    health="READY" if authoritative else ("STALE" if not ready else "UNKNOWN"),
                    market_data_type="LIVE",
                    entitlement_live=True,
                    quote_age_seconds=max(Decimal("0"), age),
                    observed_at=now,
                    authoritative=authoritative,
                    reason_codes=tuple(dict.fromkeys(semantic_reasons)),
                )
            except Exception as exc:
                if ibkr_decision is not None:
                    return ibkr_decision
                return MarketDataAuthorityDecision(
                    provider="alpaca_iex",
                    binding_id=alpaca_binding.binding_id,
                    instrument_id=instrument_id,
                    capabilities=("QUOTE", "BID_ASK", "PARTIAL_MARKET"),
                    health="ERROR",
                    observed_at=now,
                    authoritative=False,
                    reason_codes=(f"ALPACA_IEX_AUTHORITY_ERROR:{type(exc).__name__}",),
                )
        if ibkr_decision is not None:
            return ibkr_decision
        fallback_binding = alpaca_binding or requested
        return MarketDataAuthorityDecision(
            provider=fallback_binding.provider,
            binding_id=fallback_binding.binding_id,
            instrument_id=instrument_id,
            capabilities=(),
            health="CLIENT_UNAVAILABLE",
            observed_at=now,
            authoritative=False,
            reason_codes=("NO_LIVE_EQUITY_PROVIDER_READY",),
        )

    def live_data_observation(
        self,
        instrument_id: str,
        binding_id: str | None = None,
        *,
        policy: ExecutionEligibilityPolicy | None = None,
        required_capabilities: tuple[MarketDataCapability, ...] = ("QUOTE", "BID_ASK"),
        allow_partial_market: bool = False,
    ) -> ExecutionObservation:
        decision = self.resolve_live_data_authority(
            instrument_id,
            binding_id,
            required_capabilities=required_capabilities,
            allow_partial_market=allow_partial_market,
        )
        if not decision.authoritative:
            raise ValueError(
                "live_data_authority_unavailable:" + ",".join(decision.reason_codes or (decision.health,))
            )
        provider = self.provider(decision.provider)
        if decision.provider == "ibkr":
            return provider.live_observation(instrument_id, policy=policy)
        observation = provider.execution_observation(instrument_id, policy=policy)
        return assess_execution_observation(
            observation.model_copy(update={"binding_purpose": "LIVE_DATA"}),
            policy,
            binding_purpose="LIVE_DATA",
        )

    @staticmethod
    def _supports_cancellation(function: Callable[..., Any]) -> bool:
        try:
            return "cancellation" in inspect.signature(function).parameters
        except (TypeError, ValueError):
            return False

    def _bars(
        self,
        provider: Any,
        instrument_id: str,
        interval: str,
        limit: int,
        cancellation: threading.Event | None,
    ) -> BarsResponse:
        if self._supports_cancellation(provider.get_bars):
            return provider.get_bars(
                instrument_id,
                interval,
                limit,
                cancellation=cancellation,
            )
        return provider.get_bars(instrument_id, interval, limit)

    def _bars_for_interval(
        self,
        provider: Any,
        binding: ProviderBinding,
        instrument_id: str,
        interval: str,
        limit: int,
        cancellation: threading.Event | None,
        *,
        alignment: Literal["count", "clock"] = "count",
        include_extended_hours: bool = True,
    ) -> BarsResponse:
        plan = aggregation_plan(interval, binding.supported_intervals)
        if plan is None:
            return self._bars(provider, instrument_id, interval, limit, cancellation)

        base_interval, factor = plan
        base_limit = min(5_000, max(1, limit * factor + factor - 1))
        base_response = self._bars(
            provider,
            instrument_id,
            base_interval,
            base_limit,
            cancellation,
        )
        if alignment == "clock":
            return self._clock_aggregated(base_response, interval, base_interval, include_extended_hours)
        bars = aggregate_market_bars(
            base_response.bars,
            target_interval=interval,
            base_interval=base_interval,
            factor=factor,
        )
        provenance = base_response.provenance.model_copy(
            update={
                "as_of": bars[-1].end_time if bars else base_response.provenance.as_of,
                "dataset_fingerprint": aggregated_dataset_fingerprint(
                    base_response.provenance.dataset_fingerprint,
                    target_interval=interval,
                    base_interval=base_interval,
                    factor=factor,
                ),
                "history_complete": base_response.provenance.history_complete,
            }
        )
        return base_response.model_copy(
            update={
                "interval": interval,
                "bars": bars,
                "provenance": provenance,
            }
        )

    @staticmethod
    def _clock_aggregated(
        base_response: BarsResponse,
        interval: str,
        base_interval: str,
        include_extended_hours: bool,
    ) -> BarsResponse:
        """Chart-only clock-aligned aggregation (TVP-2.5); count mode is what strategies read."""
        from .clock_aggregation import aggregate_market_bars_clock, clock_aggregated_dataset_fingerprint

        instrument = base_response.instrument
        calendar = (
            instrument.session_calendar
            if us_equity_rules_apply(instrument.session_calendar, instrument.venue)
            else "24x7"
        )
        bars = aggregate_market_bars_clock(
            list(base_response.bars),
            target_interval=interval,
            base_interval=base_interval,
            calendar=calendar,
            exchange_tz=instrument.exchange_timezone,
            include_extended=include_extended_hours,
            history_complete=base_response.provenance.history_complete,
        )
        provenance = base_response.provenance.model_copy(
            update={
                "as_of": bars[-1].end_time if bars else base_response.provenance.as_of,
                "dataset_fingerprint": clock_aggregated_dataset_fingerprint(
                    base_response.provenance.dataset_fingerprint,
                    target_interval=interval,
                    base_interval=base_interval,
                    include_extended=include_extended_hours,
                ),
            }
        )
        return base_response.model_copy(update={"interval": interval, "bars": bars, "provenance": provenance})

    def bars(
        self,
        instrument_id: str,
        interval: str,
        limit: int,
        binding_id: str | None = None,
        cancellation: threading.Event | None = None,
        *,
        alignment: Literal["count", "clock"] = "count",
        include_extended_hours: bool = True,
    ) -> BarsResponse:
        requested = self.resolve_binding(instrument_id, binding_id)
        try:
            return self._bars_for_interval(
                self.provider(requested.provider),
                requested,
                instrument_id,
                interval,
                limit,
                cancellation,
                alignment=alignment,
                include_extended_hours=include_extended_hours,
            )
        except ProviderFallbackEligibleError as primary_error:
            if requested.provider != "yahoo" or interval != "1d":
                raise
            fallback = next(
                (
                    item
                    for item in all_bindings()
                    if item.instrument_id == instrument_id and item.provider == "stooq"
                ),
                None,
            )
            if fallback is None:
                raise
            result = self._bars_for_interval(
                self.provider("stooq"),
                fallback,
                instrument_id,
                interval,
                limit,
                cancellation,
                alignment=alignment,
                include_extended_hours=include_extended_hours,
            )
            result.provenance = result.provenance.model_copy(
                update={
                    "requested_binding": requested.binding_id,
                    "resolved_binding": fallback.binding_id,
                    "fallback_reason": (
                        f"Yahoo whole-dataset fallback: {type(primary_error).__name__}"
                    ),
                    "freshness_mode": "fallback",
                }
            )
            return result

    def quote(
        self,
        instrument_id: str,
        binding_id: str | None = None,
        cancellation: threading.Event | None = None,
    ) -> dict[str, Any]:
        binding = self.resolve_binding(instrument_id, binding_id)
        provider = self.provider(binding.provider)
        if self._supports_cancellation(provider.get_quote):
            return provider.get_quote(instrument_id, cancellation=cancellation)
        return provider.get_quote(instrument_id)

    def execution_observation(
        self,
        instrument_id: str,
        binding_id: str | None = None,
        *,
        policy: ExecutionEligibilityPolicy | None = None,
        cancellation: threading.Event | None = None,
    ) -> ExecutionObservation:
        requested = self.resolve_binding(instrument_id, binding_id)
        binding = self.resolve_execution_binding(instrument_id, requested.binding_id)
        provider = self.provider(binding.provider)
        if binding.provider == "alpaca_iex":
            try:
                observation = provider.execution_observation(
                    instrument_id,
                    policy=policy,
                    cancellation=cancellation,
                )
            except (ProviderContractError, ProviderFallbackEligibleError):
                # IEX sees part of the market; thin stocks often have no IEX
                # quote. A quote from IBKR is used only while IBKR holds
                # per-contract live-data authority, and stays attributed to it.
                fallback = self._ibkr_execution_fallback(instrument_id, policy)
                if fallback is None:
                    raise
                observation = fallback
            if requested.binding_id != binding.binding_id:
                observation = observation.model_copy(update={"binding_id": requested.binding_id})
            return observation
        if binding.provider == "yahoo":
            return yahoo_execution_observation(
                provider,
                instrument_id,
                policy=policy,
                cancellation=cancellation,
            )
        quote = self.quote(instrument_id, binding.binding_id, cancellation)
        observation = execution_observation_from_quote(
            quote,
            binding_id=binding.binding_id,
            provider=binding.provider,
        )
        return assess_execution_observation(observation, policy)

    def _ibkr_execution_fallback(
        self,
        instrument_id: str,
        policy: ExecutionEligibilityPolicy | None,
    ) -> ExecutionObservation | None:
        """An IBKR live observation, or None unless IBKR's live-data authority holds now.

        Authority requires the rollout gate, a connected Gateway, a uniquely
        qualified contract, LIVE market-data type, proven entitlement and a fresh
        quote. The observation keeps its LIVE_DATA purpose, so it can support a
        paper or shadow fill but never broker execution authority.
        """
        if not instrument_id.startswith("equity:"):
            return None
        try:
            ibkr = self.provider("ibkr")
            if not ibkr.authority_decision(instrument_id).authoritative:
                return None
            return ibkr.live_observation(instrument_id, policy=policy)
        except Exception:
            return None

    def execution_indicator_bars(
        self,
        instrument_id: str,
        binding_id: str | None = None,
        *,
        as_of: datetime,
        cancellation: threading.Event | None = None,
    ) -> list[MarketBar]:
        """Return causal indicator history from the authoritative execution feed.

        This deliberately follows execution binding resolution rather than chart
        history resolution. Equity strategies therefore use Alpaca IEX evidence
        even when their persisted/chart binding is Yahoo or Stooq.
        """

        requested = self.resolve_binding(instrument_id, binding_id)
        binding = self.resolve_execution_binding(instrument_id, requested.binding_id)
        provider = self.provider(binding.provider)
        method = getattr(provider, "indicator_bars_as_of", None)
        if not callable(method):
            raise ValueError(
                f"execution provider does not support indicator history: {binding.provider}"
            )
        if self._supports_cancellation(method):
            return method(
                instrument_id,
                as_of=as_of,
                cancellation=cancellation,
            )
        return method(instrument_id, as_of=as_of)

    def currency_rate(
        self,
        base_currency: str,
        quote_currency: str,
        cancellation: threading.Event | None = None,
    ) -> dict[str, Any]:
        provider = self.provider("yahoo")
        method = getattr(provider, "get_currency_rate", None)
        if not callable(method):
            raise ValueError("Yahoo provider does not support currency conversion")
        if self._supports_cancellation(method):
            return method(base_currency, quote_currency, cancellation=cancellation)
        return method(base_currency, quote_currency)

    def descriptors(self) -> list[dict[str, Any]]:
        descriptors: list[dict[str, Any]] = []
        for provider_id, policy in POLICIES.items():
            provider: MarketDataProvider = self.provider(provider_id)
            status, runtime_payload = provider.runtime_status()
            configured = provider.configured()
            descriptors.append(
                {
                    "provider": provider_id,
                    "display_name": provider.display_name,
                    "enabled": configured,
                    "status": status if configured else "unconfigured",
                    "policy": policy,
                    "bindings": [
                        binding for binding in all_bindings() if binding.provider == provider_id
                    ],
                    "runtime": runtime_payload,
                }
            )
        return descriptors
