"""Run-scoped PostgreSQL-authoritative broker for external agent capabilities."""
from __future__ import annotations

from app.capabilities.registry import capability_definition_hash

import hmac
from app.config.env import env_str as _env_str

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.capabilities import default_capability_registry
from app.platform.assistant_tools.contracts import review_assistant_tool_request
from app.capabilities.executor import LEGACY_APPROVER, CapabilityGrant, execute_capability
from .capability_requests import AssistantToolRequest, AssistantToolResult
from app.observability.tracing import set_span_attributes
from app.persistence.unit_of_work import unit_of_work
from app.security.run_tokens import RunTokenClaims, issue_run_token, verify_run_token

from .budget import AgentBudgetError, default_agent_budget_manager
from .contracts import AgentApproval, AgentEvent, RunChangeSet
from .evidence import (
    build_evidence_receipt,
    fallback_capabilities_for_requirement,
    is_evidence_capability,
    resolve_evidence_call,
)
from .repository import PostgresAgentRunRepository
from .run_token_guard import require_live_run_token
from .service import default_agent_run_service

router = APIRouter(prefix="/api/agent-runs", tags=["agent-runtime"])


# Returned by an extracted step that did not settle its caller.
_CONTINUE = object()


class BrokerToolBudgetRequest(BaseModel):
    tool_name: str


class BrokerToolBudgetResponse(BaseModel):
    allowed: bool = True
    usage: dict[str, Any] = Field(default_factory=dict)


class BrokerCommandAuthorizationRequest(BaseModel):
    command: str
    cwd: str | None = None
    workspace_root: str | None = None


class BrokerCommandAuthorizationResponse(BaseModel):
    allowed: bool = False
    approval_required: bool = False
    approval_id: str | None = None
    reason: str | None = None


class BrokerWorkspaceAuthorizationRequest(BaseModel):
    tool_name: Literal["edit", "write"]
    input: dict[str, Any] = Field(default_factory=dict)
    workspace_root: str | None = None


class BrokerRunChangeSetResponse(BaseModel):
    change_set: RunChangeSet
    patch: str


_COMMAND_FORBIDDEN_SYNTAX = re.compile(r"[\r\n;&|><`]|\$\(")
_COMMAND_ENVIRONMENT_EXPANSION = re.compile(
    r"(?:\$\{|\$[A-Za-z_]|%[A-Za-z_][A-Za-z0-9_]*%|~[\\/])"
)


def _command_authorization_rejection(command: str) -> str | None:
    normalized = str(command or "").strip()
    if not normalized:
        return "Omnix command policy requires a non-empty command."
    if _COMMAND_FORBIDDEN_SYNTAX.search(normalized):
        return (
            "Omnix command policy blocks shell chaining, pipes, redirection, "
            "command substitution, and multi-command syntax."
        )
    if _COMMAND_ENVIRONMENT_EXPANSION.search(normalized):
        return "Omnix command policy blocked an unsafe environment/path expansion."
    return None


class BrokerCapabilityRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    proposal_id: str | None = None
    approval_id: str | None = None


class BrokerCapabilityResponse(BaseModel):
    capability_id: str
    executed: bool = False
    approval_required: bool = False
    approval_id: str | None = None
    execution_key: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)


_BROWSER_ASSERTION_REQUIRED_INPUTS: dict[str, tuple[str, ...]] = {
    "browser.assert_text_contains": ("selector", "expected"),
    "browser.assert_text_not_contains": ("selector", "expected"),
    "browser.assert_attribute_contains": ("selector", "attribute", "expected"),
    "browser.assert_url_contains": ("expected",),
}


def _normalize_capability_input(
    capability_id: str,
    request: BrokerCapabilityRequest,
) -> BrokerCapabilityRequest:
    """Canonicalize and validate deterministic browser assertion arguments.

    Some model providers emit ``expected_text`` for text assertions even though
    the canonical capability schema uses ``expected``. Normalize that harmless
    alias before deriving the execution identity. Invalid assertions then fail
    at the broker boundary and never become durable validation executions.
    """

    required = _BROWSER_ASSERTION_REQUIRED_INPUTS.get(capability_id)
    if required is None:
        return request
    bounded = dict(request.input)
    if capability_id in {
        "browser.assert_text_contains",
        "browser.assert_text_not_contains",
    } and "expected_text" in bounded:
        alias = bounded.pop("expected_text")
        canonical = bounded.get("expected")
        if canonical is not None and canonical != "" and str(canonical) != str(alias):
            raise HTTPException(
                status_code=422,
                detail="agent_browser_assertion_conflicting_expected_text",
            )
        if canonical is None or canonical == "":
            bounded["expected"] = alias
    missing = [
        field
        for field in required
        if bounded.get(field) is None or bounded.get(field) == ""
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"agent_browser_assertion_missing_input:{','.join(missing)}",
        )
    return request.model_copy(update={"input": bounded})


