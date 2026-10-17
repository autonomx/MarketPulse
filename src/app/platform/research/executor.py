"""Bounded iterative execution for durable Deep Research jobs."""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from .contracts import ResearchEvidence, ResearchSource, ResearchSourceSnapshot
from .deep_manifest import create_deep_research_manifest, update_snapshot_citation_label
from .extraction import ReadablePageExtractor
from .planner import (
    ResearchPlan,
    ResearchPlanner,
    ResearchPlanningBudget,
    ResearchPlanningRequest,
    enforce_research_plan_budget,
)
from .source_store import ResearchSourceStore, default_research_source_store

if TYPE_CHECKING:
    # jobs imports this module at runtime; the input model is annotation-only here.
    from .jobs import DeepResearchJobInput

ResearchExecutionStatus = Literal["completed", "partial", "canceled"]


class ResearchConflict(BaseModel):
    conflict_id: str
    summary: str
    supporting_snapshot_ids: list[str] = Field(default_factory=list)
    contradicting_snapshot_ids: list[str] = Field(default_factory=list)
    status: Literal["unresolved", "resolved"] = "unresolved"


class ResearchExecutionCheckpoint(BaseModel):
    objective: str
    plan: ResearchPlan
    planner_backend: str
    next_operation_index: int = 0
    logical_queries: int = 0
    extracted_pages: int = 0
    duplicate_saturation: int = 0
    sources: list[ResearchSource] = Field(default_factory=list)
    snapshots: list[ResearchSourceSnapshot] = Field(default_factory=list)
    evidence: list[ResearchEvidence] = Field(default_factory=list)
    conflicts: list[ResearchConflict] = Field(default_factory=list)
    search_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stop_reason: str | None = None


class ResearchExecutionResult(BaseModel):
    objective: str
    research_status: ResearchExecutionStatus
    planner_backend: str
    source_manifest_id: str | None = None
    sources: list[ResearchSource] = Field(default_factory=list)
    snapshots: list[ResearchSourceSnapshot] = Field(default_factory=list)
    evidence: list[ResearchEvidence] = Field(default_factory=list)
    conflicts: list[ResearchConflict] = Field(default_factory=list)
    search_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stop_reason: str
    logical_queries: int = 0
    extracted_pages: int = 0


ProgressCallback = Callable[[str, str], None]
CancelCallback = Callable[[], bool]
CheckpointCallback = Callable[[str, ResearchExecutionCheckpoint], None]
QuickSearchFactory = Callable[[int, int], Any]


