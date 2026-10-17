"""The Direct and Workflow lanes of typed Chat: single governed actions and named workflows (WP-8.2).
"""
from __future__ import annotations

from .exception_logging import log_recovered_exception
import re
from typing import Any
from app.capabilities.executor import CapabilityGrant
from .capability_requests import AssistantToolRequest
from .router import OmnixRouteDecision
from .workflow_runtime import default_workflow_runtime
from .chat_lane_common import (
    GeneralizedChatResult,
)


def execute_capability(*args, **kwargs):
    # Resolved through chat_bridge at call time: the one place
    # callers and tests replace it.
    from . import chat_bridge

    return chat_bridge.execute_capability(*args, **kwargs)


def review_assistant_tool_request(*args, **kwargs):
    # Resolved through chat_bridge at call time: the one place
    # callers and tests replace it.
    from . import chat_bridge

    return chat_bridge.review_assistant_tool_request(*args, **kwargs)


_HOME_SET = re.compile(r"\bturn\s+(on|off|of)\s+(?:the\s+)?(.+?)[.!?]*$", re.I)


_HOME_STATE = re.compile(r"\b(?:status|state)\s*(?:of|for)?\s*(?:the\s+)?(.+?)[.!?]*$", re.I)


def _workflow_lookup(candidate: str) -> str | None:
    try:
        return default_workflow_runtime().lookup(candidate)
    except Exception as exc:
        log_recovered_exception("workflow lookup", exc, level="DEBUG")
        return None


def _direct_result(session: Any, user_message: Any, decision: OmnixRouteDecision) -> GeneralizedChatResult:
    request = _direct_request(
        str(user_message.content or ""),
        session_id=str(session.id),
        message_id=str(user_message.id),
        capability_id=str(decision.capability_id or ""),
    )
    if request is None:
        return GeneralizedChatResult(
            content="I recognized a direct capability request, but could not resolve its target safely.",
            metadata={
                "generation_status": "completed",
                "omnix_route": decision.model_dump(mode="json"),
                "direct_execution": {"executed": False, "error": "direct_input_not_resolved"},
            },
        )
    review = review_assistant_tool_request(request)
    if not review.allowed:
        return GeneralizedChatResult(
            content=review.result_summary or "That direct action is not allowed.",
            metadata={
                "generation_status": "completed",
                "omnix_route": decision.model_dump(mode="json"),
                "direct_execution": {"executed": False, "error": review.reason},
            },
        )
    if review.approval_required:
        target = str(request.input.get("target") or "the selected resource")
        desired = request.input.get("state")
        verb = f"set {target} {desired}" if desired else f"run {request.action_id} for {target}"
        return GeneralizedChatResult(
            content=f"I can {verb}. Say 'confirm' to run it or 'cancel' to reject it.",
            metadata={
                "generation_status": "completed",
                "omnix_route": decision.model_dump(mode="json"),
                "pending_governed_tool_request": request.model_dump(mode="json"),
                "governed_tool_execution_status": "pending",
                "review_required": True,
                "executes": False,
            },
        )

    payload = execute_capability(
        CapabilityGrant("chat", str(getattr(session, "id", "") or "chat")),
        request,
        user_request=str(user_message.content or ""),
    )
    result = payload.execution_result
    content = result.result_summary or ("Direct capability failed." if result.error else "Direct capability completed.")
    if result.error:
        content = f"{content} {result.error}".strip()
    return GeneralizedChatResult(
        content=content,
        metadata={
            "generation_status": "completed",
            "omnix_route": decision.model_dump(mode="json"),
            "direct_execution": payload.model_dump(mode="json"),
            "review_required": False,
            "executes": result.error is None,
        },
    )


def _direct_request(
    content: str,
    *,
    session_id: str,
    message_id: str,
    capability_id: str,
) -> AssistantToolRequest | None:
    proposal_id = f"direct:{session_id}:{message_id}"
    if capability_id == "home.set_state":
        match = _HOME_SET.search(content)
        if match is None:
            return None
        state = match.group(1).casefold()
        if state == "of":
            state = "off"
        target = _clean_home_target(match.group(2))
        if not target:
            return None
        return AssistantToolRequest(
            tool_id="home",
            action_id="home.set_state",
            session_id=session_id,
            proposal_id=proposal_id,
            input={"target": target, "state": state},
        )
    if capability_id == "home.get_state":
        match = _HOME_STATE.search(content)
        if match is None:
            return None
        target = _clean_home_target(match.group(1))
        if not target:
            return None
        return AssistantToolRequest(
            tool_id="home",
            action_id="home.get_state",
            session_id=session_id,
            proposal_id=proposal_id,
            input={"target": target},
        )
    return None


def _clean_home_target(value: str) -> str:
    target = " ".join(str(value or "").strip().split())
    target = re.sub(r"^(?:kasa|smart)\s+", "", target, flags=re.I)
    target = re.sub(r"\s+(?:please|now)$", "", target, flags=re.I)
    return target.strip(" .!?")


def _workflow_result(session: Any, user_message: Any, decision: OmnixRouteDecision) -> GeneralizedChatResult:
    workflow_id = str(decision.workflow_id or "")
    runtime = default_workflow_runtime()
    try:
        run_id = runtime.start(
            workflow_id,
            {
                "chat_session_id": str(session.id),
                "user_request": str(user_message.content or ""),
                "idempotency_key": f"chat:{session.id}:{user_message.id}",
            },
        )
        state = runtime.get_status(run_id) or {"run_id": run_id, "status": "unknown"}
    except Exception as exc:
        return GeneralizedChatResult(
            content=f"Workflow {workflow_id} failed to start: {type(exc).__name__}: {exc}",
            metadata={
                "generation_status": "completed",
                "omnix_route": decision.model_dump(mode="json"),
                "workflow_run": {"workflow_id": workflow_id, "status": "failed", "error": str(exc)[:500]},
            },
        )
    status = str(state.get("status") or "running")
    if status == "waiting_for_approval":
        content = f"Workflow {workflow_id} is waiting for approval."
    elif status == "completed":
        content = f"Workflow {workflow_id} completed."
    else:
        content = f"Workflow {workflow_id} started with run {run_id}."
    return GeneralizedChatResult(
        content=content,
        metadata={
            "generation_status": "completed",
            "omnix_route": decision.model_dump(mode="json"),
            "workflow_run": state,
        },
    )
