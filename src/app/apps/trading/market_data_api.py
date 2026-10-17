from __future__ import annotations

import asyncio
import sys
from datetime import date
from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.security.provider_secret_store import (
    load_trading_provider_secrets,
    save_trading_provider_secrets,
    trading_provider_credential_sources,
)
from app.errors import LegacyPersistenceRetired
from app.apps.trading.ibkr_evidence import default_ibkr_evidence_store
from app.apps.trading.ibkr_settings import load_ibkr_settings, save_ibkr_settings
from app.apps.trading.providers.ibkr_runtime import default_ibkr_runtime
from app.apps.trading.service import default_market_data_service


class CoinMarketCapCredentialStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["coinmarketcap"] = "coinmarketcap"
    configured: bool
    api_key_masked: str = ""
    api_key_source: Literal["environment", "os_protected_store", "missing"]
    api_key_editable: bool
    storage: str


class ProviderKeyStatus(BaseModel):
    """An API key's status for providers configured by one key (FRED, TVP-10.5): never the key itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    configured: bool
    api_key_masked: str = ""
    api_key_source: Literal["environment", "os_protected_store", "missing"]
    api_key_editable: bool
    storage: str


class CoinMarketCapCredentialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str | None = Field(default=None, max_length=500)
    clear_api_key: bool = False


class IbkrSettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    monitor_enabled: bool
    host: str
    port: int = Field(ge=1, le=65_535)
    client_id: int = Field(ge=0, le=32_767)
    live_authority_enabled: bool
    recovery_authority_enabled: bool


class IbkrSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    monitor_enabled: bool | None = None
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65_535)
    client_id: int | None = Field(default=None, ge=0, le=32_767)
    live_authority_enabled: bool | None = None
    recovery_authority_enabled: bool | None = None


class IbkrSettingsStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["ibkr"] = "ibkr"
    settings: IbkrSettingsPayload
    settings_source: Literal["defaults", "environment", "omnix_settings", "runtime_arguments"]
    connection_status: Literal["disabled", "client_unavailable", "connected", "disconnected"]
    official_ibapi_available: bool
    connected: bool
    last_error: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


def _ibkr_status() -> IbkrSettingsStatus:
    runtime = default_ibkr_runtime()
    runtime.refresh_settings()
    settings, source = load_ibkr_settings()
    diagnostics = runtime.diagnostics()
    connected = bool(diagnostics.get("connected"))
    enabled = bool(diagnostics.get("enabled"))
    official_available = bool(diagnostics.get("official_ibapi_available"))
    if not enabled:
        connection_status: Literal["disabled", "client_unavailable", "connected", "disconnected"] = "disabled"
    elif not official_available and runtime.transport is None:
        connection_status = "client_unavailable"
    elif connected:
        connection_status = "connected"
    else:
        connection_status = "disconnected"
    return IbkrSettingsStatus(
        settings=IbkrSettingsPayload.model_validate(settings.as_dict()),
        settings_source=source,  # type: ignore[arg-type]
        connection_status=connection_status,
        official_ibapi_available=official_available,
        connected=connected,
        last_error=diagnostics.get("last_error") if isinstance(diagnostics.get("last_error"), str) else None,
        diagnostics=diagnostics,
    )


def _mask_key(value: str) -> str:
    clean = value.strip()
    if not clean:
        return ""
    if len(clean) <= 4:
        return "****"
    return f"***{clean[-4:]}"


def _credential_status() -> CoinMarketCapCredentialStatus:
    credentials = load_trading_provider_secrets().get("coinmarketcap") or {}
    sources = trading_provider_credential_sources("coinmarketcap")
    api_key = str(credentials.get("api_key") or "")
    return CoinMarketCapCredentialStatus(
        configured=bool(api_key),
        api_key_masked=_mask_key(api_key),
        api_key_source=cast(Any, sources["api_key"]),
        api_key_editable=sys.platform == "win32" and sources["api_key"] != "environment",
        storage="Windows DPAPI user store" if sys.platform == "win32" else "environment only",
    )


def _key_status(provider: str) -> ProviderKeyStatus:
    credentials = load_trading_provider_secrets().get(provider) or {}
    sources = trading_provider_credential_sources(provider)
    api_key = str(credentials.get("api_key") or "")
    return ProviderKeyStatus(
        provider=provider,
        configured=bool(api_key),
        api_key_masked=_mask_key(api_key),
        api_key_source=cast(Any, sources["api_key"]),
        api_key_editable=sys.platform == "win32" and sources["api_key"] != "environment",
        storage="Windows DPAPI user store" if sys.platform == "win32" else "environment only",
    )


def create_trading_market_data_router() -> APIRouter:
    router = APIRouter(prefix="/api/trading/market-data", tags=["trading-market-data"])

    @router.get("/yahoo-evidence/diagnostics")
    async def yahoo_evidence_diagnostics() -> dict[str, Any]:
        """Operator diagnostics for durable Yahoo evidence and gap recovery."""

        diagnostics = await asyncio.to_thread(default_market_data_service().diagnostics)
        yahoo = diagnostics.get("yahoo_hardening")
        return yahoo if isinstance(yahoo, dict) else {}

    @router.get("/yahoo-evidence/diagnostics/{session_date}")
    async def yahoo_evidence_session_diagnostics(
        session_date: date,
    ) -> dict[str, Any]:
        """Durable Yahoo repair/block metrics for one U.S.-equity session."""

        service = default_market_data_service()
        return await asyncio.to_thread(
            service.yahoo_evidence_store.session_diagnostics,
            session_date,
        )

    @router.get("/providers/ibkr/diagnostics")
    async def ibkr_diagnostics() -> dict[str, Any]:
        """Operator view of Gateway/client/rollout state without storing credentials."""

        service = default_market_data_service()
        provider = service.registry.provider("ibkr")
        return await asyncio.to_thread(provider.diagnostics)

    @router.get("/providers/ibkr/settings", response_model=IbkrSettingsStatus)
    async def ibkr_settings() -> IbkrSettingsStatus:
        """Return persisted connection settings and current Gateway status."""

        return await asyncio.to_thread(_ibkr_status)

    @router.put("/providers/ibkr/settings", response_model=IbkrSettingsStatus)
    async def update_ibkr_settings(request: IbkrSettingsUpdate) -> IbkrSettingsStatus:
        """Save non-secret IBKR settings; authentication remains in Gateway."""

        patch = request.model_dump(exclude_unset=True)
        if patch:
            await asyncio.to_thread(save_ibkr_settings, patch)
            await asyncio.to_thread(default_ibkr_runtime().refresh_settings)
        return await asyncio.to_thread(_ibkr_status)

    @router.get("/providers/ibkr/diagnostics/{session_date}")
    async def ibkr_session_diagnostics(session_date: date) -> dict[str, Any]:
        """Durable zero-authority IBKR quote/recovery soak metrics for one session."""

        return await asyncio.to_thread(
            default_ibkr_evidence_store().session_diagnostics,
            session_date,
        )

    @router.get("/providers/ibkr/authority/{instrument_id:path}")
    async def ibkr_authority(instrument_id: str) -> dict[str, Any]:
        """Explain per-contract IBKR LIVE_DATA authority for an already observed symbol."""

        service = default_market_data_service()
        try:
            decision = await asyncio.to_thread(
                service.registry.provider("ibkr").authority_decision,
                instrument_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return decision.model_dump(mode="json")

    @router.get("/providers/coinmarketcap/credentials", response_model=CoinMarketCapCredentialStatus)
    async def coinmarketcap_credentials() -> CoinMarketCapCredentialStatus:
        return await asyncio.to_thread(_credential_status)

    @router.put("/providers/coinmarketcap/credentials", response_model=CoinMarketCapCredentialStatus)
    async def update_coinmarketcap_credentials(
        request: CoinMarketCapCredentialUpdate,
    ) -> CoinMarketCapCredentialStatus:
        updates: dict[str, str | None] = {}
        if request.api_key is not None:
            updates["api_key"] = request.api_key
        if request.clear_api_key:
            updates["api_key"] = ""
        if not updates:
            return await asyncio.to_thread(_credential_status)
        try:
            await asyncio.to_thread(save_trading_provider_secrets, "coinmarketcap", updates)
            return await asyncio.to_thread(_credential_status)
        except LegacyPersistenceRetired as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/providers/fred/credentials", response_model=ProviderKeyStatus)
    async def fred_credentials() -> ProviderKeyStatus:
        return await asyncio.to_thread(_key_status, "fred")

    @router.put("/providers/fred/credentials", response_model=ProviderKeyStatus)
    async def update_fred_credentials(request: CoinMarketCapCredentialUpdate) -> ProviderKeyStatus:
        updates: dict[str, str | None] = {}
        if request.api_key is not None:
            updates["api_key"] = request.api_key
        if request.clear_api_key:
            updates["api_key"] = ""
        if updates:
            try:
                await asyncio.to_thread(save_trading_provider_secrets, "fred", updates)
            except LegacyPersistenceRetired as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return await asyncio.to_thread(_key_status, "fred")

    return router
