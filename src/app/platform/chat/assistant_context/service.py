"""Context assembly for enriched assistant turns."""
from __future__ import annotations

import time
from typing import Callable

from typing import Any

from app.conversation.contracts import AssistantContextItem
from app.runtime.ports import optional

from .models import AssistantContextBuildResult, AssistantContextChatRequest
from app.providers.desktop_vision import DesktopVisionClient, CodexDesktopVisionClient, default_desktop_vision_client


class AssistantContextService:
    def __init__(
        self,
        *,
        web_search_factory: Callable[..., Any] | None = None,
        quick_search_factory: Callable[[], Any] | None = None,
        research_factory: Callable[[], Any] | None = None,
        desktop_vision_factory: Callable[[], DesktopVisionClient | CodexDesktopVisionClient] = (
            default_desktop_vision_client
        ),
    ) -> None:
        self.web_search_factory = web_search_factory
        self.quick_search_factory = quick_search_factory
        self.desktop_vision_factory = desktop_vision_factory
        self.research_factory = research_factory

    def _research(self):
        if self.research_factory is not None:
            return self.research_factory()
        from app.platform.chat.contracts import CHAT_RESEARCH

        return optional(CHAT_RESEARCH)

    def build(self, request: AssistantContextChatRequest) -> AssistantContextBuildResult:
        items: list[AssistantContextItem] = []
        current_image = request.desktop_current_image_data_url or request.desktop_image_data_url
        desktop_requested = bool(
            current_image
            or request.desktop_history_image_data_url
            or request.desktop_combined_image_data_url
        )
        diagnostics: dict[str, object] = {
            "web_research_mode": request.web_research_mode,
            "research_provider": request.internal_research_provider,
            "research_provider_chain": request.internal_research_provider_chain,
            "research_compatibility_warnings": request.internal_research_warnings,
            "desktop_requested": desktop_requested,
            "desktop_capture_mode": request.desktop_capture_mode,
            "desktop_history_frames": len(request.desktop_history_timestamps),
            "live_repair_requested": request.live_repair is not None,
        }

        if request.live_repair is not None:
            repair = request.live_repair
            items.append(
                AssistantContextItem(
                    source_id="live_repair",
                    title="Live conversation repair guidance",
                    content=(
                        "Trusted conversational-control guidance for this response: "
                        f"{repair.instruction.strip()} "
                        "Keep the visible user words authoritative, apply the repair briefly, and then continue naturally."
                    ),
                    metadata={
                        "kind": repair.kind,
                        "source_reason": repair.source_reason,
                        "confidence": repair.confidence,
                        "trusted_control_context": True,
                    },
                )
            )
            diagnostics["live_repair_kind"] = repair.kind
            diagnostics["live_repair_source_reason"] = repair.source_reason
            diagnostics["live_repair_confidence"] = repair.confidence

        research = self._research() if request.web_research_mode == "quick" else None
        if request.web_research_mode == "quick" and research is None:
            diagnostics["web_search_status"] = "research_unavailable"
        elif request.web_research_mode == "quick" and research is not None:
            prepared, search_diagnostics = research.quick_context(
                request,
                web_search_factory=self.web_search_factory,
                quick_search_factory=self.quick_search_factory,
            )
            items.extend(AssistantContextItem.model_validate(item) for item in prepared)
            diagnostics.update(search_diagnostics)
        elif request.web_research_mode == "deep":
            diagnostics["web_search_status"] = "deferred_to_deep_research"
        else:
            diagnostics["web_search_status"] = "skipped"

        if desktop_requested:
            started = time.perf_counter()
            try:
                if not current_image:
                    raise ValueError("desktop temporal context requires a current image")
                observation = self.desktop_vision_factory().describe(
                    current_image,
                    request.desktop_question or request.content,
                    request.vision_model_id or request.model_id,
                    history_image_data_url=request.desktop_history_image_data_url,
                    combined_image_data_url=request.desktop_combined_image_data_url,
                    history_timestamps=request.desktop_history_timestamps,
                    capture_mode=request.desktop_capture_mode,
                )
                items.append(observation)
                diagnostics["desktop_status"] = "completed"
                diagnostics["desktop_model"] = observation.metadata.get("model")
                diagnostics["desktop_fallback_mode"] = observation.metadata.get("fallback_mode")
                diagnostics["desktop_image_count"] = observation.metadata.get("image_count")
                diagnostics["desktop_fallback_errors"] = observation.metadata.get("fallback_errors", [])
            except Exception as exc:
                diagnostics["desktop_status"] = "failed"
                diagnostics["desktop_error"] = f"{type(exc).__name__}: {exc}"
                items.append(_desktop_failure_item(str(diagnostics["desktop_error"])))
            diagnostics["desktop_ms"] = round((time.perf_counter() - started) * 1000)
        else:
            diagnostics["desktop_status"] = "skipped"

        return AssistantContextBuildResult(items=items, diagnostics=diagnostics)


def default_assistant_context_service() -> AssistantContextService:
    return AssistantContextService()


def _desktop_failure_item(error: str) -> AssistantContextItem:
    return AssistantContextItem(
        source_id="desktop_vision",
        title="Desktop sharing status",
        content=(
            "The user shared their desktop for this turn, but Omnix could not inspect the image. "
            f"Vision resolver error: {error}. "
            "Do not claim to see the screen. Tell the user desktop sharing is active but a "
            "vision-capable model or vision provider configuration is needed."
        ),
        metadata={"status": "failed", "error": error},
    )