class DeepResearchExecutor:
    def __init__(
        self,
        *,
        planner: ResearchPlanner | None = None,
        quick_search_factory: QuickSearchFactory | None = None,
        source_store_factory: Callable[[], ResearchSourceStore] = default_research_source_store,
        extractor_factory: Callable[[], ReadablePageExtractor] = ReadablePageExtractor,
    ) -> None:
        self.planner = planner or ResearchPlanner()
        self.source_store_factory = source_store_factory
        self.extractor_factory = extractor_factory
        self.quick_search_factory = quick_search_factory or self._default_quick_search_factory

    def execute(
        self,
        request: DeepResearchJobInput,
        progress: ProgressCallback,
        canceled: CancelCallback,
        *,
        checkpoint: ResearchExecutionCheckpoint | None = None,
        save_checkpoint: CheckpointCallback | None = None,
    ) -> ResearchExecutionResult:
        budget = ResearchPlanningBudget(
            max_steps=request.max_steps,
            max_queries=request.max_queries,
            max_sources=request.max_sources,
            max_extracts=request.max_extracts,
        )
        if checkpoint is None:
            if request.research_plan is not None:
                plan = enforce_research_plan_budget(request.research_plan, budget)
                planner_backend = request.planner_backend or "approved_plan"
                planning_warnings: list[str] = []
            else:
                decision = self.planner.plan(
                    ResearchPlanningRequest(question=request.question, budget=budget)
                )
                plan = decision.plan
                planner_backend = decision.backend
                planning_warnings = list(decision.warnings)
            state = ResearchExecutionCheckpoint(
                objective=plan.objective,
                plan=plan,
                planner_backend=planner_backend,
                warnings=planning_warnings,
            )
            self._save("planning", state, save_checkpoint)
        else:
            state = checkpoint.model_copy(deep=True)

        store = self.source_store_factory()
        seen_source_ids = {source.source_record_id for source in state.sources}
        for operation_index in range(state.next_operation_index, len(state.plan.operations)):
            if canceled():
                state.stop_reason = "canceled"
                state.next_operation_index = operation_index
                self._save(
                    _stage_for_operation(state.plan.operations[operation_index].operation),
                    state,
                    save_checkpoint,
                )
                return self._result(state, "canceled", None)
            if operation_index >= budget.max_steps:
                state.stop_reason = "step_budget_exhausted"
                break

            operation = state.plan.operations[operation_index]
            state.next_operation_index = operation_index + 1
            if operation.operation == "web_search":
                if state.logical_queries >= budget.max_queries:
                    state.stop_reason = "query_budget_exhausted"
                    break
                remaining_sources = budget.max_sources - len(state.sources)
                if remaining_sources <= 0:
                    state.stop_reason = "source_budget_exhausted"
                    break
                remaining_extracts = max(0, budget.max_extracts - state.extracted_pages)
                progress("searching", f"Searching: {operation.query}")
                execution = self.quick_search_factory(remaining_sources, remaining_extracts).search(
                    operation.query or request.question,
                    min(8, remaining_sources),
                )
                state.logical_queries += 1
                state.search_diagnostics.append(
                    {
                        "query": operation.query or request.question,
                        **dict(getattr(execution, "diagnostics", {}) or {}),
                    }
                )
                added = self._merge_search_execution(
                    state,
                    execution,
                    store,
                    seen_source_ids,
                    budget.max_sources,
                )
                if added == 0 and execution.sources:
                    state.duplicate_saturation += 1
                else:
                    state.duplicate_saturation = 0
                state.warnings.extend(_warning_codes(execution.warnings))
                if state.duplicate_saturation >= 2:
                    state.stop_reason = "duplicate_saturation"
                    self._save("searching", state, save_checkpoint)
                    break
                self._save("searching", state, save_checkpoint)
                continue

            if operation.operation == "web_extract":
                if state.extracted_pages >= budget.max_extracts:
                    state.stop_reason = "extract_budget_exhausted"
                    break
                progress("extracting", "Reviewing a selected source")
                extracted = self._extract_named_source(
                    store,
                    state,
                    operation.source_record_id,
                )
                if extracted:
                    state.extracted_pages += 1
                else:
                    state.warnings.append("explicit_extraction_failed")
                self._save("extracting", state, save_checkpoint)
                continue

            if operation.operation == "evaluate_evidence":
                progress("evaluating", "Comparing evidence and conflicts")
                state.evidence = evaluate_evidence(state.sources, state.snapshots)
                state.conflicts = detect_conflicts(state.sources, state.snapshots)
                self._save("evaluating", state, save_checkpoint)
                continue

            if operation.operation == "stop":
                state.stop_reason = operation.reason or state.plan.stop_reason or "planner_stop"
                break

        if not state.evidence:
            progress("evaluating", "Comparing evidence and conflicts")
            state.evidence = evaluate_evidence(state.sources, state.snapshots)
            state.conflicts = detect_conflicts(state.sources, state.snapshots)
            self._save("evaluating", state, save_checkpoint)

        manifest_id: str | None = None
        if state.sources and state.snapshots:
            manifest = create_deep_research_manifest(
                store,
                state.objective,
                state.sources,
                state.snapshots,
            )
            manifest_id = manifest.manifest_id

        stop_reason = state.stop_reason or (
            "evidence_collected" if state.sources else "no_reliable_sources"
        )
        # Without sources, a planner-chosen stop reason (any wording) must not
        # read as a completed run; keep only cancellation and budget reasons.
        if not state.sources and stop_reason not in {
            "canceled",
            "step_budget_exhausted",
            "query_budget_exhausted",
            "source_budget_exhausted",
            "extract_budget_exhausted",
            "duplicate_saturation",
        }:
            stop_reason = "no_reliable_sources"
        state.stop_reason = stop_reason
        partial_reasons = {
            "step_budget_exhausted",
            "query_budget_exhausted",
            "source_budget_exhausted",
            "extract_budget_exhausted",
            "duplicate_saturation",
            "no_reliable_sources",
        }
        status: ResearchExecutionStatus = (
            "partial" if stop_reason in partial_reasons or not state.sources else "completed"
        )
        return self._result(state, status, manifest_id)

    def _default_quick_search_factory(
        self,
        remaining_sources: int,
        remaining_extracts: int,
    ) -> Any:
        from .quick_search import QuickSearchService

        return QuickSearchService(
            source_store_factory=self.source_store_factory,
            extractor_factory=self.extractor_factory,
            max_extracts=min(3, remaining_extracts),
        )

    @staticmethod
    def _merge_search_execution(
        state: ResearchExecutionCheckpoint,
        execution: Any,
        store: ResearchSourceStore,
        seen_source_ids: set[str],
        source_budget: int,
    ) -> int:
        snapshots_by_source = {
            snapshot.source_record_id: snapshot for snapshot in execution.snapshots
        }
        added = 0
        for source in execution.sources:
            if len(state.sources) >= source_budget:
                break
            if source.source_record_id in seen_source_ids:
                continue
            snapshot = snapshots_by_source.get(source.source_record_id)
            if snapshot is None:
                continue
            label = f"S{len(state.snapshots) + 1}"
            snapshot = update_snapshot_citation_label(store, snapshot, label)
            state.sources.append(source)
            state.snapshots.append(snapshot)
            state.extracted_pages += int(snapshot.extraction_status == "completed")
            seen_source_ids.add(source.source_record_id)
            added += 1
        return added

    def _extract_named_source(
        self,
        store: ResearchSourceStore,
        state: ResearchExecutionCheckpoint,
        source_record_id: str | None,
    ) -> bool:
        source = next(
            (item for item in state.sources if item.source_record_id == source_record_id),
            None,
        )
        if source is None or not source.original_url:
            return False
        snapshot_index = next(
            (
                index
                for index, snapshot in enumerate(state.snapshots)
                if snapshot.source_record_id == source.source_record_id
            ),
            None,
        )
        if snapshot_index is None:
            return False
        snapshot = state.snapshots[snapshot_index]
        if snapshot.extraction_status == "completed":
            return False
        try:
            page = self.extractor_factory().extract(source.original_url)
            state.snapshots[snapshot_index] = store.save_extraction(snapshot.snapshot_id, page)
            return True
        except Exception:
            state.snapshots[snapshot_index] = store.mark_extraction_failed(snapshot.snapshot_id) or snapshot
            return False

    @staticmethod
    def _save(
        stage_id: str,
        state: ResearchExecutionCheckpoint,
        callback: CheckpointCallback | None,
    ) -> None:
        if callback is not None:
            callback(stage_id, state.model_copy(deep=True))

    @staticmethod
    def _result(
        state: ResearchExecutionCheckpoint,
        status: ResearchExecutionStatus,
        manifest_id: str | None,
    ) -> ResearchExecutionResult:
        return ResearchExecutionResult(
            objective=state.objective,
            research_status=status,
            planner_backend=state.planner_backend,
            source_manifest_id=manifest_id,
            sources=state.sources,
            snapshots=state.snapshots,
            evidence=state.evidence,
            conflicts=state.conflicts,
            search_diagnostics=state.search_diagnostics,
            warnings=list(dict.fromkeys(state.warnings)),
            stop_reason=state.stop_reason or "completed",
            logical_queries=state.logical_queries,
            extracted_pages=state.extracted_pages,
        )


