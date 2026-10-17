"""Process-local production assembly, before importing gateway feature modules."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any
from contextlib import asynccontextmanager

from app.config.env import environment
from app.config.env import env_bool, env_str

from app.runtime.config import (
    DevicePermitSettings,
    GatewayRole,
    RuntimeConfig,
    get_runtime_config,
    install_runtime_config,
)
from app.runtime.capabilities import RuntimeCapabilities, RuntimeCapability
from app.runtime.contracts import JobService, AssetService, ChatService, ModelResidencyService


@dataclass(frozen=True, slots=True)
class GatewayRuntimeServices:
    """Process-owned repositories; requests still open their own transactions."""

    jobs: JobService
    assets: AssetService
    chat: ChatService
    model_residency: ModelResidencyService
    database: Any
    tenant: Any
    settings: Any
    agent_runs: Any | None = None


def production_readiness(config: RuntimeConfig | None = None) -> dict:
    from app.persistence.runtime import ensure_postgresql_runtime_ready

    status = ensure_postgresql_runtime_ready(
        auto_initialize_fresh_install=False,
        apply_schema_changes=False,
    )
    config = config or get_runtime_config()
    required = set(config.required_workers)
    required.update(name for name in ("tts", "stt", "image") if (endpoint := getattr(config, name)) is not None and endpoint.required)
    unavailable = []
    if required:
        from app.runtime.worker_health import get_worker_health_payload

        workers = {worker.id: worker for worker in get_worker_health_payload().workers}
        unavailable = sorted(
            worker_id
            for worker_id in required
            if worker_id not in workers
            or not workers[worker_id].ok
            or workers[worker_id].mocked
        )
    return {
        "ready": status.ready and status.backend == "postgresql" and not unavailable,
        "backend": status.backend,
        "authority_state": status.authority_state,
        "migrations_pending": list(status.migrations_pending),
        "required_workers_unavailable": unavailable,
    }


def maybe_apply_migrations_on_start(
    config: RuntimeConfig,
    *,
    env: Any | None = None,
    apply_migrations_fn=None,
) -> bool:
    """Apply release migrations only for an explicitly opted-in local worker."""
    source = environment() if env is None else env
    if not env_bool("OMNIX_MIGRATE_ON_START", False, env=source):
        return False
    if config.gateway_role is GatewayRole.JOB_WORKER:
        # The singleton gateway remains the only local DDL owner.
        return False

    auth_mode = (env_str("OMNIX_AUTH_MODE", "local", env=source) or "local").strip().lower()
    deployment = (env_str("OMNIX_ENV", "development", env=source) or "development").strip().lower()
    if (
        config.gateway_role.value != "worker"
        or auth_mode != "local"
        or deployment != "development"
    ):
        raise RuntimeError(
            "OMNIX_MIGRATE_ON_START is allowed only for a local-auth development worker"
        )

    if apply_migrations_fn is None:
        from app.persistence.migrations import apply_migrations

        apply_migrations_fn = apply_migrations
    apply_migrations_fn()
    return True


def create_production_app(config: RuntimeConfig | None = None):
    config = config or RuntimeConfig.from_environment(environment())
    maybe_apply_migrations_on_start(config)
    install_runtime_config(config)
    from app.platform.live_voice.capacity import configure_live_call_capacity

    configure_live_call_capacity(config.live_max_calls)
    capabilities = RuntimeCapabilities.from_config(config)
    from app.persistence.startup import bootstrap_status_payload

    status = bootstrap_status_payload()
    if not status["ready"] or status["backend"] != "postgresql":
        raise RuntimeError("Production gateway requires ready PostgreSQL authority")

    from app.persistence.database import default_database
    from app.security.tenant_context import TenantProvider
    from app.settings.service import SettingsService
    from app.settings.registry import core_setting_specs

    database = default_database()
    from app.persistence.connection_budget import check_connection_budget

    connection_budget = check_connection_budget(database)
    permit_config = DevicePermitSettings.from_environment(environment())
    from app.persistence.device_permits import configure_default_device_permit_service

    configure_default_device_permit_service(
        database,
        device_id=permit_config.device_id,
        capacities={
            model_class: (capacity, reserved)
            for model_class, capacity, reserved in permit_config.capacities
        },
        lease_seconds=permit_config.lease_seconds,
        tts_model_owner=permit_config.tts_model_owner,
    )
    tenant_provider = TenantProvider()
    settings_service = SettingsService(database, tenant_provider.current, specs=core_setting_specs())
    from app.settings.access import install_settings_service
    install_settings_service(settings_service)
    from app.platform.live_voice.hardware_policy import apply_live_voice_process_defaults

    apply_live_voice_process_defaults()
    # Resolve adapters after bootstrap, including when a schema exporter or test
    # previously imported the provider-free gateway factory in this process.
    from app.assets import default_asset_store
    from app.composition.runtime_composition import (
        production_chat_store,
        production_job_store,
        production_model_residency_store,
        production_owner_memory_repository,
    )
    from app.platform.assistant_memory.owner_defaults import install_default_memory_repository_factory

    # Every process (API, scheduler, job worker) serves curated memory from
    # Memory v2 (WP-8.5).
    install_default_memory_repository_factory(production_owner_memory_repository)
    from app.composition.gateway.main import create_gateway_app
    from app.persistence.gateway_runtime import GatewayRuntimeOwner
    from app.runtime.net import bind_host
    from app.security.auth import AuthService, bootstrap_authentication, resolve_auth_settings

    auth_service = AuthService(resolve_auth_settings(), database=database)
    bootstrap_authentication(auth_service, bind_host=bind_host(None))
    from app.platform.chat.generation_jobs import (
        _ChatGenerationDispatcher,
        recover_abandoned_chat_generation_jobs,
    )
    from app.runtime.background import GatewayBackgroundRuntime
    from app.runtime.scheduler import ScheduledTaskSpec, SchedulerRuntime

    tenant_context = tenant_provider.current()
    owner = GatewayRuntimeOwner(database, tenant_context.workspace_id, config=config)
    dispatcher = _ChatGenerationDispatcher()
    if config.gateway_role is not GatewayRole.JOB_WORKER:
        capabilities.require(RuntimeCapability.RUN_CHAT_DISPATCH)
    # No pinned context: request-serving stores follow the request's tenant
    # (WP-4.2); background threads resolve the process tenant as before.
    jobs = production_job_store(
        database=database,
        context=None,
        chat_execution_owner=owner,
        chat_dispatcher=dispatcher,
    )
    from app.platform.agent_runtime.service import AgentRunService

    agent_runs = AgentRunService(database, job_store=jobs)
    from app.platform.chat.live_agent_store import default_live_agent_planner

    services = GatewayRuntimeServices(
        jobs=jobs,
        assets=default_asset_store(),
        chat=production_chat_store(
            job_service=jobs,
            live_agent_planner=default_live_agent_planner(),
        ),
        model_residency=production_model_residency_store(),
        database=database,
        tenant=tenant_provider,
        settings=settings_service,
        agent_runs=agent_runs,
    )
    from app.persistence.authority import AuthorityOperation, require_authority_operation
    from app.persistence.background_authority import background_execution

    def background_authority_check(connection) -> None:
        require_authority_operation(connection, AuthorityOperation.RUNTIME_MUTATION)

    background = GatewayBackgroundRuntime(
        services.jobs.database,
        services.jobs.context.workspace_id,
        config=config,
        authority_check=background_authority_check,
        execution_scope=background_execution,
    )
    def active_workspaces():
        from app.persistence.identity_service import list_active_workspace_contexts

        return list_active_workspace_contexts(services.jobs.database)

    scheduler = SchedulerRuntime(
        services.jobs.database,
        services.jobs.context.workspace_id,
        workspace_contexts=active_workspaces,
        capabilities=capabilities,
        authority_check=background_authority_check,
        execution_scope=background_execution,
        thread_workers=config.scheduler_thread_workers,
        process_workers=config.scheduler_process_workers,
    )
    if capabilities.allows(RuntimeCapability.RUN_SCHEDULERS):
        async def recover_chat_generations(_task_context) -> None:
            await asyncio.to_thread(
                recover_abandoned_chat_generation_jobs,
                services.chat,
                services.jobs,
            )

        async def release_expired_job_leases(_task_context) -> None:
            from app.persistence.unit_of_work import unit_of_work

            def release() -> None:
                with unit_of_work(services.jobs.database) as work:
                    work.jobs.release_expired_leases(services.jobs.context)
                    work.commit()

            await asyncio.to_thread(release)

        scheduler.register_task(
            ScheduledTaskSpec(
                task_id="platform.chat-generation-recovery",
                per_workspace=True,
                run=recover_chat_generations,
                interval_seconds=owner.recovery_seconds,
                timeout_seconds=60,
                requires=frozenset(
                    {
                        RuntimeCapability.RUN_SCHEDULERS,
                        RuntimeCapability.RUN_RECOVERY,
                    }
                ),
            )
        )
        def run_retention(_task_context) -> None:
            from app.persistence.retention import RetentionWorker

            RetentionWorker(services.jobs.database).run_once()

        scheduler.register_task(
            ScheduledTaskSpec(
                task_id="platform.retention",
                run=run_retention,
                interval_seconds=3600,
                timeout_seconds=900,
                executor="thread",
            )
        )

        def converge_memory_v2(_task_context) -> None:
            from app.platform.assistant_memory.v2.curated_records import MemoryV2CuratedConvergence

            MemoryV2CuratedConvergence(services.jobs.database).run()

        scheduler.register_task(
            ScheduledTaskSpec(
                task_id="memory-v2.convergence",
                run=converge_memory_v2,
                interval_seconds=10,
                timeout_seconds=300,
                executor="thread",
            )
        )

        def relay_outbox(_task_context) -> None:
            from app.events.outbox_relay import OutboxRelayWorker

            # The registry is composed with the features, below.
            OutboxRelayWorker(services.jobs.database, gateway.state.outbox_consumers).run_once()

        scheduler.register_task(
            ScheduledTaskSpec(
                task_id="platform.outbox-relay",
                run=relay_outbox,
                interval_seconds=1,
                timeout_seconds=120,
                executor="thread",
            )
        )
        async def recover_retired_and_unclaimed_jobs(_task_context) -> None:
            """Fail unfinished jobs of retired modules once; alert on other jobs nobody here can claim (PA-4.3)."""
            from app.config.env import env_int
            from app.persistence.declarations import retired_job_types
            from app.persistence.unit_of_work import unit_of_work

            def recover() -> None:
                registry = getattr(gateway.state, "job_handler_registry", None)
                retired = retired_job_types()
                known = (*(registry.types() if registry is not None else ()), *retired)
                with unit_of_work(services.jobs.database) as work:
                    work.jobs.fail_retired_jobs(services.jobs.context, retired)
                    unclaimed = work.jobs.unclaimed_job_types(
                        services.jobs.context,
                        older_than_seconds=env_int("OMNIX_JOB_UNCLAIMED_ALERT_SECONDS", 900, minimum=60),
                        known_types=known,
                    )
                    work.commit()
                for job_type, age_seconds in unclaimed.items():
                    logging.getLogger(__name__).warning(
                        "job_unclaimed_too_long job_type=%s age_seconds=%.0f", job_type, age_seconds,
                    )

            await asyncio.to_thread(recover)

        scheduler.register_task(
            ScheduledTaskSpec(
                task_id="platform.retired-job-recovery",
                per_workspace=True,
                run=recover_retired_and_unclaimed_jobs,
                interval_seconds=60,
                timeout_seconds=60,
                requires=frozenset(
                    {
                        RuntimeCapability.RUN_SCHEDULERS,
                        RuntimeCapability.RUN_RECOVERY,
                    }
                ),
            )
        )
        scheduler.register_task(
            ScheduledTaskSpec(
                task_id="platform.job-lease-recovery",
                per_workspace=True,
                run=release_expired_job_leases,
                interval_seconds=5,
                timeout_seconds=60,
                requires=frozenset(
                    {
                        RuntimeCapability.RUN_SCHEDULERS,
                        RuntimeCapability.RUN_RECOVERY,
                    }
                ),
            )
        )

    if config.gateway_role is GatewayRole.JOB_WORKER:
        @asynccontextmanager
        async def lifecycle():
            async with background.lifespan():
                try:
                    yield
                finally:
                    remaining = await asyncio.to_thread(dispatcher.close)
                    if remaining:
                        logging.getLogger(__name__).warning(
                            "Chat dispatcher shutdown deadline exceeded: %s workers", remaining
                        )
    else:
        @asynccontextmanager
        async def lifecycle():
            async with owner.lifespan(), background.lifespan(), scheduler.lifespan():
                try:
                    yield
                finally:
                    remaining = await asyncio.to_thread(dispatcher.close)
                    if remaining:
                        logging.getLogger(__name__).warning(
                            "Chat dispatcher shutdown deadline exceeded: %s workers", remaining
                        )

    def readiness():
        from app.platform.live_voice.capacity import live_call_capacity_snapshot

        payload = production_readiness(config)
        payload["live_voice_capacity"] = live_call_capacity_snapshot()
        payload["build_revision"] = config.build_revision
        payload["execution_owner_ready"] = owner.ready()
        payload['background_role'] = background.role
        payload['background_ready'] = background.ready()
        payload["scheduler_ready"] = scheduler.ready()
        payload["database_connection_budget"] = connection_budget
        payload["ready"] = (
            payload["ready"]
            and payload["execution_owner_ready"]
            and payload['background_ready']
            and payload["scheduler_ready"]
        )
        return payload

    gateway = create_gateway_app(
        job_store_factory=lambda: services.jobs,
        asset_store_factory=lambda: services.assets,
        chat_store_factory=lambda: services.chat,
        model_residency_store_factory=lambda: services.model_residency,
        readiness_check=readiness,
        runtime_lifecycle=lifecycle,
        background_runtime=background,
        scheduler_runtime=scheduler,
        runtime_config=config,
        runtime_services=services,
        auth_service=auth_service,
    )
    gateway.state.persistence_startup = status
    gateway.state.runtime_services = services
    gateway.state.execution_owner = owner
    gateway.state.runtime_config = config
    gateway.state.runtime_capabilities = capabilities
    return gateway


class ProductionApplication:
    """Import-safe ASGI entrypoint; initializes inside the serving process."""

    def __init__(self, factory=None):
        self._application: Any = None
        self._lock = threading.Lock()
        self._factory = factory

    def _load(self):
        with self._lock:
            if self._application is None:
                self._application = (self._factory or create_production_app)()
            return self._application

    async def __call__(self, scope, receive, send):
        application = self._application
        if application is None:
            try:
                application = await asyncio.to_thread(self._load)
            except Exception:
                from app.persistence.database import close_default_database

                await asyncio.to_thread(close_default_database)
                if scope["type"] == "lifespan":
                    await receive()
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": "Omnix production initialization failed",
                        }
                    )
                    return
                raise
        try:
            await application(scope, receive, send)
        finally:
            if scope["type"] == "lifespan":
                from app.persistence.database import close_default_database

                await asyncio.to_thread(close_default_database)


app = ProductionApplication()
