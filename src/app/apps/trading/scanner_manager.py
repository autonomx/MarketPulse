from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from .scanner import (
    AsyncScannerCancellation,
    TradingScannerRun,
    execute_scanner,
)
from .scanner_repository import (
    ScannerRepositoryFactory,
    default_scanner_repository,
)
from .service import TradingMarketDataService, default_market_data_service
from typing import Any


class TradingScannerManager:
    def __init__(
        self,
        *,
        repository_factory: ScannerRepositoryFactory = default_scanner_repository,
        market_service_factory=default_market_data_service,
    ) -> None:
        self.repository_factory = repository_factory
        self.market_service_factory = market_service_factory
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancellations: dict[str, AsyncScannerCancellation] = {}

    async def start_run(self, scanner_id: str) -> TradingScannerRun:
        repository = self.repository_factory()
        definition = await asyncio.to_thread(repository.get_definition, scanner_id)
        if definition is None:
            raise ValueError(f"scanner_not_found: {scanner_id}")
        if not definition.enabled:
            raise ValueError(f"scanner_disabled: {scanner_id}")
        run_id = f"scan-{uuid4().hex}"
        run = TradingScannerRun(
            run_id=run_id,
            scanner_id=scanner_id,
            status="queued",
            universe_count=len(definition.instrument_ids),
            definition_snapshot=definition.model_dump(mode="json"),
        )
        persisted = await asyncio.to_thread(repository.create_run, run)
        cancellation = AsyncScannerCancellation()
        self._cancellations[run_id] = cancellation
        self._tasks[run_id] = asyncio.create_task(
            self._execute_run(definition, persisted, cancellation)
        )
        return persisted

    async def cancel_run(self, run_id: str) -> None:
        cancellation = self._cancellations.get(run_id)
        if cancellation is not None:
            cancellation.set()
        await asyncio.to_thread(
            self.repository_factory().request_cancellation,
            run_id,
        )

    async def wait_for_run(self, run_id: str) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            await task

    def diagnostics(self) -> dict[str, Any]:
        return {
            "active_run_count": len(self._tasks),
            "active_run_ids": sorted(self._tasks),
        }

    async def _execute_run(
        self,
        definition,
        run: TradingScannerRun,
        cancellation: AsyncScannerCancellation,
    ) -> None:
        repository = self.repository_factory()
        started_at = datetime.now(timezone.utc)
        await asyncio.to_thread(
            repository.update_run,
            run.run_id,
            status="running",
            completed_count=0,
            matched_count=0,
            started_at=started_at,
        )
        try:
            service: TradingMarketDataService = self.market_service_factory()
            summary = await execute_scanner(
                definition,
                run.run_id,
                service.bars,
                cancellation,
            )
            if summary.status == "completed":
                await asyncio.to_thread(
                    repository.replace_results,
                    run.run_id,
                    summary.results,
                )
            await asyncio.to_thread(
                repository.update_run,
                run.run_id,
                status=summary.status,
                completed_count=summary.completed_count,
                matched_count=len(summary.results),
                finished_at=datetime.now(timezone.utc),
                error_message=summary.error_message,
            )
        except asyncio.CancelledError:
            cancellation.set()
            await asyncio.to_thread(
                repository.update_run,
                run.run_id,
                status="cancelled",
                completed_count=0,
                matched_count=0,
                finished_at=datetime.now(timezone.utc),
                error_message="scanner task cancelled",
            )
            raise
        except Exception as exc:
            await asyncio.to_thread(
                repository.update_run,
                run.run_id,
                status="failed",
                completed_count=0,
                matched_count=0,
                finished_at=datetime.now(timezone.utc),
                error_message=f"{type(exc).__name__}: {exc}",
            )
        finally:
            self._tasks.pop(run.run_id, None)
            self._cancellations.pop(run.run_id, None)


_default_manager: TradingScannerManager | None = None
_default_lock = Lock()


def default_scanner_manager() -> TradingScannerManager:
    global _default_manager
    if _default_manager is None:
        with _default_lock:
            if _default_manager is None:
                _default_manager = TradingScannerManager()
    return _default_manager
