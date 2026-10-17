from __future__ import annotations

import time

import pytest

from app.apps.trading import strategy_ai_shadow_provider as provider
from app.apps.trading.strategy_ai_shadow_v2 import AIShadowV2Analyzer


def test_v2_terminal_transport_failure_trips_circuit_after_one_attempt(monkeypatch) -> None:
    circuit = provider._CIRCUIT
    circuit.success()
    calls = 0

    def fail_once(self, *, arm, rows):
        nonlocal calls
        calls += 1
        raise TimeoutError("Timed out waiting for Codex app-server")

    monkeypatch.setattr(AIShadowV2Analyzer, "_assess_core", fail_once)
    analyzer = AIShadowV2Analyzer(provider_factory=lambda: None)

    try:
        with pytest.raises(provider.AIShadowReliabilityError) as exc_info:
            analyzer.assess(
                arm="full_session_control",
                rows=[{"instrument_id": "equity:NASDAQ:ACVA"}],
            )

        assert calls == 1
        assert exc_info.value.code == "ai_shadow_v2_transport_exhausted"
        assert circuit.failure_count == 1
        assert circuit.is_open(time.monotonic()) is True
    finally:
        circuit.success()
