"""Conservative structured-response extraction and JSON decoding."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from app.providers.base import ChatResponse

from .errors import (
    ProviderEmptyResponse,
    StructuredDecodeError,
    StructuredResourceError,
)

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def canonical_structured_text(response: ChatResponse) -> str:
    """Return tool arguments when present, otherwise assistant content.

    The function deliberately performs no semantic repair. It accepts only one
    obvious tool argument payload or the provider's textual content.
    """

    tool_calls = response.tool_calls or []
    if tool_calls:
        for call in tool_calls:
            function = call.get("function") if isinstance(call, Mapping) else None
            arguments = function.get("arguments") if isinstance(function, Mapping) else None
            if isinstance(arguments, str) and arguments.strip():
                return arguments.strip()
            if isinstance(arguments, Mapping):
                return json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True)
    content = str(response.content or "").strip()
    if not content:
        raise ProviderEmptyResponse("structured provider returned no content")
    return content


def decode_json_object(content: str) -> dict[str, Any]:
    """Decode one JSON object using only conservative normalization."""

    text = _JSON_FENCE.sub("", str(content or "").strip()).strip()
    if not text:
        raise StructuredDecodeError("structured provider returned empty JSON content")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as direct_error:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise StructuredDecodeError("structured provider returned no JSON object") from direct_error
        candidate = text[start : end + 1]
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as extracted_error:
            raise StructuredDecodeError("structured provider returned invalid JSON") from extracted_error
    if not isinstance(parsed, Mapping):
        raise StructuredDecodeError("structured provider JSON root must be an object")
    return dict(parsed)


def decode_exact_json_object(content: str) -> dict[str, Any]:
    """Decode exactly one bare JSON object without fences or surrounding prose."""

    text = str(content or "").strip()
    if not text:
        raise StructuredDecodeError("structured provider returned empty JSON content")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise StructuredDecodeError(
            "structured provider must return exactly one bare JSON object"
        ) from error
    if not isinstance(parsed, Mapping):
        raise StructuredDecodeError("structured provider JSON root must be an object")
    return dict(parsed)


def validate_json_resources(
    value: Any,
    *,
    max_depth: int | None = None,
    max_nodes: int | None = None,
    max_string_length: int | None = None,
    max_array_length: int | None = None,
) -> None:
    """Iteratively bound decoded JSON before feature-owned Pydantic validation."""

    stack: list[tuple[Any, int, str]] = [(value, 1, "")]
    nodes = 0
    while stack:
        current, depth, path = stack.pop()
        nodes += 1
        if max_nodes is not None and nodes > max_nodes:
            raise StructuredResourceError(
                f"structured provider JSON exceeds node limit at {path or '/'}"
            )
        if max_depth is not None and depth > max_depth:
            raise StructuredResourceError(
                f"structured provider JSON exceeds depth limit at {path or '/'}"
            )
        if isinstance(current, str):
            if max_string_length is not None and len(current) > max_string_length:
                raise StructuredResourceError(
                    f"structured provider JSON string exceeds limit at {path or '/'}"
                )
            continue
        if isinstance(current, Mapping):
            for key, item in current.items():
                child_path = f"{path}/{key}"
                stack.append((item, depth + 1, child_path))
            continue
        if isinstance(current, list):
            if max_array_length is not None and len(current) > max_array_length:
                raise StructuredResourceError(
                    f"structured provider JSON array exceeds limit at {path or '/'}"
                )
            for index, item in enumerate(current):
                stack.append((item, depth + 1, f"{path}/{index}"))
