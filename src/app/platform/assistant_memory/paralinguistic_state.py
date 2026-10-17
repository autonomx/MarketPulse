"""Ephemeral, content-free conversational signals for live companion response style."""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from app.prompts import prompt_template


PARALINGUISTIC_PROMPT_DIRECTIVE_TEMPLATE = prompt_template(
    'assistant_memory.paralinguistic_state.paralinguistic_prompt_directive', "1",
    'Keep the opening brief and yield room for the user to continue.',
)

PARALINGUISTIC_PROMPT_DIRECTIVE_2_TEMPLATE = prompt_template(
    'assistant_memory.paralinguistic_state.paralinguistic_prompt_directive_2', "1",
    'Use a gentle, unhurried response without claiming to know how the user feels.',
)

PARALINGUISTIC_PROMPT_DIRECTIVE_3_TEMPLATE = prompt_template(
    'assistant_memory.paralinguistic_state.paralinguistic_prompt_directive_3', "1",
    'Leave conversational space and avoid rushing into a long answer.',
)

PARALINGUISTIC_PROMPT_DIRECTIVE_4_TEMPLATE = prompt_template(
    'assistant_memory.paralinguistic_state.paralinguistic_prompt_directive_4', "1",
    'Use patient wording and make it easy for the user to clarify or continue.',
)

PARALINGUISTIC_PROMPT_DIRECTIVE_5_TEMPLATE = prompt_template(
    'assistant_memory.paralinguistic_state.paralinguistic_prompt_directive_5', "1",
    'Acknowledge uncertainty without treating it as a durable fact.',
)

PARALINGUISTIC_PROMPT_DIRECTIVE_6_TEMPLATE = prompt_template(
    'assistant_memory.paralinguistic_state.paralinguistic_prompt_directive_6', "1",
    "A light response is appropriate, but do not overstate the user's mood.",
)

PARALINGUISTIC_PROMPT_DIRECTIVE_7_TEMPLATE = prompt_template(
    'assistant_memory.paralinguistic_state.paralinguistic_prompt_directive_7', "1",
    'Match the energy subtly while staying clear and grounded.',
)

PARALINGUISTIC_PROMPT_DIRECTIVE_8_TEMPLATE = prompt_template(
    'assistant_memory.paralinguistic_state.paralinguistic_prompt_directive_8', "1",
    'An immediate conversational signal may be present for this turn.',
)

PARALINGUISTIC_PROMPT_DIRECTIVE_9_TEMPLATE = prompt_template(
    'assistant_memory.paralinguistic_state.paralinguistic_prompt_directive_9', "1",
    'Treat the signal as uncertain and ephemeral; never assert an emotion diagnosis.',
)


ParalinguisticSignalKind = Literal[
    "laughter",
    "hesitation",
    "sigh",
    "reflective_pause",
    "interruption_attempt",
    "uncertain_delivery",
    "excited_delivery",
]

_LAUGHTER = re.compile(r"(?:\[(?:laughs?|laughter)\]|\((?:laughs?|laughter)\)|\b(?:ha){2,}\b)", re.I)
_HESITATION = re.compile(r"(?:\b(?:um+|uh+|erm+|hmm+)\b|\.{3,})", re.I)
_SIGH = re.compile(r"(?:\[(?:sighs?|deep breath)\]|\((?:sighs?|deep breath)\))", re.I)
_UNCERTAIN = re.compile(r"\b(?:maybe|perhaps|i think|i guess|not sure|i'm not sure)\b", re.I)
_STORE_LOCK = threading.RLock()
_STORE: dict[str, tuple[float, "EphemeralCallState"]] = {}
_TTL_SECONDS = 30 * 60
_MAX_SESSIONS = 512


class ParalinguisticSignal(BaseModel):
    """One conservative immediate signal; no transcript or audio is retained."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ParalinguisticSignalKind
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["transcript_marker", "speech_metric", "turn_metadata"]
    observed_at: str


class EphemeralCallState(BaseModel):
    """Current-turn live state that expires and is never an authoritative memory record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    signals: tuple[ParalinguisticSignal, ...] = ()
    private_mode: bool = False
    updated_at: str

    def content_free_diagnostics(self) -> dict[str, Any]:
        return {
            "signal_count": len(self.signals),
            "signal_kinds": [signal.kind for signal in self.signals],
            "max_confidence": max(
                (signal.confidence for signal in self.signals),
                default=0.0,
            ),
            "private_mode": self.private_mode,
            "durable_candidate_created": False,
        }


def _utcnow(value: datetime | None = None) -> datetime:
    resolved = value or datetime.now(timezone.utc)
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc)


def _finite_metric(metadata: dict[str, Any], *names: str) -> float:
    for name in names:
        value = metadata.get(name)
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return 0.0


