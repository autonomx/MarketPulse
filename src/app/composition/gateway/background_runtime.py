"""Gateway adapter for the feature-neutral background runtime."""
from __future__ import annotations

from app.runtime.background import (
    BackgroundOwnershipUnavailable,
    BackgroundRegistry,
    BackgroundWorker,
    GatewayBackgroundRuntime,
    register_background_worker as register_runtime_background_worker,
)
from app.runtime.capabilities import RuntimeCapability


class GatewayBackgroundRegistryAdapter(BackgroundRegistry):
    """Adapt gateway state/lifecycle fallback to the neutral registry contract."""

    def __init__(self, gateway):
        self.gateway = gateway

    def register_worker(self, worker: BackgroundWorker) -> None:
        runtime = getattr(self.gateway.state, "background_runtime", None)
        supported = {
            RuntimeCapability.OWN_BACKGROUND_RUNTIME,
            RuntimeCapability.RUN_JOB_WORKERS,
        }
        if len(worker.requires) != 1 or not worker.requires <= supported:
            raise ValueError("Background workers must declare one supported runtime role")

        if runtime is not None:
            register_runtime_background_worker(runtime, worker)
            return

        capabilities = getattr(self.gateway.state, "runtime_capabilities", None)
        if capabilities is not None:
            def denied(*args, **kwargs):
                raise BackgroundOwnershipUnavailable(
                    "No live background owner is composed for this gateway"
                )

            if hasattr(worker.monitor, "start"):
                worker.monitor.start = denied
            return

        from .feature_registry import FeatureLifecycle, register_feature_lifecycle

        register_feature_lifecycle(
            self.gateway,
            FeatureLifecycle(
                worker.name,
                worker.startup,
                worker.shutdown,
                worker.requires,
            ),
        )


__all__ = [
    "BackgroundOwnershipUnavailable",
    "BackgroundWorker",
    "GatewayBackgroundRegistryAdapter",
    "GatewayBackgroundRuntime",
]
