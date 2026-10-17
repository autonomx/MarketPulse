"""Converge typed Omnix Chat onto the generalized execution lanes.

Live voice keeps the existing latency-optimized Live Agent path. Typed requests
are classified in AUTO mode across CHAT, DIRECT, WORKFLOW, and AGENT. The
persistent Agent control forces eligible typed turns through AGENT, while
explicit /agent and per-turn Quick/Deep research commands take precedence.
"""
from __future__ import annotations

from .exception_logging import log_recovered_exception

# Lane modules resolve these through this module at call time; tests replace them here.
from app.providers.service import get_provider as get_provider
from app.platform.assistant_tools.contracts import review_assistant_tool_request as review_assistant_tool_request
from .evidence import validate_required_evidence_capabilities as validate_required_evidence_capabilities
from app.capabilities.executor import execute_capability as execute_capability

from dataclasses import dataclass
import re
from collections.abc import Callable
from typing import Any, cast

from .active_objective import (
    ActiveObjective,
    advance_active_objective,
    build_routing_environment,
    make_active_objective,
    objective_continuity_candidate,
    resolve_active_objective,
)
from .contracts import (
    RequestModeSelection,
)
from .evidence import (
    resolve_request_mode,
)
from .profiles import get_agent_profile, select_agent_profile_id
from .router import OmnixRouteDecision, route_omnix_fast_path, semantic_authority_risk
from .semantic_classifier import (
    SemanticIntentDecision,
    classify_semantic_intent_safely,
    semantic_confidence_threshold,
)
from .semantic_normalizer import normalize_semantic_task
from .semantic_task import (
    SemanticTask,
    SemanticTaskCompilation,
    semantic_task_from_legacy,
)
from .semantic_task_parser import (
    classify_semantic_task_safely,
    default_semantic_task_parser,
)
from .turn_plan import (
    TurnPlan,
    compile_turn_plan,
    derive_effective_objective,
)
from .task_graph_runtime import default_task_graph_runtime
from .service import default_agent_run_service
from .chat_lane_common import (
    GeneralizedChatResult,
    _TERMINAL_AGENT as _TERMINAL_AGENT,
)
from .chat_lane_agent import (
    _agent_reference_images,
    _agent_request_rejection,
    _agent_result,
    _agent_semantic_reference_context,
    _agent_start_failure,
    _continue_agent_run,
    _pending_failed_agent_retry,
    _AGENT_IMAGE_DATA_URL as _AGENT_IMAGE_DATA_URL,
    _CANCEL as _CANCEL,
    _CONFIRM as _CONFIRM,
    _CONTROL as _CONTROL,
    _DEFAULT_AGENT_REASONING_EFFORT as _DEFAULT_AGENT_REASONING_EFFORT,
    _LEGACY_OBJECTIVE_REVISION_SEPARATOR as _LEGACY_OBJECTIVE_REVISION_SEPARATOR,
    _PAUSE as _PAUSE,
    _PUBLICATION_REQUEST as _PUBLICATION_REQUEST,
    _PendingAgentRetry as _PendingAgentRetry,
    _REJECT as _REJECT,
    _RESUME as _RESUME,
    _TRADING_MUTATION as _TRADING_MUTATION,
    _WORKSPACE_UNAVAILABLE_RESPONSE as _WORKSPACE_UNAVAILABLE_RESPONSE,
    _agent_metadata as _agent_metadata,
    _agent_reasoning_effort as _agent_reasoning_effort,
    _agent_task as _agent_task,
    _coding_approval_policy as _coding_approval_policy,
    _latest_active_agent_run as _latest_active_agent_run,
    _latest_agent_run as _latest_agent_run,
    _latest_canonical_request as _latest_canonical_request,
    _resolve_agent_model_route as _resolve_agent_model_route,
    _unauthorized_agent_command as _unauthorized_agent_command,
)
from .chat_lane_task_graph import (
    _task_graph_result,
)
from .chat_lane_direct import (
    _direct_result,
    _workflow_lookup,
    _workflow_result,
    _HOME_SET as _HOME_SET,
    _HOME_STATE as _HOME_STATE,
    _clean_home_target as _clean_home_target,
    _direct_request as _direct_request,
)
from .chat_lane_evidence import (
    _enforce_chat_evidence,
    _CHAT_EVIDENCE_ALLOWED_CAPABILITIES as _CHAT_EVIDENCE_ALLOWED_CAPABILITIES,
    _CHAT_EVIDENCE_CAPABILITY_BY_SOURCE as _CHAT_EVIDENCE_CAPABILITY_BY_SOURCE,
    _chat_evidence_failure as _chat_evidence_failure,
    _chat_evidence_input as _chat_evidence_input,
    _chat_evidence_subject_label as _chat_evidence_subject_label,
    _localize_attached_workspace_evidence as _localize_attached_workspace_evidence,
)

