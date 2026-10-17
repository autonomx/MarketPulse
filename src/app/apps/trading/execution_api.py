from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.security.provider_secret_store import (
    load_trading_provider_secrets,
    save_trading_provider_secrets,
    trading_provider_credential_sources,
)
from app.errors import LegacyPersistenceRetired

from .execution import ExecutionObservation
from .service import TradingMarketDataService, default_market_data_service

logger = logging.getLogger(__name__)


class AlpacaIexCredentialStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["alpaca_iex"] = "alpaca_iex"
    configured: bool
    api_key_id_masked: str = ""
    api_key_source: Literal["environment", "os_protected_store", "missing"]
    secret_key_source: Literal["environment", "os_protected_store", "missing"]
    api_key_editable: bool
    secret_key_editable: bool
    storage: str


class AlpacaIexCredentialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key_id: str | None = Field(default=None, max_length=500)
    secret_key: str | None = Field(default=None, max_length=500)
    clear_api_key_id: bool = False
    clear_secret_key: bool = False


def _mask_key(value: str) -> str:
    clean = value.strip()
    if not clean:
        return ""
    if len(clean) <= 4:
        return "****"
    return f"***{clean[-4:]}"


def _alpaca_credential_status() -> AlpacaIexCredentialStatus:
    credentials = load_trading_provider_secrets().get("alpaca_iex") or {}
    sources = trading_provider_credential_sources("alpaca_iex")
    api_key = str(credentials.get("api_key_id") or "")
    secret = str(credentials.get("secret_key") or "")
    windows_store = sys.platform == "win32"
    return AlpacaIexCredentialStatus(
        configured=bool(api_key and secret),
        api_key_id_masked=_mask_key(api_key),
        api_key_source=cast(Any, sources["api_key_id"]),
        secret_key_source=cast(Any, sources["secret_key"]),
        api_key_editable=windows_store and sources["api_key_id"] != "environment",
        secret_key_editable=windows_store and sources["secret_key"] != "environment",
        storage="Windows DPAPI user store" if windows_store else "environment only",
    )


def create_trading_execution_router(
    market_service_factory=default_market_data_service,
) -> APIRouter:
    router = APIRouter(prefix="/api/trading/execution", tags=["trading-execution"])

    @router.get("/providers/alpaca-iex/credentials", response_model=AlpacaIexCredentialStatus)
    async def alpaca_iex_credentials() -> AlpacaIexCredentialStatus:
        return await asyncio.to_thread(_alpaca_credential_status)

    @router.put("/providers/alpaca-iex/credentials", response_model=AlpacaIexCredentialStatus)
    async def update_alpaca_iex_credentials(
        request: AlpacaIexCredentialUpdate,
    ) -> AlpacaIexCredentialStatus:
        updates: dict[str, str | None] = {}
        if request.api_key_id is not None:
            updates["api_key_id"] = request.api_key_id
        if request.secret_key is not None:
            updates["secret_key"] = request.secret_key
        if request.clear_api_key_id:
            updates["api_key_id"] = ""
        if request.clear_secret_key:
            updates["secret_key"] = ""
        if not updates:
            return await asyncio.to_thread(_alpaca_credential_status)
        try:
            await asyncio.to_thread(save_trading_provider_secrets, "alpaca_iex", updates)
            return await asyncio.to_thread(_alpaca_credential_status)
        except LegacyPersistenceRetired as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/observation", response_model=ExecutionObservation)
    async def observation(
        instrument_id: str = Query(min_length=3, max_length=200),
        binding_id: str | None = Query(default=None, max_length=240),
    ) -> ExecutionObservation:
        try:
            service: TradingMarketDataService = market_service_factory()
            return await asyncio.to_thread(
                service.execution_observation,
                instrument_id,
                binding_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            # The provider error can carry URLs and credentials: log it, return the code (WP-10.5).
            logger.warning("execution_market_data_failed", exc_info=True)
            raise HTTPException(
                status_code=502,
                detail={"code": "execution_market_data_failed", "message": "The execution provider's market data request failed."},
            ) from exc

    return router
