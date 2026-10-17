"""Deterministic first-pass routing across Chat, Direct, Workflow, and Agent lanes."""
from __future__ import annotations

from collections.abc import Callable
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OmnixLane = Literal["chat", "direct", "workflow", "agent"]


class OmnixRouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lane: OmnixLane
    confidence: float = Field(ge=0, le=1)
    reason: str
    explicit: bool = False
    hermes_recommended: bool = False
    workflow_id: str | None = None
    capability_id: str | None = None


_AGENT = re.compile(r"^(?:/agent\b|/agnet\b|agent[,:]\s|use (?:the )?agent\b)", re.I)
_WORKFLOW = re.compile(r"^(?:run|start|execute)\s+(?:my\s+)?(.+?)(?:\s+routine|\s+workflow)?[.!]?$", re.I)
_CASUAL = re.compile(r"^(?:hi|hello|hey|thanks|thank you|how are you|what'?s up)[.!?\s]*$", re.I)
_INFORMATIONAL = re.compile(r"^(?:what|why|when|where|who|how|explain|describe|summarize|compare|teach me)\b", re.I)
_META_INFORMATIONAL = re.compile(
    r"^(?:what\s+(?:does|would|happens?)|why\s+would|how\s+(?:do|does|would)|"
    r"explain|describe|teach me)\b",
    re.I,
)
_HYPOTHETICAL = re.compile(
    r"^(?:if\s+i\s+asked|suppose\b|imagine\b|what\s+would\s+happen\s+if|"
    r"how\s+would\s+(?:an?|the)\s+agent|could\s+(?:an?|the)\s+agent)\b",
    re.I,
)
_NEGATED_ACTION = re.compile(
    r"(?:\b(?:don'?t|do\s+not|never|no\s+need\s+to)\b|"
    r"\bi\s+(?:don'?t|do\s+not)\s+want\s+(?:you|the\s+agent)\s+to\b)",
    re.I,
)
_QUOTED_SPAN = re.compile(
    r'"[^"\r\n]*"'
    r"|“[^”\r\n]*”"
    r"|‘[^’\r\n]*’"
    r"|`[^`\r\n]*`"
    r"|(?<!\w)'[^'\r\n]+'(?!\w)"
)

