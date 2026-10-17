from __future__ import annotations

import time

from app.apps.trading.providers.errors import ProviderRateLimitedError
from scripts import run_trading_strategy_structure_sweep as diagnostic


def _patient_rate_limit_retry(label: str, function):
    # Alpaca's historical endpoint can enforce a sustained account-level request
    # ceiling after broad-listing reconstruction. Keep this diagnostic patient
    # without changing provider/runtime semantics used by production code.
    delays = (30, 60, 120, 180)
    for attempt in range(len(delays) + 1):
        try:
            return function()
        except ProviderRateLimitedError:
            if attempt >= len(delays):
                raise
            delay = delays[attempt]
            print(f"{label}: Alpaca rate limited the request; retrying after {delay}s", flush=True)
            time.sleep(delay)
    raise AssertionError("unreachable")


def main() -> int:
    diagnostic._with_rate_limit_retry = _patient_rate_limit_retry
    return diagnostic.main()


if __name__ == "__main__":
    raise SystemExit(main())
