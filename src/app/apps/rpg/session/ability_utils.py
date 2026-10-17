"""Small shared helpers for RPG ability modules."""
from __future__ import annotations

from app.runtime.clock import utc_now

from typing import Any
from app.apps.rpg.foundation.safe_values import safe_list as _safe_list


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold().replace(" ", "_")


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _non_empty_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _append(target: dict[str, Any], key: str, value: dict[str, Any], limit: int = 20) -> None:
    values = _safe_list(target.get(key))
    values.insert(0, value)
    target[key] = values[:limit]
