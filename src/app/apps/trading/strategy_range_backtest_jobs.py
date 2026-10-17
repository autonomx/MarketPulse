"""Durable strategy range-backtest job contracts and execution."""
from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.jobs.handlers import JobExecutionContext, JobHandlerSpec
from app.jobs.models import CompleteJobRequest, CreateJobRequest, JobRecord, ResourceClass

from .strategy_range_backtest import StrategyRangeBacktestRequest


class StrategyRangeBacktestJobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str = Field(min_length=1)
    request: StrategyRangeBacktestRequest
    total_sessions: int = Field(ge=1, le=252)


def create_strategy_range_backtest_request(
    strategy_id: str,
    request: StrategyRangeBacktestRequest,
    *,
    total_sessions: int,
) -> CreateJobRequest:
    payload = StrategyRangeBacktestJobInput(
        strategy_id=strategy_id,
        request=request,
        total_sessions=total_sessions,
    )
    return CreateJobRequest(
        module="trading",
        type="trading.strategy.range-backtest",
        resource_class=ResourceClass.CPU,
        priority=10,
        input_payload=payload.model_dump(mode="json"),
    )


def _execute_range_backtest_job(
    context: JobExecutionContext,
    job: JobRecord,
) -> JobRecord:
    payload = StrategyRangeBacktestJobInput.model_validate(job.input_payload or {})
    if context.cancelled():
        raise RuntimeError("strategy range backtest was cancelled before execution")

    from .strategy_api import (
        _execute_range_backtest,
        default_catalyst_repository,
        default_strategy_repository,
    )

    def report_progress(completed: int, total: int, session_date) -> None:
        if context.cancelled():
            raise RuntimeError("strategy range backtest was cancelled")
        context.job_store.update_progress(
            job.id,
            current=completed,
            total=total,
            message=session_date.isoformat(),
        )

    result = asyncio.run(
        _execute_range_backtest(
            payload.strategy_id,
            payload.request,
            job.id,
            default_strategy_repository,
            default_catalyst_repository,
            report_progress,
        )
    )
    completed = context.job_store.complete_job(
        job.id,
        CompleteJobRequest(
            output_refs=[
                {
                    "kind": "strategy_range_backtest_result",
                    "strategy_id": payload.strategy_id,
                    "result": result.model_dump(mode="json"),
                }
            ]
        ),
    )
    return completed or job


STRATEGY_RANGE_BACKTEST_JOB = JobHandlerSpec(
    type="trading.strategy.range-backtest",
    handler=_execute_range_backtest_job,
    input_model=StrategyRangeBacktestJobInput,
    resource_class=ResourceClass.CPU,
    timeout_seconds=3600,
    max_attempts=3,
)


__all__ = [
    "STRATEGY_RANGE_BACKTEST_JOB",
    "StrategyRangeBacktestJobInput",
    "create_strategy_range_backtest_request",
]
