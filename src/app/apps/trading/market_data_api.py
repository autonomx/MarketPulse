from __future__ import annotations

import sys  # noqa: F401  tests set sys.platform through this module
import asyncio
import base64
import binascii
import re
from datetime import date
from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.security.provider_secret_store import (
    credential_store_label,
    os_credential_store_available,
    load_trading_provider_secrets,
    save_trading_provider_secrets,
    trading_provider_credential_sources,
)
from app.errors import LegacyPersistenceRetired
from app.apps.trading.ibkr_evidence import default_ibkr_evidence_store
from app.apps.trading.ibkr_settings import IbkrConnection, load_ibkr_settings, save_ibkr_settings
from app.apps.trading.providers.ibkr_runtime import default_ibkr_runtime
from app.apps.trading.providers.ibkr_web_api import (
    IBKR_OAUTH_PORTAL_URL,
    SECRET_PROVIDER,
    IbkrOAuthKeys,
    generate_oauth_keys,
)
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
    connection: IbkrConnection = "gateway"


class IbkrSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    monitor_enabled: bool | None = None
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65_535)
    client_id: int | None = Field(default=None, ge=0, le=32_767)
    live_authority_enabled: bool | None = None
    recovery_authority_enabled: bool | None = None
    connection: IbkrConnection | None = None


class IbkrWebApiStatus(BaseModel):
    """What the IBKR Web API connection has saved: never a secret, only whether it is there."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    keys_generated: bool
    consumer_key: str = ""
    access_token_masked: str = ""
    access_token_secret_saved: bool
    ready: bool
    editable: bool
    storage: str
    portal_url: str


class IbkrWebApiPublicKeys(BaseModel):
    """The public files the user uploads to IBKR's OAuth page; the private keys never leave the server."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signature_public_key: str
    encryption_public_key: str
    dh_parameters: str


class IbkrWebApiKeysRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Replacing keys also clears the access token, which IBKR issued for the old ones.
    replace: bool = False


class IbkrWebApiCredentialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consumer_key: str | None = Field(default=None, max_length=32)
    access_token: str | None = Field(default=None, max_length=512)
    access_token_secret: str | None = Field(default=None, max_length=4096)


class IbkrSettingsStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["ibkr"] = "ibkr"
    settings: IbkrSettingsPayload
    settings_source: Literal["defaults", "environment", "omnix_settings", "runtime_arguments"]
    connection_status: Literal["disabled", "client_unavailable", "credentials_missing", "connected", "disconnected"]
    official_ibapi_available: bool
    connected: bool
    last_error: str | None = None
    web_api: IbkrWebApiStatus
    diagnostics: dict[str, Any] = Field(default_factory=dict)


_WEB_API_TOKEN_FIELDS = ("consumer_key", "access_token", "access_token_secret")
_WEB_API_KEY_FIELDS = ("signature_private_key", "encryption_private_key", "dh_prime")
_CONSUMER_KEY = re.compile(r"^[A-Z]{9}$")
_ACCESS_TOKEN = re.compile(r"^[A-Za-z0-9._~-]+$")


def _web_api_status() -> IbkrWebApiStatus:
    stored = load_trading_provider_secrets().get(SECRET_PROVIDER) or {}
    sources = trading_provider_credential_sources(SECRET_PROVIDER)
    present = {name: bool(str(stored.get(name) or "").strip()) for name in (*_WEB_API_TOKEN_FIELDS, *_WEB_API_KEY_FIELDS)}
    return IbkrWebApiStatus(
        keys_generated=all(present[name] for name in _WEB_API_KEY_FIELDS),
        # The consumer key is a name the user chose, not a secret.
        consumer_key=str(stored.get("consumer_key") or "").strip(),
        access_token_masked=_mask_key(str(stored.get("access_token") or "")),
        access_token_secret_saved=present["access_token_secret"],
        ready=all(present.values()),
        editable=os_credential_store_available() and "environment" not in sources.values(),
        storage=credential_store_label(),
        portal_url=IBKR_OAUTH_PORTAL_URL,
    )


def _ibkr_status() -> IbkrSettingsStatus:
    runtime = default_ibkr_runtime()
    runtime.refresh_settings()
    settings, source = load_ibkr_settings()
    diagnostics = runtime.diagnostics()
    connected = bool(diagnostics.get("connected"))
    enabled = bool(diagnostics.get("enabled"))
    official_available = bool(diagnostics.get("official_ibapi_available"))
    connection_status: Literal["disabled", "client_unavailable", "credentials_missing", "connected", "disconnected"]
    if not enabled:
        connection_status = "disabled"
    elif not runtime.client_available():
        connection_status = "credentials_missing" if settings.connection == "web_api" else "client_unavailable"
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
        web_api=_web_api_status(),
        diagnostics=diagnostics,
    )