def _execution_key(run_id: str, capability_id: str, request: BrokerCapabilityRequest) -> str:
    if request.proposal_id:
        raw = str(request.proposal_id).strip()
    else:
        payload = json.dumps(
            {"capability_id": capability_id, "input": request.input},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        raw = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"agent:{run_id}:{raw}"


def _revision_scoped_evidence_execution_key(
    execution_key: str,
    task_revision_id: str | None,
) -> str:
    if not task_revision_id:
        return execution_key
    revision_digest = hashlib.sha256(
        task_revision_id.encode("utf-8")
    ).hexdigest()[:16]
    return f"{execution_key}:evidence-revision:{revision_digest}"


def _approver(approval: AgentApproval) -> str | None:
    """The principal whose recorded decision approved ``approval`` (WP-4.5)."""
    if approval.state != "approved":
        return None
    return str(approval.resolution_payload.get("decided_by") or LEGACY_APPROVER)


def _approved_execution_key(
    run_id: str,
    canonical: str,
    request: BrokerCapabilityRequest,
    approval: AgentApproval,
) -> str:
    if approval.capability_id != canonical:
        raise HTTPException(status_code=403, detail="agent_approval_mismatch")
    approved_input = approval.request_payload.get("input")
    if not isinstance(approved_input, dict) or approved_input != request.input:
        raise HTTPException(status_code=403, detail="agent_approval_input_mismatch")
    execution_key = str(approval.request_payload.get("execution_key") or "").strip()
    if not execution_key or not execution_key.startswith(f"agent:{run_id}:"):
        raise HTTPException(status_code=409, detail="agent_approval_execution_identity_invalid")
    return execution_key


def _scope_candidate_values(
    resource_type: str,
    payload: dict[str, Any],
) -> list[object]:
    resource = str(resource_type or "").strip().casefold()
    aliases: dict[str, tuple[str, ...]] = {
        "repository": ("repository", "repo"),
        "repo": ("repository", "repo"),
        "device": ("device_id", "target", "id"),
        "target": ("target", "id"),
        "message": ("message_id", "id"),
        "thread": ("thread_id", "id"),
        "event": ("event_id", "id"),
        "recipient": ("recipient", "to", "email"),
        "contact": ("contact_id", "email", "id"),
        "account": ("account_id", "id"),
        "branch": ("branch",),
        "pull_request": ("number", "pull_request_id", "id"),
    }
    keys = list(
        dict.fromkeys(
            (
                resource,
                f"{resource}_id" if resource else "",
                "resource_id",
                *aliases.get(resource, ()),
            )
        )
    )
    values: list[object] = []
    for key in keys:
        if not key or key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)
    return values


def _constraint_matches(actual: object, expected: object) -> bool:
    if isinstance(expected, list):
        return any(_constraint_matches(actual, item) for item in expected)
    if isinstance(actual, list):
        return any(_constraint_matches(item, expected) for item in actual)
    if isinstance(expected, str):
        return str(actual or "").strip().casefold() == expected.strip().casefold()
    return actual == expected


def _request_within_resource_scopes(
    snapshot,
    capability_id: str,
    payload: dict[str, Any],
) -> bool:
    registry = default_capability_registry()
    scopes = [
        scope
        for scope in snapshot.spec.resource_scopes
        if registry.canonical_id(scope.capability) == capability_id
    ]
    if not scopes:
        return True
    for scope in scopes:
        if scope.resource_id != "*":
            candidates = _scope_candidate_values(scope.resource_type, payload)
            if not any(
                _constraint_matches(candidate, scope.resource_id)
                for candidate in candidates
            ):
                continue
        if not all(
            key in payload and _constraint_matches(payload.get(key), expected)
            for key, expected in scope.constraints.items()
        ):
            continue
        return True
    return False


def _evidence_requirement_for_capability(policy, capability_id: str):
    from .evidence import SOURCE_CAPABILITIES

    for requirement in policy.requirements:
        candidates = [requirement.source_class]
        if requirement.fallback_policy != "fail_closed":
            candidates.extend(option.source_class for option in requirement.acceptable_sources)
        for source_class in candidates:
            resolved = SOURCE_CAPABILITIES.get(source_class)
            if resolved and resolved[0] == capability_id:
                return requirement
    return None


