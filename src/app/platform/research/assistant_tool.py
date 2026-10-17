"""Read-only Omnix Research adapter for generalized Agent/Workflow execution."""
from __future__ import annotations

from typing import Any

from app.platform.assistant_tools.contracts import AssistantToolRequest, AssistantToolResult
from app.capabilities.registry import Capability, capability


def run_research_tool_request(
    request: AssistantToolRequest,
    service: Any | None = None,
) -> AssistantToolResult:
    if request.action_id != "research.web_search":
        return _result(
            request,
            "Research action is not available.",
            {},
            error="research_action_not_available",
        )

    query = " ".join(str(request.input.get("query") or "").split()).strip()
    if not query:
        return _result(
            request,
            "A research query is required.",
            {},
            error="research_query_required",
        )
    try:
        max_results = int(request.input.get("max_results", 5))
    except (TypeError, ValueError):
        return _result(
            request,
            "max_results must be an integer.",
            {},
            error="research_max_results_invalid",
        )
    max_results = max(1, min(max_results, 10))
    try:
        max_extracts = int(request.input.get("max_extracts", 2))
    except (TypeError, ValueError):
        return _result(
            request,
            "max_extracts must be an integer.",
            {},
            error="research_max_extracts_invalid",
        )
    max_extracts = max(0, min(max_extracts, 4))

    if service is None:
        # Keep process initialization acyclic: Quick Search imports the
        # assistant-context package, whose initialization can reach chat and
        # live-agent hooks that depend on this Hermes bridge.
        from .quick_search import QuickSearchService

        runtime = QuickSearchService(max_extracts=max_extracts)
    else:
        runtime = service
    try:
        execution = runtime.search(
            query,
            max_results,
            identity=str(request.session_id or "agent"),
        )
    except Exception as exc:
        return _result(
            request,
            "Web research failed.",
            {"query": query},
            error=f"{type(exc).__name__}: {exc}"[:500],
        )

    items = [
        item.model_dump(mode="json")
        if hasattr(item, "model_dump")
        else dict(item)
        for item in list(getattr(execution, "items", []) or [])
    ]
    diagnostics = dict(getattr(execution, "diagnostics", {}) or {})
    warnings = list(getattr(execution, "warnings", []) or [])
    output = {
        "query": query,
        "items": items,
        "diagnostics": diagnostics,
        "warnings": warnings,
        "source_manifest_id": getattr(execution, "source_manifest_id", None),
    }
    status = str(diagnostics.get("status") or "").casefold()
    error = "research_search_failed" if status == "failed" else None
    summary = (
        f"Found {len(items)} research result(s) for {query}."
        if error is None
        else "Web research did not return usable results."
    )
    return _result(request, summary, output, error=error)


def _result(
    request: AssistantToolRequest,
    summary: str,
    output: dict[str, Any],
    *,
    error: str | None = None,
) -> AssistantToolResult:
    return AssistantToolResult(
        tool_id=request.tool_id,
        action_id=request.action_id,
        session_id=request.session_id,
        risk_level="low",
        state_changed=False,
        result_summary=summary,
        output=output,
        error=error,
    )


class ResearchTool:
    """Research's read-only web search tool for agents and workflows."""

    tool_id = "research"
    display_name = "Research"
    description = "Governed research capabilities."
    account_label = "Omnix Research"

    def capabilities(self) -> tuple[Capability, ...]:
        return (
            capability("research.web_search", "Search the web", "Run bounded provider-neutral web research and return source-grounded results without side effects.", zone="broker", effect="read", network=True, provider="Omnix Research", category="research", assistant=True, hermes=True, input_schema={"query": "search query", "max_results": "integer 1..10", "max_extracts": "integer 0..4"}),
        )

    def default_enabled(self) -> bool:
        return True

    def run(self, request: AssistantToolRequest) -> AssistantToolResult:
        return run_research_tool_request(request)
