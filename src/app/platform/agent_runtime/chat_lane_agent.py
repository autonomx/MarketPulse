"""The Agent lane of typed Chat: starting, continuing and steering durable agent runs (WP-8.2).

The router (``chat_bridge``) decides the lane; this module turns the routed
turn into an Agent run, a command on the active run, or a refusal.
"""
from __future__ import annotations

from .exception_logging import log_recovered_exception
from app.config.env import env_str
from dataclasses import dataclass
import hashlib
import os
import re
from typing import Any, cast
from .active_objective import (
    make_active_objective,
    objective_continuity_candidate,
)
from .contracts import (
    AgentApprovalPolicy,
    AgentRunCommand,
    AgentRunSpec,
    ModelRef,
    RequestModeSelection,
    SuccessCriterion,
    WorkspaceSpec,
)
from .evidence import EvidenceCompilationError, classify_evidence, compile_task_authority, evidence_decision_from_semantic, task_requires_workspace_mutation
from .local_workspace import (
    LocalWorkspaceSelectionError,
    local_workspace_repository_root,
    validate_local_workspace_root,
)
from .profiles import get_agent_profile, profile_produces_diff
from .router import OmnixRouteDecision
from .semantic_classifier import (
    SemanticIntentDecision,
    semantic_confidence_threshold,
    semantic_profile_id,
)
from .semantic_task import (
    SemanticTask,
    SemanticTaskCompilation,
)
from .turn_plan import (
    TurnPlan,
)
from .service import AgentRunService
from .chat_lane_common import (
    GeneralizedChatResult,
    _TERMINAL_AGENT,
)


def default_agent_run_service(*args, **kwargs):
    # Resolved through chat_bridge at call time: the one place
    # callers and tests replace it.
    from . import chat_bridge

    return chat_bridge.default_agent_run_service(*args, **kwargs)


def default_task_graph_runtime(*args, **kwargs):
    # Resolved through chat_bridge at call time: the one place
    # callers and tests replace it.
    from . import chat_bridge

    return chat_bridge.default_task_graph_runtime(*args, **kwargs)


def get_provider(*args, **kwargs):
    # Resolved through chat_bridge at call time: the one place
    # callers and tests replace it.
    from . import chat_bridge

    return chat_bridge.get_provider(*args, **kwargs)


@dataclass(frozen=True)
class _PendingAgentRetry:
    task: str
    profile: str
    failed_message_id: str | None = None
    reference_message_id: str | None = None
    reference_images: tuple[dict[str, str], ...] = ()


_AGENT_IMAGE_DATA_URL = re.compile(
    r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)$",
    re.I,
)


_CONFIRM = re.compile(r"^(?:yes|confirm|approve|approved|go ahead|proceed|do it)[.!\s]*$", re.I)


_REJECT = re.compile(r"^(?:no|cancel|reject|rejected|do not|don't|never mind|nevermind)[.!\s]*$", re.I)


_PAUSE = re.compile(r"^(?:pause|hold)[.!\s]*$", re.I)


_RESUME = re.compile(r"^(?:resume|continue)[.!\s]*$", re.I)


_CANCEL = re.compile(r"^(?:cancel|stop|abort)[.!\s]*$", re.I)


_CONTROL = re.compile(r"^(?:pause|hold|resume|continue|cancel|stop|abort)[.!\s]*$", re.I)


_WORKSPACE_UNAVAILABLE_RESPONSE = re.compile(
    r"(?:don'?t have access to the project folder|coding workspace.*(?:not available|only the image)|"
    r"workspace editor.*(?:not available|unavailable)|no coding workspace is configured)",
    re.I,
)


_LEGACY_OBJECTIVE_REVISION_SEPARATOR = re.compile(
    r"\n{2,}Latest user revision:\s*\n",
    re.I,
)


def _agent_reference_images(metadata: dict[str, Any] | None) -> list[dict[str, str]]:
    source = metadata or {}
    values: list[str] = []
    raw_values = source.get("image_data_urls")
    if isinstance(raw_values, list):
        values.extend(value for value in raw_values if isinstance(value, str) and value)
    legacy = source.get("image_data_url")
    if isinstance(legacy, str) and legacy:
        values.insert(0, legacy)

    images: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        match = _AGENT_IMAGE_DATA_URL.fullmatch(normalized)
        if match is None:
            continue
        images.append({
            "type": "image",
            "data": match.group(2),
            "mimeType": match.group(1).lower(),
        })
        if len(images) >= 8:
            break
    return images