def evaluate_evidence(
    sources: list[ResearchSource],
    snapshots: list[ResearchSourceSnapshot],
) -> list[ResearchEvidence]:
    source_by_id = {source.source_record_id: source for source in sources}
    evidence: list[ResearchEvidence] = []
    for index, snapshot in enumerate(snapshots, start=1):
        source = source_by_id.get(snapshot.source_record_id)
        claim = _first_sentence(snapshot.snippet) or (source.title if source else "Source evidence")
        evidence.append(
            ResearchEvidence(
                evidence_id=f"evidence:{index}",
                claim=claim,
                source_snapshot_ids=[snapshot.snapshot_id],
                confidence=0.75 if snapshot.extraction_status == "completed" else 0.45,
                notes=(
                    "Extracted page content is available."
                    if snapshot.extraction_status == "completed"
                    else "Evidence is limited to the search result snippet."
                ),
            )
        )
    return evidence


def detect_conflicts(
    sources: list[ResearchSource],
    snapshots: list[ResearchSourceSnapshot],
) -> list[ResearchConflict]:
    source_by_id = {source.source_record_id: source for source in sources}
    conflicts: list[ResearchConflict] = []
    for left_index, left in enumerate(snapshots):
        left_text = _normalized_claim(left.snippet)
        if not left_text:
            continue
        for right in snapshots[left_index + 1 :]:
            right_text = _normalized_claim(right.snippet)
            if not right_text or not _claims_overlap(left_text, right_text):
                continue
            if _is_negative(left_text) == _is_negative(right_text):
                continue
            left_title = source_by_id.get(left.source_record_id)
            right_title = source_by_id.get(right.source_record_id)
            summary = (
                f"Sources disagree on a related claim: "
                f"{left_title.title if left_title else left.citation_label} versus "
                f"{right_title.title if right_title else right.citation_label}."
            )
            conflicts.append(
                ResearchConflict(
                    conflict_id=f"conflict:{len(conflicts) + 1}",
                    summary=summary,
                    supporting_snapshot_ids=[left.snapshot_id],
                    contradicting_snapshot_ids=[right.snapshot_id],
                )
            )
    return conflicts


