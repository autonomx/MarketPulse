from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from .breadth import BreadthRepository, default_breadth_repository
from .fundamental_metric_data import (
    YahooAnalystMetricAdapter,
    default_yahoo_analyst_metric_adapter,
)
from .metric_data import (
    MarketMetricResponse,
    TradingMetricDataService,
    default_metric_data_service,
)

logger = logging.getLogger(__name__)


def create_trading_metric_router(
    metric_service_factory: Callable[[], TradingMetricDataService] = default_metric_data_service,
    yahoo_analyst_factory: Callable[[], YahooAnalystMetricAdapter] = default_yahoo_analyst_metric_adapter,
    breadth_repository_factory: Callable[[], BreadthRepository] = default_breadth_repository,
) -> APIRouter:
    router = APIRouter(prefix="/api/trading", tags=["trading"])

    # This is an internal chart transport, not a stable generated-client API.
    # The indicator scheduler consumes the typed payload directly, so keep it
    # out of the shared public gateway contract until metric subscriptions are
    # promoted to a versioned external API.
    @router.get("/metrics", response_model=MarketMetricResponse)
    def metric_series(
        instrument_id: str = Query(min_length=3, max_length=200),
        metric: str = Query(min_length=3, max_length=120),
        interval: str = Query(default="1h", max_length=16),
        limit: int = Query(default=500, ge=1, le=1_500),
        end_time: datetime | None = Query(default=None),
    ) -> MarketMetricResponse:
        from .metric_sources import load_metric

        try:
            return load_metric(
                metric,
                instrument_id,
                interval,
                limit,
                end_time,
                metric_service_factory=metric_service_factory,
                yahoo_analyst_factory=yahoo_analyst_factory,
                breadth_repository_factory=breadth_repository_factory,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            # The provider error can carry URLs and credentials: log it, return the code (WP-10.5).
            logger.warning("metric_data_failed", exc_info=True)
            raise HTTPException(
                status_code=502,
                detail={"code": "metric_data_failed", "message": "The metric data request failed."},
            ) from exc

    return router