def _web_api_keys() -> IbkrOAuthKeys | None:
    stored = load_trading_provider_secrets().get(SECRET_PROVIDER) or {}
    values = {name: str(stored.get(name) or "").strip() for name in _WEB_API_KEY_FIELDS}
    if not all(values.values()):
        return None
    return IbkrOAuthKeys(
        signature_private_key=values["signature_private_key"],
        encryption_private_key=values["encryption_private_key"],
        dh_prime=int(values["dh_prime"], 16),
    )


def _public_keys(keys: IbkrOAuthKeys) -> IbkrWebApiPublicKeys:
    return IbkrWebApiPublicKeys(
        signature_public_key=keys.signature_public_key,
        encryption_public_key=keys.encryption_public_key,
        dh_parameters=keys.dh_parameters,
    )


def _generate_web_api_keys(replace: bool) -> IbkrWebApiPublicKeys:
    if _web_api_keys() is not None and not replace:
        raise HTTPException(status_code=409, detail="ibkr_web_api_keys_exist")
    keys = generate_oauth_keys()
    updates: dict[str, str | None] = {
        "signature_private_key": keys.signature_private_key,
        "encryption_private_key": keys.encryption_private_key,
        "dh_prime": format(keys.dh_prime, "X"),
    }
    if replace:
        # IBKR encrypted the access token secret for the old keys.
        updates.update({"access_token": "", "access_token_secret": ""})
    save_trading_provider_secrets(SECRET_PROVIDER, updates)
    _reconnect_web_api()
    return _public_keys(keys)


def _save_web_api_credentials(request: IbkrWebApiCredentialUpdate) -> None:
    updates: dict[str, str | None] = {}
    if request.consumer_key is not None:
        consumer_key = request.consumer_key.strip().upper()
        if not _CONSUMER_KEY.fullmatch(consumer_key):
            raise ValueError("ibkr_web_api_consumer_key_must_be_9_letters")
        updates["consumer_key"] = consumer_key
    if request.access_token is not None:
        access_token = request.access_token.strip()
        if not _ACCESS_TOKEN.fullmatch(access_token):
            raise ValueError("ibkr_web_api_access_token_invalid")
        updates["access_token"] = access_token
    if request.access_token_secret is not None:
        secret = "".join(request.access_token_secret.split())
        try:
            base64.b64decode(secret, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("ibkr_web_api_access_token_secret_invalid") from exc
        updates["access_token_secret"] = secret
    if updates:
        save_trading_provider_secrets(SECRET_PROVIDER, updates)
        _reconnect_web_api()


def _reconnect_web_api() -> None:
    """New credentials take effect on the next connection."""
    runtime = default_ibkr_runtime()
    runtime.refresh_settings()
    if runtime.connection == "web_api" and runtime.is_connected():
        runtime.disconnect()


def _connect_now() -> IbkrSettingsStatus:
    runtime = default_ibkr_runtime()
    runtime.refresh_settings()
    try:
        runtime.connect(force=True)
    except Exception as exc:
        runtime.last_error = f"{type(exc).__name__}: {exc}"
    return _ibkr_status()


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
        api_key_editable=os_credential_store_available() and sources["api_key"] != "environment",
        storage=credential_store_label(),
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
        api_key_editable=os_credential_store_available() and sources["api_key"] != "environment",
        storage=credential_store_label(),
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
        """Save non-secret IBKR settings; Web API keys are saved through their own routes."""

        patch = request.model_dump(exclude_unset=True)
        if patch:
            await asyncio.to_thread(save_ibkr_settings, patch)
            await asyncio.to_thread(default_ibkr_runtime().refresh_settings)
        return await asyncio.to_thread(_ibkr_status)

    @router.post("/providers/ibkr/connect", response_model=IbkrSettingsStatus)
    async def connect_ibkr() -> IbkrSettingsStatus:
        """Try the chosen IBKR connection now instead of waiting for the next monitor pass."""

        return await asyncio.to_thread(_connect_now)

    @router.get("/providers/ibkr/web-api/keys", response_model=IbkrWebApiPublicKeys)
    async def ibkr_web_api_keys() -> IbkrWebApiPublicKeys:
        """The public key files to upload to IBKR's OAuth page."""

        keys = await asyncio.to_thread(_web_api_keys)
        if keys is None:
            raise HTTPException(status_code=404, detail="ibkr_web_api_keys_missing")
        return _public_keys(keys)

    @router.post("/providers/ibkr/web-api/keys", response_model=IbkrWebApiPublicKeys)
    async def generate_ibkr_web_api_keys(request: IbkrWebApiKeysRequest) -> IbkrWebApiPublicKeys:
        """Generate and keep the signing and encryption keys; return only their public halves."""

        try:
            return await asyncio.to_thread(_generate_web_api_keys, request.replace)
        except LegacyPersistenceRetired as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.put("/providers/ibkr/web-api/credentials", response_model=IbkrSettingsStatus)
    async def update_ibkr_web_api_credentials(request: IbkrWebApiCredentialUpdate) -> IbkrSettingsStatus:
        """Save the consumer key, access token and secret from IBKR's OAuth page."""

        try:
            await asyncio.to_thread(_save_web_api_credentials, request)
        except LegacyPersistenceRetired as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
