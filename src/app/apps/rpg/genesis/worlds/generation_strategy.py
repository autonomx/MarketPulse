"""Stable identities for World Forge provider/recovery strategies."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def world_forge_strategy_identity(
    *,
    provider: str,
    model: str,
    selected_mode: str,
    prompt_version: str,
    contract_descriptor: Mapping[str, Any],
) -> str:
    payload = {
        "provider": str(provider or ""),
        "model": str(model or ""),
        "selected_mode": str(selected_mode or ""),
        "prompt_version": str(prompt_version or ""),
        "contract_descriptor": dict(contract_descriptor),
        "recovery_strategy": "deterministic_then_same_model_single_correction_v2",
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = ["world_forge_strategy_identity"]
