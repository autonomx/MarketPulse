"""Core module for RPG system — Phase 4 TIMELINE QUERY + BRANCH EVALUATION.

PHASE 1 — STABILIZE: Single game loop authority, EventBus, StoryDirector.
PHASE 2 — REPLAY ENGINE: Event-sourced save/load, replay, time-travel debug.
PHASE 2.5 — SNAPSHOTS: Deterministic ordering, deduplication, hybrid replay.
PHASE 3 — BRANCHING TIMELINES: DAG-based event causality, multiverse graph.
PHASE 4 — TIMELINE QUERY API: Query, evaluate, simulate branches.

ARCHITECTURE RULE:
This system must NOT directly call other systems.
Use EventBus for all cross-system communication.
"""

# Phase 1 — STABILIZE: New single-authority components
# These are the primary exports for the refactored architecture.
# PHASE 5.2 — DETERMINISTIC CLOCK + DETERMINISM
from .clock import DeterministicClock
from .determinism import DeterminismConfig, SeededRNG, compute_deterministic_event_id
from .effects import EffectManager, EffectPolicy, EffectRecord
from .event_bus import Event, EventBus

# PHASE 5.8 — HOST/PROCESS BOUNDARY
from .host_runtime_boundary import (
    DeterministicHostRuntimeClient,
    HostCallSpec,
    HostRuntimeGateway,
    HostRuntimeRecord,
    HostRuntimeRecorder,
)

# PHASE 5.6 — LLM BOUNDARY HARDENING
from .llm_boundary import LLMCallSpec, LLMGateway

# PHASE 5.3 — LLM RECORD/REPLAY LAYER
from .llm_recording import DeterministicLLMClient, LLMRecord, LLMRecorder
from .replay_engine import EventConsumer, ReplayConfig, ReplayEngine
from .snapshot_manager import Snapshot, SnapshotManager

# PHASE 5.5 — STATE BOUNDARIES + EFFECT ISOLATION
from .state_contracts import (
    EffectAware,
    HostRuntimeRecorderAware,
    LLMRecorderAware,
    ReplaySafe,
    SerializableState,
    ToolRuntimeRecorderAware,
)

# PHASE 3 — BRANCHING TIMELINES
from .timeline_graph import TimelineGraph, TimelineNode
from .timeline_metadata import TimelineMetadata

# PHASE 4 — TIMELINE QUERY + BRANCH EVALUATION
from .timeline_query import (
    BranchEvaluator,
    BranchScore,
    DefaultBranchEvaluator,
    EventContext,
    TimelineQueryEngine,
    TimelineSnapshot,
    create_intent_event,
)

# PHASE 5.7 — TOOL/RUNTIME BOUNDARY
from .tool_runtime_boundary import (
    DeterministicToolRuntimeClient,
    ToolCallSpec,
    ToolRuntimeGateway,
    ToolRuntimeRecord,
    ToolRuntimeRecorder,
)

__all__ = [
    # PHASE 1 — STABILIZE: New single-authority components
    # PHASE 5.2 — DETERMINISTIC CLOCK + DETERMINISM
    "DeterministicClock",
    "DeterminismConfig",
    "SeededRNG",
    "compute_deterministic_event_id",
    # PHASE 5.3 — LLM RECORD/REPLAY LAYER
    "LLMRecord",
    "LLMRecorder",
    "DeterministicLLMClient",
    # PHASE 5.6 — LLM BOUNDARY HARDENING
    "LLMCallSpec",
    "LLMGateway",
    # PHASE 5.5 — STATE BOUNDARIES + EFFECT ISOLATION
    "SerializableState",
    "ReplaySafe",
    "EffectAware",
    "LLMRecorderAware",
    "ToolRuntimeRecorderAware",
    "HostRuntimeRecorderAware",
    # PHASE 5.7 — TOOL/RUNTIME BOUNDARY
    "ToolCallSpec",
    "ToolRuntimeRecord",
    "ToolRuntimeRecorder",
    "DeterministicToolRuntimeClient",
    "ToolRuntimeGateway",
    # PHASE 5.8 — HOST/PROCESS BOUNDARY
    "HostCallSpec",
    "HostRuntimeRecord",
    "HostRuntimeRecorder",
    "DeterministicHostRuntimeClient",
    "HostRuntimeGateway",
    "EffectPolicy",
    "EffectRecord",
    "EffectManager",
    "Event",
    "EventBus",
    # PHASE 2 — REPLAY ENGINE
    "ReplayEngine",
    "ReplayConfig",
    "EventConsumer",
    # PHASE 2.5 — SNAPSHOTS & DETERMINISM
    "SnapshotManager",
    "Snapshot",
    # PHASE 3 — BRANCHING TIMELINES
    "TimelineGraph",
    "TimelineNode",
    "TimelineMetadata",
    # PHASE 4 — TIMELINE QUERY + BRANCH EVALUATION
    "TimelineQueryEngine",
    "TimelineSnapshot",
    "BranchScore",
    "BranchEvaluator",
    "DefaultBranchEvaluator",
    "EventContext",
    "create_intent_event",
]