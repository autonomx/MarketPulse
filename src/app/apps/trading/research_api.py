from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from .research import (
    MarketResearchRequest,
    MarketResearchResult,
    ProviderFactory,
    default_research_provider,
    generate_market_research,
)
from .service import TradingMarketDataService, default_market_data_service

logger = logging.getLogger(__name__)


MarketServiceFactory = Callable[[], TradingMarketDataService]


def create_trading_research_router(
    market_service_factory: MarketServiceFactory = default_market_data_service,
    provider_factory: ProviderFactory = default_research_provider,
) -> APIRouter:
    router = APIRouter(prefix="/api/trading/research", tags=["trading-research"])

    @router.post("", response_model=MarketResearchResult)
    async def create_research(request: MarketResearchRequest):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    generate_market_research,
                    request,
                    market_service_factory=market_service_factory,
                    provider_factory=provider_factory,
                ),
                timeout=90,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail={"code": "research_timeout", "message": "The registered provider exceeded 90 seconds."},
            ) from exc
        except (json.JSONDecodeError, ValidationError) as exc:
            # The parse error quotes the provider's reply: log it, return the code (WP-10.5).
            logger.warning("invalid_research_output", exc_info=True)
            raise HTTPException(
                status_code=502,
                detail={"code": "invalid_research_output", "message": "The research provider returned an unreadable result."},
            ) from exc
        except ValueError as exc:
            message = str(exc)
            provider_failure = any(
                marker in message
                for marker in (
                    "provider_",
                    "registered_provider",
                    "research_output",
                )
            )
            if provider_failure:
                logger.warning("research_provider_failed", exc_info=True)
            raise HTTPException(
                status_code=502 if provider_failure else 422,
                detail={
                    "code": "research_provider_failed" if provider_failure else "invalid_research_request",
                    # A provider's reply stays in the log; validation messages are Omnix's own.
                    "message": "The research provider request failed." if provider_failure else message,
                },
            ) from exc
        except RuntimeError as exc:
            logger.warning("research_provider_unavailable", exc_info=True)
            raise HTTPException(
                status_code=503,
                detail={"code": "research_provider_unavailable", "message": "The research provider is unavailable."},
            ) from exc
        except Exception as exc:
            logger.warning("research_failed", exc_info=True)
            raise HTTPException(
                status_code=502,
                detail={"code": "research_failed", "message": "The research provider request failed."},
            ) from exc

    return router
