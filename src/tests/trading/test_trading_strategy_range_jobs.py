from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.jobs.handlers import JobExecutionContext
from app.jobs.models import JobProgress, JobStatus
from app.apps.trading.strategy_api import create_trading_strategy_router
from app.apps.trading.strategy_range_backtest import StrategyRangeBacktestRequest
from app.apps.trading.strategy_range_backtest_jobs import (
    STRATEGY_RANGE_BACKTEST_JOB,
    StrategyRangeBacktestJobInput,
    _execute_range_backtest_job,
    create_strategy_range_backtest_request,
)


def test_range_backtest_job_contract_is_registered_as_cpu_work() -> None:
    request = StrategyRangeBacktestRequest(
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 7),
    )

    job_request = create_strategy_range_backtest_request(
        "strategy-1",
        request,
        total_sessions=3,
    )

    assert STRATEGY_RANGE_BACKTEST_JOB.type == "trading.strategy.range-backtest"
    assert job_request.module == "trading"
    assert job_request.type == STRATEGY_RANGE_BACKTEST_JOB.type
    assert job_request.resource_class.value == "cpu"
    assert StrategyRangeBacktestJobInput.model_validate(job_request.input_payload).strategy_id == "strategy-1"


def test_range_backtest_route_submits_and_reads_durable_job_state() -> None:
    jobs = MagicMock()
    job = SimpleNamespace(
        id="range-job-1",
        module="trading",
        type="trading.strategy.range-backtest",
        input_payload={"strategy_id": "strategy-1", "total_sessions": 3},
        status=JobStatus.RUNNING,
        progress=JobProgress(current=1, total=3, message="2026-01-06"),
        output_refs=[],
        error=None,
    )
    jobs.create_job.return_value = job
    jobs.get_job.return_value = job
    router = create_trading_strategy_router(job_store_factory=lambda: jobs)
    post = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/trading/strategies/{strategy_id}/backtest/range"
    )
    get = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/trading/strategies/{strategy_id}/backtest/range/{run_id}"
    )
    request = StrategyRangeBacktestRequest(
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 7),
    )

    accepted = post("strategy-1", request)
    progress = get("strategy-1", "range-job-1")

    assert accepted.run_id == "range-job-1"
    assert accepted.total_sessions == 3
    jobs.create_job.assert_called_once()
    assert jobs.create_job.call_args.args[0].type == "trading.strategy.range-backtest"
    assert progress.status == "running"
    assert progress.completed_sessions == 1
    assert progress.current_session == date(2026, 1, 6)


def test_range_backtest_job_persists_progress_and_result(monkeypatch) -> None:
    from app.apps.trading import strategy_api

    jobs = MagicMock()
    jobs.complete_job.return_value = SimpleNamespace(status=JobStatus.COMPLETED)

    async def execute(_strategy_id, request, run_id, _repo, _catalyst_repo, progress):
        assert run_id == "range-job-2"
        assert request.start_date == date(2026, 1, 5)
        progress(1, 2, date(2026, 1, 5))
        return SimpleNamespace(model_dump=lambda *, mode: {"strategy_id": "strategy-1", "mode": mode})

    monkeypatch.setattr(strategy_api, "_execute_range_backtest", execute)
    monkeypatch.setattr(strategy_api, "default_strategy_repository", lambda: object())
    monkeypatch.setattr(strategy_api, "default_catalyst_repository", lambda: object())
    request = StrategyRangeBacktestRequest(
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 6),
    )
    job = SimpleNamespace(
        id="range-job-2",
        input_payload={
            "strategy_id": "strategy-1",
            "request": request.model_dump(mode="json"),
            "total_sessions": 2,
        },
    )
    context = JobExecutionContext(job_store=jobs)

    result = _execute_range_backtest_job(context, job)

    assert result.status == JobStatus.COMPLETED
    jobs.update_progress.assert_called_once_with(
        "range-job-2",
        current=1,
        total=2,
        message="2026-01-05",
    )
    completed = jobs.complete_job.call_args.args[1]
    assert completed.output_refs == [{
        "kind": "strategy_range_backtest_result",
        "strategy_id": "strategy-1",
        "result": {"strategy_id": "strategy-1", "mode": "json"},
    }]
