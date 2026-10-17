from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from .recovery import LocalRecoveryAnalysis


_FORBIDDEN_RESULT_KEYS = {
    "state_delta", "authoritative_delta", "inventory_delta", "currency_delta",
    "health_delta", "quest_delta", "relationship_delta", "move_player",
    "execute", "tool_execution", "approved", "quest_completed", "grant_xp",
}


class HermesRecoveryClient(Protocol):
    def plan(self, payload: Mapping[str, Any], *, timeout_seconds: float) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class HermesEvidence:
    evidence_id: str
    source: str
    content: Any
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source": self.source,
            "content": self.content,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class HermesProposal:
    proposal_id: str
    proposal_type: str
    summary: str
    risk: str = "low"
    lifetime: str = "turn"
    provenance_refs: tuple[str, ...] = ()
    content: Any = None
    visibility: str = "player_visible"
    confidence: float = 0.5
    conflicts: tuple[str, ...] = ()

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return self.provenance_refs

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "proposal_type": self.proposal_type,
            "summary": self.summary,
            "risk": self.risk,
            "lifetime": self.lifetime,
            "provenance_refs": list(self.provenance_refs),
            "content": self.content,
            "visibility": self.visibility,
            "confidence": self.confidence,
            "conflicts": list(self.conflicts),
        }


@dataclass(frozen=True)
class HermesRecoveryResult:
    status: str
    query: str
    evidence: tuple[HermesEvidence, ...] = ()
    inferences: tuple[str, ...] = ()
    uncertainty: tuple[str, ...] = ()
    forward_strategies: tuple[str, ...] = ()
    proposals: tuple[HermesProposal, ...] = ()
    error: str = ""
    cache_hit: bool = False
    proposal_only: bool = True
    executes: bool = False
    state_mutation_allowed: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "success" and not self.executes and not self.state_mutation_allowed

    @property
    def reason(self) -> str:
        return self.error or self.status


@dataclass
class HermesCircuitBreaker:
    failure_threshold: int = 3
    failures: int = 0
    open: bool = False

    def success(self) -> None:
        self.failures = 0
        self.open = False

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.open = True


