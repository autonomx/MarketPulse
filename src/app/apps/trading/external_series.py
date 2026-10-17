"""External-data indicators on the server (TVP-0.2): a metric series as values at bar indexes, for alerts and the screener.

The series is the one the chart draws (``metric_sources.load_metric``). A bar takes the latest point *before it
closes* (``point.time < bar.end_time``): a daily breadth value stamped at the session close reaches the day's bar and
later bars, never an earlier intraday bar, and a point at a bar's end belongs to the next bar. A series of one point
(a snapshot such as analyst targets or dividend yield) holds on every bar, as the chart draws it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .indicators.external import external_available_for, external_indicator
from .metric_data import MarketMetricResponse

logger = logging.getLogger(__name__)

MetricLoader = Callable[[str, str, str, int, "datetime | None"], MarketMetricResponse]

# The most metric points a series is fetched with (the ``/metrics`` API's limit).
_METRIC_LIMIT = 1_500


def align_points(points: Sequence[Any], bars: Sequence[Any]) -> dict[int, Decimal]:
    """Each bar's value: the latest point before the bar's end; one point is every bar's value."""
    ordered = sorted(((point.time.astimezone(timezone.utc), Decimal(point.value)) for point in points), key=lambda item: item[0])
    if not ordered or not bars:
        return {}
    if len(ordered) == 1:
        return {index: ordered[0][1] for index in range(len(bars))}
    values: dict[int, Decimal] = {}
    cursor = -1
    for index, bar in enumerate(bars):
        end = bar.end_time.astimezone(timezone.utc)
        while cursor + 1 < len(ordered) and ordered[cursor + 1][0] < end:
            cursor += 1
        if cursor >= 0:
            values[index] = ordered[cursor][1]
    return values


def _default_loader(metric: str, instrument_id: str, interval: str, limit: int, end_time: datetime | None) -> MarketMetricResponse:
    from .metric_sources import load_metric

    return load_metric(metric, instrument_id, interval, limit, end_time)


class ExternalSeries:
    """External indicator values for one instrument and interval; each metric is fetched once per bar list."""

    def __init__(self, instrument_id: str, interval: str, loader: MetricLoader = _default_loader) -> None:
        self.instrument_id = instrument_id
        self.interval = interval
        self.loader = loader
        self._responses: dict[tuple[str, int, datetime | None], MarketMetricResponse | None] = {}

    def __call__(self, indicator_id: str, output: str, bars: Sequence[Any]) -> dict[int, Decimal]:
        definition = external_indicator(indicator_id)
        if definition is None or output not in definition.output_keys or not bars:
            return {}
        if not external_available_for(indicator_id, self.instrument_id):
            return {}
        end_time = bars[-1].end_time
        key = (definition.metric, len(bars), end_time)
        if key not in self._responses:
            try:
                self._responses[key] = self.loader(definition.metric, self.instrument_id, self.interval, min(_METRIC_LIMIT, max(1, len(bars))), end_time)
            except Exception:  # no data for this pass: the condition is false, as for an indicator still warming up
                logger.warning("external_indicator_series_failed", extra={"metric": definition.metric}, exc_info=True)
                self._responses[key] = None
        response = self._responses[key]
        if response is None:
            return {}
        series_key = output[len(indicator_id) + 1:]
        series = next((item for item in response.series if item.key == series_key), None)
        return {} if series is None else align_points(series.points, bars)


__all__ = ["ExternalSeries", "MetricLoader", "align_points"]
