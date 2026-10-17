"""PHASE 5.8 — Deterministic Host/Process Boundary

Central deterministic boundary for environment/runtime access.

Covered classes of nondeterminism:
- environment reads
- filesystem reads / listing
- wall clock access
- subprocess / process spawning

Rules:
- No subsystem should directly call os.environ, os.listdir, time.time, subprocess, etc.
- All such access should flow through HostRuntimeGateway.
- In live mode, results may be recorded.
- In replay/simulation mode, recorded results must be used.
- Missing replay data fails hard.
- Host/runtime records become snapshot-able engine state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .determinism import DeterminismConfig, stable_json


@dataclass
class HostCallSpec:
    """Structured description of a host/runtime call."""
    op_name: str
    payload: Any
    context: Optional[Dict[str, Any]] = None
    config: Optional[Dict[str, Any]] = None


@dataclass
class HostRuntimeRecord:
    """One deterministic host/runtime interaction record."""
    key: str
    result: Any


@dataclass
class HostRuntimeRecorder:
    """Stores host/runtime request/result mappings for deterministic replay."""
    records: List[HostRuntimeRecord] = field(default_factory=list)
    _map: Dict[str, Any] = field(default_factory=dict)

    def make_key(
        self,
        op_name: str,
        payload: Any,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> str:
        return stable_json(
            {
                "op_name": op_name,
                "payload": payload,
                "context": context or {},
                "config": config or {},
            }
        )

    def record(
        self,
        op_name: str,
        payload: Any,
        result: Any,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        key = self.make_key(op_name, payload, context, config)
        rec = HostRuntimeRecord(key=key, result=result)
        self.records.append(rec)
        self._map[key] = result

    def replay(
        self,
        op_name: str,
        payload: Any,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Any:
        key = self.make_key(op_name, payload, context, config)
        if key not in self._map:
            raise KeyError(f"No recorded host/runtime result for key: {key[:120]}")
        return self._map[key]

    def load_records(self, records: List[HostRuntimeRecord]) -> None:
        self.records = list(records)
        self._map = {r.key: r.result for r in self.records}

    def serialize_state(self) -> Dict[str, Any]:
        return {
            "records": [
                {"key": r.key, "result": r.result}
                for r in self.records
            ]
        }

    def deserialize_state(self, state: Dict[str, Any]) -> None:
        self.records = [
            HostRuntimeRecord(key=r["key"], result=r["result"])
            for r in state.get("records", [])
        ]
        self._map = {r.key: r.result for r in self.records}


class DeterministicHostRuntimeClient:
    """Wrapper around a real host/runtime provider.

    Supports:
    - live execution with optional recording
    - replay from recorded outputs
    - strict replay refusal if output is missing
    - effect-manager awareness for gating live host calls
    """

    OP_TO_EFFECT = {
        "get_env": "env_read",
        "list_dir": "filesystem_read",
        "read_text": "filesystem_read",
        "wall_time": "wall_clock",
        "monotonic_time": "wall_clock",
        "run_process": "process_spawn",
    }

    def __init__(
        self,
        inner_client: Any,
        recorder: HostRuntimeRecorder,
        determinism: DeterminismConfig,
        effect_manager: Optional[Any] = None,
    ):
        self.inner = inner_client
        self.recorder = recorder
        self.det = determinism
        self.effect_manager = effect_manager

    def _check_live_host_allowed(
        self,
        op_name: str,
        payload: Any,
        context: Optional[Dict[str, Any]],
    ) -> None:
        effect_type = self.OP_TO_EFFECT.get(op_name, "process_spawn")
        if self.effect_manager is not None:
            self.effect_manager.check(
                effect_type,
                {
                    "op_name": op_name,
                    "payload": payload,
                    "context": context or {},
                },
            )

    def call(
        self,
        op_name: str,
        payload: Any,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if getattr(self.det, "use_recorded_host", False):
            return self.recorder.replay(op_name, payload, context, config)

        self._check_live_host_allowed(op_name, payload, context)

        if hasattr(self.inner, "call"):
            result = self.inner.call(op_name, payload)
        else:
            runtime_fn = getattr(self.inner, op_name, None)
            if runtime_fn is None:
                raise RuntimeError(f"Host runtime has no operation '{op_name}'")
            result = runtime_fn(payload)

        if getattr(self.det, "record_host", False):
            self.recorder.record(op_name, payload, result, context, config)

        return result


class HostRuntimeGateway:
    """Single deterministic gateway for all host/runtime-backed work."""

    def __init__(
        self,
        runtime_client: Optional[Any] = None,
        recorder: Optional[HostRuntimeRecorder] = None,
        determinism: Optional[DeterminismConfig] = None,
        effect_manager: Optional[Any] = None,
    ):
        self.inner_client = runtime_client
        self.recorder = recorder or HostRuntimeRecorder()
        self.determinism = determinism or DeterminismConfig()
        self.effect_manager = effect_manager

        self.client: Optional[DeterministicHostRuntimeClient] = None
        if runtime_client is not None:
            self.client = DeterministicHostRuntimeClient(
                inner_client=runtime_client,
                recorder=self.recorder,
                determinism=self.determinism,
                effect_manager=self.effect_manager,
            )

    def set_mode(self, mode: str) -> None:
        if mode in ("replay", "simulation"):
            self.determinism.replay_mode = True
            self.determinism.use_recorded_host = True
        else:
            self.determinism.replay_mode = False
            self.determinism.use_recorded_host = False

    def set_effect_manager(self, effect_manager: Any) -> None:
        self.effect_manager = effect_manager
        if self.client is not None:
            self.client.effect_manager = effect_manager

    def set_host_runtime_recorder(self, recorder: HostRuntimeRecorder) -> None:
        self.recorder = recorder
        if self.client is not None:
            self.client.recorder = recorder

    def set_determinism(self, determinism: DeterminismConfig) -> None:
        self.determinism = determinism
        if self.client is not None:
            self.client.det = determinism

    def call(
        self,
        op_name: str,
        payload: Any,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if self.client is None:
            raise RuntimeError("HostRuntimeGateway has no runtime client configured.")
        return self.client.call(op_name, payload, context=context, config=config)