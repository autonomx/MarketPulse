"""Application lifecycle orchestration, independent of route registration."""

import asyncio
from contextlib import asynccontextmanager
import inspect
import logging
import time

from app.runtime.logging import runtime_transition

logger = logging.getLogger(__name__)


async def _invoke(callback):
    result = callback()
    if inspect.isawaitable(result):
        await result


@asynccontextmanager
async def gateway_lifespan(app, *, get_chat_store, get_job_store, recover_jobs):
    from app.runtime.capabilities import RuntimeCapability
    capabilities = getattr(app.state, "runtime_capabilities", None)
    config = getattr(app.state, 'runtime_config', None)
    role = config.gateway_role.value if config is not None else 'test'
    recovered = 0
    if capabilities is None or capabilities.allows(RuntimeCapability.RUN_RECOVERY):
        owner = getattr(app.state, 'execution_owner', None)
        recovered = await asyncio.to_thread(
            owner.run_recovery if owner is not None else lambda: recover_jobs(get_chat_store(), get_job_store())
        )
    if recovered:
        logger.warning("Recovered %s abandoned Chat generation jobs", recovered)
    started_features = []
    try:
        background = getattr(app.state, "background_runtime", None)
        if background is not None:
            await background.startup()
        for feature in getattr(app.state, 'feature_lifecycles', ()):
            started = time.monotonic()
            if capabilities is not None:
                capabilities.require(*feature.requires)
            started_features.append(feature)
            for callback in feature.startup:
                await _invoke(callback)
            runtime_transition(logger, component=feature.name, role=role, transition='started', started_at=started)
        for callback in app.router.on_startup:
            await _invoke(callback)
        app.state.runtime_started = True
        yield
    finally:
        app.state.runtime_started = False
        readers = getattr(app.state, "event_readers", None)
        if readers is not None:
            readers.stop()
        failures = []
        callbacks = list(reversed(app.router.on_shutdown))
        callbacks.extend(callback for feature in reversed(started_features) for callback in reversed(feature.shutdown))
        for callback in callbacks:
            try:
                await _invoke(callback)
            except Exception as exc:
                failures.append(exc)
                runtime_transition(logger, component='gateway_lifecycle', role=role,
                                   transition='stop_failed', error=exc, level='warning')
        if failures:
            raise RuntimeError("Gateway shutdown callbacks failed") from failures[0]
