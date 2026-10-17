from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping, Protocol, Sequence

from .candidate_ranker import CandidateRanker
from .contracts import (
    AgencyEffect,
    CandidateSource,
    ResponseCandidate,
    ResponseMode,
    ResponseRequest,
    Reversibility,
    SectionType,
    SemanticResponsePlan,
    SemanticSection,
    coerce_response_mode,
)
from .eligibility import EligibilityPolicy, eligibility_reasons
from .quality_gate import QualityGate, QualityReport
from .renderer import ResponseRenderer


class CandidateAdapter(Protocol):
    def __call__(self, request: ResponseRequest) -> Sequence[ResponseCandidate]: ...


class CandidateRewriter(Protocol):
    def __call__(
        self,
        request: ResponseRequest,
        candidate: ResponseCandidate,
        quality_report: QualityReport,
    ) -> ResponseCandidate | None: ...


class RpgResponseGenerator:
    """Canonical owner of response validation, selection, repair, and rendering."""

    def __init__(
        self,
        *,
        candidate_adapter: CandidateAdapter | None = None,
        renderer: ResponseRenderer | None = None,
        eligibility_policy: EligibilityPolicy | None = None,
        ranker: CandidateRanker | None = None,
        quality_gate: QualityGate | None = None,
        rewriter: CandidateRewriter | None = None,
        selector: Callable[[Sequence[ResponseCandidate]], ResponseCandidate] | None = None,
    ) -> None:
        self._candidate_adapter = candidate_adapter or LegacyCandidateAdapter()
        self._renderer = renderer or ResponseRenderer()
        self._eligibility = eligibility_policy or EligibilityPolicy()
        self._ranker = ranker or CandidateRanker()
        self._quality_gate = quality_gate or QualityGate()
        self._rewriter = rewriter
        self._selector = selector

    def generate(self, request: ResponseRequest):
        raw_candidates = tuple(self._candidate_adapter(request))
        if not raw_candidates:
            raw_candidates = (self._empty_candidate(request),)
        evaluated = tuple(
            self._eligibility.evaluate(candidate, request)
            for candidate in raw_candidates
        )
        if not any(candidate.eligible for candidate in evaluated):
            emergency = self._eligibility.evaluate(self._empty_candidate(request), request)
            evaluated = (*evaluated, emergency)

        ranked = (
            (self._selector(tuple(candidate for candidate in evaluated if candidate.eligible)),)
            if self._selector is not None
            else self._ranker.rank(evaluated)
        )
        quality_attempts: list[dict[str, Any]] = []
        selected: ResponseCandidate | None = None
        final_report: QualityReport | None = None
        repair_history: tuple[str, ...] = ()
        cycle_metadata: dict[str, Any] = {}
        for candidate in ranked:
            current, report, history, metadata = self._quality_cycle(request, candidate)
            quality_attempts.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "candidate_source": candidate.source.value,
                    "issues": list(report.issues),
                    "accepted": report.ok,
                }
            )
            if report.ok:
                selected = current
                final_report = report
                repair_history = history
                cycle_metadata = metadata
                break

        if selected is None or final_report is None:
            emergency = self._eligibility.evaluate(self._empty_candidate(request), request)
            selected, final_report, repair_history, cycle_metadata = self._quality_cycle(
                request,
                emergency,
            )
            if not final_report.ok:
                raise RuntimeError(
                    "canonical deterministic response failed final quality validation: "
                    + ",".join(final_report.issues)
                )

        authoritative_deltas = _mapping(
            request.authoritative_turn_result.get("state_delta")
            or request.authoritative_turn_result.get("authoritative_deltas")
        )
        rendered = self._renderer.render(
            selected.plan,
            authoritative_deltas=authoritative_deltas,
            repair_history=repair_history,
            quality_report=final_report.as_dict(),
            metadata={
                "turn_id": request.turn_id,
                "candidate_id": selected.candidate_id,
                "candidate_source": selected.source.value,
                "runtime_mode": request.runtime_mode,
                "candidate_count": len(evaluated),
                "eligible_candidate_count": len(
                    [candidate for candidate in evaluated if candidate.eligible]
                ),
                "ranked_candidate_ids": [
                    candidate.candidate_id
                    for candidate in self._ranker.rank(evaluated)
                ],
                "hard_gate_decisions": [
                    {
                        "gate": decision.gate,
                        "passed": decision.passed,
                        "reasons": list(decision.reasons),
                    }
                    for decision in selected.gate_decisions
                ],
                "quality_candidate_attempts": quality_attempts,
                **cycle_metadata,
            },
        )
        return rendered

    def _quality_cycle(
        self,
        request: ResponseRequest,
        selected: ResponseCandidate,
    ) -> tuple[ResponseCandidate, QualityReport, tuple[str, ...], dict[str, Any]]:
        current = selected
        history = list(selected.repair_history)
        rendered = self._renderer.render(current.plan)
        report = self._quality_gate.evaluate(rendered.text)
        metadata: dict[str, Any] = {
            "initial_quality_issues": list(report.issues),
            "rewrite_attempted": False,
            "rewrite_accepted": False,
            "rewrite_rejection_reasons": [],
        }

        if not report.ok:
            repaired_plan, deterministic_history = self._quality_gate.repair_plan(current.plan)
            if deterministic_history and repaired_plan.sections:
                repaired = replace(
                    current,
                    plan=repaired_plan,
                    repair_history=(*current.repair_history, *deterministic_history),
                )
                repaired = self._eligibility.evaluate(repaired, request)
                if repaired.eligible:
                    current = repaired
                    history.extend(deterministic_history)
                    rendered = self._renderer.render(current.plan)
                    report = self._quality_gate.evaluate(rendered.text)
                else:
                    metadata["deterministic_repair_rejected"] = list(
                        eligibility_reasons(repaired)
                    )

        if not report.ok and self._rewriter is not None:
            metadata["rewrite_attempted"] = True
            rewritten = self._rewriter(request, current, report)
            if rewritten is not None:
                rewritten = self._eligibility.evaluate(rewritten, request)
                if rewritten.eligible:
                    rewritten_rendered = self._renderer.render(rewritten.plan)
                    rewritten_report = self._quality_gate.evaluate(rewritten_rendered.text)
                    if rewritten_report.ok:
                        current = rewritten
                        report = rewritten_report
                        history.extend(rewritten.repair_history)
                        metadata["rewrite_accepted"] = True
                    else:
                        metadata["rewrite_rejection_reasons"] = list(
                            rewritten_report.issues
                        )
                else:
                    metadata["rewrite_rejection_reasons"] = list(
                        eligibility_reasons(rewritten)
                    )

        if (
            not report.ok
            and metadata["rewrite_attempted"]
            and not metadata["rewrite_accepted"]
        ):
            repaired_plan, deterministic_history = self._quality_gate.repair_plan(
                current.plan,
                remove_low_value_phrases=True,
            )
            if deterministic_history and repaired_plan.sections:
                repaired = replace(
                    current,
                    plan=repaired_plan,
                    repair_history=(*current.repair_history, *deterministic_history),
                )
                repaired = self._eligibility.evaluate(repaired, request)
                if repaired.eligible:
                    repaired_rendered = self._renderer.render(repaired.plan)
                    repaired_report = self._quality_gate.evaluate(repaired_rendered.text)
                    if repaired_report.ok:
                        current = repaired
                        report = repaired_report
                        history.extend(deterministic_history)
                        metadata["post_rewrite_deterministic_repair"] = list(
                            deterministic_history
                        )
                    else:
                        metadata["post_rewrite_repair_issues"] = list(
                            repaired_report.issues
                        )
                else:
                    metadata["post_rewrite_repair_rejected"] = list(
                        eligibility_reasons(repaired)
                    )

        final_rendered = self._renderer.render(current.plan)
        final_report = self._quality_gate.evaluate(final_rendered.text)
        metadata["final_quality_issues"] = list(final_report.issues)
        return current, final_report, tuple(history), metadata

    def shadow_compare(
        self,
        request: ResponseRequest,
        *,
        legacy_visible_text: str,
    ) -> dict[str, Any]:
        rendered = self.generate(request)
        return {
            "format_version": "rpg_response_shadow_v1",
            "turn_id": request.turn_id,
            "legacy_visible_text": legacy_visible_text,
            "canonical_visible_text": rendered.text,
            "changed": rendered.text.strip() != legacy_visible_text.strip(),
            "mode": rendered.mode.value,
            "approved_section_ids": list(rendered.approved_section_ids),
            "quality_report": dict(rendered.quality_report),
            "hard_gate_decisions": rendered.metadata.get("hard_gate_decisions", []),
            "authoritative_state_unchanged": True,
        }

    @staticmethod
    def _empty_candidate(request: ResponseRequest) -> ResponseCandidate:
        plan = SemanticResponsePlan(
            mode=ResponseMode.RECOVERY,
            sections=(
                SemanticSection(
                    section_id="empty.clarification",
                    section_type=SectionType.CLARIFICATION,
                    text="What outcome are you trying to achieve here?",
                ),
            ),
            forward_strategy="ask_clarification",
            agency_effect=AgencyEffect.CLARIFICATION_ONLY,
            reversibility=Reversibility.FULLY_REVERSIBLE,
        )
        return ResponseCandidate(
            candidate_id=f"{request.turn_id}:empty",
            plan=plan,
            source=CandidateSource.DETERMINISTIC,
            current_turn_relevance=0.2,
            forward_motion=0.2,
            specificity=0.1,
            naturalness=0.5,
        )


