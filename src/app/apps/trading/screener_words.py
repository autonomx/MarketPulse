"""A screen from words (TVP-9.4): the research model proposes screener rules for a sentence; the user edits and runs them.

The model's answer is only a proposal. Every rule goes through the scanner's own validation
(``TradingScannerRule``: metrics, operators, registry indicators); what doesn't validate is dropped and reported, and
nothing runs until the user runs the screen. The model never sees market data or the user's symbols.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, cast

from pydantic import BaseModel, Field, ValidationError

from app.prompts import prompt_template
from app.providers.base import ChatMessage
from app.caching.bounded_cache import bounded_lru_cache

from .alert_conditions import IndicatorSource, indicator_output_profile
from .indicators.registry import server_indicator, server_indicator_ids
from .research.market_research import _call_provider, _json_payload, default_research_provider
from .scanner import TradingScannerRule

logger = logging.getLogger(__name__)

MAX_REQUEST_CHARS = 500
MAX_PROPOSED_RULES = 10
# The screener panel's intervals.
INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")

SCREEN_FROM_WORDS_TEMPLATE = prompt_template(
    "trading.screener_from_words",
    "2",
    """You turn a trader's description of a stock or crypto screen into screener rules. Answer with one JSON object and nothing else:
{"interval": one of "1m","5m","15m","1h","4h","1d", "rules": [rule, ...], "unsupported": [text, ...], "notes": text}

