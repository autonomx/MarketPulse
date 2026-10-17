from __future__ import annotations

"""GitHub Actions wrapper for rate-limit-resilient historical strategy sweeps.

The production provider runtime keeps short retries suitable for interactive use.
Historical Actions sweeps are different: one Alpaca IEX batch can span many chunks,
and restarting the whole reconstruction after a late HTTP 429 wastes the requests
that already succeeded. This wrapper gives each individual historical request a
longer bounded backoff so the batch resumes at the failed chunk.
"""

from app.apps.trading.providers.http_runtime import ProviderHttpRuntime as _BaseProviderHttpRuntime

import app.apps.trading.historical_gapper_reconstruction as _reconstruction
import app.apps.trading.strategy_historical_bars as _historical_bars
import scripts.run_trading_strategy_liquidity_sweep as _sweep


class _ActionsHistoricalRuntime(_BaseProviderHttpRuntime):
    def __init__(
        self,
        provider_id: str,
        *,
        session=None,
        max_concurrency: int = 1,
        max_attempts: int = 8,
        initial_backoff_seconds: float = 2.0,
    ) -> None:
        # Keep historical collection serial and retry the same request/chunk on
        # 429 instead of letting the outer range sweep restart from the beginning.
        super().__init__(
            provider_id,
            session=session,
            max_concurrency=1,
            max_attempts=max(8, int(max_attempts)),
            initial_backoff_seconds=max(2.0, float(initial_backoff_seconds)),
        )


# These module globals are resolved when the reconstructor/runtime is instantiated,
# so patching them here affects only this CLI process and never production Omnix.
_reconstruction.ProviderHttpRuntime = _ActionsHistoricalRuntime
_historical_bars.ProviderHttpRuntime = _ActionsHistoricalRuntime
_sweep.ProviderHttpRuntime = _ActionsHistoricalRuntime


if __name__ == "__main__":
    raise SystemExit(_sweep.main())
