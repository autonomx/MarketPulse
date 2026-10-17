"""Payload shells for Hermes assistant capability routes."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .models import AssistantToolRequest, AssistantToolResult, AssistantToolReviewDecision
from .result_context import AssistantToolResultContext


class HermesAssistantToolRequestEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_request: str = ""
    request: AssistantToolRequest = Field(default_factory=lambda: AssistantToolRequest(tool_id="", action_id=""))


class HermesAssistantToolReviewPayload(BaseModel):
    user_request: str = ""
    selected_tool_id: str
    selected_action_id: str
    tool_request: AssistantToolRequest
    approval_decision: AssistantToolReviewDecision


class HermesAssistantToolExecutePayload(BaseModel):
    user_request: str = ""
    selected_tool_id: str
    selected_action_id: str
    approval_decision: AssistantToolReviewDecision
    execution_result: AssistantToolResult
    result_context: AssistantToolResultContext | None = None
    state_changed: bool = False