class LegacyCandidateAdapter:
    """Convert existing runtime/world-scene payloads into semantic plans."""

    def __call__(self, request: ResponseRequest) -> Sequence[ResponseCandidate]:
        payload = _mapping(request.legacy_payload)
        source = str(payload.get("source") or "").casefold()
        candidate_source = (
            CandidateSource.LEGACY_WORLD_SCENE
            if "world" in source or "scene" in source
            else CandidateSource.LEGACY_RUNTIME
        )
        plan = semantic_plan_from_legacy_payload(
            payload,
            mode=_infer_mode(request, payload),
        )
        return (
            ResponseCandidate(
                candidate_id=f"{request.turn_id}:legacy",
                plan=plan,
                source=candidate_source,
                current_turn_relevance=0.5,
                forward_motion=0.4,
                specificity=0.4,
                naturalness=0.5,
                provider_metadata={"legacy_source": payload.get("source") or ""},
            ),
        )


def semantic_plan_from_legacy_payload(
    payload: Mapping[str, Any],
    *,
    mode: ResponseMode,
) -> SemanticResponsePlan:
    sections: list[SemanticSection] = []
    narration = _text(payload.get("narration"))
    action = _text(payload.get("action"))
    result = _text(payload.get("result_text") or payload.get("result"))
    npc = _mapping(payload.get("npc"))
    npc_line = _text(npc.get("line") or payload.get("npc_line"))
    speaker = _text(npc.get("speaker") or payload.get("speaker"))

    if narration:
        sections.append(
            SemanticSection(
                section_id="legacy.narration",
                section_type=SectionType.NARRATION,
                text=narration,
                metadata={"legacy_field": "narration"},
            )
        )
    if action:
        sections.append(
            SemanticSection(
                section_id="legacy.action",
                section_type=SectionType.ACTION,
                text=action,
                metadata={"legacy_field": "action"},
            )
        )
    if result and not isinstance(payload.get("result"), Mapping):
        sections.append(
            SemanticSection(
                section_id="legacy.result",
                section_type=SectionType.RESULT,
                text=result,
                metadata={"legacy_field": "result"},
            )
        )
    if npc_line:
        sections.append(
            SemanticSection(
                section_id="legacy.npc",
                section_type=SectionType.NPC_DIALOGUE,
                text=npc_line,
                speaker_id=speaker,
                metadata={"legacy_field": "npc"},
            )
        )

    forward_strategy = _text(payload.get("forward_strategy")) or "answer_directly"
    agency_effect = _coerce_agency(payload.get("agency_effect"))
    reversibility = _coerce_reversibility(payload.get("reversibility"))
    return SemanticResponsePlan(
        mode=mode,
        sections=tuple(sections),
        forward_strategy=forward_strategy,
        agency_effect=agency_effect,
        reversibility=reversibility,
        metadata={"legacy_source": payload.get("source") or ""},
    )


