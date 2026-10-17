from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.providers.base import ChatMessage

from ..indicators.engine import (
    CORE_INDICATOR_FORMULA_VERSION,
    exponential_moving_average,
    relative_strength_index,
    simple_moving_average,
)
from ..models import BarsResponse
from ..service import TradingMarketDataService, default_market_data_service
from app.prompts import prompt_template


GENERATE_MARKET_RESEARCH_TEMPLATE = prompt_template(
    'trading.research.market_research.generate_market_research', "1",
    (
        'You are the read-only Omnix market research assistant. Analyze only the supplied '
        'normalized data. Do not invent news, fundamentals, orders, positions, alerts, or future '
        'prices. Return one JSON object with exactly: summary (string), observations (1-8 '
        'strings), risks (1-8 strings), confidence (number from 0 to 1). Do not include markdown '
        'or extra keys.'
    ),
)



MAX_RESEARCH_BARS = 200
MAX_RESEARCH_PROMPT_CHARS = 24_000
MAX_RESEARCH_QUESTION_CHARS = 800


class ResearchSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    interval: str
    provider: str
    requested_binding_id: str | None = None
    resolved_binding_id: str
    dataset_fingerprint: str
    as_of: datetime
    freshness_mode: str
    formula_version: str = CORE_INDICATOR_FORMULA_VERSION
    bar_count: int = Field(ge=1, le=MAX_RESEARCH_BARS)


class MarketResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str = Field(min_length=3, max_length=200)
    binding_id: str | None = Field(default=None, max_length=240)
    interval: str = Field(default="1d", min_length=1, max_length=16)
    bar_limit: int = Field(default=120, ge=30, le=MAX_RESEARCH_BARS)
    question: str = Field(
        default="Summarize the current technical structure, notable levels, and principal risks.",
        min_length=1,
        max_length=MAX_RESEARCH_QUESTION_CHARS,
    )
    selected_levels: list[Decimal] = Field(default_factory=list, max_length=20)
    model: str | None = Field(default=None, max_length=200)


class MarketResearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2_000)
    observations: list[str] = Field(min_length=1, max_length=8)
    risks: list[str] = Field(min_length=1, max_length=8)
    confidence: Decimal = Field(ge=0, le=1)
    provider: str
    model: str
    source: ResearchSource
    read_only: Literal[True] = True
    disclaimer: Literal[
        "Research only. Not financial advice. No order was created or executed."
    ] = "Research only. Not financial advice. No order was created or executed."

    @model_validator(mode="after")
    def enforce_read_only_output_field_contract(self):
        forbidden_keys = {"order", "trade", "execute", "alert", "position", "quantity"}
        serialized_keys = set(self.model_dump().keys())
        if serialized_keys & forbidden_keys:
            raise ValueError("research output cannot contain execution fields")
        return self


class ProviderLike(Protocol):
    def chat_completion(self, messages, model=None, stream=False, **kwargs): ...


ProviderFactory = Callable[[], Any]
MarketServiceFactory = Callable[[], TradingMarketDataService]


def default_research_provider() -> Any:
    from app.providers import service as provider_service

    getter = getattr(provider_service, "get_provider", None)
    if not callable(getter):
        raise RuntimeError("omnix_provider_registry_unavailable")
    provider = getter()
    if provider is None:
        raise RuntimeError("omnix_provider_unavailable")
    return provider


def _last(values: list[Decimal]) -> Decimal | None:
    return values[-1] if values else None


