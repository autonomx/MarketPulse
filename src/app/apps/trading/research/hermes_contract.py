from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import ResearchActionProposal, TradingEvidence, TradingResearchRequest


class EvidenceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: str
    source_type: str
    authority_tier: int
    title: str | None = None
    locator: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    clue: str = ""


class TradingHermesContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    step: int = Field(ge=0)
    remaining_steps: int = Field(ge=0)
    remaining_queries: int = Field(ge=0)
    remaining_sources: int = Field(ge=0)
    remaining_extracts: int = Field(ge=0)
    evidence: tuple[EvidenceSummary, ...] = ()
    unresolved_facts: tuple[str, ...] = ()
    prior_actions: tuple[str, ...] = ()


class TradingHermesNextActionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: ResearchActionProposal
    rationale: str = ""

    @model_validator(mode="after")
    def stop_has_no_tool_args(self):
        if self.action.operation == "stop" and self.action.args:
            raise ValueError("stop action must not include args")
        return self


def evidence_summary(item: TradingEvidence) -> EvidenceSummary:
    snippet = " ".join(item.content.split())[:500]
    return EvidenceSummary(
        evidence_id=item.evidence_id,
        source_type=item.source_type,
        authority_tier=item.source_authority_tier,
        title=item.title,
        locator=item.source_locator,
        metadata={key: value for key, value in item.metadata.items() if key in {"form", "accession", "query", "provider"}},
        clue=snippet,
    )


def trading_next_action_payload(request: TradingResearchRequest, context: TradingHermesContext) -> dict[str, Any]:
    return {
        "task": (
            "Propose exactly one next trading-research action. Do not execute tools. "
            "Investigate unresolved catalyst, novelty, financing and supply facts using only the semantic allowlist."
        ),
        "contract_version": request.contract_version,
        "allowed_operations": list(request.allowed_operations),
        "forbidden": ["place_order", "cancel_order", "size_position", "modify_strategy", "shell", "files", "github"],
        "decision_schema": TradingHermesNextActionDecision.model_json_schema(),
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
    }
