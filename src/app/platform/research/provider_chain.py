"""Ordered provider fallback for web research search clients."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

SUPPORTED_RESEARCH_PROVIDERS = ("brave", "tavily", "playwright", "duckduckgo")
DEFAULT_RESEARCH_PROVIDER = "brave"
DEFAULT_RESEARCH_PROVIDER_FALLBACKS = ("playwright", "duckduckgo")


def normalize_provider_chain(
    primary: str | None,
    fallbacks: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Return a stable, deduplicated provider chain with only supported providers."""

    ordered: list[str] = []
    if primary:
        ordered.append(str(primary).strip().lower())
    ordered.extend(str(value or "").strip().lower() for value in (fallbacks or ()))
    result: list[str] = []
    for provider in ordered:
        if provider not in SUPPORTED_RESEARCH_PROVIDERS or provider in result:
            continue
        result.append(provider)
    return tuple(result or (DEFAULT_RESEARCH_PROVIDER,))


def provider_requires_credential(provider: str) -> bool:
    return provider in {"brave", "tavily"}


def _provider_api_key(provider: str) -> str:
    if not provider_requires_credential(provider):
        return ""
    from app.security.provider_secret_store import load_research_provider_secrets

    return str(load_research_provider_secrets().get(provider) or "").strip()


def provider_credential_configured(provider: str) -> bool:
    return not provider_requires_credential(provider) or bool(_provider_api_key(provider))


def _default_client_factory(**kwargs: Any):
    # Import lazily: Settings imports this module, while assistant-context routing imports
    # Settings. Keeping the transport dependency here avoids a package-level import cycle.
    from app.platform.research.web_search import WebSearchClient

    provider = str(kwargs.get("provider") or "").strip().lower()
    if provider_requires_credential(provider) and "api_key" not in kwargs:
        kwargs["api_key"] = _provider_api_key(provider)
    return WebSearchClient(**kwargs)


class ProviderFallbackSearchClient:
    """Try configured search providers in order until one returns usable results.

    Credential-backed providers without a configured key are skipped. Provider failures and
    empty result sets fall through to the next configured provider. The public ``provider``
    attribute is updated to the provider that actually produced the returned result set so
    existing diagnostics and source manifests continue to record the real retrieval source.
    """

    def __init__(
        self,
        *,
        providers: Iterable[str],
        timeout_seconds: float,
        client_factory: Callable[..., Any] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.providers = normalize_provider_chain(None, providers)
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.client_factory = client_factory or _default_client_factory
        self.monotonic = monotonic
        self.provider = self.providers[0]
        self.attempted_providers: list[str] = []
        self.provider_errors: dict[str, str] = {}

    def search(self, query: str, max_results: int):
        started = self.monotonic()
        saw_empty = False
        for provider in self.providers:
            if provider_requires_credential(provider) and not provider_credential_configured(provider):
                self.provider_errors[provider] = "credential_not_configured"
                continue
            remaining = self.timeout_seconds - (self.monotonic() - started)
            if remaining <= 0:
                self.provider_errors[provider] = "deadline_exhausted"
                break
            self.attempted_providers.append(provider)
            client_kwargs: dict[str, Any] = {
                "provider": provider,
                "timeout_seconds": remaining,
            }
            if provider_requires_credential(provider):
                client_kwargs["api_key"] = _provider_api_key(provider)
            try:
                client = self.client_factory(**client_kwargs)
            except TypeError:
                # Preserve compatibility with injected test/custom factories that only
                # accept provider and timeout, while the built-in WebSearchClient receives
                # credentials from the protected store above.
                client_kwargs.pop("api_key", None)
                try:
                    client = self.client_factory(**client_kwargs)
                except TypeError:
                    client = self.client_factory(provider=provider)
            try:
                items = client.search(query, max_results)
            except Exception as exc:  # provider boundary; fallback is intentional
                self.provider_errors[provider] = f"{type(exc).__name__}: {exc}"
                continue
            self.provider = provider
            if items:
                return items
            saw_empty = True
            self.provider_errors[provider] = "empty_result_set"

        if saw_empty and not any(
            value not in {"credential_not_configured", "empty_result_set"}
            for value in self.provider_errors.values()
        ):
            return []
        detail = "; ".join(
            f"{provider}={self.provider_errors.get(provider, 'not_attempted')}"
            for provider in self.providers
        )
        raise RuntimeError(f"Configured search provider chain was unavailable: {detail}")
