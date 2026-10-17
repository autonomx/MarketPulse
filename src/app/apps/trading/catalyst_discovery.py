from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from .catalyst_evidence import CatalystEvidence, capture_catalyst_evidence
from .providers.errors import ProviderContractError
from .providers.http_runtime import ProviderHttpRuntime


YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
_ALLOWED_CAPTURE_SKEW_SECONDS = 120


def _news_id(instrument_id: str, published_at: datetime, locator: str, title: str) -> str:
    payload = "|".join((instrument_id, published_at.isoformat(), locator, title))
    return "yahoo-news-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def discover_yahoo_catalyst_headlines(
    *,
    instrument_id: str,
    symbol: str,
    evaluation_time: datetime,
    lookback_hours: int = 72,
    max_items: int = 10,
    runtime: ProviderHttpRuntime | None = None,
) -> tuple[CatalystEvidence, ...]:
    """Capture current, timestamped Yahoo headline evidence for one candidate.

    The endpoint is intentionally current-only. A later Yahoo search must never
    be used to reconstruct historical catalyst evidence for a backtest. Only
    headlines published at or before ``evaluation_time`` and within the
    configured lookback are accepted. The captured headline/publisher text is
    evidence, not an LLM conclusion and never authorizes an order by itself.
    """
    if evaluation_time.tzinfo is None:
        raise ValueError("evaluation_time must be timezone-aware")
    if not symbol.strip():
        raise ValueError("symbol is required")
    if lookback_hours < 1 or lookback_hours > 168:
        raise ValueError("lookback_hours must be between 1 and 168")
    if max_items < 1 or max_items > 25:
        raise ValueError("max_items must be between 1 and 25")

    evaluation = evaluation_time.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if abs((now - evaluation).total_seconds()) > _ALLOWED_CAPTURE_SKEW_SECONDS:
        raise ValueError("Yahoo catalyst capture is current-only; historical research must reuse frozen evidence")

    active_runtime = runtime or ProviderHttpRuntime("yahoo_catalyst_discovery", max_concurrency=2)
    response = active_runtime.get(
        YAHOO_SEARCH_URL,
        params={
            "q": symbol.strip(),
            "quotesCount": 1,
            "newsCount": max_items,
            "enableFuzzyQuery": "false",
        },
        headers={"User-Agent": "Mozilla/5.0 Omnix local research"},
        timeout=20,
    )
    received_at = datetime.now(timezone.utc)
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise ProviderContractError("Yahoo returned invalid catalyst search JSON") from exc
    if not isinstance(payload, dict):
        raise ProviderContractError("Yahoo catalyst search payload is malformed")
    raw_news = payload.get("news") or []
    if not isinstance(raw_news, list):
        raise ProviderContractError("Yahoo catalyst news payload is malformed")

    cutoff = evaluation - timedelta(hours=lookback_hours)
    captured: list[CatalystEvidence] = []
    seen: set[str] = set()
    for item in raw_news:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        raw_timestamp = item.get("providerPublishTime")
        if not title or raw_timestamp is None:
            continue
        try:
            published_at = datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            continue
        if published_at > evaluation or published_at < cutoff:
            continue
        publisher = str(item.get("publisher") or "Yahoo Finance source").strip()
        link = str(item.get("link") or "").strip()
        uuid = str(item.get("uuid") or "").strip()
        locator = link or (f"yahoo:news:{uuid}" if uuid else f"yahoo:search:{symbol}:{int(raw_timestamp)}")
        evidence_id = _news_id(instrument_id, published_at, locator, title)
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        text = f"{title}. Publisher: {publisher}."
        captured.append(
            capture_catalyst_evidence(
                evidence_id=evidence_id,
                instrument_id=instrument_id,
                source_type="news",
                source_locator=locator,
                published_at=published_at,
                captured_at=received_at,
                headline=title,
                raw_text=text,
                facts={
                    "provider": "yahoo",
                    "publisher": publisher,
                    "symbol": symbol.strip(),
                    "evidence_scope": "headline",
                },
            )
        )

    captured.sort(key=lambda item: (item.published_at, item.evidence_id), reverse=True)
    return tuple(captured)
