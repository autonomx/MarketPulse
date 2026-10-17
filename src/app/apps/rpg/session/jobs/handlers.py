"""RPG durable job contracts."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.jobs.models import JobRecord


class RpgTurnJobInput(BaseModel):
    model_config = ConfigDict(extra="allow")
    command: str
    provider_id: str | None = None
    model_id: str | None = None


class RpgReportJobInput(BaseModel):
    model_config = ConfigDict(extra="allow")


class RpgNarrationJobInput(BaseModel):
    """One deferred narration request (``rpg.narration``, WP-5.6)."""

    model_config = ConfigDict(extra="allow")
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    interaction_id: str = ""
    tick: int = 0
    job_kind: str = "player_turn"
    narration_request: dict[str, Any] = Field(default_factory=dict)


def execute_rpg_turn_job(job_store: Any, job: JobRecord) -> JobRecord:
    from app.apps.rpg.session.jobs.turn_executor import execute_inline_feature_job

    return execute_inline_feature_job(job_store, job)


def execute_rpg_report_job(job_store: Any, job: JobRecord) -> JobRecord:
    from app.apps.rpg.session.jobs.last10_report import execute_rpg_last10_report_job

    return execute_rpg_last10_report_job(job_store, job)


def execute_rpg_narration_job(job_store: Any, job: JobRecord) -> JobRecord:
    from app.apps.rpg.session.jobs.narration_job import execute_rpg_narration_job as execute

    return execute(job_store, job)
