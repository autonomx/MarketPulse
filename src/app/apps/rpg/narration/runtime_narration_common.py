"""Shared helpers for runtime narration contract modules."""
from __future__ import annotations

from typing import Any

NARRATION_FORMAT_VERSION = "rpg_narration_v2"


def _safe_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())
