"""Explicit deterministic World Forge fixture-mode detection.

Production code must never infer deterministic lore as a fallback. Test and offline
workflows may opt in by setting ``RPG_TEST_MODE`` to a recognised fixture value.
"""
from __future__ import annotations
from app.config.env import environment as _environment

from typing import Mapping

_TEST_VALUES = {"deterministic", "test", "offline"}


def deterministic_world_forge_test_mode(
    environ: Mapping[str, str] | None = None,
) -> bool:
    env = _environment() if environ is None else environ
    return str(env.get("RPG_TEST_MODE") or "").strip().casefold() in _TEST_VALUES


__all__ = ["deterministic_world_forge_test_mode"]
