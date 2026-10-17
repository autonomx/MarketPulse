from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.api import ProviderDescriptor
from app.apps.trading.binding_authority import MarketDataAuthorityDecision
from app.apps.trading.catalog import bindings_for_instrument
from app.apps.trading.execution import ExecutionObservation, assess_execution_observation
from app.apps.trading.providers.ibkr import IbkrEquityProvider
from app.apps.trading.providers.ibkr_runtime import (
    FakeIbkrTransport,
    IbkrContractIdentity,
    IbkrQuoteSnapshot,
    IbkrRuntime,
)
from app.apps.trading.providers.registry import ProviderRegistry
import app.apps.trading.providers.registry as registry_module


INSTRUMENT = "equity:NASDAQ:AAPL"
NOW = datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc)


def _contract() -> IbkrContractIdentity:
    return IbkrContractIdentity(
        con_id=265598,
        symbol="AAPL",
        local_symbol="AAPL",
        sec_type="STK",
        currency="USD",
        exchange="SMART",
        primary_exchange="NASDAQ",
        trading_class="NMS",
    )


def _provider(monkeypatch):
    monkeypatch.setenv("OMNIX_IBKR_LIVE_AUTHORITY", "1")
    contract = _contract()
    transport = FakeIbkrTransport(contracts={"AAPL": [contract]})
    runtime = IbkrRuntime(transport=transport, enabled=True)
    provider = IbkrEquityProvider(runtime=runtime, clock=lambda: NOW)
    token = provider.subscribe_quote(INSTRUMENT, lambda snapshot: None)
    return provider, transport, token, contract


def test_ibkr_live_authority_requires_live_entitled_fresh_complete_quote(monkeypatch):
    provider, transport, token, contract = _provider(monkeypatch)
    transport.emit(
        token,
        IbkrQuoteSnapshot(
            contract=contract,
            bid=Decimal("100.00"),
            ask=Decimal("100.02"),
            last=Decimal("100.01"),
            source_time=NOW,
            received_at=NOW,
            market_data_type="LIVE",
        ),
    )

    decision = provider.authority_decision(INSTRUMENT)

    assert decision.authoritative is True
    assert decision.health == "READY"
    assert decision.entitlement_live is True


def test_ibkr_delayed_quote_never_becomes_live_authority(monkeypatch):
    provider, transport, token, contract = _provider(monkeypatch)
    transport.emit(
        token,
        IbkrQuoteSnapshot(
            contract=contract,
            bid=Decimal("100.00"),
            ask=Decimal("100.02"),
            last=Decimal("100.01"),
            source_time=NOW,
            received_at=NOW,
            market_data_type="DELAYED",
        ),
    )

    decision = provider.authority_decision(INSTRUMENT)

    assert decision.authoritative is False
    assert decision.health == "DELAYED"
    assert decision.entitlement_live is False


def test_ibkr_authority_distinguishes_entitlement_denial_from_missing_quote(monkeypatch):
    provider, transport, token, _ = _provider(monkeypatch)
    transport.request_health_by_token[token] = {
        "request_id": token,
        "market_data_type": "UNKNOWN",
        "error": {"code": 354, "message": "Not subscribed"},
        "entitlement_denied": True,
    }

    decision = provider.authority_decision(INSTRUMENT)

    assert decision.authoritative is False
    assert decision.health == "ENTITLEMENT_MISSING"
    assert decision.entitlement_live is False
    assert decision.reason_codes == ("IBKR_LIVE_ENTITLEMENT_DENIED:354",)


def test_live_data_observation_fails_closed_when_entitlement_is_unknown():
    observation = ExecutionObservation(
        instrument_id=INSTRUMENT,
        binding_id="live:test",
        provider="test",
        binding_purpose="LIVE_DATA",
        bid=Decimal("10"),
        ask=Decimal("10.01"),
        last=Decimal("10.005"),
        source_time=NOW,
        received_at=NOW,
        session="regular",
        freshness_mode="live",
        market_data_type="UNKNOWN",
        live_entitled=None,
    )

    assessed = assess_execution_observation(observation, binding_purpose="LIVE_DATA")

    assert assessed.market_data_eligible is False
    assert "MARKET_DATA_NOT_LIVE" in assessed.rejection_reasons
    assert "LIVE_ENTITLEMENT_UNPROVEN" in assessed.rejection_reasons
    assert assessed.broker_execution_authorized is False


