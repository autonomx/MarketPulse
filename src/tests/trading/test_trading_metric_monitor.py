from __future__ import annotations

from app.apps.trading.metric_monitor import TradingMetricMonitor, trading_liquidation_collector_enabled


class BufferStub:
    def __init__(self) -> None:
        self.started: list[str] = []

    def ensure_started(self, symbol: str) -> None:
        self.started.append(symbol)

    def is_collecting(self, symbol: str) -> bool:
        return symbol in self.started


class ServiceStub:
    def __init__(self) -> None:
        self.binance = type("BinanceStub", (), {"liquidation_buffer": BufferStub()})()


def test_metric_monitor_starts_all_catalogued_binance_force_order_streams() -> None:
    service = ServiceStub()
    monitor = TradingMetricMonitor(service=service)  # type: ignore[arg-type]

    monitor.start()

    assert monitor.started_symbols
    assert "BTCUSDT" in monitor.started_symbols
    assert set(service.binance.liquidation_buffer.started) == set(monitor.started_symbols)
    assert all(monitor.diagnostics()["collecting"].values())


def test_metric_monitor_is_disabled_by_default_in_legacy_tests(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    monkeypatch.delenv("OMNIX_TRADING_LIQUIDATION_COLLECTOR_IN_TESTS", raising=False)

    assert trading_liquidation_collector_enabled() is False
