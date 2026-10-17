"""Gateway adapter for the feature-neutral scheduled-task registry."""

from __future__ import annotations

from app.runtime.scheduler import ScheduledTaskSpec, SchedulerRuntime


class GatewaySchedulerRegistryAdapter:
    """Register feature tasks with the process scheduler when one is composed."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway

    def register_task(self, task: ScheduledTaskSpec) -> None:
        runtime: SchedulerRuntime | None = getattr(
            self.gateway.state, "scheduler_runtime", None
        )
        if runtime is not None:
            runtime.register_task(task)


__all__ = ["GatewaySchedulerRegistryAdapter"]
