from __future__ import annotations

from typing import Any

SOURCE = "hermes_assist_mode_policy"
MODES = {"off", "suggest_only", "review_each_step", "auto_low_risk", "manual_override"}
DEFAULT_MODE = "review_each_step"


def hermes_assist_mode_policy(mode: Any, *, checkpoint_reason: str | None = None, high_risk: bool = False) -> dict[str, Any]:
    selected = mode.strip() if isinstance(mode, str) and mode.strip() in MODES else DEFAULT_MODE
    high_risk = high_risk or checkpoint_reason == "high_risk_sequence"
    if selected == "off":
        return {
            "ok": True,
            "source": SOURCE,
            "mode": selected,
            "review_allowed": False,
            "execution_allowed": False,
            "requires_review": True,
            "blocked_reason": "assist_mode_off",
        }
    if selected == "suggest_only":
        return {
            "ok": True,
            "source": SOURCE,
            "mode": selected,
            "review_allowed": True,
            "execution_allowed": False,
            "requires_review": True,
            "blocked_reason": "suggest_only",
        }
    if selected == "auto_low_risk" and not checkpoint_reason and not high_risk:
        return {
            "ok": True,
            "source": SOURCE,
            "mode": selected,
            "review_allowed": True,
            "execution_allowed": True,
            "requires_review": False,
            "blocked_reason": None,
        }
    return {
        "ok": True,
        "source": SOURCE,
        "mode": selected,
        "review_allowed": True,
        "execution_allowed": not high_risk,
        "requires_review": True,
        "blocked_reason": "high_risk_requires_review" if high_risk else None,
    }