class _UnavailableIbkr:
    def authority_decision(self, instrument_id):
        binding = next(
            item
            for item in bindings_for_instrument(instrument_id)
            if item.provider == "ibkr"
        )
        return MarketDataAuthorityDecision(
            provider="ibkr",
            binding_id=binding.binding_id,
            instrument_id=instrument_id,
            capabilities=("QUOTE", "BID_ASK"),
            health="DISCONNECTED",
            observed_at=NOW,
            authoritative=False,
            reason_codes=("IBKR_DISCONNECTED",),
        )


class _ReadyIex:
    def execution_observation(self, instrument_id, policy=None):
        binding = next(
            item
            for item in bindings_for_instrument(instrument_id)
            if item.provider == "alpaca_iex"
        )
        observation = ExecutionObservation(
            instrument_id=instrument_id,
            binding_id=binding.binding_id,
            provider="alpaca_iex",
            bid=Decimal("10"),
            ask=Decimal("10.01"),
            last=Decimal("10.005"),
            source_time=NOW,
            received_at=NOW,
            session="regular",
            freshness_mode="live",
        )
        return assess_execution_observation(observation, policy)


def test_iex_live_fallback_requires_explicit_partial_market_permission(monkeypatch):
    monkeypatch.setattr(registry_module, "alpaca_iex_configured", lambda: True)
    registry = ProviderRegistry(
        factories={
            "ibkr": lambda: _UnavailableIbkr(),
            "alpaca_iex": lambda: _ReadyIex(),
        }
    )

    blocked = registry.resolve_live_data_authority(INSTRUMENT)
    allowed = registry.resolve_live_data_authority(
        INSTRUMENT,
        allow_partial_market=True,
    )

    assert blocked.authoritative is False
    assert "PARTIAL_MARKET_NOT_AUTHORIZED" in blocked.reason_codes
    assert allowed.authoritative is True
    assert allowed.provider == "alpaca_iex"
    assert "PARTIAL_MARKET" in allowed.capabilities


def test_configured_but_disconnected_ibkr_descriptor_is_typed_unavailable():
    contract = _contract()
    transport = FakeIbkrTransport(contracts={"AAPL": [contract]})
    runtime = IbkrRuntime(transport=transport, enabled=True)
    registry = ProviderRegistry()
    registry._providers["ibkr"] = IbkrEquityProvider(runtime=runtime)

    descriptor = next(
        item for item in registry.descriptors() if item["provider"] == "ibkr"
    )

    assert descriptor["enabled"] is True
    assert descriptor["status"] == "unavailable"
    assert ProviderDescriptor.model_validate(descriptor).status == "unavailable"


def test_ibkr_authority_uses_bbo_freshness_not_last_trade_timestamp(monkeypatch):
    provider, transport, token, contract = _provider(monkeypatch)
    old_trade = NOW.replace(hour=13, minute=30)
    transport.emit(
        token,
        IbkrQuoteSnapshot(
            contract=contract,
            bid=Decimal("100.00"),
            ask=Decimal("100.02"),
            last=Decimal("100.01"),
            source_time=NOW,
            received_at=NOW,
            last_trade_at=old_trade,
            market_data_type="LIVE",
        ),
    )

    decision = provider.authority_decision(INSTRUMENT)

    assert decision.authoritative is True
    assert decision.quote_age_seconds == Decimal("0")


def test_ibkr_fresh_last_trade_does_not_rescue_stale_bbo(monkeypatch):
    provider, transport, token, contract = _provider(monkeypatch)
    transport.emit(
        token,
        IbkrQuoteSnapshot(
            contract=contract,
            bid=Decimal("100.00"),
            ask=Decimal("100.02"),
            last=Decimal("100.01"),
            source_time=NOW - timedelta(seconds=6),
            received_at=NOW,
            last_trade_at=NOW,
            market_data_type="LIVE",
        ),
    )

    decision = provider.authority_decision(INSTRUMENT)

    assert decision.authoritative is False
    assert decision.health == "STALE"
    assert "IBKR_QUOTE_STALE" in decision.reason_codes