def render_execution_summary(result: ResearchExecutionResult) -> str:
    lines = ["## Research findings"]
    if result.evidence:
        for evidence in result.evidence:
            snapshot = next(
                (
                    item
                    for item in result.snapshots
                    if item.snapshot_id in evidence.source_snapshot_ids
                ),
                None,
            )
            citation = f" [{snapshot.citation_label}]" if snapshot else ""
            lines.append(f"- {evidence.claim}{citation}")
    else:
        lines.append("No reliable source evidence was collected.")
    if result.conflicts:
        lines.append("\n## Unresolved conflicts")
        lines.extend(f"- {conflict.summary}" for conflict in result.conflicts)
    lines.append("\n## Limitations")
    lines.append(f"- Research stopped because: {result.stop_reason}.")
    for warning in result.warnings:
        lines.append(f"- {warning.replace('_', ' ')}.")
    return "\n".join(lines)


def _warning_codes(warnings: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("code") or "research_warning") for item in warnings]


def _stage_for_operation(operation: str) -> str:
    return {
        "web_search": "searching",
        "web_extract": "extracting",
        "evaluate_evidence": "evaluating",
        "stop": "evaluating",
    }.get(operation, "evaluating")


def _first_sentence(value: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    return re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0][:500]


def _normalized_claim(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _claims_overlap(left: str, right: str) -> bool:
    ignored = {"the", "a", "an", "is", "are", "was", "were", "not", "no", "does", "do"}
    left_words = {word for word in left.split() if word not in ignored and len(word) > 2}
    right_words = {word for word in right.split() if word not in ignored and len(word) > 2}
    return len(left_words & right_words) >= 2


def _is_negative(value: str) -> bool:
    words = set(value.split())
    return bool(words & {"not", "no", "never", "without", "false", "denied", "rejects"})
