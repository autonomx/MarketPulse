"""Generalized Omnix agent/workflow runtime foundations.

Names load on first use, so importing a submodule (such as the module's
declarations.py) loads nothing else (PA-2.1).
"""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.capabilities import (
        Capability,
        CapabilityEffect,
        CapabilityExecutionZone,
        CapabilityRegistry,
        CapabilityRisk,
        default_capability_registry,
    )
    from .contracts import (
        AcceptancePlan,
        AgentApproval,
        AgentArtifact,
        AgentEvent,
        AgentRunCommand,
        AgentRunSnapshot,
        AgentRunSpec,
        EvidenceCoverage,
        ModelRef,
        ResourceScope,
        SuccessCriterion,
        WorkerLease,
    )
    from .interfaces import AgentRuntime, WorkflowRuntime
    from .task_graph import (
        TaskEdge,
        TaskGraph,
        TaskGraphCompilation,
        TaskGraphRunSnapshot,
        TaskNode,
        compile_task_graph,
    )
    from .task_graph_optimizer import TaskGraphOptimizationPlan, optimize_task_graph
    from .task_graph_runtime import PostgresTaskGraphRuntime, default_task_graph_runtime

_LAZY_EXPORTS = {
    "Capability": "app.capabilities",
    "CapabilityEffect": "app.capabilities",
    "CapabilityExecutionZone": "app.capabilities",
    "CapabilityRegistry": "app.capabilities",
    "CapabilityRisk": "app.capabilities",
    "default_capability_registry": "app.capabilities",
    "AcceptancePlan": "app.platform.agent_runtime.contracts",
    "AgentApproval": "app.platform.agent_runtime.contracts",
    "AgentArtifact": "app.platform.agent_runtime.contracts",
    "AgentEvent": "app.platform.agent_runtime.contracts",
    "AgentRunCommand": "app.platform.agent_runtime.contracts",
    "AgentRunSnapshot": "app.platform.agent_runtime.contracts",
    "AgentRunSpec": "app.platform.agent_runtime.contracts",
    "EvidenceCoverage": "app.platform.agent_runtime.contracts",
    "ModelRef": "app.platform.agent_runtime.contracts",
    "ResourceScope": "app.platform.agent_runtime.contracts",
    "SuccessCriterion": "app.platform.agent_runtime.contracts",
    "WorkerLease": "app.platform.agent_runtime.contracts",
    "AgentRuntime": "app.platform.agent_runtime.interfaces",
    "WorkflowRuntime": "app.platform.agent_runtime.interfaces",
    "TaskEdge": "app.platform.agent_runtime.task_graph",
    "TaskGraph": "app.platform.agent_runtime.task_graph",
    "TaskGraphCompilation": "app.platform.agent_runtime.task_graph",
    "TaskGraphRunSnapshot": "app.platform.agent_runtime.task_graph",
    "TaskNode": "app.platform.agent_runtime.task_graph",
    "compile_task_graph": "app.platform.agent_runtime.task_graph",
    "TaskGraphOptimizationPlan": "app.platform.agent_runtime.task_graph_optimizer",
    "optimize_task_graph": "app.platform.agent_runtime.task_graph_optimizer",
    "PostgresTaskGraphRuntime": "app.platform.agent_runtime.task_graph_runtime",
    "default_task_graph_runtime": "app.platform.agent_runtime.task_graph_runtime",
}

__all__ = [
    "AcceptancePlan",
    "AgentApproval",
    "AgentArtifact",
    "AgentEvent",
    "AgentRunCommand",
    "AgentRunSnapshot",
    "AgentRunSpec",
    "AgentRuntime",
    "Capability",
    "CapabilityEffect",
    "CapabilityExecutionZone",
    "CapabilityRegistry",
    "CapabilityRisk",
    "EvidenceCoverage",
    "ModelRef",
    "ResourceScope",
    "SuccessCriterion",
    "TaskEdge",
    "TaskGraph",
    "TaskGraphCompilation",
    "TaskGraphOptimizationPlan",
    "TaskGraphRunSnapshot",
    "TaskNode",
    "WorkerLease",
    "WorkflowRuntime",
    "PostgresTaskGraphRuntime",
    "compile_task_graph",
    "default_capability_registry",
    "default_task_graph_runtime",
    "optimize_task_graph",
]


def __getattr__(name: str) -> Any:
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module), name)