_BROAD_SEMANTIC = re.compile(r"\b(?:take care of|anything important|whatever needs|prepare everything|handle everything)\b", re.I)
_AGENTIC = re.compile(r"\b(?:fix|debugg?|investigate|figure out|diagnose|research|reseach|analy[sz]e|anlyze|refactor|implement)\b", re.I)
_WORKSPACE_RETRY = re.compile(
    r"\b(?:try\s+again|retry|do\s+it\s+again)\b.{0,100}"
    r"\b(?:in|with|using)\s+(?:the\s+)?(?:code|coding|repo(?:sitory)?|workspace|project(?:\s+folder)?)\b",
    re.I,
)
_RESEARCH_ACTION = re.compile(r"\b(?:research|reseach|investigate|analy[sz]e|anlyze)\b", re.I)
_CODE_STRONG_TARGET = (
    r"(?:\b(?:code|codebase|repo(?:sitory)?|workspace|tests?|pytest|vitest|"
    r"selector|classname|css|html|stylesheet|stylesheets|theme|themes|theming|tsx?|jsx?|frontend|backend|"
    r"middleware|callback|handler|hook|endpoint|router|api|module|function|source)\b|"
    r"\b(?:light|dark)\s+mode\b|"
    r"\b(?:color|colour)\s+scheme\b|"
    r"\b(?:aurora|liquid\s+glass)\b.{0,80}\b(?:mode|theme|style|styles|styling)\b|"
    r"(?<!\w)[.#]?(?:[A-Za-z][A-Za-z0-9_]*-){2,}[A-Za-z][A-Za-z0-9_]*|"
    r"(?:^|[\\/])(?:src|app|tests?|packages?|components?)[\\/][^\s]+|"
    r"\b[A-Za-z0-9_.-]+\.(?:py|pyi|js|jsx|ts|tsx|go|rs|java|rb|php|cs|cpp|c|h)\b)"
)
_UI_CODE_TARGET = (
    r"(?:(?:\b(?:app|ui|ux|frontend|web|page|screen|interface|react|vue|css|html|omnix)\b"
    r".{0,100}\b(?:button|icon|element|component|layout|menu|modal|dropdown|tab|sidebar|"
    r"header|footer|form|input|textarea|dialog|tooltip|badge|chip)\b)|"
    r"(?:\b(?:button|icon|element|component|layout|menu|modal|dropdown|tab|sidebar|"
    r"header|footer|form|input|textarea|dialog|tooltip|badge|chip)\b"
    r".{0,100}\b(?:app|ui|ux|frontend|web|page|screen|interface|react|vue|css|html|omnix)\b))"
)
_CODE_TARGET = f"(?:{_CODE_STRONG_TARGET}|{_UI_CODE_TARGET})"
_WORKSPACE_ANCHOR = re.compile(
    r"(?:\b(?:repo(?:sitory)?|codebase|workspace|project|existing|current|omnix|"
    r"frontend|backend|app)\b|"
    r"(?<!\w)[.#]?(?:[A-Za-z][A-Za-z0-9_]*-){2,}[A-Za-z][A-Za-z0-9_]*|"
    r"(?:^|[\\/])(?:src|app|tests?|packages?|components?)[\\/][^\s]+|"
    r"\b[A-Za-z0-9_.-]+\.(?:py|pyi|js|jsx|ts|tsx|go|rs|java|rb|php|cs|cpp|c|h)\b)",
    re.I,
)
_STANDALONE_CODE_GENERATION = re.compile(
    r"(?:^(?:write|create|generate|show\s+me|give\s+me)\b.{0,180}"
    r"\b(?:code|function|class|script|snippet|example|sample|regex|query|css|html|"
    r"component)\b|"
    r"\b(?:example|sample|snippet|code\s+below|following\s+code|pasted\s+code)\b)",
    re.I,
)
_WORKSPACE_MUTATION = re.compile(
    r"(?:"
    r"\b(?:fix|edit|modify|patch|change|update|create|add|remove|delete|implement|apply|"
    r"refactor|write|make|center|centre|align|move|style|restyle|increase|decrease|"
    r"reduce|expand|widen|narrow|adjust)\b.{0,160}"
    + _CODE_TARGET
    + r"|"
    + _CODE_TARGET
    + r".{0,160}\b(?:should|needs?|must)\b.{0,80}"
    r"\b(?:center(?:ed|ing)?|centre(?:d|ing)?|align(?:ed|ing)?|move|style|"
    r"restyle|change|update|fix)\b|"
    + _CODE_TARGET
    + r".{0,160}\b(?:fix|edit|modify|patch|change|update|implement|apply|"
    r"refactor|make|style|restyle|improve)\b"
    r")",
    re.I,
)
_WORKSPACE_READ = re.compile(
    r"\b(?:inspect|review|read|check|find|locate|search|trace|examine|diagnose)\b"
    r".{0,160}" + _CODE_TARGET,
    re.I,
)
_HOME_SEMANTIC_TASK = re.compile(
    r"(?:\b(?:turn|set|adjust|lower|raise|check|inspect|prepare)\b.{0,100}"
    r"\b(?:thermostat|home|lights?|lamps?|plugs?|outlets?|kasa)\b|"
    r"\b(?:thermostat|home|lights?|lamps?|plugs?|outlets?|kasa)\b.{0,100}"
    r"\b(?:down|up|to|check|inspect|prepare)\b)",
    re.I,
)
_PERSONAL_TASK = re.compile(
    r"\b(?:chec?k|inspect|read|summarize|find|draft|send|schedule|create|look\s+up|resolve)\b"
    r".{0,100}\b(?:gmail|emails?|calendar|meetings?|contacts?|appointments?|schedule)\b",
    re.I,
)
_CONJUNCTION = re.compile(r"\b(?:and|then|also)\b", re.I)
_CONVERSATIONAL_SECOND_INTENT = re.compile(r"\b(?:tell me a joke|explain it|summarize it|answer me)\b", re.I)
_REPO_READ_TASK = re.compile(
    r"(?:\b(?:check|inspect|read|summarize|review|diagnose)\b.{0,100}"
    r"\b(?:repo(?:sitory)?|ci|github actions?|workflows?|checks?|branch|diff)\b|"
    r"\bwhat\s+changed\b.{0,100}\b(?:repo(?:sitory)?|branch|diff)\b)",
    re.I,
)
_TRADING_SUBJECT = re.compile(
    r"\b(?:stocks?|trading|trades?|tickers?|markets?|shares?|equities|nvda|gme|tsla|"
    r"gainers?|losers?|orders?|positions?)\b",
    re.I,
)
_TRADING_MUTATION = re.compile(
    r"\b(?:buy|sell|purchase|short|cover)\b|"
    r"\b(?:place|submit|cancel)\b.{0,60}\b(?:order|trade|position)\b",
    re.I,
)

