"""Registry-driven FeatureModule composition."""

import logging
from typing import cast

from fastapi import Depends, HTTPException

from app.config.env import environment
from app.config.load import load_feature_config
from app.jobs.handlers import JobHandlerRegistry, JobObserver
from app.jobs.handlers import JobHandlerSpec as KernelJobHandlerSpec
from app.jobs.probe import PLATFORM_PROBE_JOB
from app.persistence.repository_registry import RepositorySpec as KernelRepositorySpec
from app.persistence.repository_registry import install_repository_specs, reset_repository_specs
from app.persistence.shared_repository_specs import shared_repository_specs
from app.runtime.background import BackgroundRegistry, register_background_worker
from app.runtime.contracts import KernelServices
from app.runtime.feature_catalog import enabled_feature_ids, load_feature
from app.runtime.features import FeatureContext, FeatureLifecycle
from app.runtime.ports import PortBinding, PortBindings, install_port_bindings
from app.runtime.scheduler import ScheduledTaskSpec as RuntimeScheduledTaskSpec
from app.security.permissions import feature_permission_guard, internal_permission_guard


def register_feature_lifecycle(gateway, feature: FeatureLifecycle):
    capabilities = getattr(gateway.state, "runtime_capabilities", None)
    if capabilities is not None:
        capabilities.require(*feature.requires)
    lifecycles = getattr(gateway.state, "feature_lifecycles", None)
    if lifecycles is None:
        for callback in feature.startup:
            gateway.router.add_event_handler("startup", callback)
        for callback in feature.shutdown:
            gateway.router.add_event_handler("shutdown", callback)
        return
    if any(item.name == feature.name for item in lifecycles):
        raise ValueError(f"Feature lifecycle already registered: {feature.name}")
    lifecycles.append(feature)


_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def feature_guard(feature_id: str):
    """Return the composition-level feature policy hook for mounted routes.

    A draining module answers 503 to anything but reads; a retired one to
    everything (PA-4.3). The database state wins over the runtime configuration.
    """
    from starlette.requests import HTTPConnection

    def guard(connection: HTTPConnection) -> None:
        from app.persistence.module_states import cached_module_state

        state = cached_module_state(feature_id)
        if state.state == "active":
            return None
        if state.state == "draining" and connection.scope.get("method") in _READ_METHODS:
            return None
        raise HTTPException(status_code=503, detail=f"module_{state.state}", headers={"Retry-After": "30"})

    guard.__name__ = "feature_guard_" + feature_id.replace("-", "_")
    return guard


def _router_paths(router) -> list[str]:
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:  # FastAPI without lazy router inclusion
        return [str(getattr(route, "path")) for route in router.routes if getattr(route, "path", None)]
    return [
        str(context.path or getattr(context.original_route, "path", ""))
        for context in iter_route_contexts(router.routes)
    ]


def _router_operations(router) -> list[tuple[str, str]]:
    """Every (METHOD, path) the router serves, with nested routers' prefixes applied."""
    from fastapi.routing import APIRoute

    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:  # FastAPI without lazy router inclusion
        return [
            (method, str(route.path))
            for route in router.routes if isinstance(route, APIRoute)
            for method in sorted(route.methods or ())
        ]
    return [
        (method, str(context.path or context.original_route.path))
        for context in iter_route_contexts(router.routes)
        if isinstance(context.original_route, APIRoute)
        for method in sorted(context.original_route.methods or ())
    ]


def _paused_while_draining(task: RuntimeScheduledTaskSpec, feature_id: str) -> RuntimeScheduledTaskSpec:
    """A scheduled task starts no new run while its module is draining or retired (PA-4.3)."""
    import dataclasses

    from app.persistence import module_states

    enabled = task.enabled

    def enabled_while_active() -> bool:
        return module_states.cached_module_state(feature_id).accepts_new_work and enabled()

    return dataclasses.replace(task, enabled=enabled_while_active)