def _pending_failed_agent_retry(
    session: Any,
    user_message: Any,
) -> _PendingAgentRetry | None:
    """Resolve a coding retry to the immediately preceding user task.

    The normal path uses trusted application metadata from a non-durable Agent
    start failure. A legacy Chat response may only contain the old workspace-
    unavailable text, so that response is also accepted for coding retries. In
    both cases the retry turn must still attach a Local folder or use the
    operator-configured default repository.
    """

    current_message_id = str(getattr(user_message, "id", "") or "")
    messages = list(getattr(session, "messages", []) or [])
    failed_index: int | None = None
    failed_message: Any | None = None
    for index in range(len(messages) - 1, -1, -1):
        candidate = messages[index]
        if current_message_id and str(getattr(candidate, "id", "") or "") == current_message_id:
            continue
        if getattr(candidate, "role", None) != "assistant":
            return None
        failed_index = index
        failed_message = candidate
        break

    if failed_message is None or failed_index is None:
        return None
    metadata = getattr(failed_message, "metadata", {}) or {}
    start = metadata.get("agent_start")
    raw_run = metadata.get("agent_run")
    if isinstance(start, dict) and start.get("status") == "failed":
        if not isinstance(raw_run, dict) or str(raw_run.get("run_id") or "").strip():
            return None
        task = str(raw_run.get("task") or "").strip()
        profile = str(raw_run.get("profile") or "").strip()
    elif _WORKSPACE_UNAVAILABLE_RESPONSE.search(str(getattr(failed_message, "content", "") or "")):
        # Older Chat turns could produce this message without recording a
        # durable Agent-start failure. Treat a coding retry as a retry of the
        # preceding user task so the attached Local folder is actually used.
        source = messages[failed_index - 1] if failed_index > 0 else None
        if getattr(source, "role", None) != "user":
            return None
        task = str(getattr(source, "content", "") or "").strip()
        profile = "coding"
    else:
        return None
    if not task or not profile:
        return None

    reference_images: tuple[dict[str, str], ...] = ()
    if failed_index > 0:
        source = messages[failed_index - 1]
        if getattr(source, "role", None) == "user":
            reference_images = tuple(
                _agent_reference_images(getattr(source, "metadata", {}) or {})
            )
    return _PendingAgentRetry(
        task=task,
        profile=profile,
        failed_message_id=str(getattr(failed_message, "id", "") or "") or None,
        reference_message_id=(
            str(getattr(messages[failed_index - 1], "id", "") or "") or None
            if failed_index > 0 and getattr(messages[failed_index - 1], "role", None) == "user"
            else None
        ),
        reference_images=reference_images,
    )


_TRADING_MUTATION = re.compile(
    r"(?:"
    r"\b(?:buy|sell|purchase|short|cover)\b"
    r"(?=.{0,60}\b(?:shares?|stocks?|equities?|securities?|positions?|orders?|trades?)\b)"
    r"|\b(?:buy|sell|purchase|short|cover)\s+\$?(?-i:[A-Z]{1,5})\b"
    r"|\b(?:place|submit|cancel)\b.{0,60}\b(?:order|trade|position)\b"
    r")",
    re.I,
)


_PUBLICATION_REQUEST = re.compile(
    r"\b(?:git\s+push|push\s+(?:the\s+)?(?:current\s+)?branch|open\s+(?:a\s+)?pull\s+request|create\s+(?:a\s+)?pull\s+request)\b",
    re.I,
)


_DEFAULT_AGENT_REASONING_EFFORT = "none"


