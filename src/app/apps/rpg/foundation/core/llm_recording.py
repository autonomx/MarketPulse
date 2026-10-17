"""PHASE 5.3 — LLM Record/Replay Layer

Make LLM-backed systems deterministic by:
- Recording prompt -> response pairs during live execution
- Replaying recorded responses during replay/simulation
- Refusing fresh LLM calls in replay mode unless explicitly allowed

This closes the biggest remaining nondeterminism hole.

After this layer:
- Live runs can record model outputs
- Replay/simulation runs can reuse exact recorded outputs
- Missing recorded outputs fail hard instead of silently diverging
- Branch evaluation becomes deterministic across replay
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LLMRecord:
    """One deterministic LLM interaction record."""
    key: str
    response: Any


@dataclass
class LLMRecorder:
    """
    Stores prompt/response mappings for deterministic replay.

    Attributes:
        records: Ordered list of all recorded LLM interactions.
        _map: Quick lookup dictionary keyed by stable JSON hash.
    """
    records: List[LLMRecord] = field(default_factory=list)
    _map: Dict[str, Any] = field(default_factory=dict)

    def make_key(
        self,
        prompt: Any,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create a deterministic key from prompt and context.

        Args:
            prompt: The LLM prompt (any JSON-serializable type).
            context: Optional context dictionary.
            config: Optional call configuration dictionary (method/model/etc).

        Returns:
            Stable JSON string that uniquely identifies this interaction.
        """
        from .determinism import stable_json
        return stable_json({
            "prompt": prompt,
            "context": context or {},
            "config": config or {},
        })

    def record(
        self,
        prompt: Any,
        response: Any,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record an LLM interaction for later replay.

        Args:
            prompt: The original prompt.
            response: The LLM response to record.
            context: Optional context dictionary.
            config: Optional call configuration dictionary (method/model/etc).
        """
        key = self.make_key(prompt, context, config)
        record = LLMRecord(key=key, response=response)
        self.records.append(record)
        self._map[key] = response

    def replay(
        self,
        prompt: Any,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Retrieve a recorded LLM response for replay.

        Args:
            prompt: The original prompt to look up.
            context: Optional context dictionary.
            config: Optional call configuration dictionary (method/model/etc).

        Returns:
            The recorded LLM response.

        Raises:
            KeyError: If no recorded response exists for this prompt/context.
        """
        key = self.make_key(prompt, context, config)
        if key not in self._map:
            raise KeyError(
                f"No recorded LLM response for key: {key[:120]}"
            )
        return self._map[key]

    def load_records(self, records: List[LLMRecord]) -> None:
        """Load a batch of pre-recorded LLM interactions.

        Args:
            records: List of LLMRecord instances to load.
        """
        self.records = list(records)
        self._map = {r.key: r.response for r in self.records}

    def serialize_state(self) -> Dict[str, Any]:
        """Serialize LLM recorder state for snapshots.

        Returns:
            Dictionary containing all recorded LLM interactions.
        """
        return {
            "records": [
                {"key": r.key, "response": r.response}
                for r in self.records
            ]
        }

    def deserialize_state(self, state: Dict[str, Any]) -> None:
        """Restore LLM recorder state from a snapshot.

        Args:
            state: Dictionary containing serialized LLM interactions.
        """
        self.records = [
            LLMRecord(key=r["key"], response=r["response"])
            for r in state.get("records", [])
        ]
        self._map = {r.key: r.response for r in self.records}


class DeterministicLLMClient:
    """
    Wrapper around a real LLM client that supports:
    - Live recording of prompt/response pairs
    - Replay from recorded outputs
    - Strict replay refusal if output is missing
    - Effect-manager awareness for gating live LLM calls

    Usage pattern for any LLM-powered system::

        self.determinism = determinism or DeterminismConfig()
        self.recorder = recorder or LLMRecorder()
        self.llm = DeterministicLLMClient(
            inner_client=real_llm,
            recorder=self.recorder,
            determinism=self.determinism,
        )

    Then all model calls go through::

        self.llm.complete(prompt, context={...})

    Never call the raw model client directly again.
    """

    def __init__(
        self,
        inner_client: Any,
        recorder: LLMRecorder,
        determinism: Any,
        effect_manager: Optional[Any] = None,
    ):
        """Initialize the deterministic LLM client wrapper.

        Args:
            inner_client: The real LLM client with complete/chat/generate methods.
            recorder: LLMRecorder for storing/retrieving responses.
            determinism: DeterminismConfig controlling record/replay behavior.
            effect_manager: Optional EffectManager for gating live LLM calls.
        """
        self.inner = inner_client
        self.recorder = recorder
        self.det = determinism
        self.effect_manager = effect_manager

    def _check_live_llm_allowed(self, prompt: Any, context: Optional[Dict[str, Any]]) -> None:
        """Check if live LLM calls are allowed by the effect manager."""
        if self.effect_manager is not None:
            self.effect_manager.check(
                "live_llm",
                {
                    "prompt": prompt,
                    "context": context or {},
                },
            )

    def complete(
        self,
        prompt: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Execute or replay a completion request.

        In replay mode (use_recorded_llm=True), retrieves the recorded response
        without calling the inner LLM. In live mode with recording enabled
        (record_llm=True), calls the inner LLM and records the response.

        Args:
            prompt: The prompt to send.
            context: Optional context dictionary for key generation.

        Returns:
            LLM response (from inner client or recorded).

        Raises:
            KeyError: If replay mode is on but no recording exists.
            RuntimeError: If live LLM calls are blocked by effect manager.
        """
        call_config = {"method": "complete"}
        if getattr(self.inner, "model", None) is not None:
            call_config["model"] = self.inner.model

        if getattr(self.det, "use_recorded_llm", False):
            return self.recorder.replay(prompt, context, call_config)

        self._check_live_llm_allowed(prompt, context)
        response = self.inner.complete(prompt)

        if getattr(self.det, "record_llm", False):
            self.recorder.record(prompt, response, context, call_config)

        return response

    def chat(
        self,
        messages: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Execute or replay a chat request.

        In replay mode (use_recorded_llm=True), retrieves the recorded response
        without calling the inner LLM. In live mode with recording enabled
        (record_llm=True), calls the inner LLM and records the response.

        Args:
            messages: Chat messages (list or string).
            context: Optional context dictionary for key generation.

        Returns:
            LLM response (from inner client or recorded).

        Raises:
            KeyError: If replay mode is on but no recording exists.
        """
        call_config = {"method": "chat"}
        if getattr(self.inner, "model", None) is not None:
            call_config["model"] = self.inner.model

        if getattr(self.det, "use_recorded_llm", False):
            return self.recorder.replay(messages, context, call_config)

        self._check_live_llm_allowed(messages, context)
        if hasattr(self.inner, "chat"):
            response = self.inner.chat(messages)
        else:
            response = self.inner.complete(messages)

        if getattr(self.det, "record_llm", False):
            self.recorder.record(messages, response, context, call_config)

        return response

    def generate(
        self,
        prompt: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Execute or replay a generate request.

        In replay mode (use_recorded_llm=True), retrieves the recorded response
        without calling the inner LLM. In live mode with recording enabled
        (record_llm=True), calls the inner LLM and records the response.

        Args:
            prompt: The prompt to generate from.
            context: Optional context dictionary for key generation.

        Returns:
            LLM response (from inner client or recorded).

        Raises:
            KeyError: If replay mode is on but no recording exists.
        """
        call_config = {"method": "generate"}
        if getattr(self.inner, "model", None) is not None:
            call_config["model"] = self.inner.model

        if getattr(self.det, "use_recorded_llm", False):
            return self.recorder.replay(prompt, context, call_config)

        self._check_live_llm_allowed(prompt, context)
        if hasattr(self.inner, "generate"):
            response = self.inner.generate(prompt)
        else:
            response = self.inner.complete(prompt)

        if getattr(self.det, "record_llm", False):
            self.recorder.record(prompt, response, context, call_config)

        return response