class RpgHermesRecoveryAdapter:
    def __init__(
        self,
        client: HermesRecoveryClient,
        *,
        timeout_seconds: float = 4.0,
        max_evidence: int = 8,
        circuit_breaker: HermesCircuitBreaker | None = None,
    ) -> None:
        self.client = client
        self.timeout_seconds = max(0.1, min(float(timeout_seconds), 10.0))
        self.max_evidence = max(1, max_evidence)
        self.circuit_breaker = circuit_breaker or HermesCircuitBreaker()
        self._cache: dict[str, HermesRecoveryResult] = {}

    def research(
        self,
        query: str,
        analysis: LocalRecoveryAnalysis,
        *,
        campaign_version: str = "",
        lore_version: str = "",
        cancelled: Callable[[], bool] | None = None,
    ) -> HermesRecoveryResult:
        if not analysis.needs_hermes:
            return HermesRecoveryResult("not_needed", query)
        return self._request(
            query,
            self._request_payload(
                query,
                analysis,
                campaign_version=campaign_version,
                lore_version=lore_version,
            ),
            campaign_version=campaign_version,
            lore_version=lore_version,
            cancelled=cancelled,
        )

    def recover(
        self,
        *,
        campaign_id: str,
        lore_version: str,
        query: str,
        unresolved_question: str,
        evidence: Sequence[Mapping[str, Any]],
        local_strategy: str,
        cancelled: Callable[[], bool] | None = None,
    ) -> HermesRecoveryResult:
        """Proposal-only compatibility entry used by the production pipeline."""

        visible_evidence = [
            {
                "evidence_id": str(row.get("evidence_id") or ""),
                "source": str(row.get("source") or "local"),
                "content": row.get("content"),
                "confidence": float(row.get("confidence") or 0.0),
            }
            for row in evidence[: self.max_evidence]
            if isinstance(row, Mapping)
            and str(row.get("visibility") or "player_visible") != "hidden"
        ]
        payload = {
            "schema_version": "rpg_hermes_recovery_request_v1",
            "query": query,
            "unresolved_question": unresolved_question,
            "local_strategy": local_strategy,
            "visible_evidence": visible_evidence,
            "campaign_version": campaign_id,
            "lore_version": lore_version,
            "constraints": {
                "proposal_only": True,
                "review_required": True,
                "executes": False,
                "state_mutation_allowed": False,
                "hidden_information_forbidden": True,
                "player_choice_must_not_be_taken": True,
            },
            "requested_output": {
                "evidence": True,
                "inferences": True,
                "uncertainty": True,
                "forward_strategies": True,
                "proposals": True,
            },
        }
        return self._request(
            query,
            payload,
            campaign_version=campaign_id,
            lore_version=lore_version,
            cancelled=cancelled,
        )

    def _request(
        self,
        query: str,
        payload: Mapping[str, Any],
        *,
        campaign_version: str,
        lore_version: str,
        cancelled: Callable[[], bool] | None,
    ) -> HermesRecoveryResult:
        if cancelled is not None and cancelled():
            return HermesRecoveryResult("cancelled", query, error="cancelled_before_request")
        cache_key = _cache_key(query, campaign_version, lore_version)
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            return HermesRecoveryResult(**{**cached.__dict__, "cache_hit": True})
        if self.circuit_breaker.open:
            return HermesRecoveryResult("unavailable", query, error="circuit_open")
        try:
            raw = self.client.plan(payload, timeout_seconds=self.timeout_seconds)
            if cancelled is not None and cancelled():
                return HermesRecoveryResult("cancelled", query, error="cancelled_after_request")
            result = self._parse_result(query, raw)
        except TimeoutError:
            self.circuit_breaker.failure()
            return HermesRecoveryResult("timeout", query, error="hermes_timeout")
        except Exception as exc:
            self.circuit_breaker.failure()
            return HermesRecoveryResult("unavailable", query, error=str(exc) or "hermes_unavailable")
        if result.status == "success":
            self.circuit_breaker.success()
            self._cache[cache_key] = result
        else:
            self.circuit_breaker.failure()
        return result

    def _request_payload(
        self,
        query: str,
        analysis: LocalRecoveryAnalysis,
        *,
        campaign_version: str,
        lore_version: str,
    ) -> dict[str, Any]:
        evidence = analysis.retrieval.evidence[: self.max_evidence]
        return {
            "schema_version": "rpg_hermes_recovery_request_v1",
            "query": query,
            "intent": analysis.intent.selected.intent,
            "affordance": analysis.intent.selected.affordance,
            "underlying_goal": analysis.intent.selected.underlying_goal,
            "knowledge_status": analysis.retrieval.knowledge_status,
            "visible_evidence": [
                {
                    "evidence_id": row.evidence_id,
                    "source": row.source,
                    "content": row.content,
                    "confidence": row.confidence,
                }
                for row in evidence
                if row.visibility != "hidden"
            ],
            "campaign_version": campaign_version,
            "lore_version": lore_version,
            "constraints": {
                "proposal_only": True,
                "review_required": True,
                "executes": False,
                "state_mutation_allowed": False,
                "hidden_information_forbidden": True,
                "player_choice_must_not_be_taken": True,
            },
            "requested_output": {
                "evidence": True,
                "inferences": True,
                "uncertainty": True,
                "forward_strategies": True,
                "proposals": True,
            },
        }

    def _parse_result(self, query: str, raw: Mapping[str, Any]) -> HermesRecoveryResult:
        if not isinstance(raw, Mapping):
            return HermesRecoveryResult("malformed", query, error="result_not_mapping")
        forbidden = _find_forbidden_keys(raw)
        if forbidden:
            return HermesRecoveryResult(
                "rejected",
                query,
                error="forbidden_result_keys:" + ",".join(sorted(forbidden)),
            )
        if bool(raw.get("executes")) or bool(raw.get("state_mutation_allowed")):
            return HermesRecoveryResult("rejected", query, error="execution_or_mutation_requested")
        evidence = tuple(
            HermesEvidence(
                evidence_id=str(row.get("evidence_id") or ""),
                source=str(row.get("source") or "hermes"),
                content=row.get("content"),
                confidence=float(row.get("confidence") or 0.0),
            )
            for row in raw.get("evidence", ())
            if isinstance(row, Mapping) and str(row.get("visibility") or "player_visible") != "hidden"
        )
        proposals = tuple(
            HermesProposal(
                proposal_id=str(row.get("proposal_id") or ""),
                proposal_type=str(row.get("proposal_type") or "inference"),
                summary=str(row.get("summary") or ""),
                risk=str(row.get("risk") or "low"),
                lifetime=str(row.get("lifetime") or "turn"),
                provenance_refs=tuple(
                    str(item)
                    for item in (
                        row.get("provenance_refs")
                        or row.get("evidence_refs")
                        or ()
                    )
                    if str(item)
                ),
                content=row.get("content"),
                visibility=str(row.get("visibility") or "player_visible"),
                confidence=float(row.get("confidence") or 0.5),
                conflicts=tuple(str(item) for item in row.get("conflicts", ()) if str(item)),
            )
            for row in raw.get("proposals", ())
            if isinstance(row, Mapping) and str(row.get("summary") or "").strip()
        )
        return HermesRecoveryResult(
            status="success",
            query=query,
            evidence=evidence,
            inferences=_strings(raw.get("inferences")),
            uncertainty=_strings(raw.get("uncertainty")),
            forward_strategies=_strings(raw.get("forward_strategies")),
            proposals=proposals,
        )


def _find_forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized in _FORBIDDEN_RESULT_KEYS:
                found.add(normalized)
            found.update(_find_forbidden_keys(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(_find_forbidden_keys(item))
    return found


def _cache_key(query: str, campaign_version: str, lore_version: str) -> str:
    payload = json.dumps(
        [" ".join(str(query).casefold().split()), campaign_version, lore_version],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    try:
        return tuple(str(item) for item in value if str(item))
    except TypeError:
        return ()
