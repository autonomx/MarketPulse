from __future__ import annotations

import json
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


def parse_conversation_line_response(text: str) -> Dict[str, Any]:
    try:
        data = json.loads(text)
    except Exception:
        return {}
    data = _safe_dict(data)
    return {
        "speaker": _safe_str(data.get("speaker")),
        "text": _safe_str(data.get("text")),
        "kind": _safe_str(data.get("kind")) or "statement",
    }


def is_valid_conversation_line(parsed: Dict[str, Any]) -> bool:
    parsed = _safe_dict(parsed)
    return bool(_safe_str(parsed.get("speaker")) and _safe_str(parsed.get("text")))
