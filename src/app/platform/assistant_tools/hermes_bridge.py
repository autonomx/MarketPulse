"""Bridge helpers for Hermes assistant capability routes.

Execution goes through ``app.capabilities.executor`` (WP-4.5).
"""
from __future__ import annotations

from .gate import review_assistant_tool_request
from .hermes_payloads import HermesAssistantToolReviewPayload
from .models import AssistantToolRequest


def hermes_assistant_tool_review_payload(
    user_request: str,
    request: AssistantToolRequest,
) -> HermesAssistantToolReviewPayload:
    decision = review_assistant_tool_request(request)
    return HermesAssistantToolReviewPayload(
        user_request=user_request,
        selected_tool_id=request.tool_id,
        selected_action_id=request.action_id,
        tool_request=request,
        approval_decision=decision,
    )