def _resolve_agent_model_route(
    provider_id: str | None,
    model_id: str | None,
) -> tuple[str, str]:
    """Normalize Chat's provider/model IDs and fill a provider default model.

    Browser Chat persists selectable models as ``llm:<provider>:<model>`` while
    older sessions can retain only a provider. Pi needs a concrete, matched
    provider/model pair, unlike ordinary chat providers that can infer their
    configured default model.
    """

    provider = str(provider_id or "").strip().removeprefix("llm:")
    model = str(model_id or "").strip()
    if model.startswith("llm:"):
        parts = model.split(":", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            _, model_provider, selected_model = parts
            provider = model_provider
            model = selected_model
    if provider and not model:
        try:
            configured_provider = get_provider(provider)
            model = str(
                getattr(getattr(configured_provider, "config", None), "model", "")
                or ""
            ).strip()
        except Exception as exc:
            # Preserve the existing clear configuration failure below when the
            # selected provider itself cannot be constructed.
            log_recovered_exception("chat provider model lookup", exc, level="DEBUG")
            pass
    return provider, model


def _agent_reasoning_effort(provider_id: str | None = None) -> str:
    """Return the selected reasoning level for Chat-created Pi runs."""
    configured = env_str("OMNIX_AGENT_REASONING_EFFORT", "").strip()
    if configured:
        return _DEFAULT_AGENT_REASONING_EFFORT if configured.casefold() in {"off", "disabled"} else configured
    provider_key = str(provider_id or "").strip().removeprefix("llm:")
    if provider_key:
        try:
            provider = get_provider(provider_key)
            value = str(getattr(provider, "reasoning_effort", "") or "").strip()
            if not value:
                config = getattr(provider, "config", None)
                extra = getattr(config, "extra_params", None)
                if isinstance(extra, dict):
                    value = str(extra.get("reasoning_effort") or "").strip()
            if value:
                return value
        except Exception as exc:
            log_recovered_exception("chat reasoning effort lookup", exc, level="DEBUG")
            pass
    return _DEFAULT_AGENT_REASONING_EFFORT


def _latest_canonical_request(value: str) -> str:
    """Recover the latest request from objectives persisted by older builds."""

    text = str(value or "").strip()
    if not text:
        return text
    revisions = _LEGACY_OBJECTIVE_REVISION_SEPARATOR.split(text)
    return next(
        (revision.strip() for revision in reversed(revisions) if revision.strip()),
        text,
    )


def _agent_result(
    session: Any,
    user_message: Any,
    decision: OmnixRouteDecision,
    *,
    provider_id: str | None,
    model_id: str | None,
    request_mode: RequestModeSelection,
    semantic_intent: SemanticIntentDecision | None = None,
    semantic_task: SemanticTask | None = None,
    semantic_compilation: SemanticTaskCompilation | None = None,
    semantic_context: str = "",
    routing_shadow: dict[str, Any] | None = None,
    turn_plan: TurnPlan | None = None,
    content_override: str | None = None,
    reference_images_override: list[dict[str, str]] | None = None,
    retry_source: _PendingAgentRetry | None = None,
) -> GeneralizedChatResult | None:
    content, message_metadata, reference_images, selected_workspace, profile_id = _agent_turn_inputs(
        user_message,
        content_override=content_override,
        reference_images_override=reference_images_override,
        semantic_intent=semantic_intent,
        semantic_compilation=semantic_compilation,
    )
    profile = get_agent_profile(profile_id)
    try:
        service = default_agent_run_service()
    except Exception as exc:
        return _agent_start_failure(
            decision,
            run_id=None,
            profile=profile_id,
            task=_agent_task(content),
            error=exc,
        )
    latest = _latest_agent_run(service, session)
    active = latest if latest is not None and latest.status not in _TERMINAL_AGENT else None
    force_new_agent = _replaces_active_run(turn_plan)
    if active is not None and not force_new_agent:
        return _steer_active_agent(
            service,
            active,
            content,
            decision,
            profile_id=profile_id,
            selected_workspace=selected_workspace,
            semantic_context=semantic_context,
            reference_images=reference_images,
            turn_plan=turn_plan,
        )
    if latest is not None and _CONTROL.fullmatch(content):
        return _finished_run_reply(latest, decision)

    placement = _agent_workspace(profile, profile_id, selected_workspace, decision, content)
    if isinstance(placement, GeneralizedChatResult):
        return placement
    repository, selected_workspace, selected_repository = placement

    resolved_provider, resolved_model = _agent_model_route(session, provider_id, model_id)
    if not resolved_provider or not resolved_model:
        return _agent_start_failure(
            decision,
            run_id=None,
            profile=profile_id,
            task=_agent_task(content),
            error=RuntimeError("Agent provider/model is not configured"),
        )

    authority_task = _agent_task(content)
    # The start job reloads prompt context and images from durable Chat message
    # references; it never stores copied conversation or image bodies.
    authority = _agent_authority(
        profile,
        profile_id,
        authority_task,
        content,
        decision,
        semantic_intent=semantic_intent,
        semantic_compilation=semantic_compilation,
    )
    if isinstance(authority, GeneralizedChatResult):
        return authority
    evidence_decision, semantic_actions, allow_text_semantic_fallback, compiled = authority
    local = list(compiled.required_local)
    external = list(compiled.required_external)
    spec = _agent_run_spec(
        session,
        profile,
        profile_id,
        authority_task,
        model=ModelRef(
            provider_id=resolved_provider,
            model_id=resolved_model,
            reasoning_effort=_agent_reasoning_effort(resolved_provider),
        ),
        local=local,
        external=external,
        request_mode=request_mode,
        evidence_decision=evidence_decision,
        workspace=_agent_workspace_spec(
            profile,
            repository=repository,
            selected_workspace=selected_workspace,
            selected_repository=selected_repository,
        ),
        coding_approval_policy=message_metadata.get("coding_approval_policy"),
        semantic_actions=semantic_actions,
        allow_text_semantic_fallback=allow_text_semantic_fallback,
    )
    try:
        snapshot = _submit_agent_run(
            service,
            spec,
            session,
            user_message,
            superseded=active if force_new_agent else None,
            turn_plan=turn_plan,
            semantic_context=semantic_context,
            reference_images=reference_images,
            retry_source=retry_source,
        )
    except Exception as exc:
        return _agent_start_failure(
            decision,
            run_id=spec.run_id,
            profile=profile_id,
            task=authority_task,
            error=exc,
            service=service,
        )
    return _agent_started_result(
        snapshot,
        decision,
        user_message,
        profile_id=profile_id,
        authority_task=authority_task,
        request_mode=request_mode,
        evidence_decision=evidence_decision,
        semantic_intent=semantic_intent,
        semantic_task=semantic_task,
        semantic_compilation=semantic_compilation,
        routing_shadow=routing_shadow,
        selected_workspace=selected_workspace,
        local=local,
        external=external,
        retry_source=retry_source,
    )


def _agent_run_spec(
    session: Any,
    profile: Any,
    profile_id: str,
    authority_task: str,
    *,
    model: ModelRef,
    local: list[str],
    external: list[str],
    request_mode: RequestModeSelection,
    evidence_decision: Any,
    workspace: WorkspaceSpec | None,
    coding_approval_policy: Any,
    semantic_actions: list[str],
    allow_text_semantic_fallback: bool,
) -> AgentRunSpec:
    return AgentRunSpec(
        session_id=str(session.id),
        task=authority_task,
        objective=authority_task,
        profile=profile_id,
        model=model,
        capabilities=local,
        external_capabilities=external,
        context_sources=list(profile.context_sources),
        request_mode=request_mode,
        evidence_policy=evidence_decision.policy,
        workspace=workspace,
        approval_policy=(
            _coding_approval_policy(coding_approval_policy)
            if profile_produces_diff(profile_id)
            else "ask_sensitive"
        ),
        success_criteria=[
            SuccessCriterion(
                id="user-request",
                description=(
                    "Complete the user's requested task, run the smallest relevant "
                    "validation for the changed area, and report verifiable evidence."
                ),
            ),
        ],
        expected_artifacts=(
            ["diff"]
            if profile_produces_diff(profile_id)
            and task_requires_workspace_mutation(
                authority_task,
                semantic_action_intents=semantic_actions,
                allow_text_semantic_fallback=allow_text_semantic_fallback,
            )
            else []
        ),
    )


def _agent_started_result(
    snapshot: Any,
    decision: OmnixRouteDecision,
    user_message: Any,
    *,
    profile_id: str,
    authority_task: str,
    request_mode: RequestModeSelection,
    evidence_decision: Any,
    semantic_intent: SemanticIntentDecision | None,
    semantic_task: SemanticTask | None,
    semantic_compilation: SemanticTaskCompilation | None,
    routing_shadow: dict[str, Any] | None,
    selected_workspace: str,
    local: list[str],
    external: list[str],
    retry_source: _PendingAgentRetry | None,
) -> GeneralizedChatResult:
    result_metadata = {
        "generation_status": "queued" if snapshot.status == "queued" else "completed",
        "agent_mode": True,
        "omnix_route": decision.model_dump(mode="json"),
        "agent_run": _agent_metadata(snapshot),
        "request_mode": request_mode.model_dump(mode="json"),
        "evidence_decision": evidence_decision.model_dump(mode="json"),
        "semantic_intent": (
            semantic_intent.model_dump(mode="json")
            if semantic_intent is not None
            else None
        ),
        "semantic_task": (
            semantic_task.model_dump(mode="json")
            if semantic_task is not None
            else None
        ),
        "semantic_compilation": (
            semantic_compilation.model_dump(mode="json")
            if semantic_compilation is not None
            else None
        ),
        "routing_decision": routing_shadow,
        "active_objective": make_active_objective(
            canonical_request=authority_task,
            profile=profile_id,
            status="active",
            workspace_name=(
                re.split(r"[\\/]", selected_workspace.rstrip("\\/"))[-1]
                if selected_workspace
                else None
            ),
            originating_turn_id=str(getattr(user_message, "id", "") or "") or None,
            last_relevant_turn_id=str(getattr(user_message, "id", "") or "") or None,
            run_id=str(snapshot.run_id),
        ).model_dump(mode="json"),
        "authority_compilation": {
            "issued_local": local,
            "issued_external": external,
            "denied_actions": (
                list(semantic_compilation.denied_actions)
                if semantic_compilation is not None
                else []
            ),
        },
    }
    if retry_source is not None:
        result_metadata["agent_retry"] = {
            "status": "started",
            "failed_message_id": retry_source.failed_message_id,
            "task": retry_source.task,
            "profile": retry_source.profile,
        }
    return GeneralizedChatResult(
        content=(
            f"Queued {profile_id} Agent run {snapshot.run_id}. "
            "I'll keep the run durable; send another Agent-mode message to steer it."
        ),
        metadata=result_metadata,
    )



def _agent_turn_inputs(
    user_message: Any,
    *,
    content_override: str | None,
    reference_images_override: list[dict[str, str]] | None,
    semantic_intent: SemanticIntentDecision | None,
    semantic_compilation: SemanticTaskCompilation | None,
) -> tuple[str, dict[str, Any], list[dict[str, str]], str, str]:
    """The request text, message metadata, images, Local folder and profile of a turn."""
    raw_content = str(content_override or user_message.content or "").strip()
    content = (
        _latest_canonical_request(raw_content)
        if content_override is not None
        else raw_content
    )
    message_metadata = getattr(user_message, "metadata", {}) or {}
    reference_images = _agent_reference_images(message_metadata)
    if not reference_images and reference_images_override:
        reference_images = list(reference_images_override)
    selected_workspace = str(message_metadata.get("workspace_root") or "").strip()
    profile_id: str
    if semantic_compilation is not None:
        profile_id = semantic_compilation.profile_id or "research"
    else:
        # Explicit /agent, persistent Agent control, and injected compatibility
        # callers may omit compilation. AUTO natural-language routing never
        # reaches this fallback.
        profile_id = semantic_profile_id(content, semantic_intent)
    return content, message_metadata, reference_images, selected_workspace, profile_id


def _replaces_active_run(turn_plan: TurnPlan | None) -> bool:
    """Whether the turn starts a new Agent run in place of the active one."""
    return bool(
        turn_plan is not None
        and turn_plan.run_action in {
            "replace_agent_with_agent",
            "replace_task_graph_with_agent",
        }
    )


def _finished_run_reply(latest: Any, decision: OmnixRouteDecision) -> GeneralizedChatResult:
    """A pause/resume/cancel word for a run that has already finished."""
    return GeneralizedChatResult(
        content=f"Agent run {latest.run_id} is already {latest.status}.",
        metadata={
            "generation_status": "completed",
            "agent_mode": True,
            "omnix_route": decision.model_dump(mode="json"),
            "agent_run": _agent_metadata(latest),
        },
    )


def _agent_model_route(session: Any, provider_id: str | None, model_id: str | None) -> tuple[str, str]:
    """The run's provider and model: the turn's, the session's, else the configured default."""
    return _resolve_agent_model_route(
        str(
            provider_id
            or getattr(session, "provider_id", None)
            or env_str("OMNIX_AGENT_DEFAULT_PROVIDER_ID", "")
        ).strip(),
        str(
            model_id
            or getattr(session, "model_id", None)
            or env_str("OMNIX_AGENT_DEFAULT_MODEL_ID", "")
        ).strip(),
    )


def _steer_active_agent(
    service: Any,
    active: Any,
    content: str,
    decision: OmnixRouteDecision,
    *,
    profile_id: str,
    selected_workspace: str,
    semantic_context: str,
    reference_images: list[dict[str, str]],
    turn_plan: TurnPlan | None,
) -> GeneralizedChatResult:
    """Send the turn to the session's active run, refusing a different Local folder."""
    if selected_workspace:
        try:
            selected_workspace = validate_local_workspace_root(selected_workspace)
        except LocalWorkspaceSelectionError as exc:
            return _agent_request_rejection(
                decision,
                profile=profile_id,
                task=_agent_task(content),
                reason="local_workspace_unavailable",
                message=f"I can't use the attached Local folder: {exc}",
            )
        issued_workspace = getattr(active.spec, "workspace", None)
        issued_paths = {
            str(value)
            for value in (
                getattr(issued_workspace, "root", None),
                getattr(issued_workspace, "worktree", None),
                getattr(issued_workspace, "repository", None),
            )
            if value
        }
        normalized_issued = {
            os.path.normcase(os.path.abspath(path))
            for path in issued_paths
        }
        if (
            normalized_issued
            and os.path.normcase(os.path.abspath(selected_workspace))
            not in normalized_issued
        ):
            return _agent_request_rejection(
                decision,
                profile=profile_id,
                task=_agent_task(content),
                reason="active_run_workspace_mismatch",
                message=(
                    "The active Agent run is bound to a different Local folder. "
                    "Cancel or finish that run before switching workspaces."
                ),
            )
    return _continue_agent_run(
        service,
        active,
        content,
        decision,
        reference_context=semantic_context,
        reference_images=reference_images,
        turn_plan=turn_plan,
    )


def _agent_workspace(
    profile: Any,
    profile_id: str,
    selected_workspace: str,
    decision: OmnixRouteDecision,
    content: str,
) -> GeneralizedChatResult | tuple[str, str, str | None]:
    """The repository a workspace profile runs in: the Local folder or the default."""
    repository = env_str("OMNIX_AGENT_DEFAULT_REPOSITORY", "").strip()
    selected_repository: str | None = None
    if profile.requires_workspace and selected_workspace:
        try:
            selected_workspace = validate_local_workspace_root(selected_workspace)
            selected_repository = local_workspace_repository_root(selected_workspace)
        except LocalWorkspaceSelectionError as exc:
            return _agent_start_failure(
                decision,
                run_id=None,
                profile=profile_id,
                task=_agent_task(content),
                error=exc,
            )
        repository = selected_workspace
    if profile.requires_workspace and not repository:
        return _agent_start_failure(
            decision,
            run_id=None,
            profile=profile_id,
            task=_agent_task(content),
            error=RuntimeError(
                f"the {profile_id} profile requires OMNIX_AGENT_DEFAULT_REPOSITORY "
                "or a Local folder"
            ),
        )
    return repository, selected_workspace, selected_repository


def _agent_authority(
    profile: Any,
    profile_id: str,
    authority_task: str,
    content: str,
    decision: OmnixRouteDecision,
    *,
    semantic_intent: SemanticIntentDecision | None,
    semantic_compilation: SemanticTaskCompilation | None,
) -> GeneralizedChatResult | tuple[Any, list[str], bool, Any]:
    """Refuse authority Chat never issues, then compile the run's capabilities."""
    if _PUBLICATION_REQUEST.search(content):
        return _agent_request_rejection(
            decision,
            profile=profile_id,
            task=authority_task,
            reason="github_publication_capability_not_issued",
            message=(
                "I can't publish from a Chat-created coding run: GitHub push/PR "
                "capabilities were not issued. Start a separately scoped, "
                "approval-gated publication run."
            ),
        )
    if profile_id in {"research", "trading-research"} and _TRADING_MUTATION.search(content):
        return _agent_request_rejection(
            decision,
            profile=profile_id,
            task=authority_task,
            reason="trading_execution_capability_not_issued",
            message=(
                "I can't place or manage trades from a research run: trading "
                "execution authority was not issued."
            ),
        )
    if semantic_compilation is not None:
        evidence_decision = semantic_compilation.evidence_decision
        semantic_actions = list(semantic_compilation.action_intents)
        allow_text_semantic_fallback = False
    else:
        semantic_evidence = (
            evidence_decision_from_semantic(authority_task, semantic_intent)
            if semantic_intent is not None
            else None
        )
        semantic_actions = (
            list(semantic_intent.action_intents)
            if semantic_intent is not None
            and semantic_intent.confidence >= semantic_confidence_threshold()
            else []
        )
        evidence_decision = classify_evidence(
            authority_task,
            profile_id=profile_id,
            semantic_adviser=(
                (lambda _task, _profile: semantic_evidence)
                if semantic_evidence is not None
                else None
            ),
        )
        allow_text_semantic_fallback = True
    try:
        compiled = compile_task_authority(
            profile,
            authority_task,
            evidence_decision,
            semantic_action_intents=semantic_actions,
            allow_text_semantic_fallback=allow_text_semantic_fallback,
        )
    except EvidenceCompilationError as exc:
        return _agent_request_rejection(
            decision,
            profile=profile_id,
            task=authority_task,
            reason=exc.code,
            message=f"I can't safely compile this Agent task: {exc}",
        )
    return evidence_decision, semantic_actions, allow_text_semantic_fallback, compiled


def _agent_workspace_spec(
    profile: Any,
    *,
    repository: str,
    selected_workspace: str,
    selected_repository: str | None,
) -> WorkspaceSpec | None:
    if not (repository and profile.requires_workspace):
        return None
    if selected_workspace:
        return WorkspaceSpec(
            root=selected_workspace,
            repository=selected_repository,
            worktree=selected_workspace if selected_repository else None,
            base_ref="HEAD",
            isolation_policy=profile.isolation_policy,
        )
    return WorkspaceSpec(
        root=repository,
        repository=repository,
        base_ref=env_str("OMNIX_AGENT_DEFAULT_BASE_REF", "HEAD").strip() or "HEAD",
        isolation_policy=profile.isolation_policy,
    )


def _submit_agent_run(
    service: Any,
    spec: AgentRunSpec,
    session: Any,
    user_message: Any,
    *,
    superseded: Any,
    turn_plan: TurnPlan | None,
    semantic_context: str,
    reference_images: list[dict[str, str]],
    retry_source: _PendingAgentRetry | None,
) -> Any:
    """Cancel what the new run replaces, then submit it as a durable start job."""
    if superseded is not None:
        service.command(
            AgentRunCommand(
                run_id=superseded.run_id,
                command_type="cancel",
                payload={
                    "reason": "superseded_by_new_agent_objective"
                },
            )
        )
    if (
        turn_plan is not None
        and turn_plan.run_action == "replace_task_graph_with_agent"
    ):
        old_graph_run_id = str(turn_plan.active_run_id or "").strip()
        if old_graph_run_id:
            default_task_graph_runtime().cancel(
                old_graph_run_id,
                reason="superseded_by_agent_objective",
            )
    job_store = getattr(service, "job_store", None)
    submit_start = getattr(service, "submit_start", None)
    if isinstance(service, AgentRunService):
        if job_store is None or not callable(submit_start):
            raise RuntimeError("durable agent job service is not composed")
        reference_message_ids = [
            retry_source.reference_message_id
            if retry_source is not None and retry_source.reference_message_id
            else ""
        ]
        reference_message_ids = [
            message_id for message_id in reference_message_ids if message_id
        ]
        return submit_start(
            spec,
            job_store=job_store,
            reference_session_id=str(getattr(session, "id", "") or "") or None,
            reference_message_id=str(getattr(user_message, "id", "") or "") or None,
            reference_message_ids=reference_message_ids or None,
        )
    # In-memory AgentRunService ports keep routing tests and simulations
    # independent of the durable worker composition.
    contextual_start = getattr(service, "start_with_context", None)
    if callable(contextual_start):
        start_kwargs: dict[str, Any] = {"reference_context": semantic_context}
        if reference_images:
            start_kwargs["reference_images"] = reference_images
        return contextual_start(spec, **start_kwargs)
    return service.start(spec)


def _agent_task(content: str) -> str:
    task = re.sub(
        r"^(?:/agent\b|/agnet\b|agent[,:]\s*|use (?:the )?agent\b\s*)",
        "",
        content,
        flags=re.I,
    ).strip()
    return task or content


def _agent_semantic_reference_context(
    reference_context: str,
    semantic_task: SemanticTask | None,
    semantic_compilation: SemanticTaskCompilation | None,
    *,
    latest_user_message: str = "",
    attached_workspace: bool = False,
) -> str:
    """Give PI the bounded Semantic v2 target as reference, not authority."""

    if not attached_workspace or semantic_task is None or semantic_compilation is None:
        return reference_context
    if not profile_produces_diff(semantic_compilation.profile_id):
        return reference_context
    if not set(semantic_compilation.action_intents) & {
        "workspace_read",
        "workspace_mutate",
        "workspace_execute",
    }:
        return reference_context
    operation_summary = ", ".join(
        f"{operation.kind}:{operation.target}"
        for operation in semantic_task.operations
    )
    target = (
        "Semantic v2 execution target (reference only; the latest user request "
        "and issued capabilities remain authoritative):\n"
        f"Intent: {semantic_task.intent}\n"
        f"Operations: {operation_summary or 'workspace action'}\n"
        "Inspect the attached Local folder and implement this workspace change; "
        "treat conversation history as reference only and do not substitute a "
        "response-only task for the requested workspace work."
    )
    # A complete current request needs no historical Chat authority and should
    # not expose PI to stale, conflicting plans. Preserve history only for
    # genuinely referential messages such as "try again" or "fix it".
    prior = (
        str(reference_context or "").strip()
        if objective_continuity_candidate(latest_user_message)
        else ""
    )
    return f"{target}\n\n{prior}" if prior else target


def _coding_approval_policy(value: Any) -> AgentApprovalPolicy:
    normalized = str(value or "ask_sensitive").strip().casefold()
    if normalized in {"always_ask", "ask_sensitive", "allow_automatic"}:
        return cast(AgentApprovalPolicy, normalized)
    return "ask_sensitive"


def _agent_start_failure(
    decision: OmnixRouteDecision,
    *,
    run_id: str | None,
    profile: str,
    task: str,
    error: Exception,
    service: Any | None = None,
) -> GeneralizedChatResult:
    persisted = None
    if service is not None and run_id:
        try:
            persisted = service.get(run_id)
        except Exception:
            persisted = None
    error_text = f"{type(error).__name__}: {error}"[:2000]
    durable = persisted is not None
    workspace_required = "requires OMNIX_AGENT_DEFAULT_REPOSITORY or a Local folder" in error_text
    if workspace_required and not run_id:
        content = (
            "Agent request could not start because no coding workspace is configured. "
            "Attach a Local folder and send \"try again\", or restart the Omnix launcher "
            "to use its checkout as the default repository."
        )
    else:
        content = (
            f"Agent run {run_id} failed to start: {error_text}"
            if run_id
            else f"Agent request could not start: {error_text}"
        )
    return GeneralizedChatResult(
        content=content,
        metadata={
            "generation_status": "completed",
            "agent_mode": True,
            "omnix_route": decision.model_dump(mode="json"),
            "agent_start": {
                "status": "failed",
                "durable": durable,
                "error": error_text,
                "reason": "workspace_required" if workspace_required else "start_failed",
            },
            "active_objective": make_active_objective(
                canonical_request=task,
                profile=profile,
                status="blocked",
                blocking_reason=(
                    "workspace_required" if workspace_required else error_text
                ),
                run_id=run_id,
            ).model_dump(mode="json"),
            "agent_run": (
                {
                    "run_id": run_id,
                    "status": str(persisted.status),
                    "profile": str(persisted.spec.profile),
                    "task": str(persisted.spec.task),
                    "revision": persisted.revision,
                    "last_error": persisted.last_error,
                }
                if persisted is not None
                else {
                    "run_id": run_id,
                    "status": "failed",
                    "profile": profile,
                    "task": task,
                    "revision": None,
                    "last_error": error_text,
                }
            ),
        },
    )


def _agent_request_rejection(
    decision: OmnixRouteDecision,
    *,
    profile: str,
    task: str,
    reason: str,
    message: str,
) -> GeneralizedChatResult:
    return GeneralizedChatResult(
        content=message,
        metadata={
            "generation_status": "completed",
            "agent_mode": True,
            "omnix_route": decision.model_dump(mode="json"),
            "agent_start": {
                "status": "rejected",
                "durable": False,
                "reason": reason,
            },
            "active_objective": make_active_objective(
                canonical_request=task,
                profile=profile,
                status="blocked",
                blocking_reason=reason,
            ).model_dump(mode="json"),
            "agent_run": {
                "run_id": None,
                "status": "rejected",
                "profile": profile,
                "task": task,
                "revision": None,
                "last_error": reason,
            },
        },
    )


def _latest_active_agent_run(service: Any, session: Any):
    snapshot = _latest_agent_run(service, session)
    if snapshot is not None and snapshot.status not in _TERMINAL_AGENT:
        return snapshot
    return None


def _latest_agent_run(service: Any, session: Any):
    for message in reversed(list(getattr(session, "messages", []) or [])):
        if getattr(message, "role", None) != "assistant":
            continue
        metadata = getattr(message, "metadata", {}) or {}
        raw = metadata.get("agent_run")
        if not isinstance(raw, dict):
            continue
        run_id = str(raw.get("run_id") or "").strip()
        if not run_id:
            continue
        try:
            snapshot = service.get(run_id)
        except Exception as exc:
            log_recovered_exception("agent run lookup during chat reconciliation", exc)
            continue
        if snapshot is not None:
            return snapshot
    return None


def _continue_agent_run(
    service: Any,
    snapshot: Any,
    content: str,
    decision: OmnixRouteDecision,
    *,
    reference_context: str = "",
    reference_images: list[dict[str, str]] | None = None,
    turn_plan: TurnPlan | None = None,
) -> GeneralizedChatResult:
    rejection = _unauthorized_agent_command(snapshot, content)
    if rejection is not None:
        return GeneralizedChatResult(
            content=rejection["message"],
            metadata={
                "generation_status": "completed",
                "agent_mode": True,
                "omnix_route": decision.model_dump(mode="json"),
                "agent_run": _agent_metadata(snapshot),
                "agent_command": {
                    "accepted": False,
                    "command_type": "steer",
                    "reason": rejection["reason"],
                    "required_capabilities": rejection["required_capabilities"],
                },
            },
        )
    command_type = "steer"
    payload: dict[str, Any] = {"message": content}
    normalized = " ".join(content.strip().split())
    if _PAUSE.fullmatch(normalized):
        command_type, payload = "pause", {}
    elif _RESUME.fullmatch(normalized) and snapshot.status == "paused":
        command_type, payload = "resume", {"message": "Resume from the current workspace state."}
    elif snapshot.status == "waiting_for_approval" and (_CONFIRM.fullmatch(normalized) or _REJECT.fullmatch(normalized)):
        pending = service.approvals(snapshot.run_id, state="pending")
        if len(pending) == 1:
            command_type = "approve" if _CONFIRM.fullmatch(normalized) else "reject"
            payload = {"approval_id": pending[0].approval_id}
    elif _CANCEL.fullmatch(normalized):
        command_type, payload = "cancel", {}

    digest_material = normalized
    if command_type == "steer" and reference_context:
        digest_material += "\nreference-context:\n" + reference_context
    if command_type == "steer" and reference_images:
        digest_material += "\nreference-images:\n" + "\n".join(
            hashlib.sha256(
                image.get("data", "").encode("ascii", errors="ignore")
            ).hexdigest()
            for image in reference_images
        )
    command_digest = hashlib.sha256(digest_material.encode("utf-8")).hexdigest()[:24]
    command = AgentRunCommand(
        run_id=snapshot.run_id,
        command_type=cast(Any, command_type),
        payload=payload,
        idempotency_key=f"chat:{snapshot.run_id}:{command_type}:{command_digest}",
    )

    try:
        contextual_command = getattr(service, "command_with_context", None)
        updated = (
            contextual_command(
                command,
                reference_context=reference_context,
                **({"reference_images": reference_images} if reference_images else {}),
                **({"turn_plan": turn_plan} if turn_plan is not None else {}),
            )
            if command_type == "steer" and callable(contextual_command)
            else service.command(command)
        )
    except Exception as exc:
        return GeneralizedChatResult(
            content=f"Agent run {snapshot.run_id} could not accept that command: {type(exc).__name__}: {exc}",
            metadata={
                "generation_status": "completed",
                "agent_mode": True,
                "omnix_route": decision.model_dump(mode="json"),
                "agent_run": _agent_metadata(snapshot),
            },
        )
    if command_type == "steer" and updated.run_id != snapshot.run_id:
        return GeneralizedChatResult(
            content=(
                f"Started superseding Agent run {updated.run_id} because the revised task "
                "requires a different authority/evidence contract."
            ),
            metadata={
                "generation_status": "completed",
                "agent_mode": True,
                "omnix_route": decision.model_dump(mode="json"),
                "agent_run": _agent_metadata(updated),
                "supersedes_run_id": snapshot.run_id,
            },
        )
    verb = {
        "steer": "Steering sent to",
        "pause": "Pause requested for",
        "resume": "Resume requested for",
        "cancel": "Cancellation requested for",
        "approve": "Approval sent to",
        "reject": "Rejection sent to",
    }[command_type]
    return GeneralizedChatResult(
        content=f"{verb} Agent run {updated.run_id}.",
        metadata={
            "generation_status": "completed",
            "agent_mode": True,
            "omnix_route": decision.model_dump(mode="json"),
            "agent_run": _agent_metadata(updated),
        },
    )


def _unauthorized_agent_command(snapshot: Any, content: str) -> dict[str, Any] | None:
    external_capabilities = {str(value) for value in (snapshot.spec.external_capabilities or [])}
    profile = str(snapshot.spec.profile or "")
    if profile in {"research", "trading-research"} and _TRADING_MUTATION.search(content):
        return {
            "reason": "trading_execution_capability_not_issued",
            "required_capabilities": ["trading.order"],
            "message": (
                "I can't place or manage trades from this read-only research run. "
                "Start a separately scoped, approval-gated trading run if execution is intended."
            ),
        }
    if _PUBLICATION_REQUEST.search(content) and not {
        "github.push",
        "github.create_pr",
    }.issubset(external_capabilities):
        return {
            "reason": "github_publication_capability_not_issued",
            "required_capabilities": ["github.push", "github.create_pr"],
            "message": (
                "I can't publish from this run: GitHub push/PR capabilities were not issued. "
                "The local workspace authority does not grant publication authority."
            ),
        }
    return None


def _agent_metadata(snapshot: Any) -> dict[str, Any]:
    return {
        "run_id": snapshot.run_id,
        "status": snapshot.status,
        "profile": snapshot.spec.profile,
        "task": snapshot.spec.task,
        "revision": snapshot.revision,
        "last_error": snapshot.last_error,
        "superseded_by_run_id": getattr(snapshot, "superseded_by_run_id", None),
        "supersedes_run_id": getattr(snapshot.spec, "supersedes_run_id", None),
        "request_mode": snapshot.spec.request_mode.model_dump(mode="json") if snapshot.spec.request_mode else None,
        "evidence_policy": snapshot.spec.evidence_policy.model_dump(mode="json"),
    }
