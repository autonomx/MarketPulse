from __future__ import annotations

"""API for the machine-readable prospective-gap experiment authority."""

import shutil
import sys
from datetime import date, datetime, timezone
from typing import Any, Callable, Literal, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.errors import LegacyPersistenceRetired
from app.security.permissions import requires
from app.security.provider_secret_store import save_trading_provider_secrets, trading_provider_credential_sources

from .prospective_gap_repository import ProspectiveGapSessionLedger
from .prospective_gap_runtime import (
    ConfirmationRunResult,
    PostcloseRunResult,
    PremarketFreezeRequest,
    PremarketFreezeResult,
    ProspectiveGapRuntime,
    default_prospective_gap_runtime,
)


RuntimeFactory = Callable[[], ProspectiveGapRuntime]


class ConfirmationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_date: date
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PostcloseRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_date: date
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MarkdownProjectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    markdown: str
    authority: str = "prospective-gap StrategyEvent ledger"


class ResearchRepositoryCredentialStatus(BaseModel):
    """Where research handoffs are imported from (MarketPulse S-7)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: str
    ref: str
    transport: Literal["gh", "http"]
    token_configured: bool
    token_masked: str
    token_source: Literal["environment", "os_protected_store", "missing"]
    token_editable: bool


class ResearchRepositoryCredentialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str | None = Field(default=None, max_length=500)
    clear_token: bool = False


def _github_credential_status() -> ResearchRepositoryCredentialStatus:
    from .prospective_gap_inputs import github_repository, github_token

    repository, ref = github_repository()
    token = github_token()
    source = trading_provider_credential_sources("prospective_gap_github")["token"]
    return ResearchRepositoryCredentialStatus(
        repository=repository,
        ref=ref,
        transport="gh" if shutil.which("gh") else "http",
        token_configured=bool(token),
        token_masked=f"***{token[-4:]}" if len(token) > 4 else ("****" if token else ""),
        token_source=cast(Any, source),
        token_editable=sys.platform == "win32" and source != "environment",
    )


def create_trading_prospective_gap_router(
    runtime_factory: RuntimeFactory = default_prospective_gap_runtime,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/trading/prospective-gap",
        tags=["trading-prospective-gap"],
    )

    @router.get(
        "/github-credentials",
        response_model=ResearchRepositoryCredentialStatus,
        openapi_extra=requires("trading:control"),
    )
    def github_credentials() -> ResearchRepositoryCredentialStatus:
        """The research repository and whether a read token is set; the token itself is never returned (S-7)."""
        return _github_credential_status()

    @router.put(
        "/github-credentials",
        response_model=ResearchRepositoryCredentialStatus,
        openapi_extra=requires("trading:control"),
    )
    def update_github_credentials(request: ResearchRepositoryCredentialUpdate) -> ResearchRepositoryCredentialStatus:
        updates: dict[str, str | None] = {}
        if request.token is not None:
            updates["token"] = request.token
        if request.clear_token:
            updates["token"] = ""
        if updates:
            try:
                save_trading_provider_secrets("prospective_gap_github", updates)
            except LegacyPersistenceRetired as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _github_credential_status()

    @router.post("/premarket/freeze", response_model=PremarketFreezeResult)
    def freeze_premarket(request: PremarketFreezeRequest) -> PremarketFreezeResult:
        return runtime_factory().freeze_premarket(request)

    @router.post("/confirmation/run", response_model=ConfirmationRunResult)
    def run_confirmation(request: ConfirmationRunRequest) -> ConfirmationRunResult:
        return runtime_factory().run_confirmation(
            session_date=request.session_date,
            evaluated_at=request.evaluated_at,
        )

    @router.post("/postclose/finalize", response_model=PostcloseRunResult)
    def finalize_postclose(request: PostcloseRunRequest) -> PostcloseRunResult:
        return runtime_factory().finalize_postclose(
            session_date=request.session_date,
            evaluated_at=request.evaluated_at,
        )

    @router.get("/session/{session_date}", response_model=ProspectiveGapSessionLedger)
    def session_ledger(session_date: date) -> ProspectiveGapSessionLedger:
        return runtime_factory().session_ledger(session_date)

    @router.get(
        "/session/{session_date}/markdown",
        response_model=MarkdownProjectionResponse,
    )
    def markdown_projection(session_date: date) -> MarkdownProjectionResponse:
        return MarkdownProjectionResponse(
            session_date=session_date,
            markdown=runtime_factory().render_markdown(session_date),
        )

    return router


__all__ = [
    "ConfirmationRunRequest",
    "ResearchRepositoryCredentialStatus",
    "ResearchRepositoryCredentialUpdate",
    "MarkdownProjectionResponse",
    "PostcloseRunRequest",
    "create_trading_prospective_gap_router",
]
