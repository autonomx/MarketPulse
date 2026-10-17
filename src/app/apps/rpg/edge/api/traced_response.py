"""The RPG turn's JSON response with its trace headers (moved from foundation.performance_trace, R-3)."""
from __future__ import annotations

import json
from typing import Any

from fastapi.responses import Response

from app.apps.rpg.foundation.performance_trace import (
    current_rpg_pipeline_trace,
    rpg_pipeline_span,
    server_timing_header,
)
from app.apps.rpg.narration.presentation.turn_response import TURN_RESPONSE_MAX_BYTES
from app.apps.rpg.narration.presentation.turn_response_budget import enforce_turn_response_budget


def build_traced_json_response(payload: dict[str, Any], *, status_code: int = 200) -> Response:
    if payload.get("contract_version") == "rpg_turn_response_v2":
        payload = enforce_turn_response_budget(
            payload,
            max_bytes=TURN_RESPONSE_MAX_BYTES,
        )
    with rpg_pipeline_span("turn.response_json_encode") as span:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        budget = raw_response_budget if isinstance(raw_response_budget := payload.get("response_budget"), dict) else {}
        span["response_bytes"] = len(encoded)
        span["contract_version"] = payload.get("contract_version")
        span["response_compacted"] = budget.get("compacted") is True
        span["response_fallback"] = budget.get("fallback") is True
    trace = current_rpg_pipeline_trace()
    headers: dict[str, str] = {"X-Omnix-Rpg-Response-Bytes": str(len(encoded))}
    if trace is not None:
        trace.fields["response_bytes"] = len(encoded)
        trace.fields["response_contract_version"] = payload.get("contract_version")
        trace.fields["response_compacted"] = budget.get("compacted") is True
        summary = trace.summary()
        headers["X-Omnix-Rpg-Trace-Id"] = trace.trace_id
        headers["X-Omnix-Rpg-Attribution-Pct"] = str(summary["attribution_percent"])
        headers["Server-Timing"] = server_timing_header(trace.spans)
    return Response(content=encoded, status_code=status_code, media_type="application/json", headers=headers)