def _detected_signals(
    transcript: str,
    metadata: dict[str, Any],
    observed_at: str,
) -> list[ParalinguisticSignal]:
    text = transcript[:2_000]
    signals: list[ParalinguisticSignal] = []
    if _LAUGHTER.search(text):
        signals.append(
            ParalinguisticSignal(
                kind="laughter",
                confidence=0.82,
                source="transcript_marker",
                observed_at=observed_at,
            )
        )
    hesitation_matches = len(_HESITATION.findall(text))
    if hesitation_matches:
        signals.append(
            ParalinguisticSignal(
                kind="hesitation",
                confidence=min(0.78, 0.52 + hesitation_matches * 0.08),
                source="transcript_marker",
                observed_at=observed_at,
            )
        )
    if _SIGH.search(text):
        signals.append(
            ParalinguisticSignal(
                kind="sigh",
                confidence=0.72,
                source="transcript_marker",
                observed_at=observed_at,
            )
        )
    if _UNCERTAIN.search(text):
        signals.append(
            ParalinguisticSignal(
                kind="uncertain_delivery",
                confidence=0.62,
                source="transcript_marker",
                observed_at=observed_at,
            )
        )
    if text.count("!") >= 2:
        signals.append(
            ParalinguisticSignal(
                kind="excited_delivery",
                confidence=0.58,
                source="transcript_marker",
                observed_at=observed_at,
            )
        )
    pause_ms = _finite_metric(
        metadata,
        "pause_ms",
        "leading_silence_ms",
        "removed_audio_ms",
    )
    speech_ms = _finite_metric(
        metadata,
        "speech_duration_ms",
        "original_audio_ms",
        "transcribed_audio_ms",
    )
    if pause_ms >= 900 or (speech_ms >= 3_500 and len(text.strip()) <= 32):
        signals.append(
            ParalinguisticSignal(
                kind="reflective_pause",
                confidence=0.56,
                source="speech_metric",
                observed_at=observed_at,
            )
        )
    if any(
        bool(metadata.get(name))
        for name in ("interrupted", "barge_in", "interruption_attempt")
    ):
        signals.append(
            ParalinguisticSignal(
                kind="interruption_attempt",
                confidence=0.9,
                source="turn_metadata",
                observed_at=observed_at,
            )
        )
    by_kind: dict[str, ParalinguisticSignal] = {}
    for signal in signals:
        current = by_kind.get(signal.kind)
        if current is None or signal.confidence > current.confidence:
            by_kind[signal.kind] = signal
    return list(by_kind.values())


def _prune(now_monotonic: float) -> None:
    for key, (expires_at, _) in list(_STORE.items()):
        if expires_at <= now_monotonic:
            _STORE.pop(key, None)
    while len(_STORE) > _MAX_SESSIONS:
        _STORE.pop(next(iter(_STORE)))


def observe_paralinguistic_turn(
    session_id: str,
    transcript: str,
    *,
    metadata: dict[str, Any] | None = None,
    private_mode: bool = False,
    observed_at: datetime | None = None,
) -> EphemeralCallState:
    """Replace immediate state with signals derived from the current turn only."""

    now = _utcnow(observed_at)
    signals = _detected_signals(
        transcript,
        dict(metadata or {}),
        now.isoformat(),
    )
    monotonic_now = time.monotonic()
    with _STORE_LOCK:
        _prune(monotonic_now)
        state = EphemeralCallState(
            session_id=session_id,
            signals=tuple(signals),
            private_mode=private_mode,
            updated_at=now.isoformat(),
        )
        _STORE[session_id] = (monotonic_now + _TTL_SECONDS, state)
        return state


def get_ephemeral_call_state(session_id: str) -> EphemeralCallState | None:
    monotonic_now = time.monotonic()
    with _STORE_LOCK:
        _prune(monotonic_now)
        stored = _STORE.get(session_id)
        return stored[1] if stored is not None else None


def clear_ephemeral_call_state(session_id: str | None = None) -> None:
    with _STORE_LOCK:
        if session_id is None:
            _STORE.clear()
        else:
            _STORE.pop(session_id, None)


def paralinguistic_prompt_directive(
    state: EphemeralCallState,
    *,
    emotional_attunement: Literal["off", "subtle", "expressive"] = "subtle",
) -> str | None:
    """Return a conservative immediate style hint, never a factual emotion claim."""

    if emotional_attunement == "off" or not state.signals:
        return None
    priority = {
        "interruption_attempt": 700,
        "sigh": 600,
        "reflective_pause": 500,
        "hesitation": 400,
        "uncertain_delivery": 350,
        "laughter": 300,
        "excited_delivery": 250,
    }
    signal = max(
        state.signals,
        key=lambda item: (priority.get(item.kind, 0), item.confidence),
    )
    guidance = {
        "interruption_attempt": PARALINGUISTIC_PROMPT_DIRECTIVE_TEMPLATE.text,
        "sigh": PARALINGUISTIC_PROMPT_DIRECTIVE_2_TEMPLATE.text,
        "reflective_pause": PARALINGUISTIC_PROMPT_DIRECTIVE_3_TEMPLATE.text,
        "hesitation": PARALINGUISTIC_PROMPT_DIRECTIVE_4_TEMPLATE.text,
        "uncertain_delivery": PARALINGUISTIC_PROMPT_DIRECTIVE_5_TEMPLATE.text,
        "laughter": PARALINGUISTIC_PROMPT_DIRECTIVE_6_TEMPLATE.text,
        "excited_delivery": PARALINGUISTIC_PROMPT_DIRECTIVE_7_TEMPLATE.text,
    }[signal.kind]
    return "\n".join(
        [
            PARALINGUISTIC_PROMPT_DIRECTIVE_8_TEMPLATE.text,
            guidance,
            PARALINGUISTIC_PROMPT_DIRECTIVE_9_TEMPLATE.text,
        ]
    )


def durable_affect_candidate_allowed(state: EphemeralCallState) -> bool:
    """Raw immediate affect signals never qualify as durable memory on their own."""

    del state
    return False


__all__ = [
    "EphemeralCallState",
    "ParalinguisticSignal",
    "clear_ephemeral_call_state",
    "durable_affect_candidate_allowed",
    "get_ephemeral_call_state",
    "observe_paralinguistic_turn",
    "paralinguistic_prompt_directive",
]
