from __future__ import annotations


class TradingProviderError(RuntimeError):
    """Base class for normalized market-data provider failures."""


class ProviderFallbackEligibleError(TradingProviderError):
    """A transport, rate-limit, availability, or no-data failure eligible for fallback."""


class ProviderUnavailableError(ProviderFallbackEligibleError):
    pass


class ProviderRateLimitedError(ProviderFallbackEligibleError):
    pass


class ProviderDataUnavailableError(ProviderFallbackEligibleError):
    pass


class ProviderCancelledError(TradingProviderError):
    pass


class ProviderContractError(TradingProviderError):
    """A provider payload or Omnix adapter contract defect. Never silently fallback."""