_DIRECT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bturn\s+on\b.*\b(?:plug|outlet|kasa|lamp|light)\b", re.I), "home.set_state"),
    (re.compile(r"\bturn\s+of{1,2}\b.*\b(?:plug|outlet|kasa|lamp|light)\b", re.I), "home.set_state"),
    (re.compile(r"\b(?:status|state)\b.*\b(?:plug|outlet|kasa|lamp|light)\b", re.I), "home.get_state"),
)


def route_omnix_fast_path(
    content: str,
    *,
    workflow_lookup: Callable[[str], str | None] | None = None,
) -> OmnixRouteDecision:
    """Recognize syntax-level routes only; leave natural-language meaning to the LLM.

    This intentionally does not infer coding/home/research/personal domains.
    Unmatched meaningful language is marked semantic_required and must be parsed
    before Omnix grants stateful Agent authority.
    """

    text = " ".join(str(content or "").strip().split())
    if not text or _CASUAL.fullmatch(text):
        return OmnixRouteDecision(
            lane="chat",
            confidence=1.0,
            reason="casual_or_empty",
        )
    if _AGENT.search(text):
        return OmnixRouteDecision(
            lane="agent",
            confidence=1.0,
            reason="explicit_agent",
            explicit=True,
        )

    actionable_text = _QUOTED_SPAN.sub(" ", text)
    actionable_text = " ".join(actionable_text.split())

    workflow_match = _WORKFLOW.fullmatch(actionable_text)
    if workflow_match and workflow_lookup is not None:
        candidate = workflow_match.group(1).strip()
        workflow_id = workflow_lookup(candidate)
        if workflow_id:
            return OmnixRouteDecision(
                lane="workflow",
                confidence=1.0,
                reason="exact_known_workflow",
                workflow_id=workflow_id,
            )

    for pattern, capability_id in _DIRECT_PATTERNS:
        if pattern.fullmatch(actionable_text):
            return OmnixRouteDecision(
                lane="direct",
                confidence=1.0,
                reason="exact_direct_capability",
                capability_id=capability_id,
            )

    return OmnixRouteDecision(
        lane="chat",
        confidence=0.0,
        reason="semantic_required",
        hermes_recommended=False,
    )


