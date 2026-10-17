from __future__ import annotations

from typing import Any, Dict, Iterable

FAST_PATH_SOURCE = "phase14_16_pre_runtime_intent_fast_path_v1"
PRE_RUNTIME_FAST_PATH_ENABLED = False


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _d(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


