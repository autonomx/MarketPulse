"""Read-only trading market-data adapter for governed Agent evidence."""
from __future__ import annotations

from app.platform.assistant_tools.contracts import AssistantToolRequest, AssistantToolResult
from app.capabilities.registry import Capability, capability


def run_trading_tool_request(
    request: AssistantToolRequest,
    *,
    provider=None,
) -> AssistantToolResult:
    if request.action_id != "trading.market_quote":
        return AssistantToolResult(
            tool_id=request.tool_id,
            action_id=request.action_id,
            session_id=request.session_id,
            state_changed=False,
            result_summary="Trading action is not available.",
            error="trading_action_not_available",
        )

    ticker = str(
        request.input.get("ticker")
        or request.input.get("symbol")
        or ""
    ).strip().upper().removeprefix("$")
    if not ticker:
        return AssistantToolResult(
            tool_id=request.tool_id,
            action_id=request.action_id,
            session_id=request.session_id,
            state_changed=False,
            result_summary="A ticker is required.",
            error="trading_ticker_required",
        )

    try:
        from app.apps.trading.catalog import search_instruments
        from app.apps.trading.models import AssetClass
        from app.apps.trading.providers.alpaca_iex import AlpacaIexExecutionProvider

        candidates = [
            item
            for item in search_instruments(ticker)
            if item.asset_class is AssetClass.EQUITY
            and item.display_symbol.upper() == ticker
        ]
        if len(candidates) != 1:
            return AssistantToolResult(
                tool_id=request.tool_id,
                action_id=request.action_id,
                session_id=request.session_id,
                state_changed=False,
                result_summary=f"Could not resolve {ticker} to exactly one canonical equity instrument.",
                error="trading_instrument_not_resolved",
            )
        runtime = provider or AlpacaIexExecutionProvider()
        observation = runtime.execution_observation(candidates[0].instrument_id)
    except Exception as exc:
        return AssistantToolResult(
            tool_id=request.tool_id,
            action_id=request.action_id,
            session_id=request.session_id,
            state_changed=False,
            result_summary=f"Market quote lookup failed for {ticker}.",
            error=f"{type(exc).__name__}: {exc}"[:1000],
        )

    output = observation.model_dump(mode="json")
    output.update({
        "ticker": ticker,
        "source_class": "market_quote",
        "provider": observation.provider,
        "authoritative_read_only": True,
    })
    return AssistantToolResult(
        tool_id=request.tool_id,
        action_id=request.action_id,
        session_id=request.session_id,
        state_changed=False,
        result_summary=(
            f"{ticker} quote from {observation.provider}: "
            f"last {observation.last}, bid {observation.bid}, ask {observation.ask}."
        ),
        output=output,
    )


class TradingMarketDataTool:
    """Trading's read-only market-data tool. It has no order or broker mutation authority."""

    tool_id = "trading"
    display_name = "Trading Market Data"
    description = "Read authoritative market data without order or broker mutation authority."
    account_label = "Alpaca IEX"

    def capabilities(self) -> tuple[Capability, ...]:
        return (
            capability("trading.market_quote", "Read market quote", "Read a current read-only US equity quote from the configured authoritative market-data provider. This capability cannot place or modify orders.", zone="broker", effect="read", network=True, credentials=True, connection=True, provider="Alpaca IEX", category="trading", assistant=True, hermes=True, input_schema={"ticker": "US equity ticker symbol"}),
            capability("market.status", "Read market status", "Read authoritative current market-session status when a market-status provider is configured.", zone="broker", effect="read", network=True, credentials=True, connection=True, enabled=False, provider="Market Status", category="trading", assistant=True, hermes=True),
        )

    def default_enabled(self) -> bool:
        try:
            from app.apps.trading.providers.alpaca_iex import alpaca_iex_configured

            return alpaca_iex_configured()
        except Exception:
            return False

    def run(self, request: AssistantToolRequest) -> AssistantToolResult:
        return run_trading_tool_request(request)


class TradingSecurityInstruments:
    """Trading's canonical-instrument lookup for agent evidence subjects."""

    def equity_instrument_id(self, ticker: str) -> str | None:
        from app.apps.trading.catalog import search_instruments
        from app.apps.trading.models import AssetClass

        candidates = [
            item for item in search_instruments(ticker)
            if item.asset_class is AssetClass.EQUITY and item.display_symbol.upper() == ticker
        ]
        return candidates[0].instrument_id if len(candidates) == 1 else None
