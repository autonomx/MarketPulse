"""PHASE 5.7 — Deterministic Tool/Runtime Boundary

Central deterministic boundary for all tool/runtime access.

Rules:
- No subsystem should call raw tools/runtime providers directly.
- All tool/runtime access must pass through ToolRuntimeGateway.
- In live mode, calls may be recorded.
- In replay/simulation mode, recorded outputs must be used.
- If replay requests a missing output, fail hard.
- Tool/runtime records become snapshot-able engine state.
- Effect policy and tool/runtime policy stay aligned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .determinism import DeterminismConfig, stable_json


@dataclass
class ToolCallSpec:
    """Structured description of a tool/runtime call."""
    tool_name: str
    payload: Any
    context: Optional[Dict[str, Any]] = None
    config: Optional[Dict[str, Any]] = None


@dataclass
class ToolRuntimeRecord:
    """One deterministic tool/runtime interaction record."""
    key: str
    result: Any


@dataclass
class ToolRuntimeRecorder:
    """Stores tool/runtime request/result mappings for deterministic replay."""
    records: List[ToolRuntimeRecord] = field(default_factory=list)
    _map: Dict[str, Any] = field(default_factory=dict)

    def make_key(
        self,
        tool_name: str,
        payload: Any,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> str:
        return stable_json({
            "tool_name": tool_name,
            "payload": payload,
            "context": context or {},
            "config": config or {},
        })

    def record(
        self,
        tool_name: str,
        payload: Any,
        result: Any,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        key = self.make_key(tool_name, payload, context, config)
        rec = ToolRuntimeRecord(key=key, result=result)
        self.records.append(rec)
        self._map[key] = result

    def replay(
        self,
        tool_name: str,
        payload: Any,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Any:
        key = self.make_key(tool_name, payload, context, config)
        if key not in self._map:
            raise KeyError(f"No recorded tool/runtime result for key: {key[:120]}")
        return self._map[key]

    def load_records(self, records: List[ToolRuntimeRecord]) -> None:
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
            ToolRuntimeRecord(key=r["key"], result=r["result"])
            for r in state.get("records", [])
        ]
        self._map = {r.key: r.result for r in self.records}


class DeterministicToolRuntimeClient:
    """Wrapper around a real tool/runtime provider.

    Supports:
    - live execution with optional recording
    - replay from recorded outputs
    - strict replay refusal if output is missing
    - effect-manager awareness for gating live tool/runtime calls
    """

    def __init__(
        self,
        inner_client: Any,
        recorder: ToolRuntimeRecorder,
        determinism: DeterminismConfig,
        effect_manager: Optional[Any] = None,
    ):
        self.inner = inner_client
        self.recorder = recorder
        self.det = determinism
        self.effect_manager = effect_manager

    def _check_live_tool_allowed(
        self,
        tool_name: str,
        payload: Any,
        context: Optional[Dict[str, Any]],
    ) -> None:
        if self.effect_manager is not None:
            self.effect_manager.check(
                "tool_call",
                {
                    "tool_name": tool_name,
                    "payload": payload,
                    "context": context or {},
                },
            )

    def call(
        self,
        tool_name: str,
        payload: Any,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if getattr(self.det, "use_recorded_tools", False):
            return self.recorder.replay(tool_name, payload, context, config)

        self._check_live_tool_allowed(tool_name, payload, context)

        if hasattr(self.inner, "call"):
            result = self.inner.call(tool_name, payload)
        else:
            tool_fn = getattr(self.inner, tool_name, None)
            if tool_fn is None:
                raise RuntimeError(f"Tool runtime has no tool '{tool_name}'")
            result = tool_fn(payload)

        if getattr(self.det, "record_tools", False):
            self.recorder.record(tool_name, payload, result, context, config)

        return result


class ToolRuntimeGateway:
    """Single deterministic gateway for all tool/runtime-backed work."""

    def __init__(
        self,
        runtime_client: Optional[Any] = None,
        recorder: Optional[ToolRuntimeRecorder] = None,
        determinism: Optional[DeterminismConfig] = None,
        effect_manager: Optional[Any] = None,
    ):
        self.inner_client = runtime_client
        self.recorder = recorder or ToolRuntimeRecorder()
        self.determinism = determinism or DeterminismConfig()
        self.effect_manager = effect_manager

        self.client: Optional[DeterministicToolRuntimeClient] = None
        if runtime_client is not None:
            self.client = DeterministicToolRuntimeClient(
                inner_client=runtime_client,
                recorder=self.recorder,
                determinism=self.determinism,
                effect_manager=self.effect_manager,
            )

    def set_mode(self, mode: str) -> None:
        if mode in ("replay", "simulation"):
            self.determinism.replay_mode = True
            self.determinism.use_recorded_tools = True
        else:
            self.determinism.replay_mode = False
            self.determinism.use_recorded_tools = False

    def set_effect_manager(self, effect_manager: Any) -> None:
        self.effect_manager = effect_manager
        if self.client is not None:
            self.client.effect_manager = effect_manager

    def set_tool_runtime_recorder(self, recorder: ToolRuntimeRecorder) -> None:
        self.recorder = recorder
        if self.client is not None:
            self.client.recorder = recorder

    def set_determinism(self, determinism: DeterminismConfig) -> None:
        self.determinism = determinism
        if self.client is not None:
            self.client.det = determinism

    def call(
        self,
        tool_name: str,
        payload: Any,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if self.client is None:
            raise RuntimeError("ToolRuntimeGateway has no runtime client configured.")
        return self.client.call(tool_name, payload, context=context, config=config)