_CODE = re.compile(
    r"(?:"
    r"\b(?:code|repo(?:sitory)?|branch|pull request|bug(?:s)?|test(?:s|ing)?|pytest|vitest|"
    r"refactor(?:ing)?|implement(?:ation|ing)?|fix(?:es|ing)?|debugg?(?:ing)?|edit(?:ing)?|"
    r"modify|patch|workspace|file(?:s)?|module|function|class)\b"
    r"|\.(?:py|pyi|js|jsx|ts|tsx|go|rs|java|rb|php|cs|cpp|c|h)\b"
    r"|\b(?:add|write|change|update|comment)\b.{0,120}\b(?:router|file|code|function|class|module|"
    r"repository|repo|workspace|source)\b"
    r")",
    re.I,
)
_HOME = re.compile(r"\b(?:kasa|smart\s+plugs?|plugs?|outlets?|lamps?|lights?|thermostats?|home)\b", re.I)
_PERSONAL = re.compile(r"\b(?:gmail|emails?|calendars?|meetings?|contacts?|appointments?|schedules?)\b", re.I)
_TRADING = re.compile(
    r"\b(?:stocks?|trading|trades?|tickers?|markets?|shares?|equities|gainers?|losers?|"
    r"orders?|positions?|buy|sell|purchase|short|cover)\b",
    re.I,
)
_TICKER_CONTEXT = re.compile(
    r"\b(?:research|reseach|investigate|analy[sz]e|anlyze|buy|sell|purchase|short)\b"
    r".{0,80}(?:\$[A-Z]{1,5}\b|\b(?:NVDA|GME|TSLA)\b)"
)
_RETRY_FAILED_AGENT = re.compile(
    r"^(?:please\s+)?(?:try\s+again|try\s+agian|retry(?:\s+(?:it|that|the\s+request))?|do\s+it\s+again)[.!\s]*$",
    re.I,
)
_WORKSPACE_RETRY = re.compile(
    r"\b(?:try\s+again|retry|do\s+it\s+again)\b.{0,100}"
    r"\b(?:in|with|using)\s+(?:the\s+)?(?:code|coding|repo(?:sitory)?|workspace|project(?:\s+folder)?)\b",
    re.I,
)
_WORKSPACE_MUTATION = re.compile(
    r"(?:\b(?:edit|modify|write|change|patch|commit|delete|remove|create)\b.{0,120}\b(?:repo(?:sitory)?|"
    r"file|code|workspace|branch|source|module|script)\b|\.(?:py|pyi|js|jsx|ts|tsx|go|rs|java|rb|php|cs|cpp|c|h)\b|"
    r"\b(?:git\s+push|push\s+to\s+origin|open\s+(?:a\s+)?pull\s+request)\b)",
    re.I,
)
_CLASSIFIER_STEERING = re.compile(
    r"(?:\b(?:ignore|disregard|override)\b.{0,100}\b(?:classifier|routing|router|rules?)\b|"
    r"\b(?:label|classify|route)\s+(?:this|it)\s+(?:as\s+)?(?:chat|agent)\b)",
    re.I,
)
_SEMANTIC_AUTO = object()


