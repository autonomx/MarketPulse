"""One way to load a chart metric (TVP-0.2): the ``/metrics`` API, server alerts and the screener read the same series.

Market breadth comes from Omnix's own table (TVP-6.6), Yahoo analyst targets from the analyst adapter, everything else
from ``TradingMetricDataService``; provider-native units are normalised to the units the chart shows.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from .breadth import BREADTH_METRICS, BreadthRepository, breadth_metric, default_breadth_repository
from .fundamental_metric_data import YahooAnalystMetricAdapter, default_yahoo_analyst_metric_adapter
from .metric_data import MarketMetricResponse, TradingMetricDataService, default_metric_data_service


def normalize_metric_units(response: MarketMetricResponse) -> MarketMetricResponse:
    """Normalize provider-native units to the units exposed by the chart metric."""
    if response.metric != "binance.premium":
        return response
    return response.model_copy(
        update={
            "series": [
                series.model_copy(
                    update={
                        "points": [
                            point.model_copy(update={"value": point.value * Decimal("100")})
                            for point in series.points
                        ]
                    }
                )
                for series in response.series
            ]
        }
    )


def load_metric(
    metric: str,
    instrument_id: str,
    interval: str,
    limit: int,
    end_time: datetime | None = None,
    *,
    metric_service_factory: Callable[[], TradingMetricDataService] = default_metric_data_service,
    yahoo_analyst_factory: Callable[[], YahooAnalystMetricAdapter] = default_yahoo_analyst_metric_adapter,
    breadth_repository_factory: Callable[[], BreadthRepository] = default_breadth_repository,
) -> MarketMetricResponse:
    """A metric series as the chart draws it. Raises ``ValueError`` for an unknown metric or bad arguments."""
    if metric in BREADTH_METRICS:
        # Market breadth (TVP-6.6): the same for every chart.
        response = breadth_metric(metric, breadth_repository_factory(), instrument_id=instrument_id, interval=interval, limit=limit, end_time=end_time)
    elif metric in {"yahoo.analyst_price_forecast", "yahoo.price_target"}:
        response = yahoo_analyst_factory().analyst_targets(instrument_id, interval, limit, end_time=end_time)
        if metric == "yahoo.price_target":
            response = response.model_copy(update={"metric": metric})
    else:
        response = metric_service_factory().metric(instrument_id, metric, interval, limit, end_time=end_time)
    return normalize_metric_units(response)


__all__ = ["load_metric", "normalize_metric_units"]
