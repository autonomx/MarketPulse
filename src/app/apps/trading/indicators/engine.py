from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Sequence


CORE_INDICATOR_FORMULA_VERSION = "omnix-indicators-v2"


def _values(values: Iterable[Decimal | float | int | str]) -> list[Decimal]:
    return [value if isinstance(value, Decimal) else Decimal(str(value)) for value in values]


def _period(period: int) -> int:
    clean = int(period)
    if clean < 1:
        raise ValueError("Indicator period must be positive")
    return clean


def simple_moving_average(values: Iterable[Decimal | float | int | str], period: int) -> list[Decimal]:
    data = _values(values)
    clean_period = _period(period)
    if len(data) < clean_period:
        return []
    running = sum(data[:clean_period], Decimal("0"))
    result = [running / clean_period]
    for index in range(clean_period, len(data)):
        running += data[index] - data[index - clean_period]
        result.append(running / clean_period)
    return result


def exponential_moving_average(values: Iterable[Decimal | float | int | str], period: int) -> list[Decimal]:
    data = _values(values)
    clean_period = _period(period)
    if len(data) < clean_period:
        return []
    average = sum(data[:clean_period], Decimal("0")) / clean_period
    multiplier = Decimal("2") / Decimal(clean_period + 1)
    result = [average]
    for value in data[clean_period:]:
        average = (value - average) * multiplier + average
        result.append(average)
    return result


def relative_strength_index(values: Iterable[Decimal | float | int | str], period: int) -> list[Decimal]:
    data = _values(values)
    clean_period = _period(period)
    if len(data) <= clean_period:
        return []
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for previous, current in zip(data, data[1:]):
        change = current - previous
        gains.append(max(change, Decimal("0")))
        losses.append(max(-change, Decimal("0")))
    average_gain = sum(gains[:clean_period], Decimal("0")) / clean_period
    average_loss = sum(losses[:clean_period], Decimal("0")) / clean_period

    def value() -> Decimal:
        if average_loss == 0:
            return Decimal("100")
        strength = average_gain / average_loss
        return Decimal("100") - Decimal("100") / (Decimal("1") + strength)

    result = [value()]
    for gain, loss in zip(gains[clean_period:], losses[clean_period:]):
        average_gain = (average_gain * (clean_period - 1) + gain) / clean_period
        average_loss = (average_loss * (clean_period - 1) + loss) / clean_period
        result.append(value())
    return result


def stochastic_rsi(
    values: Iterable[Decimal | float | int | str],
    period: int,
    smoothing: int = 3,
    signal: int = 3,
) -> list[tuple[Decimal, Decimal]]:
    """Return Stoch RSI %K/%D pairs using the web indicator's calculation."""
    clean_period = _period(period)
    clean_smoothing = _period(smoothing)
    clean_signal = _period(signal)
    rsi_values = relative_strength_index(values, clean_period)
    if len(rsi_values) < clean_period:
        return []

    raw: list[Decimal] = []
    for index in range(len(rsi_values) - clean_period + 1):
        window = rsi_values[index : index + clean_period]
        minimum = min(window)
        maximum = max(window)
        current = rsi_values[index + clean_period - 1]
        raw.append(
            Decimal("50")
            if maximum == minimum
            else max(
                Decimal("0"),
                min(Decimal("100"), (current - minimum) / (maximum - minimum) * Decimal("100")),
            )
        )

    k_values = simple_moving_average(raw, clean_smoothing)
    d_values = simple_moving_average(k_values, clean_signal)
    return [
        (k_values[index + clean_signal - 1], value)
        for index, value in enumerate(d_values)
    ]


def bollinger_bands(
    values: Iterable[Decimal | float | int | str],
    period: int,
    standard_deviations: Decimal | float | int | str = Decimal("2"),
) -> list[tuple[Decimal, Decimal, Decimal]]:
    data = _values(values)
    clean_period = _period(period)
    multiplier = Decimal(str(standard_deviations))
    if multiplier <= 0:
        raise ValueError("Bollinger deviation must be positive")
    if len(data) < clean_period:
        return []
    result: list[tuple[Decimal, Decimal, Decimal]] = []
    for index in range(clean_period - 1, len(data)):
        window = data[index - clean_period + 1 : index + 1]
        middle = sum(window, Decimal("0")) / clean_period
        variance = sum(((value - middle) ** 2 for value in window), Decimal("0")) / clean_period
        deviation = variance.sqrt() * multiplier
        result.append((middle, middle + deviation, middle - deviation))
    return result


def average_true_range(
    highs: Iterable[Decimal | float | int | str],
    lows: Iterable[Decimal | float | int | str],
    closes: Iterable[Decimal | float | int | str],
    period: int,
) -> list[Decimal]:
    high_values = _values(highs)
    low_values = _values(lows)
    close_values = _values(closes)
    if not (len(high_values) == len(low_values) == len(close_values)):
        raise ValueError("ATR inputs must have equal length")
    clean_period = _period(period)
    ranges = [
        high - low
        if index == 0
        else max(high - low, abs(high - close_values[index - 1]), abs(low - close_values[index - 1]))
        for index, (high, low) in enumerate(zip(high_values, low_values))
    ]
    if len(ranges) < clean_period:
        return []
    average = sum(ranges[:clean_period], Decimal("0")) / clean_period
    result = [average]
    for value in ranges[clean_period:]:
        average = (average * (clean_period - 1) + value) / clean_period
        result.append(average)
    return result


def moving_average_convergence_divergence(
    values: Iterable[Decimal | float | int | str],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> list[tuple[Decimal, Decimal, Decimal]]:
    data = _values(values)
    fast = _period(fast_period)
    slow = _period(slow_period)
    signal = _period(signal_period)
    if fast >= slow:
        raise ValueError("MACD fast period must be smaller than slow period")
    fast_values = exponential_moving_average(data, fast)
    slow_values = exponential_moving_average(data, slow)
    if not slow_values:
        return []
    macd_values = [
        fast_values[index + slow - fast] - slow_value
        for index, slow_value in enumerate(slow_values)
    ]
    signal_values = exponential_moving_average(macd_values, signal)
    if not signal_values:
        return []
    return [
        (macd_values[index + signal - 1], signal_value, macd_values[index + signal - 1] - signal_value)
        for index, signal_value in enumerate(signal_values)
    ]


def anchored_volume_weighted_average_price(
    highs: Sequence[Decimal | float | int | str],
    lows: Sequence[Decimal | float | int | str],
    closes: Sequence[Decimal | float | int | str],
    volumes: Sequence[Decimal | float | int | str],
    anchor_index: int = 0,
) -> list[Decimal]:
    high_values = _values(highs)
    low_values = _values(lows)
    close_values = _values(closes)
    volume_values = _values(volumes)
    if not (len(high_values) == len(low_values) == len(close_values) == len(volume_values)):
        raise ValueError("VWAP inputs must have equal length")
    if anchor_index < 0 or anchor_index >= len(close_values):
        return []
    cumulative_price_volume = Decimal("0")
    cumulative_volume = Decimal("0")
    result: list[Decimal] = []
    for high, low, close, volume in zip(
        high_values[anchor_index:],
        low_values[anchor_index:],
        close_values[anchor_index:],
        volume_values[anchor_index:],
    ):
        typical = (high + low + close) / Decimal("3")
        cumulative_price_volume += typical * volume
        cumulative_volume += volume
        result.append(typical if cumulative_volume == 0 else cumulative_price_volume / cumulative_volume)
    return result