_UI_MUTATION_AUTHORITY_RISK = re.compile(
    r"(?:\b(?:change|rename|update|edit|replace|set|remove|add)\b.{0,140}"
    r"\b(?:title|label|text|name|button|menu|tab|setting|settings|control)\b.{0,140}"
    r"\b(?:app|ui|web|chat|omnix|workspace|project|frontend)\b|"
    r"\b(?:app|ui|web|chat|omnix|workspace|project|frontend)\b.{0,140}"
    r"\b(?:change|rename|update|edit|replace|set|remove|add)\b.{0,140}"
    r"\b(?:title|label|text|name|button|menu|tab|setting|settings|control)\b)",
    re.I,
)
_FILE_MUTATION_AUTHORITY_RISK = re.compile(
    r"\b(?:delete|remove|move|rename|edit|modify|write|overwrite|create)\b"
    r".{0,100}\b(?:file|folder|directory|path)\b",
    re.I,
)
_ATTACHED_WORKSPACE_ACTION_RISK = re.compile(
    r"\b(?:fix|debugg?|diagnose|refactor|implement|patch|edit|modify)\b",
    re.I,
)


def semantic_authority_risk(
    content: str,
    *,
    workspace_attached: bool = False,
) -> bool:
    """Return whether parser outage must fail closed before ordinary Chat.

    This is a one-way safety detector only. It can block a turn that may require
    stateful/private execution authority, but it can never select a lane, profile,
    capability, evidence policy, or tool. SemanticTask v2 remains the sole
    natural-language authority compiler.
    """

    text = " ".join(str(content or "").strip().split())
    if not text or _CASUAL.fullmatch(text):
        return False
    actionable_text = _QUOTED_SPAN.sub(" ", text)
    actionable_text = " ".join(actionable_text.split())

    # Questions, hypotheticals, and explicitly negated actions are safe to leave
    # with the response-only Chat provider when semantic parsing is unavailable.
    if (
        _HYPOTHETICAL.match(text)
        or _NEGATED_ACTION.search(actionable_text)
        or _META_INFORMATIONAL.match(text)
        or _INFORMATIONAL.match(text)
    ):
        return False
    if (
        _STANDALONE_CODE_GENERATION.search(actionable_text)
        and not _WORKSPACE_ANCHOR.search(actionable_text)
    ):
        return False

    if _WORKFLOW.fullmatch(actionable_text):
        return True
    if any(pattern.search(actionable_text) for pattern, _ in _DIRECT_PATTERNS):
        return True
    if any(
        pattern.search(actionable_text)
        for pattern in (
            _WORKSPACE_RETRY,
            _BROAD_SEMANTIC,
            _WORKSPACE_MUTATION,
            _WORKSPACE_READ,
            _HOME_SEMANTIC_TASK,
            _PERSONAL_TASK,
            _UI_MUTATION_AUTHORITY_RISK,
            _FILE_MUTATION_AUTHORITY_RISK,
            _TRADING_MUTATION,
        )
    ):
        return True
    if workspace_attached and (
        _ATTACHED_WORKSPACE_ACTION_RISK.search(actionable_text)
        or (
            _WORKSPACE_ANCHOR.search(actionable_text)
            and _AGENTIC.search(actionable_text)
        )
    ):
        return True
    return False