def _decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def build_research_context(
    request: MarketResearchRequest,
    response: BarsResponse,
) -> tuple[dict[str, Any], ResearchSource]:
    finalized = [bar for bar in response.bars if bar.is_final][-request.bar_limit :]
    if len(finalized) < 30:
        raise ValueError("research_requires_at_least_30_finalized_bars")
    closes = [Decimal(bar.close) for bar in finalized]
    current = finalized[-1]
    previous = finalized[-2]
    change_percent = (
        Decimal("0")
        if previous.close == 0
        else (current.close / previous.close - Decimal("1")) * Decimal("100")
    )
    sma20 = _last(simple_moving_average(closes, 20))
    ema20 = _last(exponential_moving_average(closes, 20))
    rsi14 = _last(relative_strength_index(closes, 14))
    recent = finalized[-40:]
    context = {
        "instrument": response.instrument.model_dump(mode="json"),
        "interval": response.interval,
        "latest": {
            "time": current.end_time.isoformat(),
            "open": str(current.open),
            "high": str(current.high),
            "low": str(current.low),
            "close": str(current.close),
            "volume": str(current.volume),
            "change_percent": str(change_percent),
        },
        "indicators": {
            "formula_version": CORE_INDICATOR_FORMULA_VERSION,
            "sma_20": _decimal(sma20),
            "ema_20": _decimal(ema20),
            "rsi_14": _decimal(rsi14),
        },
        "selected_levels": [str(level) for level in request.selected_levels],
        "recent_finalized_bars": [
            {
                "time": bar.end_time.isoformat(),
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": str(bar.volume),
            }
            for bar in recent
        ],
        "provider_status": {
            "provider": response.binding.provider,
            "requested_binding": response.provenance.requested_binding,
            "resolved_binding": response.provenance.resolved_binding,
            "freshness_mode": response.provenance.freshness_mode,
            "history_complete": response.provenance.history_complete,
            "cached": response.provenance.cached,
            "as_of": response.provenance.as_of.isoformat(),
        },
    }
    source = ResearchSource(
        instrument_id=response.instrument.instrument_id,
        interval=response.interval,
        provider=response.binding.provider,
        requested_binding_id=request.binding_id,
        resolved_binding_id=response.binding.binding_id,
        dataset_fingerprint=response.provenance.dataset_fingerprint,
        as_of=response.provenance.as_of,
        freshness_mode=response.provenance.freshness_mode,
        bar_count=len(finalized),
    )
    return context, source


def _provider_identity(provider: Any, model_override: str | None) -> tuple[str, str]:
    provider_name = str(
        getattr(provider, "provider_name", None)
        or getattr(provider, "name", None)
        or provider.__class__.__name__
    )
    configured_model = (
        getattr(getattr(provider, "config", None), "model", None)
        or getattr(provider, "model", None)
        or getattr(provider, "model_name", None)
        or "configured-model"
    )
    return provider_name, str(model_override or configured_model)


def _provider_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(result, dict):
        for key in ("content", "text", "response"):
            value = result.get(key)
            if isinstance(value, str):
                return value
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
    raise ValueError("research_provider_returned_no_text")


def _call_provider(provider: Any, messages: list[ChatMessage], model: str | None) -> str:
    if hasattr(provider, "chat_completion") and callable(provider.chat_completion):
        return _provider_text(
            provider.chat_completion(
                messages,
                model=model,
                stream=False,
                temperature=0,
                max_tokens=1_200,
            )
        )
    plain_messages = [message.to_dict() for message in messages]
    if hasattr(provider, "chat") and callable(provider.chat):
        return _provider_text(provider.chat(messages=plain_messages, model=model))
    prompt = "\n\n".join(message.content for message in messages)
    if hasattr(provider, "complete") and callable(provider.complete):
        return _provider_text(provider.complete(prompt))
    if hasattr(provider, "generate") and callable(provider.generate):
        return _provider_text(provider.generate(prompt))
    raise ValueError("registered_provider_has_no_supported_chat_method")


def _json_payload(text: str) -> dict[str, Any]:
    """One JSON object from research output, via the structured-output parser."""
    from app.providers.structured.errors import StructuredDecodeError
    from app.providers.structured.parsing import decode_json_object

    try:
        return decode_json_object(text)
    except StructuredDecodeError as exc:
        if isinstance(exc.__cause__, json.JSONDecodeError):
            raise exc.__cause__ from None  # the API reports invalid_research_output
        raise ValueError("research_output_must_be_a_json_object") from exc


def generate_market_research(
    request: MarketResearchRequest,
    *,
    market_service_factory: MarketServiceFactory = default_market_data_service,
    provider_factory: ProviderFactory = default_research_provider,
) -> MarketResearchResult:
    response = market_service_factory().bars(
        request.instrument_id,
        request.interval,
        request.bar_limit,
        request.binding_id,
    )
    context, source = build_research_context(request, response)
    context_json = json.dumps(context, separators=(",", ":"), sort_keys=True)
    if len(context_json) > MAX_RESEARCH_PROMPT_CHARS:
        raise ValueError("research_context_exceeds_bounded_prompt_size")

    system_prompt = (
        GENERATE_MARKET_RESEARCH_TEMPLATE.text
    )
    user_prompt = json.dumps(
        {
            "question": request.question,
            "normalized_context": context,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    provider = provider_factory()
    provider_name, model_name = _provider_identity(provider, request.model)
    text = _call_provider(
        provider,
        [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_prompt),
        ],
        request.model,
    )
    payload = _json_payload(text)
    return MarketResearchResult.model_validate(
        {
            **payload,
            "provider": provider_name,
            "model": model_name,
            "source": source.model_dump(mode="json"),
            "read_only": True,
        }
    )
