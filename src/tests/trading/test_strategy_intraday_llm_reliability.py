from __future__ import annotations

import time

import pytest

from app.apps.trading import strategy_ai_shadow_provider as provider
from app.apps.trading import strategy_intraday_llm as intraday


def test_default_intraday_transport_failure_trips_shared_circuit_once(monkeypatch) -> None:
    circuit = provider._CIRCUIT
    circuit.success()
    calls = 0

    def fail_once(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("Timed out waiting for Codex turn completion")

    monkeypatch.setattr(intraday.IntradayLLMAnalyzer, "_assess_core", fail_once)
    analyzer = intraday.IntradayLLMAnalyzer()

    try:
        with pytest.raises(provider.AIShadowReliabilityError) as exc_info:
            analyzer.assess([], ranks={})

        assert calls == 1
        assert exc_info.value.code == "intraday_llm_transport_exhausted"
        assert circuit.failure_count == 1
        assert circuit.is_open(time.monotonic()) is True
    finally:
        circuit.success()


def test_injected_intraday_provider_is_not_coupled_to_shared_circuit(monkeypatch) -> None:
    sentinel = object()

    def succeeds(self, *args, **kwargs):
        return sentinel

    monkeypatch.setattr(intraday.IntradayLLMAnalyzer, "_assess_core", succeeds)
    analyzer = intraday.IntradayLLMAnalyzer(provider_factory=lambda: object())
    circuit = provider._CIRCUIT
    circuit.success()
    circuit.failure_count = 1
    circuit.open_until_monotonic = time.monotonic() + 60
    try:
        assert analyzer.assess([], ranks={}) is sentinel
    finally:
        circuit.success()