Each rule: {"metric": metric, "operator": "gt"|"gte"|"lt"|"lte", "threshold": number, "period": integer 1-500, "lookback_bars": integer 1-499, "role": "filter"|"column", "source": null or indicator}
Metrics (on the chosen interval's bars, at the latest bar):
- "close": the last price. "volume": the last bar's volume.
- "percent_change": percent change over lookback_bars bars (1 = since the previous bar).
- "sma", "ema": moving average of the close over period bars. "rsi": RSI over period. "atr": ATR over period.
- "relative_volume": the last bar's volume divided by the average of the previous period bars.
- "gap_percent": percent gap between the last open and the previous close.
- "high_distance_percent", "low_distance_percent": percent distance of the close below the highest high / above the lowest low of the last period bars (period 252 on "1d" is the 52-week high/low).
- "indicator": any other indicator line; "source" is {"kind": "indicator", "indicator_id": id, "inputs": {"period": n}, "output": output key}, chosen from the indicator list in the request.
- US stocks' fundamentals from SEC filings, at the last price: "market_cap" (USD), "pe_ratio", "ps_ratio", "pb_ratio", "eps_ttm", "revenue_growth" (percent, year over year), "net_margin" (percent); "period" and "lookback_bars" are ignored for these.
"filter" rules must all match; "column" rules are only shown. Use at least one filter. Thresholds are plain numbers: percentages as percent (5 means 5%), prices in the quote currency.
Comparing two indicators (for example close above its 200-day average) is not possible: say so in "unsupported" and add the nearest filters you can.
Put every part of the description you could not express in "unsupported". Never invent symbols, metrics or indicators.""",
)


class ScreenProposal(BaseModel):
    """What the model proposed, after validation: rules to show in the editor, not a screen that runs."""

    interval: str = "1d"
    rules: list[TradingScannerRule] = Field(default_factory=list)
    # Parts of the request the rules don't express, and rules the scanner refused.
    unsupported: list[str] = Field(default_factory=list)
    notes: str = ""
    provider: str = ""


def _output_keys(indicator_id: str, inputs: dict[str, Any] | None = None) -> list[str]:
    source = IndicatorSource(kind="indicator", indicator_id=indicator_id, inputs=cast(Any, inputs or {}), output="_")
    return [key for key, _first in indicator_output_profile(source)]


@bounded_lru_cache(max_entries=1, ttl_seconds=86_400.0)
def _indicator_catalog() -> tuple[dict[str, Any], ...]:
    """Indicator ids, names and output keys at default inputs, for the model to choose from (computed once)."""
    catalog: list[dict[str, Any]] = []
    for indicator_id in server_indicator_ids():
        indicator = server_indicator(indicator_id)
        try:
            keys = _output_keys(indicator_id)
        except Exception:  # an indicator that needs inputs the catalog doesn't give is left out
            logger.debug("suppressed error in %s", "_indicator_catalog", exc_info=True)
            continue
        if keys and indicator is not None:
            catalog.append({"id": indicator_id, "name": indicator.name, "outputs": keys[:6]})
    return tuple(catalog)


def _fit_output(source: dict[str, Any]) -> dict[str, Any]:
    """An indicator source whose output key the model wrote for other inputs, moved to the key at the same position."""
    try:
        indicator_id = str(source.get("indicator_id"))
        inputs = source.get("inputs") if isinstance(source.get("inputs"), dict) else {}
        keys = _output_keys(indicator_id, inputs)
        if source.get("output") in keys:
            return source
        defaults = _output_keys(indicator_id)
        position = defaults.index(str(source.get("output"))) if source.get("output") in defaults else 0
        return {**source, "output": keys[min(position, len(keys) - 1)]} if keys else source
    except Exception:
        return source


def propose_screen(
    text: str,
    *,
    provider_factory: Callable[[], Any] = default_research_provider,
    model: str | None = None,
    catalog: list[dict[str, Any]] | None = None,
) -> ScreenProposal:
    """Rules for ``text``; ValueError when the request is empty or nothing usable came back."""
    request = text.strip()
    if not request:
        raise ValueError("describe the screen in a sentence")
    if len(request) > MAX_REQUEST_CHARS:
        raise ValueError(f"describe the screen in at most {MAX_REQUEST_CHARS} characters")
    context = {"description": request, "indicators": catalog if catalog is not None else list(_indicator_catalog())}
    messages = [
        ChatMessage(role="system", content=SCREEN_FROM_WORDS_TEMPLATE.text),
        ChatMessage(role="user", content=json.dumps(context, separators=(",", ":"))),
    ]
    provider = provider_factory()
    payload = _json_payload(_call_provider(provider, messages, model))
    return validated_proposal(payload, provider=str(getattr(provider, "name", "") or type(provider).__name__))


def validated_proposal(payload: dict[str, Any], *, provider: str = "") -> ScreenProposal:
    """The model's JSON as a proposal: each rule validated by the scanner, the rest reported."""
    interval = payload.get("interval") if payload.get("interval") in INTERVALS else "1d"
    unsupported = [str(item)[:300] for item in payload.get("unsupported") or [] if str(item).strip()][:10]
    rules: list[TradingScannerRule] = []
    raw_rules: list[Any] = cast(list[Any], payload["rules"]) if isinstance(payload.get("rules"), list) else []
    for position, raw in enumerate(raw_rules[: MAX_PROPOSED_RULES * 2]):
        if not isinstance(raw, dict) or len(rules) >= MAX_PROPOSED_RULES:
            continue
        candidate = {key: value for key, value in raw.items() if key in TradingScannerRule.model_fields and key != "rule_id"}
        if candidate.get("source") is None:
            candidate.pop("source", None)
        elif isinstance(candidate["source"], dict):
            candidate["source"] = _fit_output(candidate["source"])
        try:
            rules.append(TradingScannerRule.model_validate({**candidate, "rule_id": f"proposed-{position + 1}"}))
        except ValidationError as error:
            first = error.errors()[0]
            unsupported.append(f"a {raw.get('metric', 'proposed')} rule the screener can't use: {first.get('msg', 'invalid')}")
    if not any(rule.role == "filter" for rule in rules):
        raise ValueError("that description didn't give any screener filters; try naming a price, change, volume or indicator condition")
    notes = str(payload.get("notes") or "")[:500]
    return ScreenProposal(interval=str(interval), rules=rules, unsupported=unsupported, notes=notes, provider=provider)


__all__ = ["SCREEN_FROM_WORDS_TEMPLATE", "ScreenProposal", "propose_screen", "validated_proposal"]
