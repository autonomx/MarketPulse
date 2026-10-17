"""Resolve the model/reasoning configuration that Pi actually receives."""
from __future__ import annotations

from .exception_logging import log_recovered_exception

from app.config.env import env_str, environment

import os
from typing import Any

from .contracts import AgentRunSpec, ModelRef


_DISABLED = {"", "none", "off", "disabled"}


def _provider_reasoning_effort(provider_id: str) -> str | None:
    try:
        from app.providers.service import get_provider

        provider = get_provider(str(provider_id or "").removeprefix("llm:"))
        config = getattr(provider, "config", None)
        extra = getattr(config, "extra_params", None)
        if isinstance(extra, dict):
            value = str(extra.get("reasoning_effort") or "").strip()
            if value:
                return value
        value = str(getattr(provider, "reasoning_effort", "") or "").strip()
        return value or None
    except Exception as exc:
        log_recovered_exception("provider reasoning capability lookup", exc, level="DEBUG")
        return None


def resolve_model_ref(model: ModelRef) -> ModelRef:
    """Resolve reasoning without silently downgrading Chat-selected settings.

    Precedence: explicit operator override, explicit non-disabled RunSpec value,
    selected provider configuration, then medium.  An explicit operator value of
    `none/off/disabled` is honored as an intentional worker override.
    """
    requested = str(model.reasoning_effort or "").strip()
    operator = str(environment().get("OMNIX_AGENT_REASONING_EFFORT", "")).strip()
    provider = _provider_reasoning_effort(model.provider_id)

    if operator:
        resolved = operator
        source = "operator_override"
    elif requested.casefold() not in _DISABLED:
        resolved = requested
        source = "run_spec"
    elif provider:
        resolved = provider
        source = "provider_settings"
    elif requested:
        # Preserve an explicitly disabled setting only when no richer selected
        # provider configuration exists. This keeps API callers able to request
        # no reasoning while fixing Chat's historical synthetic `none` default.
        resolved = requested
        source = "run_spec_disabled"
    else:
        resolved = "medium"
        source = "safe_default"

    parameters: dict[str, Any] = dict(model.parameters)
    parameters.update(
        {
            "requested_provider_id": model.provider_id,
            "resolved_provider_id": model.provider_id,
            "requested_model_id": model.model_id,
            "resolved_model_id": model.model_id,
            "requested_reasoning_effort": requested or None,
            "resolved_reasoning_effort": resolved,
            "reasoning_effort_source": source,
        }
    )
    return model.model_copy(
        update={
            "reasoning_effort": resolved,
            "parameters": parameters,
        }
    )


def resolve_run_model_fidelity(spec: AgentRunSpec) -> AgentRunSpec:
    return spec.model_copy(update={"model": resolve_model_ref(spec.model)})
