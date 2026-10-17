from __future__ import annotations

import logging

import hashlib
from typing import Any, Dict

from app.apps.rpg.world.social.state import (
    clamp_social_value,
    ensure_relationship,
    ensure_social_state,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

logger = logging.getLogger(__name__)


def _stable_leverage_id(npc_id: str, summary: str, kind: str) -> str:
    digest = hashlib.sha1(f"{npc_id}|{kind}|{summary}".encode("utf-8")).hexdigest()[:16]
    return f"lev:{digest}"


def normalize_social_leverage(value: Dict[str, Any]) -> Dict[str, Any]:
    value = _safe_dict(value)
    npc_id = str(value.get("npc_id") or "")
    kind = str(value.get("kind") or "favor")
    summary = str(value.get("summary") or "")
    leverage_id = str(value.get("leverage_id") or "") or _stable_leverage_id(npc_id, summary, kind)
    return {
        "leverage_id": leverage_id,
        "npc_id": npc_id,
        "source_memory_id": str(value.get("source_memory_id") or ""),
        "kind": kind,
        "summary": summary,
        "strength": clamp_social_value(value.get("strength"), minimum=0, maximum=100),
        "valid": bool(value.get("valid", True)),
        "expires_after_turn": value.get("expires_after_turn"),
        "tags": [str(tag) for tag in _safe_list(value.get("tags")) if str(tag)],
    }


def add_social_leverage(
    simulation_state: Dict[str, Any],
    leverage: Dict[str, Any],
) -> Dict[str, Any]:
    leverage = normalize_social_leverage(leverage)
    npc_id = str(leverage.get("npc_id") or "")
    if not npc_id:
        return {"ok": False, "reason": "missing_npc_id", "leverage": leverage}

    state = ensure_social_state(simulation_state)
    relationship = ensure_relationship(simulation_state, npc_id)
    max_items = int(state.get("max_leverage_per_npc") or 20)
    rows = list(relationship.get("leverage") or [])
    by_id = {str(row.get("leverage_id")): dict(row) for row in rows if isinstance(row, dict)}
    by_id[str(leverage.get("leverage_id"))] = leverage
    relationship["leverage"] = list(by_id.values())[-max_items:]
    return {
        "ok": True,
        "reason": "recorded",
        "leverage": leverage,
    }


def get_social_leverage(
    simulation_state: Dict[str, Any],
    npc_id: str,
    leverage_id: str,
) -> Dict[str, Any] | None:
    relationship = ensure_relationship(simulation_state, npc_id)
    for row in relationship.get("leverage") or []:
        if isinstance(row, dict) and row.get("leverage_id") == leverage_id:
            return normalize_social_leverage(row)
    return None


def _source_memory_exists(
    simulation_state: Dict[str, Any],
    npc_id: str,
    source_memory_id: str,
) -> bool:
    if not source_memory_id:
        return True
    memory_state = _safe_dict(simulation_state.get("npc_memory_state"))
    rows = _safe_dict(memory_state.get("memories_by_subject")).get(npc_id) or []
    for row in rows:
        if isinstance(row, dict) and row.get("memory_id") == source_memory_id:
            return True
    return False


def validate_leverage(
    simulation_state: Dict[str, Any],
    npc_id: str,
    leverage_id: str,
    *,
    actor_id: str = "player",
    request: str | None = None,
    current_turn: int = 0,
) -> Dict[str, Any]:
    leverage = get_social_leverage(simulation_state, npc_id, leverage_id)
    if not leverage:
        return {
            "ok": False,
            "reason": "missing",
            "npc_id": npc_id,
            "actor_id": actor_id,
            "leverage_id": leverage_id,
            "bonus": 0,
        }

    if not leverage.get("valid"):
        return {
            "ok": False,
            "reason": "invalid",
            "npc_id": npc_id,
            "actor_id": actor_id,
            "leverage_id": leverage_id,
            "leverage": leverage,
            "bonus": 0,
        }

    expires_after_turn = leverage.get("expires_after_turn")
    if expires_after_turn is not None:
        try:
            if int(current_turn) > int(expires_after_turn):
                return {
                    "ok": False,
                    "reason": "expired",
                    "npc_id": npc_id,
                    "actor_id": actor_id,
                    "leverage_id": leverage_id,
                    "leverage": leverage,
                    "bonus": 0,
                }
        except Exception:
            logger.debug("suppressed error in %s", "validate_leverage", exc_info=True)

    if not _source_memory_exists(
        simulation_state,
        npc_id,
        str(leverage.get("source_memory_id") or ""),
    ):
        return {
            "ok": False,
            "reason": "missing_source_memory",
            "npc_id": npc_id,
            "actor_id": actor_id,
            "leverage_id": leverage_id,
            "leverage": leverage,
            "bonus": 0,
        }

    request_text = (request or "").lower()
    tags = {str(tag).lower() for tag in leverage.get("tags") or []}
    relevant = True
    if request_text and tags:
        relevant = any(tag in request_text for tag in tags)
        # Keep v1 permissive for broad social leverage.
        if not relevant and leverage.get("kind") in {"favor", "debt", "evidence", "secret"}:
            relevant = True

    if not relevant:
        return {
            "ok": False,
            "reason": "not_relevant",
            "npc_id": npc_id,
            "actor_id": actor_id,
            "leverage_id": leverage_id,
            "leverage": leverage,
            "bonus": 0,
        }

    return {
        "ok": True,
        "reason": "valid",
        "npc_id": npc_id,
        "actor_id": actor_id,
        "leverage_id": leverage_id,
        "leverage": leverage,
        "bonus": int(leverage.get("strength") or 0),
    }