def _bind_authoritative_capability_input(
    snapshot,
    capability_id: str,
    request: BrokerCapabilityRequest,
    *,
    policy=None,
    requirement=None,
) -> BrokerCapabilityRequest:
    bounded = dict(request.input)
    if requirement is None and policy is not None:
        requirement = _evidence_requirement_for_capability(policy, capability_id)
    subject = requirement.subject if requirement is not None else None

    def bind_exact(key: str, value: object) -> None:
        if value in {None, ""}:
            return
        if key in bounded and str(bounded.get(key)).casefold() != str(value).casefold():
            raise HTTPException(
                status_code=403,
                detail=f"agent_evidence_subject_input_mismatch:{key}",
            )
        bounded[key] = value

    if capability_id == "trading.market_quote" and subject is not None:
        bind_exact("ticker", subject.qualifiers.get("ticker"))
    elif capability_id in {"github.inspect_ci", "github.read_repo"} and subject is not None:
        bind_exact("repository", subject.canonical_id)
        requested_ref = subject.qualifiers.get("requested_ref")
        resolved_commit = subject.qualifiers.get("resolved_commit")
        bind_exact("requested_ref", requested_ref)
        if resolved_commit:
            if "sha" in bounded and str(bounded.get("sha")).casefold() != str(resolved_commit).casefold():
                raise HTTPException(
                    status_code=403,
                    detail="agent_evidence_subject_input_mismatch:sha",
                )
            bind_exact("ref", resolved_commit)
            bounded["resolved_commit"] = resolved_commit

    if capability_id == "github.push":
        workspace = snapshot.spec.workspace
        if workspace is None:
            raise HTTPException(
                status_code=409,
                detail="agent_push_requires_issued_workspace",
            )
        if "worktree" in bounded:
            raise HTTPException(
                status_code=403,
                detail="agent_push_worktree_is_omnix_managed",
            )
        if "remote" in bounded:
            raise HTTPException(
                status_code=403,
                detail="agent_push_remote_is_omnix_managed",
            )
        issued_worktree = workspace.worktree or workspace.root
        bounded.update({
            "worktree": issued_worktree,
            "remote": "origin",
        })
    return request.model_copy(update={"input": bounded})

def _effective_evidence_context(service, snapshot):
    with unit_of_work(service.database) as work:
        repository = PostgresAgentRunRepository(work.connection, service.context)
        latest_revision = repository.latest_task_revision(snapshot.run_id)
        receipts = repository.list_evidence_receipts(snapshot.run_id)
        work.rollback()
    policy = (
        latest_revision.evidence_decision.policy
        if latest_revision is not None
        else snapshot.spec.evidence_policy
    )
    revision_id = latest_revision.revision_id if latest_revision is not None else None
    started_at = latest_revision.created_at if latest_revision is not None else snapshot.created_at
    return policy, revision_id, started_at, receipts


def _reserve_evidence_retrieval_budget(
    repository: PostgresAgentRunRepository,
    run_id: str,
    capability_id: str,
    execution_key: str,
    request: BrokerCapabilityRequest,
    *,
    policy,
    revision_id: str | None,
    started_at,
) -> BrokerCapabilityRequest:
    if not is_evidence_capability(capability_id):
        return request
    if not revision_id:
        raise HTTPException(
            status_code=409,
            detail="agent_evidence_task_revision_unavailable",
        )
    retrieval = policy.retrieval
    now = datetime.now(timezone.utc)
    if started_at is not None:
        age = (now - started_at.astimezone(timezone.utc)).total_seconds()
        if age > retrieval.max_wall_time_seconds:
            raise HTTPException(
                status_code=429,
                detail="agent_evidence_retrieval_wall_time_exceeded",
            )

    bounded = dict(request.input)
    if capability_id == "research.web_search":
        try:
            requested_sources = int(bounded.get("max_results", 5))
        except (TypeError, ValueError):
            requested_sources = 5
        try:
            requested_extracts = int(bounded.get("max_extracts", retrieval.max_extracts))
        except (TypeError, ValueError):
            requested_extracts = retrieval.max_extracts
        requested_sources = max(1, min(requested_sources, 10))
        requested_extracts = max(0, min(requested_extracts, 4))
    else:
        requested_sources = 1
        requested_extracts = 0

    reservation = repository.reserve_evidence_query(
        run_id,
        revision_id,
        execution_key,
        max_queries=retrieval.max_queries,
        max_sources=retrieval.max_sources,
        max_extracts=retrieval.max_extracts,
        requested_sources=requested_sources,
        requested_extracts=requested_extracts,
    )
    if not reservation.get("allowed"):
        reason = str(reservation.get("reason") or "query_budget_exceeded")
        detail = {
            "query_budget_exceeded": "agent_evidence_retrieval_query_budget_exceeded",
            "source_budget_exceeded": "agent_evidence_retrieval_source_budget_exceeded",
        }.get(reason, "agent_evidence_retrieval_budget_exceeded")
        raise HTTPException(status_code=429, detail=detail)

    if capability_id == "research.web_search":
        bounded["max_results"] = int(reservation["reserved_sources"])
        bounded["max_extracts"] = int(reservation["reserved_extracts"])
    return request.model_copy(update={"input": bounded})