def _register_feature_modules(gateway) -> None:
    config = gateway.state.runtime_config
    capabilities = gateway.state.runtime_capabilities
    services = getattr(gateway.state, "runtime_services", None)
    registry = getattr(gateway.state, "background_registry", None)
    scheduler_registry = getattr(gateway.state, "scheduler_registry", None)
    registered: list[str] = []
    loaded_features = []
    internal_paths: list[str] = []
    public_paths: list[str] = []
    route_owners: dict[tuple[str, str], str] = {}
    port_bindings: list[PortBinding] = []
    job_handlers = JobHandlerRegistry()
    # Kernel-owned synthetic job used by canaries and deployment tests.
    job_handlers.register(PLATFORM_PROBE_JOB)
    reset_repository_specs()
    install_repository_specs(shared_repository_specs())
    from app.composition.runtime_composition import shared_service_repository_specs

    install_repository_specs(shared_service_repository_specs())

    for feature_id in enabled_feature_ids(config):
        feature = load_feature(feature_id)
        capabilities.require(*feature.requires)
        loaded_features.append(feature)
        settings_service = getattr(services, "settings", None)
        if feature.settings and settings_service is not None:
            settings_service.register_specs(tuple(feature.settings))
        for handler in feature.job_handlers:
            # Features declare the structural spec; the kernel registry holds the concrete one.
            job_handlers.register(cast(KernelJobHandlerSpec, handler), owner=feature.id)
        for observer_factory in feature.job_observers:
            observer = observer_factory()
            if observer is not None:
                job_handlers.register_observer(cast(JobObserver, observer))
        if feature.repositories:
            install_repository_specs(cast(tuple[KernelRepositorySpec, ...], tuple(feature.repositories)))
        context = FeatureContext(
            feature_id=feature.id,
            config=load_feature_config(feature.id, feature.config_model, env=environment()),
            runtime=config,
            capabilities=capabilities,
            services=cast(KernelServices, services),
            logger=logging.getLogger(f"app.feature.{feature.id}"),
            runtime_state=gateway.state,
        )
        # ADR-0016: typed ports replace string hooks; each contribution is built
        # from its own feature's context and validated with the rest below.
        for contribution in feature.contributions:
            port_bindings.append(PortBinding(
                contribution.port, contribution.factory(context), owner=feature.id, priority=contribution.priority,
            ))
        # Every feature route is authorized (WP-4.3): its declared permission,
        # else the feature's read/write default.
        permission_guard = feature_permission_guard(feature.id)
        for router_factory in feature.routers:
            router = router_factory(context)
            for operation in _router_operations(router):
                owner = route_owners.setdefault(operation, feature.id)
                if owner != feature.id:
                    raise RuntimeError(f"{operation[0]} {operation[1]} is mounted by both {owner} and {feature.id}")
            gateway.include_router(
                router,
                dependencies=[Depends(feature_guard(feature.id)), Depends(permission_guard)],
            )
        for router_factory in feature.internal_routers:
            internal_router = router_factory(context)
            internal_paths.extend(_router_paths(internal_router))
            gateway.include_router(
                internal_router,
                dependencies=[Depends(feature_guard(feature.id)), Depends(internal_permission_guard)],
                include_in_schema=False,
            )
        public_paths.extend(sorted(feature.public_paths))
        for worker_factory in feature.background_workers:
            worker = worker_factory(context)
            if worker is not None:
                register_background_worker(cast(BackgroundRegistry, registry), worker)
        for task_factory in feature.scheduled_tasks:
            task = task_factory(context)
            if task is not None:
                if scheduler_registry is None:
                    raise RuntimeError(
                        f"Feature {feature.id} declares scheduled tasks without a scheduler registry"
                    )
                scheduler_registry.register_task(_paused_while_draining(cast(RuntimeScheduledTaskSpec, task), feature.id))
        if feature.lifecycle is not None:
            register_feature_lifecycle(gateway, feature.lifecycle)
        registered.append(feature.id)

    # The authentication middleware accepts the service token only on these
    # paths and lets declared public paths through without a principal.
    from app.composition.runtime_composition import composition_port_bindings

    bindings = PortBindings.build([*composition_port_bindings(), *port_bindings])
    install_port_bindings(bindings)
    gateway.state.port_bindings = bindings
    gateway.state.internal_route_paths = tuple(dict.fromkeys(internal_paths))
    gateway.state.public_route_paths = tuple(dict.fromkeys(public_paths))
    # Which feature mounted each public operation (PA-2.4); unlisted ones are kernel routes.
    gateway.state.route_owners = dict(sorted(route_owners.items()))
    gateway.state.feature_modules = tuple(registered)
    gateway.state.loaded_feature_modules = tuple(loaded_features)
    gateway.state.job_handler_registry = job_handlers
    from app.events.outbox_relay import outbox_consumer_registry

    gateway.state.outbox_consumers = outbox_consumer_registry(loaded_features)
    jobs = getattr(services, "jobs", None)
    configure_handlers = getattr(jobs, "configure_handler_registry", None)
    if callable(configure_handlers):
        configure_handlers(job_handlers)


def _install_kernel_extensions(gateway) -> None:
    from .event_loop_lag_monitor import register_event_loop_lag_monitor

    register_event_loop_lag_monitor(gateway)


def compose_features(gateway):
    if getattr(gateway.state, "features_registered", False):
        return
    _install_kernel_extensions(gateway)
    _register_feature_modules(gateway)
    gateway.state.features_registered = True