def _routing_context_text(value: Any) -> str:
    """Read only the canonical Chat reference projection, never its authority."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        candidate = value.get("reference_context")
    else:
        candidate = getattr(value, "reference_context", None)
    return str(candidate or "").strip()


def _resolve_routing_context(
    session: Any,
    user_message: Any,
    factory: Callable[[], Any] | None,
) -> str:
    """Prefer the canonical Chat memory/history/summary context.

    Production Chat passes a lazy factory from ChatSessionStore. The fallback
    also uses PromptAssembly so direct/unit callers do not revive a parallel
    ad-hoc transcript window.
    """

    if factory is not None:
        try:
            return _routing_context_text(factory())
        except Exception as exc:
            log_recovered_exception("chat routing context factory", exc, level="DEBUG")
            pass

    try:
        from app.platform.chat.contracts import build_chat_routing_context, build_prompt_assembly

        assembly = build_prompt_assembly(
            session,
            user_message,
            global_system_prompt="",
            context_items=[],
            approved_memory=[],
            retrieved_history=[],
        )
        return build_chat_routing_context(assembly).reference_context
    except Exception as exc:
        log_recovered_exception("chat routing context assembly", exc, level="DEBUG")
        return ""


def _compact_routing_context(value: str, *, max_chars: int = 6000) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    # The active objective is supplied separately, so a retry can safely keep
    # only the most recent conversational tail instead of replaying a huge
    # reference package after a parser failure.
    return "[older routing context omitted]\n" + text[-max_chars:]


def _continuity_content_override(
    submitted_content: str,
    active_objective: ActiveObjective | None,
    semantic_task: SemanticTask | None,
    semantic_compilation: SemanticTaskCompilation | None,
) -> str | None:
    """Compatibility wrapper; TurnPlanCompiler owns continuity semantics."""

    if semantic_task is None:
        return None
    plan = compile_turn_plan(
        submitted_content,
        semantic_task,
        active_objective=active_objective,
    )
    if plan.relation == "none":
        return None
    return plan.effective_request


def _should_use_semantic_classifier(decision: OmnixRouteDecision, content: str) -> bool:
    if not str(content or "").strip():
        return False
    if decision.reason == "casual_or_empty":
        return False
    if decision.lane in {"direct", "workflow"} and decision.confidence >= 0.95:
        return False
    return True


def _negated_action_allows_semantic_agent(
    content: str,
    semantic: SemanticIntentDecision,
) -> bool:
    """Distinguish total refusal from a narrow prohibition plus allowed work."""

    if semantic.lane != "agent":
        return False
    actions = {str(value) for value in semantic.action_intents}
    if not actions:
        return False

    text = " ".join(str(content or "").split())
    if re.match(
        r"^(?:don'?t|do\s+not)\s+just\s+(?:tell|explain|describe)\b",
        text,
        re.I,
    ):
        return True

    # A broad refusal such as "don't touch anything" still blocks semantic
    # promotion. Narrow prohibitions below only remove the forbidden action;
    # another requested action may still justify Agent.
    if re.match(
        r"^(?:don'?t|do\s+not|never)\s+(?:touch|access)\s+anything\b",
        text,
        re.I,
    ):
        return False

    forbidden: set[str] = set()
    if re.search(r"\b(?:don'?t|do\s+not|never)\s+(?:send|reply|forward)\b", text, re.I):
        forbidden.add("email_send")
    if re.search(r"\b(?:don'?t|do\s+not|never)\s+(?:draft|compose)\b", text, re.I):
        forbidden.add("email_draft")
    if re.search(
        r"\b(?:don'?t|do\s+not|never)\s+(?:schedule|book|create|add)\b.{0,80}"
        r"\b(?:calendar|meeting|appointment|event)\b",
        text,
        re.I,
    ):
        forbidden.add("calendar_create")
    if re.search(
        r"\b(?:don'?t|do\s+not|never)\s+(?:turn|set|adjust|lower|raise|dim|brighten|change)\b.{0,80}"
        r"\b(?:light|lamp|plug|outlet|thermostat|home)\b",
        text,
        re.I,
    ) or re.search(
        r"\b(?:don'?t|do\s+not|never)\s+change\s+(?:the\s+)?(?:lights?|lamps?)\b",
        text,
        re.I,
    ):
        forbidden.add("home_mutate")
    if re.search(
        r"\b(?:don'?t|do\s+not|never)\s+(?:edit|modify|write|change|patch|update|delete|remove)\b",
        text,
        re.I,
    ):
        forbidden.add("workspace_mutate")

    return bool(actions - forbidden)


def _apply_semantic_route_decision(
    deterministic: OmnixRouteDecision,
    semantic: SemanticIntentDecision | None,
    *,
    content: str | None = None,
) -> OmnixRouteDecision:
    if semantic is None or semantic.confidence < semantic_confidence_threshold():
        return deterministic
    # Hypotheticals remain non-executing. A broad no-action request also stays
    # Chat, but a narrow prohibition (for example "don't send; draft instead")
    # must not suppress a separately requested allowed Agent action. Capability
    # compilation still enforces the explicit prohibition deterministically.
    if deterministic.reason == "hypothetical_or_conditional":
        return deterministic
    if deterministic.reason == "negated_action" and not (
        content is not None
        and _negated_action_allows_semantic_agent(content, semantic)
    ):
        return deterministic
    if (
        content is not None
        and deterministic.lane == "agent"
        and semantic.lane == "chat"
        and _CLASSIFIER_STEERING.search(content)
    ):
        return deterministic.model_copy(
            update={
                "reason": f"{deterministic.reason}+classifier_steering_ignored"[:240],
                "hermes_recommended": deterministic.hermes_recommended or semantic.multi_step,
            }
        )
    if deterministic.explicit:
        return deterministic.model_copy(
            update={
                "reason": f"{deterministic.reason}+semantic:{semantic.primary_intent}"[:240],
                "hermes_recommended": deterministic.hermes_recommended or semantic.multi_step,
            }
        )
    if deterministic.lane in {"direct", "workflow"} and deterministic.confidence >= 0.95:
        return deterministic
    if (
        deterministic.lane == "agent"
        and deterministic.reason in {
            "workspace_mutation_request",
            "workspace_read_request",
            "workspace_retry_request",
        }
        and deterministic.confidence >= 0.95
    ):
        # Concrete workspace reads/mutations are executable requests even when
        # the advisory classifier mistakes a terse repository request for Chat.
        return deterministic
    if (
        deterministic.lane == "chat"
        and semantic.lane == "agent"
        and not semantic.action_intents
    ):
        # A semantic Agent label without any executable semantic action is too
        # weak to promote a conversational request into an autonomous run.
        # This preserves deterministic Chat for planning-only or malformed
        # classifier outputs while still allowing action-bearing semantic
        # upgrades for indirect coding, personal-assistant, home, and research work.
        return deterministic
    return OmnixRouteDecision(
        lane=semantic.lane,
        confidence=semantic.confidence,
        reason=f"semantic:{semantic.primary_intent}"[:240],
        explicit=False,
        hermes_recommended=semantic.multi_step,
    )


def _mark_chat_route(
    user_message: Any,
    decision: OmnixRouteDecision,
    *,
    semantic_intent: SemanticIntentDecision | None = None,
    semantic_task: SemanticTask | None = None,
    semantic_compilation: SemanticTaskCompilation | None = None,
    routing_shadow: dict[str, Any] | None = None,
    request_mode: RequestModeSelection | None = None,
) -> None:
    metadata = getattr(user_message, "metadata", None)
    if not isinstance(metadata, dict):
        return
    metadata["omnix_chat_routed"] = True
    metadata["omnix_route"] = decision.model_dump(mode="json")
    if semantic_intent is not None:
        metadata["semantic_intent"] = semantic_intent.model_dump(mode="json")
    if semantic_task is not None:
        metadata["semantic_task"] = semantic_task.model_dump(mode="json")
    if semantic_compilation is not None:
        metadata["semantic_compilation"] = semantic_compilation.model_dump(mode="json")
    if routing_shadow is not None:
        metadata["routing_decision"] = routing_shadow
    if request_mode is not None:
        metadata["request_mode"] = request_mode.model_dump(mode="json")


def _promote_active_agent_response_continuation(
    active_objective: ActiveObjective | None,
    task: SemanticTask | None,
    compilation: SemanticTaskCompilation | None,
    *,
    latest_user_message: str,
) -> SemanticTaskCompilation | None:
    """Compatibility wrapper; TurnPlanCompiler owns final lane selection."""

    if task is None or compilation is None:
        return compilation
    return compile_turn_plan(
        latest_user_message,
        task,
        active_objective=active_objective,
    ).compilation


def _semantic_route_from_compilation(
    fast_path: OmnixRouteDecision,
    task: SemanticTask,
    compilation: SemanticTaskCompilation,
) -> OmnixRouteDecision:
    if fast_path.explicit:
        return fast_path.model_copy(
            update={
                "reason": f"explicit_agent+semantic_v2:{compilation.reason_code}"[:240],
                "hermes_recommended": compilation.multi_step,
            }
        )
    return OmnixRouteDecision(
        lane=compilation.lane,
        confidence=task.confidence,
        reason=f"semantic_v2:{compilation.reason_code}"[:240],
        explicit=False,
        hermes_recommended=compilation.multi_step,
    )


def _routing_decision_payload(
    production: OmnixRouteDecision,
    semantic: OmnixRouteDecision | None,
    *,
    parser_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "production_router": "semantic_v2",
        "production_lane": production.lane,
        "semantic_v2": (
            semantic.model_dump(mode="json")
            if semantic is not None
            else production.model_dump(mode="json")
        ),
    }
    if parser_diagnostics:
        payload["parser"] = dict(parser_diagnostics)
    return payload


def _semantic_clarification_result(
    decision: OmnixRouteDecision,
    *,
    task: SemanticTask | None,
    compilation: SemanticTaskCompilation | None,
    request_mode: RequestModeSelection,
    routing_shadow: dict[str, Any],
    canonical_request: str = "",
    parser_unavailable: bool = False,
) -> GeneralizedChatResult:
    if parser_unavailable:
        content = (
            "I couldn't safely determine which execution domain this request belongs to, "
            "so I won't guess and start a stateful Agent. Please clarify what you want "
            "Omnix to act on."
        )
        reason = "semantic_parser_unavailable"
    else:
        candidates = list(task.candidate_interpretations) if task is not None else []
        if compilation is not None:
            for anomaly in compilation.anomalies:
                if anomaly.code == "unsupported_composite_profiles":
                    candidates.append(anomaly.detail)
        suffix = f" Possible interpretations: {'; '.join(dict.fromkeys(candidates))}." if candidates else ""
        content = (
            "I need one clarification before starting a stateful Agent because the "
            "execution target is ambiguous."
            + suffix
        )
        reason = "semantic_clarification_required"
    objective_request = str(canonical_request or "").strip() or (
        str(task.intent).strip() if task is not None else "clarify the pending request"
    )
    # A clarification has not selected an executable Agent profile. Keep the
    # reference metadata on a registered, least-surprising profile so a later
    # ordinary Chat turn cannot promote an invalid sentinel such as "agent"
    # into get_agent_profile().
    objective_profile = str(
        compilation.profile_id
        if compilation is not None and compilation.profile_id
        else "research"
    ).strip()
    try:
        get_agent_profile(objective_profile)
    except ValueError:
        objective_profile = "research"
    return GeneralizedChatResult(
        content=content,
        metadata={
            "generation_status": "completed",
            "agent_mode": request_mode.mode == "agent",
            "omnix_route": decision.model_dump(mode="json"),
            "request_mode": request_mode.model_dump(mode="json"),
            "semantic_task": task.model_dump(mode="json") if task is not None else None,
            "semantic_compilation": (
                compilation.model_dump(mode="json")
                if compilation is not None
                else None
            ),
            "routing_decision": routing_shadow,
            "clarification": {
                "status": "waiting_for_input",
                "reason": reason,
                "question": content,
            },
            "active_objective": make_active_objective(
                canonical_request=objective_request,
                profile=objective_profile,
                status="awaiting_user",
                blocking_reason=reason,
            ).model_dump(mode="json"),
            "semantic_gate": {
                "accepted": False,
                "reason": reason,
            },
        },
    )


@dataclass
class _ChatTurn:
    """One typed Chat turn as it moves through routing (WP-8.2).

    Each phase of ``route_typed_chat_turn`` reads and fills these fields; a
    phase that settles the turn returns its result instead of ``_PROCEED``.
    """

    session: Any
    user_message: Any
    submitted_content: str
    metadata: dict[str, Any]
    active_objective: ActiveObjective | None
    routing_environment: Any
    active_objective_text: str
    contextual_resolution_required: bool
    pending_retry: Any
    explicit_agent: bool
    research_mode: str | None
    fast_path: OmnixRouteDecision
    previous_routing_context: str = ""
    semantic_intent: SemanticIntentDecision | None = None
    semantic_task: SemanticTask | None = None
    semantic_compilation: SemanticTaskCompilation | None = None
    turn_plan: TurnPlan | None = None
    semantic_parser_diagnostics: dict[str, Any] | None = None
    semantic_parser_for_retry: Any | None = None
    semantic_route: OmnixRouteDecision | None = None
    decision: OmnixRouteDecision | None = None
    routing: dict[str, Any] | None = None
    mode: RequestModeSelection | None = None
    parser_unavailable_safe_chat: bool = False

    @property
    def content(self) -> str:
        return self.submitted_content

    # Routing fills decision, mode and routing before any later phase reads them.
    @property
    def routed(self) -> OmnixRouteDecision:
        if self.decision is None:
            raise RuntimeError("chat turn read before routing settled its decision")
        return self.decision

    @property
    def routed_mode(self) -> RequestModeSelection:
        if self.mode is None:
            raise RuntimeError("chat turn read before routing settled its request mode")
        return self.mode

    @property
    def routed_routing(self) -> dict[str, Any]:
        if self.routing is None:
            raise RuntimeError("chat turn read before routing recorded its routing decision")
        return self.routing

    def mark_route(self) -> None:
        _mark_chat_route(
            self.user_message,
            self.routed,
            semantic_intent=self.semantic_intent,
            semantic_task=self.semantic_task,
            semantic_compilation=self.semantic_compilation,
            routing_shadow=self.routing,
            request_mode=self.mode,
        )

    def clarification(self, *, task_known: bool = True, parser_unavailable: bool = False) -> GeneralizedChatResult:
        return _semantic_clarification_result(
            self.routed,
            task=self.semantic_task if task_known else None,
            compilation=self.semantic_compilation if task_known else None,
            request_mode=self.routed_mode,
            routing_shadow=self.routed_routing,
            canonical_request=self.submitted_content,
            **({"parser_unavailable": True} if parser_unavailable else {}),
        )


# Returned by a routing phase that has not settled the turn.
_PROCEED = object()


def route_typed_chat_turn(
    session: Any,
    user_message: Any,
    *,
    provider_id: str | None,
    model_id: str | None,
    context_items: list[dict[str, Any]] | None = None,
    routing_deadline_at: float | None = None,
    semantic_classifier: Any = _SEMANTIC_AUTO,
    routing_context_factory: Callable[[], Any] | None = None,
) -> GeneralizedChatResult | None:
    # External assistant-context enrichment is intentionally not routing
    # authority. Conversational reference context comes from the canonical Chat
    # prompt pipeline (recent turns, summary, approved memory, retrieved history).
    # The same mutable context list may receive governed, read-only evidence
    # after routing has completed so the provider cannot answer current facts
    # from model memory alone.
    #
    # ``None`` means "answer as ordinary Chat"; a result settles the turn.
    if _is_live_voice(user_message):
        return None

    turn = _begin_chat_turn(session, user_message)
    answered = _answer_waiting_agent(turn, routing_context_factory)
    if answered is not None:
        return answered
    if _explicit_research_turn(turn):
        return None

    _parse_turn_semantics(
        turn,
        provider_id=provider_id,
        model_id=model_id,
        routing_deadline_at=routing_deadline_at,
        semantic_classifier=semantic_classifier,
        routing_context_factory=routing_context_factory,
    )
    for phase in (_decide_turn_route, _cancel_superseded_task_graph):
        outcome = phase(turn)
        if outcome is not _PROCEED:
            return outcome
    if turn.routed.lane == "chat":
        return _chat_lane_turn(turn, context_items)
    if turn.routed.lane == "direct":
        return _direct_result(session, user_message, turn.routed)
    if turn.routed.lane == "workflow":
        return _workflow_result(session, user_message, turn.routed)
    return _execute_turn(
        turn,
        provider_id=provider_id,
        model_id=model_id,
        routing_deadline_at=routing_deadline_at,
        routing_context_factory=routing_context_factory,
    )


def _begin_chat_turn(session: Any, user_message: Any) -> _ChatTurn:
    """Resolve the active objective, routing environment and syntax-only route."""
    submitted_content = str(user_message.content or "").strip()
    metadata = getattr(user_message, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
    active_objective = _settled_task_graph_objective(
        resolve_active_objective(session, user_message),
        metadata,
    )
    routing_environment = build_routing_environment(user_message)
    metadata["routing_environment"] = routing_environment.model_dump(mode="json")
    if active_objective is not None:
        # Persist the objective reference across ordinary chat turns. It is
        # still reference-only; SemanticTask + deterministic compilation
        # decide whether the latest user message actually resumes it.
        metadata["active_objective"] = active_objective.model_dump(mode="json")

    pending_retry = (
        _pending_failed_agent_retry(session, user_message)
        if _RETRY_FAILED_AGENT.fullmatch(submitted_content)
        or _WORKSPACE_RETRY.search(submitted_content)
        else None
    )
    return _ChatTurn(
        session=session,
        user_message=user_message,
        submitted_content=submitted_content,
        metadata=metadata,
        active_objective=active_objective,
        routing_environment=routing_environment,
        active_objective_text=(
            active_objective.reference_text() if active_objective is not None else ""
        ),
        contextual_resolution_required=bool(
            active_objective is not None
            and objective_continuity_candidate(submitted_content)
        ),
        pending_retry=pending_retry,
        explicit_agent=(
            bool(metadata.get("agent_mode"))
            or pending_retry is not None
            or (active_objective is not None and active_objective.status == "awaiting_user")
        ),
        research_mode=_message_research_mode(metadata),
        # Production deterministic routing is deliberately syntax-only.
        # SemanticTask v2 plus deterministic compilation owns all
        # natural-language meaning.
        fast_path=route_omnix_fast_path(
            submitted_content,
            workflow_lookup=_workflow_lookup,
        ),
    )


def _settled_task_graph_objective(
    active_objective: ActiveObjective | None,
    metadata: dict[str, Any],
) -> ActiveObjective | None:
    """Reflect a finished or approval-waiting task graph in the active objective."""
    if (
        active_objective is None
        or active_objective.profile != "task-graph"
        or not active_objective.run_id
    ):
        return active_objective
    try:
        graph_snapshot = default_task_graph_runtime().get_status(
            active_objective.run_id
        )
    except Exception as exc:
        log_recovered_exception("active task graph lookup", exc, level="DEBUG")
        graph_snapshot = None
    if graph_snapshot is None:
        return active_objective
    graph_status = str(graph_snapshot.status).casefold()
    if graph_status in {"completed", "failed", "cancelled"}:
        terminal_status = (
            "completed"
            if graph_status == "completed"
            else "cancelled"
            if graph_status == "cancelled"
            else "abandoned"
        )
        metadata["active_objective"] = active_objective.model_copy(
            update={"status": terminal_status}
        ).model_dump(mode="json")
        return None
    if graph_status == "waiting_for_approval":
        return active_objective.model_copy(update={"status": "awaiting_user"})
    return active_objective


def _answer_waiting_agent(
    turn: _ChatTurn,
    routing_context_factory: Callable[[], Any] | None,
) -> GeneralizedChatResult | None:
    """A clarification answer belongs to the Agent run waiting for it.

    This runs before semantic routing so a short answer that looks like
    ordinary Chat cannot bypass the durable run.
    """
    waiting_run_id = (
        str(turn.active_objective.run_id or "").strip() if turn.active_objective else ""
    )
    if not waiting_run_id:
        return None
    try:
        waiting_service = default_agent_run_service()
        waiting_snapshot = waiting_service.get(waiting_run_id)
    except Exception as exc:
        log_recovered_exception("waiting agent run lookup", exc)
        return None
    if waiting_snapshot is None or waiting_snapshot.status != "waiting_for_input":
        return None
    decision = turn.fast_path.model_copy(update={
        "lane": "agent",
        "confidence": 1.0,
        "reason": "pending_agent_clarification",
        "explicit": True,
    })
    routing = _routing_decision_payload(decision, None)
    mode = resolve_request_mode(
        turn.content,
        turn_research_mode=None,
        persistent_agent=True,
        classifier_lane="agent",
    )
    _mark_chat_route(
        turn.user_message,
        decision,
        routing_shadow=routing,
        request_mode=mode,
    )
    result = _continue_agent_run(
        waiting_service,
        waiting_snapshot,
        turn.content,
        decision,
        reference_context=_resolve_routing_context(
            turn.session,
            turn.user_message,
            routing_context_factory,
        ),
        reference_images=_agent_reference_images(turn.metadata),
    )
    result.metadata.setdefault("clarification", {
        "status": "answered",
        "run_id": waiting_snapshot.run_id,
    })
    return result


def _explicit_research_turn(turn: _ChatTurn) -> bool:
    """Explicit research syntax is the only request that skips semantic parsing.

    A persistent research setting is resolved after compilation so a concrete
    workspace action cannot be diverted away from the Agent lane.
    """
    preliminary_mode = resolve_request_mode(
        turn.content,
        turn_research_mode=None,
        persistent_agent=turn.explicit_agent,
        classifier_lane=turn.fast_path.lane,
    )
    if preliminary_mode.mode not in {"quick_research", "deep_research"}:
        return False
    _mark_chat_route(
        turn.user_message,
        turn.fast_path,
        routing_shadow=_routing_decision_payload(turn.fast_path, None),
        request_mode=preliminary_mode,
    )
    return True


def _parse_turn_semantics(
    turn: _ChatTurn,
    *,
    provider_id: str | None,
    model_id: str | None,
    routing_deadline_at: float | None,
    semantic_classifier: Any,
    routing_context_factory: Callable[[], Any] | None,
) -> None:
    """Parse the SemanticTask (retrying with compact context) and compile the turn plan."""
    if not _should_use_semantic_classifier(turn.fast_path, turn.content):
        return
    turn.previous_routing_context = _resolve_routing_context(
        turn.session,
        turn.user_message,
        routing_context_factory,
    )
    environment = turn.routing_environment.model_dump(mode="json")
    if semantic_classifier is _SEMANTIC_AUTO:
        parser = default_semantic_task_parser(
            provider_id=(
                str(provider_id or getattr(turn.session, "provider_id", None) or "").strip()
                or None
            ),
            model_id=(
                str(model_id or getattr(turn.session, "model_id", None) or "").strip()
                or None
            ),
        )
        _classify_turn(turn, parser, environment, routing_deadline_at)
    elif callable(getattr(semantic_classifier, "parse_contextual", None)) or callable(
        getattr(semantic_classifier, "parse", None)
    ):
        # Compatibility for tests/extensions that still provide v1 semantic
        # classifiers. Production AUTO mode uses SemanticTask v2.
        _classify_turn(turn, semantic_classifier, environment, routing_deadline_at)
    else:
        turn.semantic_intent = classify_semantic_intent_safely(
            semantic_classifier,
            turn.content,
            reference_context=turn.previous_routing_context,
        )
        if turn.semantic_intent is not None:
            turn.semantic_task = semantic_task_from_legacy(turn.semantic_intent)

    if (
        turn.semantic_task is None
        and turn.semantic_parser_for_retry is not None
        and turn.contextual_resolution_required
    ):
        retry_context = _compact_routing_context(turn.previous_routing_context)
        turn.semantic_task = classify_semantic_task_safely(
            turn.semantic_parser_for_retry,
            turn.content,
            reference_context=retry_context,
            previous_objective=turn.active_objective_text,
            current_environment=environment,
            deadline_at=routing_deadline_at,
        )
        retry_diag = dict(turn.semantic_parser_diagnostics or {})
        retry_diag["context_retry_attempted"] = True
        retry_diag["context_retry_chars"] = len(retry_context)
        retry_diag["context_retry_succeeded"] = turn.semantic_task is not None
        turn.semantic_parser_diagnostics = retry_diag

    if turn.semantic_task is not None:
        turn.turn_plan = compile_turn_plan(
            turn.content,
            turn.semantic_task,
            active_objective=turn.active_objective,
            routing_environment=turn.routing_environment,
            force_agent=turn.explicit_agent,
        )
        turn.semantic_task = turn.turn_plan.semantic_task
        turn.semantic_compilation = turn.turn_plan.compilation
        turn.metadata["turn_plan"] = turn.turn_plan.model_dump(mode="json")


def _classify_turn(
    turn: _ChatTurn,
    parser: Any,
    environment: dict[str, Any],
    routing_deadline_at: float | None,
) -> None:
    turn.semantic_parser_for_retry = parser
    turn.semantic_task = classify_semantic_task_safely(
        parser,
        turn.content,
        reference_context=turn.previous_routing_context,
        previous_objective=turn.active_objective_text,
        current_environment=environment,
        deadline_at=routing_deadline_at,
    )
    raw_diagnostics = getattr(parser, "last_diagnostics", None)
    if isinstance(raw_diagnostics, dict):
        turn.semantic_parser_diagnostics = dict(raw_diagnostics)


def _decide_turn_route(turn: _ChatTurn) -> Any:
    """Choose the lane and request mode, failing closed when meaning is missing."""
    turn.semantic_route = (
        _semantic_route_from_compilation(
            turn.fast_path,
            turn.semantic_task,
            turn.semantic_compilation,
        )
        if turn.semantic_task is not None and turn.semantic_compilation is not None
        else None
    )
    turn.decision = turn.semantic_route or turn.fast_path
    turn.routing = _routing_decision_payload(
        turn.decision,
        turn.semantic_route,
        parser_diagnostics=turn.semantic_parser_diagnostics,
    )
    concrete_workspace_action = bool(
        turn.decision.lane == "agent"
        and turn.semantic_compilation is not None
        and any(
            action in {"workspace_read", "workspace_mutate", "workspace_execute"}
            for action in turn.semantic_compilation.action_intents
        )
    )
    turn.mode = resolve_request_mode(
        turn.content,
        turn_research_mode=None if concrete_workspace_action else turn.research_mode,
        persistent_agent=turn.explicit_agent,
        classifier_lane=turn.decision.lane,
    )
    if (
        turn.semantic_compilation is not None
        and turn.semantic_compilation.requires_clarification
    ):
        return turn.clarification()
    if turn.semantic_task is None and turn.contextual_resolution_required:
        return turn.clarification(task_known=False, parser_unavailable=True)

    # Parser outage removes all natural-language execution authority, but it
    # must not take down response-only Chat. A deterministic deny-only detector
    # blocks requests that may need stateful/private authority; harmless Chat
    # continues to the configured conversational provider without granting any
    # capabilities.
    if (
        turn.semantic_task is None
        and turn.fast_path.reason == "semantic_required"
        and not turn.explicit_agent
    ):
        if semantic_authority_risk(
            turn.content,
            workspace_attached=bool(turn.metadata.get("workspace_root")),
        ):
            return turn.clarification(task_known=False, parser_unavailable=True)
        turn.decision = OmnixRouteDecision(
            lane="chat",
            confidence=0.0,
            reason="semantic_parser_unavailable_safe_chat",
        )
        turn.routing = _routing_decision_payload(
            turn.decision,
            None,
            parser_diagnostics=turn.semantic_parser_diagnostics,
        )
        turn.parser_unavailable_safe_chat = True

    if turn.mode.mode in {"quick_research", "deep_research"}:
        turn.mark_route()
        return None

    # AUTO natural-language routing fails closed without SemanticTask v2.
    # Explicit /agent syntax and the user's persistent Agent control remain
    # deterministic command paths when the parser is unavailable.
    if (
        turn.mode.mode == "agent"
        and turn.semantic_task is None
        and turn.mode.source not in {"explicit_command", "persistent_setting"}
    ):
        return turn.clarification(task_known=False, parser_unavailable=True)

    if turn.mode.mode == "agent" and turn.decision.lane != "agent":
        turn.decision = OmnixRouteDecision(
            lane="agent",
            confidence=(
                1.0
                if turn.mode.source in {"explicit_command", "persistent_setting"}
                else turn.decision.confidence
            ),
            reason=f"request_mode:{turn.mode.source}+semantic_v2",
            explicit=turn.mode.source == "explicit_command",
            hermes_recommended=(
                turn.semantic_compilation.multi_step if turn.semantic_compilation else False
            ),
        )
        turn.routing = _routing_decision_payload(
            turn.decision,
            turn.semantic_route,
            parser_diagnostics=turn.semantic_parser_diagnostics,
        )
    return _PROCEED


def _cancel_superseded_task_graph(turn: _ChatTurn) -> Any:
    """A response-only correction withdraws the active task graph before Chat answers.

    Cancellation is an execution-control effect even though the replacement
    response itself belongs to Chat, so withdrawn graph authority cannot
    survive a response-only correction.
    """
    turn_plan = turn.turn_plan
    if turn_plan is None or turn_plan.run_action != "cancel_task_graph_then_chat":
        return _PROCEED
    turn.mark_route()
    active_run_id = str(turn_plan.active_run_id or "").strip()
    if not active_run_id:
        return _agent_request_rejection(
            turn.routed,
            profile="task-graph",
            task=turn.submitted_content,
            reason="active_task_graph_unavailable",
            message=(
                "I couldn't safely cancel the superseded task graph because "
                "its active run id is unavailable."
            ),
        )
    try:
        runtime = default_task_graph_runtime()
        current_graph = runtime.get_status(active_run_id)
        if current_graph is not None and current_graph.status not in {
            "completed",
            "failed",
            "cancelled",
        }:
            runtime.cancel(
                active_run_id,
                reason="superseded_by_response_only_revision",
            )
    except Exception as exc:
        return _agent_start_failure(
            turn.routed,
            run_id=active_run_id,
            profile="task-graph",
            task=turn.submitted_content,
            error=RuntimeError(
                "failed to cancel superseded TaskGraph authority: "
                f"{type(exc).__name__}: {exc}"
            ),
        )
    if turn.active_objective is not None:
        turn.metadata["active_objective"] = _advanced_objective(
            turn,
            profile="task-graph",
            run_id=active_run_id,
            status="cancelled",
        )
    return None


def _chat_lane_turn(
    turn: _ChatTurn,
    context_items: list[dict[str, Any]] | None,
) -> GeneralizedChatResult | None:
    turn.mark_route()
    if turn.parser_unavailable_safe_chat:
        turn.metadata["semantic_gate"] = {
            "accepted": True,
            "reason": "semantic_parser_unavailable_safe_chat",
            "authority_granted": False,
        }
    if turn.semantic_compilation is None:
        return None
    return _enforce_chat_evidence(
        turn.session,
        turn.user_message,
        turn.routed,
        request_mode=turn.routed_mode,
        semantic_task=turn.semantic_task,
        semantic_compilation=turn.semantic_compilation,
        routing_shadow=turn.routed_routing,
        context_items=context_items,
    )


_TASK_GRAPH_RUN_ACTIONS = {
    "start_task_graph",
    "steer_task_graph",
    "replace_task_graph_with_task_graph",
    "replace_agent_with_task_graph",
}


def _execute_turn(
    turn: _ChatTurn,
    *,
    provider_id: str | None,
    model_id: str | None,
    routing_deadline_at: float | None,
    routing_context_factory: Callable[[], Any] | None,
) -> GeneralizedChatResult | None:
    """Start or steer the Agent run or task graph the turn compiled to."""
    if (
        turn.semantic_task is None or turn.semantic_compilation is None
    ) and not (
        turn.routed_mode.mode == "agent"
        and turn.routed_mode.source in {"explicit_command", "persistent_setting"}
    ):
        return turn.clarification(parser_unavailable=True)

    if not turn.previous_routing_context:
        turn.previous_routing_context = _resolve_routing_context(
            turn.session,
            turn.user_message,
            routing_context_factory,
        )
    pending_retry = turn.pending_retry
    retry_override = (
        pending_retry.task
        if pending_retry is not None
        and turn.semantic_compilation is not None
        and (
            not pending_retry.profile
            or turn.semantic_compilation.profile_id == pending_retry.profile
        )
        else None
    )
    # Persist the production decision before crossing into execution. Any
    # provider boundary that sees this turn can now fail closed on Agent rather
    # than accidentally generating an ordinary Chat response.
    turn.mark_route()
    task_graph_semantic_task = _complete_graph_objective(turn, routing_deadline_at)
    if isinstance(task_graph_semantic_task, GeneralizedChatResult):
        return task_graph_semantic_task

    turn_plan = turn.turn_plan
    reference_context = _agent_semantic_reference_context(
        turn.previous_routing_context,
        turn.semantic_task,
        turn.semantic_compilation,
        latest_user_message=turn.submitted_content,
        attached_workspace=bool(turn.metadata.get("workspace_root")),
    )
    result: GeneralizedChatResult | None
    if (
        turn_plan is not None
        and turn_plan.run_action in _TASK_GRAPH_RUN_ACTIONS
        and turn.semantic_task is not None
    ):
        result = _task_graph_result(
            turn.session,
            turn.user_message,
            turn.routed,
            provider_id=provider_id,
            model_id=model_id,
            request_mode=turn.routed_mode,
            semantic_task=task_graph_semantic_task or turn.semantic_task,
            semantic_compilation=turn.semantic_compilation,
            routing_shadow=turn.routing,
            turn_plan=turn_plan,
            active_objective=turn.active_objective,
            semantic_reference_context=reference_context,
        )
    else:
        result = _agent_result(
            turn.session,
            turn.user_message,
            turn.routed,
            provider_id=provider_id,
            model_id=model_id,
            request_mode=turn.routed_mode,
            semantic_intent=turn.semantic_intent,
            semantic_task=turn.semantic_task,
            semantic_compilation=turn.semantic_compilation,
            semantic_context=reference_context,
            routing_shadow=turn.routing,
            turn_plan=turn_plan,
            content_override=(
                retry_override
                or (turn_plan.effective_request if turn_plan is not None else None)
            ),
            reference_images_override=(
                list(pending_retry.reference_images) if pending_retry is not None else None
            ),
            retry_source=pending_retry,
        )
    if result is not None:
        _record_turn_outcome(turn, result)
    return result


def _complete_graph_objective(turn: _ChatTurn, routing_deadline_at: float | None) -> Any:
    """The SemanticTask a task graph is compiled from: the whole objective when steering.

    Executor promotion and additive graph steering both need the complete
    user-authored objective. For an active TaskGraph, the latest semantic parse
    is a routing delta only; reference context may cause a model to restate
    already-active operations in message chronology, which is not a safe graph
    dependency order. Reparse the durable effective objective and let graph
    revision diff the complete authority/dependency contract.
    """
    turn_plan = turn.turn_plan
    rebuild = bool(
        turn_plan is not None
        and (
            turn_plan.run_action == "replace_agent_with_task_graph"
            or (
                turn_plan.run_action == "steer_task_graph"
                and turn_plan.relation == "continue"
                and turn_plan.disposition != "replay_objective"
            )
        )
        and turn.semantic_task is not None
        and turn.active_objective is not None
    )
    if not rebuild or turn_plan is None or turn.active_objective is None:
        return turn.semantic_task
    effective_graph_request = derive_effective_objective(
        turn.active_objective.effective_objective_text(),
        turn_plan,
    )
    if turn.semantic_parser_for_retry is None:
        return turn.clarification(parser_unavailable=True)
    combined_task = classify_semantic_task_safely(
        turn.semantic_parser_for_retry,
        effective_graph_request,
        reference_context=turn.previous_routing_context,
        previous_objective="",
        current_environment=turn.routing_environment.model_dump(mode="json"),
        deadline_at=routing_deadline_at,
    )
    if combined_task is None:
        return turn.clarification(parser_unavailable=True)
    task_graph_semantic_task = normalize_semantic_task(combined_task)
    turn.metadata["task_graph_semantic_task"] = task_graph_semantic_task.model_dump(mode="json")
    return task_graph_semantic_task


def _advanced_objective(turn: _ChatTurn, *, profile: str, run_id: str, status: str) -> dict[str, Any]:
    turn_plan = turn.turn_plan
    if turn_plan is None:
        raise RuntimeError("an objective advances only on a planned turn")
    return advance_active_objective(
        turn.active_objective,
        request=turn_plan.latest_request,
        profile=profile,
        relation=turn_plan.relation,
        disposition=turn_plan.disposition,
        turn_id=str(getattr(turn.user_message, "id", "") or "") or None,
        run_id=run_id,
        status=cast(Any, status),
        workspace_name=(
            turn.routing_environment.active_workspace
            if turn.routing_environment is not None
            else None
        ),
    ).model_dump(mode="json")


def _record_turn_outcome(turn: _ChatTurn, result: GeneralizedChatResult) -> None:
    """Attach routing evidence to the result and advance the active objective."""
    result.metadata.setdefault("routing_decision", turn.routing)
    if turn.semantic_task is not None:
        result.metadata.setdefault(
            "semantic_task",
            turn.semantic_task.model_dump(mode="json"),
        )
    if turn.semantic_compilation is not None:
        result.metadata.setdefault(
            "semantic_compilation",
            turn.semantic_compilation.model_dump(mode="json"),
        )
    result.metadata.setdefault("request_mode", turn.routed_mode.model_dump(mode="json"))
    turn_plan = turn.turn_plan
    if turn_plan is None:
        return
    result.metadata.setdefault("turn_plan", turn_plan.model_dump(mode="json"))
    agent_run = result.metadata.get("agent_run") or {}
    graph_run = result.metadata.get("task_graph_run") or {}
    graph_mode = bool(result.metadata.get("task_graph_mode"))
    run_id = str(
        graph_run.get("run_id")
        or agent_run.get("run_id")
        or turn_plan.active_run_id
        or ""
    ).strip() or None
    objective_profile = "task-graph" if graph_mode else turn_plan.profile_id
    raw_status = str(
        graph_run.get("status")
        or agent_run.get("status")
        or "active"
    ).casefold()
    objective_status = (
        "completed"
        if raw_status == "completed"
        else "cancelled"
        if raw_status in {"cancelled", "canceled"}
        else "blocked"
        if raw_status == "failed"
        else "awaiting_user"
        if raw_status == "waiting_for_approval"
        else "active"
    )
    if run_id and objective_profile:
        result.metadata["active_objective"] = _advanced_objective(
            turn,
            profile=objective_profile,
            run_id=run_id,
            status=objective_status,
        )


def _select_profile(content: str) -> str:
    """Compatibility wrapper around the shared deterministic profile classifier."""
    return select_agent_profile_id(content)


def _message_research_mode(metadata: dict[str, Any]) -> str | None:
    direct = metadata.get("research_mode") or metadata.get("web_research_mode")
    if direct is not None:
        return str(direct)
    diagnostics = metadata.get("context_diagnostics")
    if isinstance(diagnostics, dict):
        value = diagnostics.get("research_effective_mode") or diagnostics.get("web_research_mode")
        if value is not None:
            return str(value)
    return None


def _is_live_voice(user_message: Any) -> bool:
    metadata = getattr(user_message, "metadata", {}) or {}
    return str(metadata.get("user_turn_id") or "").startswith("voice-user-turn:") or str(
        metadata.get("speech_segment_id") or ""
    ).startswith("voice-segment:")
