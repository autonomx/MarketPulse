"""Causal and market research APIs owned by the research package."""
from __future__ import annotations

from .market_research import (
    MAX_RESEARCH_BARS,
    MAX_RESEARCH_PROMPT_CHARS,
    MAX_RESEARCH_QUESTION_CHARS,
    MarketResearchRequest,
    MarketResearchResult,
    MarketServiceFactory,
    ProviderFactory,
    ProviderLike,
    ResearchSource,
    _call_provider as _call_provider,
    _json_payload as _json_payload,
    _provider_identity as _provider_identity,
    _provider_text as _provider_text,
    build_research_context,
    default_research_provider,
    generate_market_research,
)

from .contracts import (  # noqa: E402
    IssuerIdentity,
    StrategyResearchFeatures,
    TradingEvidence,
    TradingFactSet,
    TradingResearchReport,
    TradingResearchRequest,
)

__all__ = [
    "MAX_RESEARCH_BARS",
    "MAX_RESEARCH_PROMPT_CHARS",
    "MAX_RESEARCH_QUESTION_CHARS",
    "ResearchSource",
    "MarketResearchRequest",
    "MarketResearchResult",
    "ProviderLike",
    "ProviderFactory",
    "MarketServiceFactory",
    "default_research_provider",
    "build_research_context",
    "generate_market_research",
    "IssuerIdentity",
    "StrategyResearchFeatures",
    "TradingEvidence",
    "TradingFactSet",
    "TradingResearchReport",
    "TradingResearchRequest",
]
