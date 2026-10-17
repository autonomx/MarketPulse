"""Gateway application composition root."""

from __future__ import annotations

from app.assets.protocol import AssetStore
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Any, cast

from app.runtime.contracts import ModelResidencyService

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import HTTPConnection

from app.assets import default_asset_store
from app.platform.chat import ChatSessionStore, default_chat_store
from app.platform.chat.generation_jobs import recover_abandoned_chat_generation_jobs
from app.jobs import (
    default_model_residency_store,
)
from app.providers.facade import ProviderFacade, default_provider_facade
from app.apps.rpg.edge.replay import RpgReplayPersistenceAdapter, default_rpg_replay_adapter

_LOCAL_BROWSER_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
)


def _install_local_browser_cors(gateway: FastAPI) -> None:
    """Attach local development CORS policy to this gateway instance."""
    gateway.add_middleware(
        CORSMiddleware,
        allow_origins=list(_LOCAL_BROWSER_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        max_age=86_400,
    )


def _gateway_lifespan(app, *, get_chat_store, get_job_store):
    from .lifecycle import gateway_lifespan

    return gateway_lifespan(
        app,
        get_chat_store=get_chat_store,
        get_job_store=get_job_store,
        recover_jobs=recover_abandoned_chat_generation_jobs,
    )



def _install_request_middleware(gateway: FastAPI, auth_service, membership_resolver=None) -> None:
    """Install request middleware; each one added wraps the ones before it."""
    from app.errors import install_error_envelope

    # Errors answer with problem details carrying the request id (WP-10.5).
    install_error_envelope(gateway)
    from app.runtime.net import allowed_origins
    from app.security.request_guard import RequestGuardMiddleware
    from fastapi.middleware.cors import CORSMiddleware

    from app.security.auth import AuthService, AuthenticationMiddleware, resolve_auth_settings

    # Deny by default once OMNIX_AUTH_MODE is explicit (WP-4.1). Without an
    # injected service, every composition resolves the same configuration.
    # Added before CORS so preflights and CORS headers wrap auth rejections.
    gateway.state.auth_service = auth_service or AuthService(resolve_auth_settings())
    from app.security.request_tenant import RequestTenantMiddleware

    # Inside authentication: binds the caller's workspace for the request (WP-4.2).
    gateway.add_middleware(
        RequestTenantMiddleware,
        resolver=membership_resolver,
    )
    gateway.add_middleware(
        AuthenticationMiddleware,
        authenticator_factory=lambda: gateway.state.auth_service,
    )
    gateway.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        max_age=86_400,
    )
    gateway.add_middleware(RequestGuardMiddleware)
    from app.runtime.drain import DrainMiddleware

    # Outermost: while draining (WP-6.7) new requests are refused before any
    # other work, and in-flight requests are counted until they finish.
    gateway.add_middleware(DrainMiddleware)
    from app.security.headers import SecurityHeadersMiddleware

    # Outermost of all: every response, including drain and auth refusals,
    # carries the security headers (WP-4.10).
    gateway.add_middleware(SecurityHeadersMiddleware)
    from app.observability.logging import RequestContextMiddleware

    # Around everything: each request's log lines and response carry its id (WP-10.2).
    gateway.add_middleware(RequestContextMiddleware)
    from app.observability.tracing import configure_tracing, instrument_app

    # Optional server spans around all of it (WP-10.4); a no-op unless enabled.
    configure_tracing(service_name="omnix-gateway")
    instrument_app(gateway)


def _docs_permission(connection: HTTPConnection) -> None:
    """API docs need admin:docs outside development (WP-4.10)."""
    from app.config.env import env_str
    from app.security.permissions import ensure_permission

    environment = (env_str("OMNIX_ENV", "development") or "development").strip().lower()
    if environment not in {"development", "local"}:
        ensure_permission("admin:docs")


def _install_api_docs(gateway: FastAPI) -> None:
    from fastapi import APIRouter, Depends
    from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
    from fastapi.responses import HTMLResponse, JSONResponse

    router = APIRouter(include_in_schema=False, dependencies=[Depends(_docs_permission)])

    @router.get("/openapi.json")
    def openapi() -> JSONResponse:
        return JSONResponse(gateway.openapi())

    @router.get("/docs")
    def swagger() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url="/openapi.json", title=f"{gateway.title} - Swagger UI",
            oauth2_redirect_url="/docs/oauth2-redirect",
        )

    @router.get("/docs/oauth2-redirect")
    def swagger_redirect() -> HTMLResponse:
        return get_swagger_ui_oauth2_redirect_html()

    @router.get("/redoc")
    def redoc() -> HTMLResponse:
        return get_redoc_html(openapi_url="/openapi.json", title=f"{gateway.title} - ReDoc")

    gateway.include_router(router)