def _evidence_result_usage(result_payload: dict[str, Any], *, failed: bool) -> tuple[int, int]:
    if failed:
        return 0, 0
    output = result_payload.get("output")
    output = dict(output) if isinstance(output, dict) else {}
    diagnostics = output.get("diagnostics")
    diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
    items = output.get("items")
    source_count = diagnostics.get("source_count", output.get("source_count"))
    try:
        sources = int(cast(Any, source_count))
    except (TypeError, ValueError):
        sources = len(items) if isinstance(items, list) else 1
    try:
        extracts = int(diagnostics.get("extracted_pages", 0))
    except (TypeError, ValueError):
        extracts = 0
    return max(0, sources), max(0, extracts)


def _review_with_run_policy(
    request: AssistantToolRequest,
    run_policy: str,
):
    """Apply run policy as an approval floor, never as a weakening override."""
    base_request = request
    base = review_assistant_tool_request(base_request)
    if not base.allowed or run_policy == "allow_automatic":
        return base_request, base
    if run_policy == "disabled":
        return request, review_assistant_tool_request(request, policy_floor="disabled")
    # Governed browser actions already have explicit capability-level policy:
    # they are task-scoped, origin-restricted and executed through the bounded
    # browser adapter. The normal coding default (ask_sensitive) must not turn
    # those automatic validation interactions into approval waits. An explicit
    # always_ask run policy still reaches the overlay below.
    if (
        run_policy == "ask_sensitive"
        and request.tool_id == "browser"
        and request.action_id.startswith("browser.")
    ):
        return base_request, base
    # If the canonical tool/action/config already requires approval, preserve
    # that stronger decision rather than replacing it with a weaker run policy.
    if base.approval_required:
        return base_request, base
    overlay = review_assistant_tool_request(request, policy_floor=run_policy)
    return request, overlay


def _validate_execution_input(
    request: BrokerCapabilityRequest,
    stored: dict[str, Any],
) -> None:
    request_payload = stored.get("request_payload")
    stored_input = (
        request_payload.get("input")
        if isinstance(request_payload, dict)
        else None
    )
    if not isinstance(stored_input, dict) or stored_input != request.input:
        raise HTTPException(
            status_code=409,
            detail="agent_execution_key_input_mismatch",
        )


def _stored_response(
    capability_id: str,
    execution_key: str,
    stored: dict[str, Any],
) -> BrokerCapabilityResponse:
    result = dict(stored.get("result_payload") or {})
    return BrokerCapabilityResponse(
        capability_id=capability_id,
        execution_key=execution_key,
        executed=stored.get("state") == "completed" and not stored.get("error"),
        result=result,
    )


