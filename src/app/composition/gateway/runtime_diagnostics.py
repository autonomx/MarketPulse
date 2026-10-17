"""Read-only, workspace-scoped runtime diagnostics over existing runtime ledgers."""

from __future__ import annotations

import logging
import os
import time

from typing import Any

from app.composition.gateway.diagnostics import RuntimeDiagnostics, VersionDiagnostics


def _durable_snapshot(services):
    from app.persistence.authority import AuthorityOperation, require_authority_operation
    from app.persistence.retention import latest_cleanup_run
    from app.persistence.unit_of_work import unit_of_work

    jobs = services.jobs
    database = jobs.database
    with unit_of_work(database, authority_operation=AuthorityOperation.DIAGNOSTIC_READ) as work:
        policy = require_authority_operation(work.connection, AuthorityOperation.DIAGNOSTIC_READ)
        snapshot = work.jobs.diagnostic_snapshot(jobs.context)
        groups = snapshot["groups"]
        events = snapshot["events"]
        session_owners = snapshot["session_owners"]
        dead_letters = snapshot["dead_letter_count"]
        outbox = work.outbox.lag(jobs.context)
        retention = latest_cleanup_run(work.connection)
        work.rollback()
    return {
        "connectivity": True, "authority_state": policy.authority_state,
        "statement_timeout_ms": database.settings.statement_timeout_ms,
        "pool": database.pool_statistics(),
        # Events the outbox relay has not delivered yet (WP-5.3).
        "outbox": outbox,
        "retention": retention,
    }, {
        "available": True,
        "by_resource_class": [{"resource_class": resource, "status": status, "count": count} for resource, status, count, _, _ in groups],
        "oldest_queued_age_seconds": max((float(row[3]) for row in groups), default=0),
        "expired_lease_count": sum(row[4] for row in groups),
        "dead_letter_count": dead_letters,
        "events_last_60_seconds": dict(events),
        "claim_rate_per_second": dict(events).get('job.claimed', 0) / 60,
        "failure_rate_per_second": dict(events).get('job.failed', 0) / 60,
        "retry_rate_per_second": dict(events).get('job.retry_scheduled', 0) / 60,
    }, session_owners


def durable_metrics_snapshot(services):
    """The workspace's active jobs, dead letters and outbox lag for ``/metrics`` (WP-10.3)."""
    from app.persistence.authority import AuthorityOperation, require_authority_operation
    from app.persistence.unit_of_work import unit_of_work

    jobs = services.jobs
    with unit_of_work(jobs.database, authority_operation=AuthorityOperation.DIAGNOSTIC_READ) as work:
        require_authority_operation(work.connection, AuthorityOperation.DIAGNOSTIC_READ)
        snapshot = work.jobs.metrics_snapshot(jobs.context)
        snapshot["outbox"] = work.outbox.lag(jobs.context)
        work.rollback()
    return snapshot


def _capability_catalog_digest() -> str | None:
    try:
        from app.capabilities.registry import capability_catalog_digest

        return capability_catalog_digest()
    except Exception:
        # Diagnostics never fail the status endpoint; the failure is logged.
        logging.getLogger(__name__).warning("capability catalog digest unavailable", exc_info=True)
        return None


def runtime_diagnostics(state) -> RuntimeDiagnostics:
    from app.observability.metrics import request_snapshot

    config, capabilities = state.runtime_config, state.runtime_capabilities
    owner = getattr(state, 'execution_owner', None)
    background = getattr(state, 'background_runtime', None)
    scheduler = getattr(state, 'scheduler_runtime', None)
    services = getattr(state, 'runtime_services', None)
    postgres: dict[str, Any] = {"connectivity": None}
    jobs: dict[str, Any] = {"available": False}
    sessions: Any = None
    if services is not None:
        try:
            postgres, jobs, sessions = _durable_snapshot(services)
            postgres['migrations_pending'] = state.persistence_startup.get('migrations_pending', [])
        except Exception as exc:
            # No exception message or database URL crosses this boundary.
            postgres = {"connectivity": False, "error_class": type(exc).__name__}
    dispatcher = getattr(getattr(services, 'jobs', None), 'chat_dispatcher', None)
    chat = dispatcher.diagnostics() if dispatcher is not None else {}
    chat['active_session_ownership'] = sessions
    if owner is not None:
        chat.update(owner.recovery_diagnostics())
        chat['execution_owner_healthy'] = owner.healthy
    tts_snapshot = getattr(state, "tts_stream_snapshot", None)
    stream_snapshot = tts_snapshot() if callable(tts_snapshot) else {}
    resolver = getattr(state, 'live_voice_tts_provider_resolver', None)
    delivery = getattr(state, 'live_voice_delivery_persistence_worker', None)
    from app.persistence.migrations import SCHEMA_KNOWN
    from app.runtime.feature_catalog import enabled_feature_ids

    readers = getattr(state, "event_readers", None)
    retention = postgres.pop("retention", None)
    return RuntimeDiagnostics(
        version=VersionDiagnostics(build_revision=config.build_revision, application_schema=SCHEMA_KNOWN),
        features=sorted(enabled_feature_ids(config)),
        events=readers.diagnostics() if readers is not None else {},
        retention=retention,
        process={"process_id": os.getpid(), "runtime_id": getattr(owner, 'node_id', None),
                 "gateway_role": config.gateway_role.value, "uptime_seconds": time.monotonic() - state.started_monotonic,
                 "build_revision": config.build_revision, "capabilities": sorted(value.value for value in capabilities.granted),
                 # Diagnostic only: processes may differ during a rollout (PA-1.4).
                 "capability_catalog_digest": _capability_catalog_digest(),
                 **request_snapshot()},
        postgresql=postgres,
        background=background.diagnostics() if background is not None else {"role": config.gateway_role.value, "owns_lock": False},
        jobs=jobs, chat=chat,
        scheduler=scheduler.diagnostics() if scheduler is not None else {},
        tts={"mode": 'remote' if config.use_remote_tts else 'local' if config.allow_local_tts else 'worker_routed',
             "endpoint": config.tts.url if config.tts else None, **stream_snapshot,
             "provider_refresh": resolver.diagnostics() if resolver is not None else {},
             "delivery_queue": delivery.diagnostics() if delivery is not None else {}},
        replicas={"known_origins": list(config.api_replica_origins), "local_runtime_started": state.runtime_started,
                  "remote_readiness": None},
    )