def create_gateway_app(
    job_store_factory: Callable[[], Any] | None = None,
    provider_facade_factory: Callable[[], ProviderFacade] | None = None,
    asset_store_factory: Callable[[], AssetStore] | None = None,
    chat_store_factory: Callable[[], ChatSessionStore] | None = None,
    replay_adapter_factory: Callable[[], RpgReplayPersistenceAdapter] | None = None,
    model_residency_store_factory: Callable[[], ModelResidencyService]
    | None = None,
    readiness_check: Callable[[], dict[str, Any]] | None = None,
    runtime_lifecycle: Callable[[], AbstractAsyncContextManager] | None = None,
    background_runtime=None,
    scheduler_runtime=None,
    runtime_config=None,
    runtime_services=None,
    auth_service=None,
    membership_resolver=None,
) -> FastAPI:
    from app.runtime.config import get_runtime_config
    from app.runtime.capabilities import RuntimeCapabilities

    runtime_config = runtime_config or get_runtime_config()
    from app.platform.chat.delivery_sync import persist_live_voice_delivery
    from app.platform.live_voice.diagnostics import configure_delivery_checkpoint_recorder

    configure_delivery_checkpoint_recorder(persist_live_voice_delivery)
    if job_store_factory is None:
        from app.composition.runtime_composition import production_job_store

        get_job_store: Callable[[], Any] = production_job_store
    else:
        get_job_store = job_store_factory
    from app.jobs.store import install_default_job_store_factory

    install_default_job_store_factory(get_job_store)
    get_provider_facade = provider_facade_factory or default_provider_facade
    get_asset_store = asset_store_factory or default_asset_store
    get_chat_store = chat_store_factory or default_chat_store
    get_replay_adapter = replay_adapter_factory or default_rpg_replay_adapter
    model_residency_store_is_injected = model_residency_store_factory is not None
    get_model_residency_store = (
        model_residency_store_factory or default_model_residency_store
    )

    @asynccontextmanager
    async def gateway_lifespan(_app: FastAPI):
        from app.observability.metrics import event_loop_lag_monitor

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(event_loop_lag_monitor())
            if runtime_lifecycle is not None:
                await stack.enter_async_context(runtime_lifecycle())
            await stack.enter_async_context(
                _gateway_lifespan(
                    _app,
                    get_chat_store=get_chat_store,
                    get_job_store=get_job_store,
                )
            )
            yield

    gateway = FastAPI(
        title="Omnix Web Gateway",
        version="0.1.0",
        summary="Thin local-first gateway foundation for the Omnix web app redesign.",
        lifespan=gateway_lifespan,
        # Served by _install_api_docs behind a permission (WP-4.10).
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    _install_local_browser_cors(gateway)
    from app.runtime.feature_catalog import enabled_feature_ids

    if "rpg" in enabled_feature_ids(runtime_config):
        # Importing it loads the RPG route package; skip when RPG is off (WP-7.7).
        from app.apps.rpg.edge.api.feature_routes import add_rpg_debug_middleware

        add_rpg_debug_middleware(gateway)
    gateway.state.runtime_started = False
    gateway.state.background_runtime = background_runtime
    gateway.state.scheduler_runtime = scheduler_runtime
    gateway.state.runtime_config = runtime_config
    if runtime_services is None:
        from types import SimpleNamespace

        from app.runtime.contracts import KernelServices, LazyServiceProxy

        runtime_services = SimpleNamespace(
            jobs=LazyServiceProxy(get_job_store),
            assets=LazyServiceProxy(get_asset_store),
            chat=LazyServiceProxy(get_chat_store),
            model_residency=LazyServiceProxy(get_model_residency_store),
            database=None,
            tenant=None,
            settings=None,
        )
        runtime_services = cast(KernelServices, runtime_services)
    gateway.state.runtime_services = runtime_services
    gateway.state.runtime_capabilities = RuntimeCapabilities.from_config(runtime_config)
    from .background_runtime import GatewayBackgroundRegistryAdapter
    from .scheduler_runtime import GatewaySchedulerRegistryAdapter

    gateway.state.background_registry = GatewayBackgroundRegistryAdapter(gateway)
    gateway.state.scheduler_registry = GatewaySchedulerRegistryAdapter(gateway)
    gateway.state.feature_lifecycles = []
    from app.observability.metrics import HttpMetricsMiddleware, install_provider_metrics

    gateway.state.started_monotonic = time.monotonic()
    install_provider_metrics()
    from app.observability.tts_stream_diagnostics import runtime_stream_snapshot

    gateway.state.tts_stream_snapshot = runtime_stream_snapshot
    gateway.add_middleware(HttpMetricsMiddleware)
    from .feature_registry import compose_features

    _install_request_middleware(gateway, auth_service, membership_resolver)
    compose_features(gateway)

    from app.security.auth import create_auth_router

    from fastapi import Depends

    from app.security.permissions import kernel_permission_guard

    gateway.include_router(
        create_auth_router(lambda: gateway.state.auth_service),
        dependencies=[Depends(kernel_permission_guard)],
    )

    from .kernel_routes import create_kernel_router

    gateway.include_router(
        create_kernel_router(
            gateway.state,
            readiness_check=readiness_check,
            get_job_store=get_job_store,
            get_provider_facade=get_provider_facade,
            get_asset_store=get_asset_store,
            get_chat_store=get_chat_store,
            get_replay_adapter=get_replay_adapter,
            get_model_residency_store=get_model_residency_store,
            allow_offline_model_residency_store=model_residency_store_is_injected,
        ),
        dependencies=[Depends(kernel_permission_guard)],
    )
    _install_api_docs(gateway)

    return gateway
