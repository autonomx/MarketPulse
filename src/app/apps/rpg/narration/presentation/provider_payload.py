from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

"""Runtime provider-payload normalization for RPG presentation.

This module is intentionally small and dependency-light so both runtime and
autoplay/test harnesses can share the same soft JSON fallback behavior.
"""

import json
import re
from typing import Any, Dict, List, Tuple
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _strip_fences(text: str) -> str:
    text = _safe_str(text).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    return text


def _json_candidates(text: str) -> List[str]:
    text = _strip_fences(text)
    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    repaired = repair_truncated_json_object(text)
    if repaired and repaired not in candidates:
        candidates.append(repaired)
    return [candidate for candidate in candidates if candidate.strip()]


def repair_truncated_json_object(text: Any) -> str:
    """Best-effort bounded repair for common local-model truncated JSON."""
    source = _strip_fences(_safe_str(text))
    if not source:
        return ""

    start = source.find("{")
    if start >= 0:
        source = source[start:]

    # Drop trailing Markdown or prose after the last likely JSON token.
    source = source.strip()
    source = re.sub(r",\s*([}\]])", r"\1", source)

    # If the model stopped after a property name or colon, remove dangling token.
    source = re.sub(r',\s*"[^"]*"\s*:\s*$', "", source)
    source = re.sub(r',\s*"[^"]*"\s*$', "", source)
    source = re.sub(r':\s*$', ':""', source)

    # Close a dangling string when quote count is odd.
    escaped = False
    quote_count = 0
    for ch in source:
        if ch == "\\" and not escaped:
            escaped = True
            continue
        if ch == '"' and not escaped:
            quote_count += 1
        escaped = False
    if quote_count % 2 == 1:
        source += '"'

    stack: List[str] = []
    in_string = False
    escaped = False
    for ch in source:
        if ch == "\\" and not escaped:
            escaped = True
            continue
        if ch == '"' and not escaped:
            in_string = not in_string
        elif not in_string:
            if ch in "{[":
                stack.append(ch)
            elif ch == "}" and stack and stack[-1] == "{":
                stack.pop()
            elif ch == "]" and stack and stack[-1] == "[":
                stack.pop()
        escaped = False

    while stack:
        opener = stack.pop()
        source += "}" if opener == "{" else "]"

    return source


def extract_json_object(text: Any) -> Tuple[Dict[str, Any], str]:
    for candidate in _json_candidates(_safe_str(text)):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                method = "json" if candidate == _strip_fences(_safe_str(text)) else "json_repaired_or_substring"
                return value, method
        except Exception:
            logger.debug("suppressed error in %s", "extract_json_object", exc_info=True)
            continue
    return {}, ""


def _normalize_npc(value: Any) -> Dict[str, str]:
    npc = _safe_dict(value)
    return {
        "speaker": _safe_str(npc.get("speaker") or npc.get("name")).strip(),
        "line": _safe_str(npc.get("line") or npc.get("dialogue") or npc.get("text")).strip(),
    }


def normalize_runtime_provider_payload(value: Any) -> Dict[str, Any]:
    raw = _safe_dict(value)
    if raw.get("primary") and isinstance(raw.get("primary"), dict):
        raw = _safe_dict(raw.get("primary"))

    return {
        "format_version": _safe_str(raw.get("format_version")) or "rpg_narration_v2",
        "narration": _safe_str(raw.get("narration") or raw.get("narrator")).strip(),
        "action": _safe_str(raw.get("action")).strip(),
        "npc": _normalize_npc(raw.get("npc")),
        "reward": _safe_str(raw.get("reward")).strip() if raw.get("reward") is not None else "",
        "followup_hooks": _safe_list(raw.get("followup_hooks")),
        "presentation_intent": _safe_dict(raw.get("presentation_intent")),
        "current_action_response": _safe_dict(raw.get("current_action_response")),
        "prompt_contract_ack": _safe_dict(raw.get("prompt_contract_ack")),
    }


def _field_level_salvage(text: str) -> Dict[str, Any]:
    def grab_string(key: str) -> str:
        pattern = rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)'
        match = re.search(pattern, text, flags=re.DOTALL)
        if not match:
            return ""
        value = match.group(1)
        try:
            return json.loads('"' + value + '"')
        except Exception:
            return value.replace('\\"', '"').strip()

    npc_block = ""
    npc_match = re.search(r'"npc"\s*:\s*\{(.*?)\}', text, flags=re.DOTALL)
    if npc_match:
        npc_block = npc_match.group(1)

    payload: dict[str, Any] = {
        "format_version": "rpg_narration_v2",
        "narration": grab_string("narration"),
        "action": grab_string("action"),
        "npc": {
            "speaker": "",
            "line": "",
        },
        "reward": grab_string("reward"),
        "followup_hooks": [],
    }
    if npc_block:
        payload["npc"]["speaker"] = grab_string_from_block(npc_block, "speaker")
        payload["npc"]["line"] = grab_string_from_block(npc_block, "line")
    return payload


def grab_string_from_block(block: str, key: str) -> str:
    pattern = rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)'
    match = re.search(pattern, block, flags=re.DOTALL)
    if not match:
        return ""
    value = match.group(1)
    try:
        return json.loads('"' + value + '"')
    except Exception:
        return value.replace('\\"', '"').strip()


def parse_runtime_provider_payload(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return {
            "ok": True,
            "payload": normalize_runtime_provider_payload(raw),
            "method": "dict",
            "soft_fallback": False,
        }

    text = _safe_str(raw).strip()
    if not text:
        return {"ok": False, "payload": {}, "method": "empty", "soft_fallback": False}

    parsed, method = extract_json_object(text)
    if parsed:
        return {
            "ok": True,
            "payload": normalize_runtime_provider_payload(parsed),
            "method": method,
            "soft_fallback": method != "json",
        }

    salvaged = normalize_runtime_provider_payload(_field_level_salvage(text))
    useful = bool(salvaged.get("narration") or _safe_dict(salvaged.get("npc")).get("line"))
    return {
        "ok": useful,
        "payload": salvaged if useful else {},
        "method": "field_level_salvage" if useful else "unparseable",
        "soft_fallback": bool(useful),
    }
