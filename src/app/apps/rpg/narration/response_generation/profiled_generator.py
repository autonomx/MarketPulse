from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .contracts import ResponseMode, coerce_response_mode
from .orchestration import RpgResponseGenerator
from .performance import blocking_path_decision
from .profiles import ResponseProfileRegistry


class ProfiledRpgResponseGenerator:
    """Resolve authoritative policy before any candidate generation occurs."""

    def __init__(
        self,
        generator: RpgResponseGenerator | None = None,
        registry: ResponseProfileRegistry | None = None,
    ) -> None:
        self.generator = generator or RpgResponseGenerator()
        self.registry = registry or ResponseProfileRegistry()

    def generate(self, request):
        mode = self._request_mode(request)
        result = _mapping(request.authoritative_turn_result)
        recovery_needed = bool(
            result.get("recovery_needed")
            or result.get("resolver_status") in {"unresolved", "partial", "unsupported", "no_match"}
            or mode in {ResponseMode.RECOVERY, ResponseMode.INVESTIGATION}
            and request.runtime_mode not in {"supported_mechanic", "utility"}
        )
        profile, ignored = self.registry.resolve_from_request(
            mode,
            request.provider_policy,
            recovery_needed=recovery_needed,
        )
        profiled_request = replace(
            request,
            provider_policy={
                **dict(request.provider_policy),
                "_resolved_profile": profile.debug_payload(),
            },
        )
        rendered = self.generator.generate(profiled_request)
        path = blocking_path_decision(
            rendered.mode,
            profile,
            supported_mechanic=bool(
                result.get("mechanic_resolved")
                or result.get("supported_mechanic")
                or request.runtime_mode in {"supported_mechanic", "utility"}
            ),
            recovery_needed=recovery_needed,
            cache_hit=bool(request.provider_policy.get("cache_hit")),
        )
        metadata = {
            **dict(rendered.metadata),
            "response_profile": profile.debug_payload(),
            "ignored_runtime_profile_overrides": list(ignored),
            "profile_resolved_before_generation": True,
            "blocking_path": {
                "action": path.action,
                "reason": path.reason,
                "use_provider": path.use_provider,
                "use_hermes": path.use_hermes,
                "cacheable": path.cacheable,
                "blocking_budget_ms": path.blocking_budget_ms,
            },
            "validation_complete": True,
        }
        return replace(rendered, metadata=metadata)

    @staticmethod
    def _request_mode(request) -> ResponseMode:
        result = _mapping(request.authoritative_turn_result)
        resolved = _mapping(
            result.get("resolved_result")
            or result.get("resolved_action")
            or result.get("result")
        )
        return coerce_response_mode(
            result.get("response_mode")
            or resolved.get("response_mode")
            or result.get("semantic_family")
            or resolved.get("semantic_family")
            or result.get("action_type")
            or resolved.get("action_type"),
            ResponseMode.RECOVERY
            if result.get("resolver_status")
            in {"unresolved", "partial", "unsupported", "no_match"}
            else ResponseMode.ACTION,
        )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}