def test_ibkr_crossed_bbo_fails_closed(monkeypatch):
    provider, transport, token, contract = _provider(monkeypatch)
    transport.emit(
        token,
        IbkrQuoteSnapshot(
            contract=contract,
            bid=Decimal("100.03"),
            ask=Decimal("100.02"),
            last=Decimal("100.025"),
            source_time=NOW,
            received_at=NOW,
            market_data_type="LIVE",
        ),
    )

    decision = provider.authority_decision(INSTRUMENT)

    assert decision.authoritative is False
    assert decision.health == "ERROR"
    assert decision.reason_codes == ("IBKR_CROSSED_OR_INVALID_BBO",)


def test_ibkr_reconnect_requires_fresh_new_socket_quote_before_live_authority(monkeypatch):
    provider, transport, token, contract = _provider(monkeypatch)
    transport.emit(
        token,
        IbkrQuoteSnapshot(
            contract=contract,
            bid=Decimal("100.00"),
            ask=Decimal("100.02"),
            last=Decimal("100.01"),
            source_time=NOW,
            received_at=NOW,
            market_data_type="LIVE",
        ),
    )
    assert provider.authority_decision(INSTRUMENT).authoritative is True

    transport.disconnect()
    disconnected = provider.authority_decision(INSTRUMENT)
    assert disconnected.authoritative is False
    assert disconnected.health == "DISCONNECTED"

    provider.runtime.connect()
    before_fresh_quote = provider.authority_decision(INSTRUMENT)
    assert before_fresh_quote.authoritative is False
    assert before_fresh_quote.reason_codes == ("IBKR_QUOTE_MISSING",)

    new_token = provider.subscribe_quote(INSTRUMENT, lambda snapshot: None)
    assert new_token != token
    transport.emit(
        new_token,
        IbkrQuoteSnapshot(
            contract=contract,
            bid=Decimal("100.01"),
            ask=Decimal("100.03"),
            last=Decimal("100.02"),
            source_time=NOW,
            received_at=NOW,
            market_data_type="LIVE",
        ),
    )

    restored = provider.authority_decision(INSTRUMENT)
    assert restored.authoritative is True
    assert restored.health == "READY"


def test_execution_observation_falls_back_to_an_authoritative_ibkr_quote_when_iex_has_none(monkeypatch):
    from app.apps.trading.providers.errors import ProviderContractError

    monkeypatch.setattr(registry_module, "alpaca_iex_configured", lambda: True)

    class _NoIexQuote:
        def execution_observation(self, instrument_id, policy=None, cancellation=None):
            raise ProviderContractError("Alpaca IEX snapshot is missing executable prices")

    class _AuthoritativeIbkr(_UnavailableIbkr):
        def __init__(self, authoritative: bool) -> None:
            self.authoritative = authoritative

        def authority_decision(self, instrument_id):
            return super().authority_decision(instrument_id).model_copy(update={"authoritative": self.authoritative})

        def live_observation(self, instrument_id, *, policy=None):
            binding = next(item for item in bindings_for_instrument(instrument_id) if item.provider == "ibkr")
            observation = ExecutionObservation(
                instrument_id=instrument_id, binding_id=binding.binding_id, provider="ibkr",
                bid=Decimal("10"), ask=Decimal("10.01"), last=Decimal("10.005"),
                source_time=NOW, received_at=NOW, session="regular", freshness_mode="live",
                market_data_type="LIVE", live_entitled=True,
            )
            return assess_execution_observation(observation, policy, binding_purpose="LIVE_DATA")

    def registry(authoritative: bool) -> ProviderRegistry:
        return ProviderRegistry(factories={
            "alpaca_iex": lambda: _NoIexQuote(),
            "ibkr": lambda: _AuthoritativeIbkr(authoritative),
        })

    observation = registry(True).execution_observation(INSTRUMENT)
    assert observation.provider == "ibkr"  # attributed to its real source
    assert observation.paper_fill_eligible is True
    assert observation.broker_execution_authorized is False  # LIVE_DATA never authorizes orders

    try:
        registry(False).execution_observation(INSTRUMENT)
    except ProviderContractError as exc:
        assert "missing executable prices" in str(exc)  # without IBKR authority: the IEX error stands
    else:
        raise AssertionError("no fallback without IBKR live-data authority")