def build_runtime_shadow_report(
    *,
    turn_id: str,
    player_input: str,
    runtime_payload: Mapping[str, Any],
    authoritative_turn_result: Mapping[str, Any] | None = None,
    legacy_visible_text: str = "",
) -> dict[str, Any]:
    request = ResponseRequest(
        turn_id=turn_id,
        player_input=player_input,
        authoritative_turn_result=dict(authoritative_turn_result or {}),
        runtime_mode="runtime_shadow",
        legacy_payload=dict(runtime_payload),
    )
    return RpgResponseGenerator().shadow_compare(
        request,
        legacy_visible_text=legacy_visible_text,
    )


def build_world_scene_shadow_report(
    *,
    turn_id: str,
    player_input: str,
    world_scene_payload: Mapping[str, Any],
    authoritative_turn_result: Mapping[str, Any] | None = None,
    legacy_visible_text: str = "",
) -> dict[str, Any]:
    payload = dict(world_scene_payload)
    payload.setdefault("source", "legacy_world_scene")
    request = ResponseRequest(
        turn_id=turn_id,
        player_input=player_input,
        authoritative_turn_result=dict(authoritative_turn_result or {}),
        runtime_mode="world_scene_shadow",
        legacy_payload=payload,
    )
    return RpgResponseGenerator().shadow_compare(
        request,
        legacy_visible_text=legacy_visible_text,
    )


def _infer_mode(request: ResponseRequest, payload: Mapping[str, Any]) -> ResponseMode:
    requested = (
        payload.get("response_mode")
        or payload.get("mode")
        or request.authoritative_turn_result.get("response_mode")
        or request.authoritative_turn_result.get("semantic_family")
        or request.authoritative_turn_result.get("action_type")
    )
    return coerce_response_mode(requested, ResponseMode.ACTION)


def _coerce_agency(value: Any) -> AgencyEffect:
    try:
        return AgencyEffect(str(value or AgencyEffect.NONE.value))
    except ValueError:
        return AgencyEffect.NONE


def _coerce_reversibility(value: Any) -> Reversibility:
    try:
        return Reversibility(str(value or Reversibility.FULLY_REVERSIBLE.value))
    except ValueError:
        return Reversibility.FULLY_REVERSIBLE


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()
