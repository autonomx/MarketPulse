"""Provider probing, text extraction, and JSON parsing for runtime narration."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from .runtime_narration_common import _safe_str
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict

logger = logging.getLogger(__name__)

PROVIDER_METHOD_CANDIDATES = [
    "chat",
    "complete",
    "generate",
    "invoke",
    "generate_response",
    "generate_text",
    "generate_completion",
    "complete_text",
    "ask",
    "prompt",
    "run",
    "call",
    "completion",
    "create_completion",
    "create_chat_completion",
    "chat_completion",
    "send",
    "send_prompt",
    "request",
    "respond",
    "get_response",
    "get_completion",
    "__call__",
]


CHAT_LIKE_PROVIDER_METHODS = {
    "chat",
    "chat_completion",
    "create_chat_completion",
}


PROVIDER_CHILD_CANDIDATES = [
    "client",
    "_client",
    "llm",
    "_llm",
    "model",
    "_model",
    "backend",
    "_backend",
    "provider",
    "_provider",
    "adapter",
    "_adapter",
    "engine",
    "_engine",
    "service",
    "_service",
    "runtime",
    "_runtime",
    "api",
    "_api",
    "connection",
    "_connection",
]


class _ProviderChatMessage:
    """Small adapter for app provider wrappers that expect message.to_dict()."""

    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content

    def to_dict(self) -> Dict[str, str]:
        return {
            "role": self.role,
            "content": self.content,
        }


def _extract_json_object_with_diagnostics_from_provider_text(text: Any) -> Dict[str, Any]:
    """Extract provider JSON and return parse diagnostics.

    Returns:
      {
        "payload": dict,
        "ok": bool,
        "error": str,
        "strategy": str,
        "raw_length": int,
        "contains_candidate_marker": bool,
        "brace_balance": int,
        "ends_with_brace": bool
      }
    """
    raw = _safe_str(text)
    diagnostics: Dict[str, Any] = {
        "payload": {},
        "ok": False,
        "error": "",
        "strategy": "",
        "raw_length": len(raw),
        "contains_candidate_marker": "rpg_narration_candidates_v1" in raw,
        "brace_balance": raw.count("{") - raw.count("}"),
        "ends_with_brace": raw.rstrip().endswith("}"),
    }

    if not raw.strip():
        diagnostics["error"] = "empty_provider_text"
        return diagnostics

    cleaned = raw.strip().lstrip("\ufeff").strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json|JSON)?\s*", "", cleaned).strip()
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            diagnostics.update(
                {
                    "payload": parsed,
                    "ok": True,
                    "strategy": "direct",
                    "error": "",
                }
            )
            return diagnostics
        diagnostics["error"] = "json_root_not_object"
    except Exception as exc:
        diagnostics["error"] = f"direct:{type(exc).__name__}:{exc}"

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        candidate = cleaned[start : end + 1].strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                diagnostics.update(
                    {
                        "payload": parsed,
                        "ok": True,
                        "strategy": "first_brace_to_last_brace",
                        "error": "",
                    }
                )
                return diagnostics
            diagnostics["error"] = "slice_root_not_object"
        except Exception as exc:
            diagnostics["error"] = f"slice:{type(exc).__name__}:{exc}"

    start = cleaned.find("{")
    if start < 0:
        diagnostics["error"] = diagnostics["error"] or "missing_open_brace"
        return diagnostics

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]

        if escape:
            escape = False
            continue

        if char == "\\" and in_string:
            escape = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start : index + 1].strip()
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        diagnostics.update(
                            {
                                "payload": parsed,
                                "ok": True,
                                "strategy": "balanced_braces",
                                "error": "",
                            }
                        )
                        return diagnostics
                    diagnostics["error"] = "balanced_root_not_object"
                except Exception as exc:
                    diagnostics["error"] = f"balanced:{type(exc).__name__}:{exc}"
                return diagnostics

    diagnostics["error"] = diagnostics["error"] or "unterminated_json_object"
    diagnostics["brace_balance_after_scan"] = depth
    diagnostics["in_string_after_scan"] = in_string
    return diagnostics


def _extract_json_object_from_provider_text(text: Any) -> Dict[str, Any]:
    return _safe_dict(
        _extract_json_object_with_diagnostics_from_provider_text(text).get("payload")
    )


def _call_provider_text(provider: Any, prompt: str, *, max_tokens: int = 320) -> str:
    if provider is None:
        return ""
    for method_name in ("chat", "complete", "generate", "invoke"):
        method = getattr(provider, method_name, None)
        if not callable(method):
            continue
        try:
            if method_name == "chat":
                response = method(
                    [
                        {
                            "role": "system",
                            "content": (
                                "You are the presentation-only narration layer for a deterministic RPG. "
                                "Return JSON only."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens,
                )
            else:
                response = method(prompt, max_tokens=max_tokens)
            if isinstance(response, str):
                return response
            if isinstance(response, dict):
                return _safe_str(response.get("content") or response.get("text") or response.get("response"))
            content = getattr(response, "content", "")
            if content:
                return str(content)
        except Exception:
            logger.debug("suppressed error in %s", "_call_provider_text", exc_info=True)
            continue
    return ""


def _provider_shape(provider: Any) -> Dict[str, Any]:
    if provider is None:
        return {"present": False}
    candidates = []
    for candidate in _provider_candidates(provider):
        obj = candidate["object"]
        candidates.append(
            {
                "path": candidate["path"],
                "type": type(obj).__name__,
                "candidate_methods": [
                    name
                    for name in PROVIDER_METHOD_CANDIDATES
                    if callable(getattr(obj, name, None))
                ],
                "public_callables": _public_callable_names(obj),
            }
        )
    return {
        "present": True,
        "type": type(provider).__name__,
        "callable_methods": [
            name
            for name in PROVIDER_METHOD_CANDIDATES
            if callable(getattr(provider, name, None))
        ],
        "candidate_objects": candidates,
        "has_chat": callable(getattr(provider, "chat", None)),
        "has_complete": callable(getattr(provider, "complete", None)),
        "has_generate": callable(getattr(provider, "generate", None)),
        "has_invoke": callable(getattr(provider, "invoke", None)),
        "has_generate_response": callable(getattr(provider, "generate_response", None)),
        "has_generate_text": callable(getattr(provider, "generate_text", None)),
        "has_ask": callable(getattr(provider, "ask", None)),
        "has_call": callable(getattr(provider, "__call__", None)),
    }


def _extract_provider_text(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        for key in ("content", "text", "response", "output", "message"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, dict):
                nested = _extract_provider_text(value)
                if nested:
                    return nested
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            nested = _extract_provider_text(choices[0])
            if nested:
                return nested
        return ""
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        nested = _extract_provider_text(choices[0])
        if nested:
            return nested
    message = getattr(response, "message", None)
    if message is not None:
        nested = _extract_provider_text(message)
        if nested:
            return nested
    delta = getattr(response, "delta", None)
    if delta is not None:
        nested = _extract_provider_text(delta)
        if nested:
            return nested
    for attr in ("content", "text", "response", "output"):
        value = getattr(response, attr, None)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            nested = _extract_provider_text(value)
            if nested:
                return nested
    return ""


def _public_callable_names(value: Any, *, limit: int = 80) -> List[str]:
    if value is None:
        return []
    names: List[str] = []
    try:
        for name in dir(value):
            if name.startswith("__") and name != "__call__":
                continue
            if name.startswith("_") and name not in {"_client", "_provider", "_backend", "_model", "_llm"}:
                continue
            try:
                attr = getattr(value, name)
            except Exception:
                logger.debug("suppressed error in %s", "_public_callable_names", exc_info=True)
                continue
            if callable(attr):
                names.append(name)
            if len(names) >= limit:
                break
    except Exception:
        return names
    return sorted(set(names))


def _safe_child_objects(provider: Any) -> List[Dict[str, Any]]:
    children: List[Dict[str, Any]] = []
    if provider is None:
        return children
    seen = {id(provider)}
    for attr_name in PROVIDER_CHILD_CANDIDATES:
        try:
            child = getattr(provider, attr_name, None)
        except Exception:
            logger.debug("suppressed error in %s", "_safe_child_objects", exc_info=True)
            continue
        if child is None:
            continue
        if id(child) in seen:
            continue
        if isinstance(child, (str, int, float, bool, list, tuple, dict, set)):
            continue
        seen.add(id(child))
        children.append(
            {
                "path": attr_name,
                "object": child,
            }
        )
    return children


def _provider_candidates(provider: Any) -> List[Dict[str, Any]]:
    candidates = [{"path": "root", "object": provider}]
    candidates.extend(_safe_child_objects(provider))
    return candidates


def _try_provider_call(method: Any, method_name: str, prompt: str, *, max_tokens: int) -> Any:
    dict_messages = [
        {
            "role": "system",
            "content": (
                "You are the presentation-only narration layer for a deterministic RPG. "
                "Return JSON only."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    object_messages = [
        _ProviderChatMessage(
            "system",
            "You are the presentation-only narration layer for a deterministic RPG. Return JSON only.",
        ),
        _ProviderChatMessage("user", prompt),
    ]

    attempts = []
    if method_name in CHAT_LIKE_PROVIDER_METHODS:
        attempts.extend(
            [
                lambda: method(object_messages, max_tokens=max_tokens),
                lambda: method(messages=object_messages, max_tokens=max_tokens),
                lambda: method(object_messages),
                lambda: method(dict_messages, max_tokens=max_tokens),
                lambda: method(messages=dict_messages, max_tokens=max_tokens),
                lambda: method(dict_messages),
            ]
        )
    else:
        attempts.extend(
            [
                lambda: method(prompt, max_tokens=max_tokens),
                lambda: method(prompt=prompt, max_tokens=max_tokens),
                lambda: method(text=prompt, max_tokens=max_tokens),
                lambda: method(message=prompt, max_tokens=max_tokens),
                lambda: method(user_message=prompt, max_tokens=max_tokens),
                lambda: method(input=prompt, max_tokens=max_tokens),
                lambda: method(messages=object_messages, max_tokens=max_tokens),
                lambda: method(messages=dict_messages, max_tokens=max_tokens),
                lambda: method(prompt),
                lambda: method(prompt=prompt),
                lambda: method(text=prompt),
                lambda: method(message=prompt),
                lambda: method(user_message=prompt),
                lambda: method(input=prompt),
                lambda: method(messages=object_messages),
                lambda: method(messages=dict_messages),
            ]
        )

    last_error = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return None


def _call_provider_text_with_diagnostics(
    provider: Any,
    prompt: str,
    *,
    max_tokens: int = 320,
) -> Dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "provider_shape": _provider_shape(provider),
        "attempted_methods": [],
        "method_errors": {},
        "selected_method": "",
        "raw_text_length": 0,
        "raw_text_excerpt": "",
        "error": "",
    }
    if provider is None:
        diagnostics["error"] = "provider_not_available"
        return {"text": "", "diagnostics": diagnostics, "parsed_payload": {}}

    any_supported = False
    for candidate in _provider_candidates(provider):
        candidate_path = str(candidate.get("path") or "root")
        candidate_obj = candidate.get("object")
        callable_methods = [
            name
            for name in PROVIDER_METHOD_CANDIDATES
            if callable(getattr(candidate_obj, name, None))
        ]
        if not callable_methods:
            continue
        any_supported = True
        for method_name in callable_methods:
            method = getattr(candidate_obj, method_name)
            attempt_name = f"{candidate_path}.{method_name}"
            diagnostics["attempted_methods"].append(attempt_name)
            try:
                response = _try_provider_call(
                    method,
                    method_name,
                    prompt,
                    max_tokens=max_tokens,
                )
                text = _extract_provider_text(response)
                diagnostics["raw_text_length"] = len(text)
                diagnostics["raw_text_excerpt"] = text[:3000]
                diagnostics["raw_text_tail_excerpt"] = text[-1000:]

                # Parse JSON to add candidate envelope diagnostics
                parse_diagnostics = _extract_json_object_with_diagnostics_from_provider_text(text)
                parsed_payload = _safe_dict(parse_diagnostics.get("payload"))
                if not parsed_payload:
                    logger.warning(
                        "[N101][provider_parse] failed to extract JSON object from provider text len=%s excerpt=%r",
                        len(_safe_str(text)),
                        _safe_str(text)[:700],
                    )
                if "rpg_narration_candidates_v1" in _safe_str(text) and not parsed_payload:
                    logger.error(
                        "[N101][provider_parse] provider text contains candidate marker but JSON extraction failed excerpt=%r",
                        _safe_str(text)[:1200],
                    )
                diagnostics["parsed_format_version"] = _safe_str(_safe_dict(parsed_payload).get("format_version"))
                diagnostics["parsed_keys"] = sorted([str(k) for k in _safe_dict(parsed_payload).keys()])
                diagnostics["parsed_is_candidate_envelope"] = _is_runtime_narration_candidate_envelope(parsed_payload)
                diagnostics["parsed_candidate_shape"] = _candidate_debug_shape(parsed_payload)
                if _safe_str(_safe_dict(parsed_payload).get("format_version")) == "rpg_narration_candidates_v1" or "primary" in _safe_dict(parsed_payload) or "safe_fallback" in _safe_dict(parsed_payload):
                    pass  # already set above
                else:
                    diagnostics["parsed_candidate_shape"] = {}
                diagnostics["parsed_json_ok"] = bool(parse_diagnostics.get("ok"))
                diagnostics["parsed_json_strategy"] = _safe_str(parse_diagnostics.get("strategy"))
                diagnostics["parsed_json_error"] = _safe_str(parse_diagnostics.get("error"))
                diagnostics["parsed_json_raw_length"] = int(parse_diagnostics.get("raw_length") or 0)
                diagnostics["parsed_json_contains_candidate_marker"] = bool(parse_diagnostics.get("contains_candidate_marker"))
                diagnostics["parsed_json_brace_balance"] = int(parse_diagnostics.get("brace_balance") or 0)
                diagnostics["parsed_json_ends_with_brace"] = bool(parse_diagnostics.get("ends_with_brace"))

                if text.strip():
                    diagnostics["selected_method"] = attempt_name
                    return {"text": text, "diagnostics": diagnostics, "parsed_payload": parsed_payload}
                diagnostics["method_errors"][attempt_name] = "provider_returned_empty_text"
            except Exception as exc:
                diagnostics["method_errors"][attempt_name] = f"{type(exc).__name__}: {exc}"

    if not any_supported:
        diagnostics["error"] = "provider_has_no_supported_call_method"
        return {"text": "", "diagnostics": diagnostics, "parsed_payload": {}}

    if diagnostics["method_errors"]:
        diagnostics["error"] = "provider_call_failed"
    else:
        diagnostics["error"] = "provider_returned_empty_text"
    return {"text": "", "diagnostics": diagnostics, "parsed_payload": {}}


def _candidate_debug_shape(value: Any) -> Dict[str, Any]:
    value = _safe_dict(value)
    primary = _safe_dict(value.get("primary"))
    safe_fallback = _safe_dict(value.get("safe_fallback"))
    return {
        "format_version": _safe_str(value.get("format_version")),
        "keys": sorted([str(k) for k in value.keys()]),
        "is_candidate": _is_runtime_narration_candidate_envelope(value),
        "primary_keys": sorted([str(k) for k in primary.keys()]),
        "safe_fallback_keys": sorted([str(k) for k in safe_fallback.keys()]),
        "primary_format_version": _safe_str(primary.get("format_version")),
        "safe_fallback_format_version": _safe_str(safe_fallback.get("format_version")),
    }


def _is_runtime_narration_candidate_envelope(value: Any) -> bool:
    value = _safe_dict(value)
    if _safe_str(value.get("format_version")) != "rpg_narration_candidates_v1":
        return False
    primary = _safe_dict(value.get("primary"))
    safe_fallback = _safe_dict(value.get("safe_fallback"))
    return bool(primary) and bool(safe_fallback)