def route_omnix_request(
    content: str,
    *,
    workflow_lookup: Callable[[str], str | None] | None = None,
    research_mode: str | None = None,
) -> OmnixRouteDecision:
    text = " ".join(str(content or "").strip().split())
    if not text or _CASUAL.match(text):
        return OmnixRouteDecision(lane="chat", confidence=0.99, reason="casual_or_empty")
    if _AGENT.search(text):
        return OmnixRouteDecision(lane="agent", confidence=1.0, reason="explicit_agent", explicit=True)
    actionable_text = _QUOTED_SPAN.sub(" ", text)
    actionable_text = " ".join(actionable_text.split())
    if _HYPOTHETICAL.match(text):
        return OmnixRouteDecision(lane="chat", confidence=0.98, reason="hypothetical_or_conditional")
    if _NEGATED_ACTION.search(actionable_text):
        return OmnixRouteDecision(lane="chat", confidence=0.99, reason="negated_action")
    if _WORKSPACE_RETRY.search(actionable_text):
        return OmnixRouteDecision(
            lane="agent",
            confidence=0.98,
            reason="workspace_retry_request",
        )
    if _META_INFORMATIONAL.match(text):
        return OmnixRouteDecision(lane="chat", confidence=0.97, reason="informational")
    if _is_mixed_intent(actionable_text):
        return OmnixRouteDecision(
            lane="agent",
            confidence=0.82,
            reason="mixed_intent_task",
            hermes_recommended=True,
        )
    if _BROAD_SEMANTIC.search(text):
        return OmnixRouteDecision(
            lane="agent",
            confidence=0.72,
            reason="broad_semantic_task",
            hermes_recommended=True,
        )
    if _INFORMATIONAL.match(text):
        return OmnixRouteDecision(lane="chat", confidence=0.95, reason="informational")
    if (
        _STANDALONE_CODE_GENERATION.search(actionable_text)
        and not _WORKSPACE_ANCHOR.search(actionable_text)
    ):
        return OmnixRouteDecision(
            lane="chat",
            confidence=0.96,
            reason="standalone_code_generation",
        )
    if _WORKSPACE_MUTATION.search(actionable_text):
        return OmnixRouteDecision(
            lane="agent",
            confidence=0.98,
            reason="workspace_mutation_request",
        )
    if _WORKSPACE_READ.search(actionable_text):
        return OmnixRouteDecision(
            lane="agent",
            confidence=0.96,
            reason="workspace_read_request",
        )

    workflow_match = _WORKFLOW.match(actionable_text)
    if workflow_match and workflow_lookup is not None:
        candidate = workflow_match.group(1).strip()
        workflow_id = workflow_lookup(candidate)
        if workflow_id:
            return OmnixRouteDecision(
                lane="workflow",
                confidence=0.98,
                reason="known_workflow",
                workflow_id=workflow_id,
            )

    for pattern, capability_id in _DIRECT_PATTERNS:
        if pattern.search(actionable_text):
            return OmnixRouteDecision(
                lane="direct",
                confidence=0.96,
                reason="known_single_capability",
                capability_id=capability_id,
            )

    if _INFORMATIONAL.match(text):
        return OmnixRouteDecision(lane="chat", confidence=0.95, reason="informational")
    normalized_research_mode = str(research_mode or "disabled").strip().casefold()
    if normalized_research_mode in {"quick", "deep"} and _RESEARCH_ACTION.search(actionable_text):
        return OmnixRouteDecision(
            lane="chat",
            confidence=0.99,
            reason=f"{normalized_research_mode}_research_chat",
        )
    if _HOME_SEMANTIC_TASK.search(actionable_text):
        return OmnixRouteDecision(lane="agent", confidence=0.84, reason="home_semantic_task")
    if _PERSONAL_TASK.search(actionable_text):
        return OmnixRouteDecision(lane="agent", confidence=0.84, reason="personal_assistant_task")
    if _AGENTIC.search(actionable_text):
        return OmnixRouteDecision(lane="agent", confidence=0.9, reason="open_ended_execution")
    return OmnixRouteDecision(
        lane="chat",
        confidence=0.7,
        reason="conversation_default",
        hermes_recommended=False,
    )


def _is_mixed_intent(text: str) -> bool:
    if _CONJUNCTION.search(text) is None:
        return False
    signals = 0
    if any(pattern.search(text) for pattern, _ in _DIRECT_PATTERNS):
        signals += 1
    if re.search(r"\b(?:run|start|execute)\b.{0,80}\b(?:routine|workflow)\b", text, re.I):
        signals += 1
    if _PERSONAL_TASK.search(text):
        signals += 1
    if _HOME_SEMANTIC_TASK.search(text):
        signals += 1
    if _REPO_READ_TASK.search(text):
        signals += 1
    if _AGENTIC.search(text):
        signals += 1
    if _TRADING_SUBJECT.search(text) and _RESEARCH_ACTION.search(text):
        signals += 1
    if _TRADING_MUTATION.search(text):
        signals += 1
    if _CONVERSATIONAL_SECOND_INTENT.search(text):
        signals += 1
    return signals >= 2