@router.get("/{run_id}/run-change-set", response_model=BrokerRunChangeSetResponse)
def read_agent_run_change_set(run_id: str) -> BrokerRunChangeSetResponse:
    service = default_agent_run_service()
    snapshot = service.get(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    if "workspace.run_change_set" not in snapshot.spec.capabilities:
        raise HTTPException(status_code=403, detail="workspace_run_change_set_not_issued")
    try:
        change_set, patch = service.run_change_set(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="agent_run_change_set_not_found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return BrokerRunChangeSetResponse(change_set=change_set, patch=patch)


class BrokerRunTokenResponse(BaseModel):
    token: str
    expires_at: int


@router.post("/{run_id}/run-token", response_model=BrokerRunTokenResponse)
def renew_agent_run_token(
    run_id: str,
    claims: Annotated[RunTokenClaims, Depends(require_live_run_token)],
) -> BrokerRunTokenResponse:
    """A fresh token for a live run, requested with its current token (WP-4.6)."""
    token = issue_run_token(
        run_id=claims.run_id,
        workspace_id=claims.workspace_id,
        owner=claims.owner,
        caps_digest=claims.caps_digest,
    )
    return BrokerRunTokenResponse(token=token, expires_at=verify_run_token(token, run_id=run_id).expires_at)


@router.post(
    "/{run_id}/budget/tool",
    response_model=BrokerToolBudgetResponse,
)
def authorize_agent_tool_call(
    run_id: str,
    request: BrokerToolBudgetRequest,
) -> BrokerToolBudgetResponse:
    try:
        usage = default_agent_budget_manager().authorize_tool_call(
            run_id,
            tool_name=request.tool_name,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="agent_run_not_found") from exc
    except AgentBudgetError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return BrokerToolBudgetResponse(usage=dict(usage))


def _path_is_within(root: str, candidate: str) -> bool:
    try:
        root_path = Path(os.path.realpath(root))
        candidate_path = Path(os.path.realpath(candidate))
        return os.path.commonpath([str(root_path), str(candidate_path)]) == str(root_path)
    except (OSError, ValueError):
        return False


def _workspace_approval(
    service,
    snapshot,
    *,
    capability_id: str,
    request_payload: dict[str, Any],
) -> AgentApproval:
    with unit_of_work(service.database) as work:
        repository = PostgresAgentRunRepository(work.connection, service.context)
        approval = repository.workspace_approval(
            snapshot.run_id, capability_id, request_payload,
        )
        if approval.state == "pending":
            current = repository.get_run(snapshot.run_id)
            if current is not None and current.status != "waiting_for_approval":
                repository.update_state(
                    snapshot.run_id,
                    expected_revision=current.revision,
                    status="waiting_for_approval",
                )
        work.commit()
    return approval


def approval_definition_refusal(approval: AgentApproval) -> str | None:
    """Why an approval cannot authorize now, or None (PA-1.4).

    An approval authorizes only the capability definition it was issued for:
    a missing capability, an approval issued before definitions were bound,
    or a changed definition refuses it, and the run asks for approval again.
    """
    current = capability_definition_hash(approval.capability_id)
    if current is None:
        return "capability_unavailable"
    if approval.capability_definition_hash is None:
        return "approval_definition_unbound"
    if not hmac.compare_digest(current, approval.capability_definition_hash):
        return "capability_definition_changed"
    return None


def _authorization_response(approval: AgentApproval) -> BrokerCommandAuthorizationResponse:
    refusal = approval_definition_refusal(approval) if approval.state == "approved" else None
    if refusal is not None:
        return BrokerCommandAuthorizationResponse(
            approval_id=approval.approval_id,
            reason=f"This approval no longer authorizes the action ({refusal}); request approval again.",
        )
    if approval.state == "approved":
        return BrokerCommandAuthorizationResponse(
            allowed=True,
            approval_id=approval.approval_id,
        )
    if approval.state == "rejected":
        return BrokerCommandAuthorizationResponse(
            approval_id=approval.approval_id,
            reason="The user rejected this exact workspace action.",
        )
    return BrokerCommandAuthorizationResponse(
        approval_required=True,
        approval_id=approval.approval_id,
        reason=(
            "User approval is required for this exact workspace action. "
            "Approve the pending action in Omnix, then retry it."
        ),
    )


@router.post(
    "/{run_id}/command-authorization",
    response_model=BrokerCommandAuthorizationResponse,
)
def authorize_agent_command(
    run_id: str,
    request: BrokerCommandAuthorizationRequest,
) -> BrokerCommandAuthorizationResponse:
    """Authorize one previously blocked local command after user approval.

    The Pi guard calls this only for commands outside the built-in safe prefix
    list. Approval is exact-command and run-scoped; it never expands the
    workspace or permits shell composition.
    """
    service = default_agent_run_service()
    snapshot = service.get(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    if snapshot.status not in {"starting", "running", "waiting_for_approval"}:
        raise HTTPException(
            status_code=409,
            detail=f"agent_run_not_runnable:{snapshot.status}",
        )
    if snapshot.desired_state != "running":
        raise HTTPException(
            status_code=409,
            detail=f"agent_run_not_runnable:{snapshot.desired_state}",
        )
    if "workspace.command" not in snapshot.spec.capabilities:
        raise HTTPException(status_code=403, detail="workspace_command_not_issued")
    workspace = snapshot.spec.workspace
    if workspace is None:
        raise HTTPException(status_code=403, detail="workspace_not_issued")
    rejection = _command_authorization_rejection(request.command)
    if rejection:
        raise HTTPException(status_code=403, detail=rejection)
    for value in (request.workspace_root, request.cwd):
        if value and not _path_is_within(workspace.root, value):
            raise HTTPException(status_code=403, detail="agent_workspace_scope_mismatch")

    command = request.command.strip()
    request_payload = {
        "command": command,
        "cwd": request.cwd,
        "workspace_root": request.workspace_root or workspace.root,
        "permission": "exact_command",
    }
    approval = _workspace_approval(
        service,
        snapshot,
        capability_id="workspace.command",
        request_payload=request_payload,
    )
    return _authorization_response(approval)


@router.post(
    "/{run_id}/workspace-authorization",
    response_model=BrokerCommandAuthorizationResponse,
)
def authorize_agent_workspace_tool(
    run_id: str,
    request: BrokerWorkspaceAuthorizationRequest,
) -> BrokerCommandAuthorizationResponse:
    """Request approval for a coding file mutation in the issued workspace."""
    service = default_agent_run_service()
    snapshot = service.get(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    if snapshot.status not in {"starting", "running", "waiting_for_approval"}:
        raise HTTPException(
            status_code=409,
            detail=f"agent_run_not_runnable:{snapshot.status}",
        )
    if snapshot.desired_state != "running":
        raise HTTPException(
            status_code=409,
            detail=f"agent_run_not_runnable:{snapshot.desired_state}",
        )
    if request.tool_name == "edit":
        capability_id = "workspace.edit"
    else:
        capability_id = "workspace.write"
    if capability_id not in snapshot.spec.capabilities:
        raise HTTPException(status_code=403, detail=f"{capability_id}_not_issued")
    workspace = snapshot.spec.workspace
    if workspace is None:
        raise HTTPException(status_code=403, detail="workspace_not_issued")
    candidate = str(request.input.get("path") or "").strip().lstrip("@")
    if not candidate:
        raise HTTPException(status_code=403, detail="workspace_path_required")
    candidate_path = Path(candidate)
    if not candidate_path.is_absolute():
        candidate = str(Path(workspace.root) / candidate_path)
    if not _path_is_within(workspace.root, candidate):
        raise HTTPException(status_code=403, detail="agent_workspace_scope_mismatch")
    request_payload = {
        "tool_name": request.tool_name,
        "input": request.input,
        "path": str(request.input.get("path") or ""),
        "workspace_root": request.workspace_root or workspace.root,
        "permission": "exact_workspace_tool",
    }
    if request.workspace_root and not _path_is_within(workspace.root, request.workspace_root):
        raise HTTPException(status_code=403, detail="agent_workspace_scope_mismatch")
    approval = _workspace_approval(
        service,
        snapshot,
        capability_id=capability_id,
        request_payload=request_payload,
    )
    return _authorization_response(approval)


@router.post("/{run_id}/capabilities/{capability_id:path}", response_model=BrokerCapabilityResponse)
def execute_agent_capability(
    run_id: str,
    capability_id: str,
    request: BrokerCapabilityRequest,
) -> BrokerCapabilityResponse:
    # Agent step for traces (WP-10.4); identifiers only, never the capability input.
    set_span_attributes(agent_run_id=run_id, agent_step="capability", capability_id=capability_id)
    canonical, capability, service, snapshot = _runnable_capability(run_id, capability_id)
    request = _normalize_capability_input(canonical, request)
    policy, task_revision_id, evidence_started_at, existing_receipts = _effective_evidence_context(
        service,
        snapshot,
    )
    evidence_requirement, evidence_source_class = resolve_evidence_call(
        policy,
        canonical,
        request.input,
    )
    request = _bind_authoritative_capability_input(
        snapshot,
        canonical,
        request,
        policy=policy,
        requirement=evidence_requirement,
    )
    if not _request_within_resource_scopes(snapshot, canonical, request.input):
        raise HTTPException(status_code=403, detail="agent_resource_scope_mismatch")

    execution_key = _execution_key(run_id, canonical, request)
    if task_revision_id and is_evidence_capability(canonical):
        execution_key = _revision_scoped_evidence_execution_key(
            execution_key,
            task_revision_id,
        )
    approved_by: str | None = None
    approval = None

    with unit_of_work(service.database) as work:
        repository = PostgresAgentRunRepository(work.connection, service.context)
        request = _reserve_evidence_retrieval_budget(
            repository,
            run_id,
            canonical,
            execution_key,
            request,
            policy=policy,
            revision_id=task_revision_id,
            started_at=evidence_started_at,
        )
        if request.approval_id:
            approval = repository.get_approval(run_id, request.approval_id)
            if approval is None:
                raise HTTPException(status_code=403, detail="agent_approval_mismatch")
            refusal = approval_definition_refusal(approval) if approval.state == "approved" else None
            if refusal is not None:
                raise HTTPException(status_code=403, detail=refusal)
            execution_key = _approved_execution_key(
                run_id,
                canonical,
                request,
                approval,
            )
            approved_by = _approver(approval)

        stored = repository.ensure_capability_execution(
            run_id,
            execution_key,
            canonical,
            {"input": request.input, "approval_id": request.approval_id},
        )
        if stored["capability_id"] != canonical:
            raise HTTPException(status_code=409, detail="agent_execution_key_capability_mismatch")
        _validate_execution_input(request, stored)
        if stored["state"] in {"completed", "failed"}:
            work.rollback()
            return _stored_response(canonical, execution_key, stored)
        stored = _reclaim_running_execution(stored, capability, repository, run_id, execution_key, canonical, request, work)

        outcome = _finish_rejected_capability(approval, repository, run_id, execution_key, canonical, task_revision_id, work)
        if outcome is not _CONTINUE:
            return outcome

        if approval is None and stored["state"] == "waiting_for_approval":
            approval = repository.find_capability_approval(
                run_id,
                canonical,
                execution_key,
            )
            if approval is not None:
                execution_key = _approved_execution_key(
                    run_id,
                    canonical,
                    request,
                    approval,
                )
                approved_by = _approver(approval)

        tool_request = AssistantToolRequest(
            tool_id=canonical.split(".", 1)[0],
            action_id=canonical,
            session_id=snapshot.spec.session_id or f"agent:{run_id}",
            proposal_id=execution_key,
            input=request.input,
        )
        tool_request, decision = _review_with_run_policy(
            tool_request,
            snapshot.spec.approval_policy,
        )
        outcome = _refuse_capability(decision, evidence_requirement, canonical, snapshot, repository, run_id, execution_key, task_revision_id, work)
        if outcome is not _CONTINUE:
            return outcome
        outcome = _await_capability_approval(approved_by, decision, execution_key, repository, run_id, approval, canonical, request, work)
        if outcome is not _CONTINUE:
            return outcome
        if not repository.claim_capability_execution(run_id, execution_key):
            work.rollback()
            raise HTTPException(status_code=409, detail="agent_execution_not_claimable")
        work.commit()

    # The tool budget is charged here, once per claimed execution, so a run
    # cannot avoid it by skipping the Pi guard (WP-4.5).
    _charge_capability_budget(canonical, run_id, service, execution_key, task_revision_id)

    payload = execute_capability(
        CapabilityGrant(
            "agent_run",
            run_id,
            approved_by=approved_by,
            policy_floor=(snapshot.spec.approval_policy if tool_request.tool_id != "browser" else None),
        ),
        tool_request,
        user_request=f"agent:{run_id}",
    )
    result: AssistantToolResult = payload.execution_result
    result_payload = _capability_result_payload(result, evidence_requirement, canonical, snapshot)

    _record_capability_result(service, run_id, execution_key, result_payload, result, canonical, task_revision_id, policy, request, evidence_requirement, evidence_source_class)

    return BrokerCapabilityResponse(
        capability_id=canonical,
        execution_key=execution_key,
        executed=result.error is None,
        approval_required=False,
        approval_id=request.approval_id,
        result=result_payload,
    )


def _capability_result_payload(result, evidence_requirement, canonical, snapshot):
    """The result as stored; a failed evidence call names the issued capabilities that can still satisfy it."""
    result_payload = result.model_dump(mode="json")
    if result.error is not None and evidence_requirement is not None:
        fallbacks = fallback_capabilities_for_requirement(
            evidence_requirement,
            current_capability=canonical,
            issued_capabilities=snapshot.spec.external_capabilities,
        )
        if fallbacks:
            result_payload["evidence_fallback_capabilities"] = list(fallbacks)
            result_payload["evidence_requirement_id"] = evidence_requirement.id
    return result_payload


def _runnable_capability(run_id, capability_id):
    """The run and the broker capability it may call: 404/409/403 when it cannot."""
    service = default_agent_run_service()
    snapshot = service.get(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    if snapshot.status not in {"starting", "running", "waiting_for_approval"}:
        raise HTTPException(
            status_code=409,
            detail=f"agent_run_not_runnable:{snapshot.status}",
        )
    if snapshot.desired_state != "running":
        raise HTTPException(
            status_code=409,
            detail=f"agent_run_not_runnable:{snapshot.desired_state}",
        )
    capability = default_capability_registry().get(capability_id)
    if capability is None or capability.execution_zone != "broker":
        raise HTTPException(status_code=404, detail="agent_capability_not_found")
    canonical = capability.id
    if canonical not in snapshot.spec.external_capabilities:
        raise HTTPException(status_code=403, detail="agent_capability_outside_run_spec")
    return canonical, capability, service, snapshot


def _reclaim_running_execution(stored, capability, repository, run_id, execution_key, canonical, request, work):
    """Reclaim a stale read still marked running; any other running call has an unknown outcome."""
    if stored["state"] == "running":
        reclaimed = False
        if capability.effect == "read":
            try:
                retry_after = max(
                    1,
                    int(_env_str("OMNIX_AGENT_READ_RETRY_AFTER_SECONDS", "30")),
                )
            except ValueError:
                retry_after = 30
            reclaimed = repository.reclaim_stale_read_capability_execution(
                run_id,
                execution_key,
                stale_before=datetime.now(timezone.utc) - timedelta(seconds=retry_after),
            )
            if reclaimed:
                stored = repository.ensure_capability_execution(
                    run_id,
                    execution_key,
                    canonical,
                    {"input": request.input, "approval_id": request.approval_id},
                )
        if not reclaimed:
            work.rollback()
            raise HTTPException(
                status_code=409,
                detail="agent_execution_outcome_unknown_or_in_progress",
            )
    return stored


def _finish_rejected_capability(approval, repository, run_id, execution_key, canonical, task_revision_id, work):
    """Finish a call whose approval was rejected without running it."""
    if approval is not None and approval.state == "rejected":
        repository.finish_capability_execution(
            run_id,
            execution_key,
            result_payload={"error": "approval_rejected"},
            error="approval_rejected",
            state_changed=False,
        )
        if task_revision_id and is_evidence_capability(canonical):
            repository.finish_evidence_query(
                run_id,
                task_revision_id,
                execution_key,
                actual_sources=0,
                actual_extracts=0,
                failed=True,
            )
        work.commit()
        return BrokerCapabilityResponse(
            capability_id=canonical,
            execution_key=execution_key,
            result={"error": "approval_rejected"},
        )
    return _CONTINUE


def _refuse_capability(decision, evidence_requirement, canonical, snapshot, repository, run_id, execution_key, task_revision_id, work):
    """Finish a call the tool policy refuses, offering evidence fallbacks when the tool is only unavailable."""
    if not decision.allowed:
        error = decision.reason or "not_allowed"
        result_payload: dict[str, Any] = {"error": error}
        if evidence_requirement is not None and error in {"missing_connection", "tool_disabled"}:
            fallbacks = fallback_capabilities_for_requirement(
                evidence_requirement,
                current_capability=canonical,
                issued_capabilities=snapshot.spec.external_capabilities,
            )
            if fallbacks:
                result_payload["evidence_fallback_capabilities"] = list(fallbacks)
                result_payload["evidence_requirement_id"] = evidence_requirement.id
        repository.finish_capability_execution(
            run_id,
            execution_key,
            result_payload=result_payload,
            error=error,
            state_changed=False,
        )
        if task_revision_id and is_evidence_capability(canonical):
            repository.finish_evidence_query(
                run_id,
                task_revision_id,
                execution_key,
                actual_sources=0,
                actual_extracts=0,
                failed=True,
            )
        work.commit()
        return BrokerCapabilityResponse(
            capability_id=canonical,
            execution_key=execution_key,
            result=result_payload,
        )
    return _CONTINUE


def _await_capability_approval(approved_by, decision, execution_key, repository, run_id, approval, canonical, request, work):
    """Park the call until a recorded approval: one approval row per execution key, run waits."""
    if decision.approval_required and approved_by is None:
        repository.mark_capability_waiting_for_approval(run_id, execution_key)
        if approval is None:
            approval = AgentApproval(
                run_id=run_id,
                capability_id=canonical,
                request_payload={
                    "input": request.input,
                    "proposal_id": execution_key,
                    "execution_key": execution_key,
                },
            )
            repository.add_approval(approval)
            current = repository.get_run(run_id)
            if current is not None and current.status != "waiting_for_approval":
                repository.update_state(
                    run_id,
                    expected_revision=current.revision,
                    status="waiting_for_approval",
                )
        work.commit()
        return BrokerCapabilityResponse(
            capability_id=canonical,
            approval_required=True,
            approval_id=approval.approval_id,
            execution_key=execution_key,
        )
    return _CONTINUE


def _charge_capability_budget(canonical, run_id, service, execution_key, task_revision_id):
    """Charge the run's tool budget once per claimed execution; refuse when it is spent."""
    try:
        default_agent_budget_manager().authorize_tool_call(run_id, tool_name=canonical)
    except AgentBudgetError as exc:
        with unit_of_work(service.database) as work:
            repository = PostgresAgentRunRepository(work.connection, service.context)
            repository.finish_capability_execution(
                run_id,
                execution_key,
                result_payload={"error": str(exc)},
                error=str(exc),
                state_changed=False,
            )
            if task_revision_id and is_evidence_capability(canonical):
                repository.finish_evidence_query(
                    run_id,
                    task_revision_id,
                    execution_key,
                    actual_sources=0,
                    actual_extracts=0,
                    failed=True,
                )
            work.commit()
        raise HTTPException(status_code=429, detail=str(exc)) from exc


def _record_capability_result(service, run_id, execution_key, result_payload, result, canonical, task_revision_id, policy, request, evidence_requirement, evidence_source_class):
    """Record the capability outcome, its evidence receipt and the tool event in one transaction."""
    with unit_of_work(service.database) as work:
        repository = PostgresAgentRunRepository(work.connection, service.context)
        repository.finish_capability_execution(
            run_id,
            execution_key,
            result_payload=result_payload,
            error=result.error,
            state_changed=result.state_changed,
        )
        if task_revision_id and is_evidence_capability(canonical):
            actual_sources, actual_extracts = _evidence_result_usage(
                result_payload,
                failed=result.error is not None,
            )
            repository.finish_evidence_query(
                run_id,
                task_revision_id,
                execution_key,
                actual_sources=actual_sources,
                actual_extracts=actual_extracts,
                failed=result.error is not None,
            )
        receipt = build_evidence_receipt(
            run_id=run_id,
            task_revision_id=task_revision_id,
            policy=policy,
            capability_id=canonical,
            request_input=request.input,
            result_payload=result_payload,
            error=result.error,
            requirement_id=(
                evidence_requirement.id if evidence_requirement is not None else None
            ),
            source_class_hint=evidence_source_class,
        )
        if receipt is not None:
            repository.add_evidence_receipt(receipt)
        repository.append_event(
            AgentEvent(
                run_id=run_id,
                event_type="tool.completed",
                payload={
                    "capability_id": canonical,
                    "execution_key": execution_key,
                    "broker": True,
                    "state_changed": result.state_changed,
                    "error": result.error,
                    "result_summary": result.result_summary,
                    "evidence_requirement_id": (
                        evidence_requirement.id if evidence_requirement is not None else None
                    ),
                    "evidence_source_class": evidence_source_class,
                    "task_revision_id": task_revision_id,
                },
            )
        )
        work.commit()
