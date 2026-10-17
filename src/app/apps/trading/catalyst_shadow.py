from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.providers.base import ChatMessage

from .catalyst_evidence import CatalystEvidence, CatalystShadowClassification
from .research import _call_provider, _json_payload, _provider_identity, default_research_provider


ProviderFactory = Callable[[], Any]


def generate_catalyst_shadow_classification(
    evidence: list[CatalystEvidence] | tuple[CatalystEvidence, ...],
    *,
    provider_factory: ProviderFactory = default_research_provider,
    model: str | None = None,
) -> CatalystShadowClassification:
    """Classify immutable evidence with the configured Omnix LLM in shadow mode.

    This function has no dependency on paper repositories, strategy monitors or
    order APIs. Its output cannot authorize an order.
    """
    frozen = tuple(sorted(evidence, key=lambda item: (item.published_at, item.evidence_id)))
    if not frozen:
        raise ValueError("catalyst_classification_requires_evidence")
    provider = provider_factory()
    provider_name, resolved_model = _provider_identity(provider, model)
    evidence_payload = [
        {
            "evidence_id": item.evidence_id,
            "source_type": item.source_type,
            "source_locator": item.source_locator,
            "published_at": item.published_at.isoformat(),
            "captured_at": item.captured_at.isoformat(),
            "headline": item.headline,
            "content": item.content,
            "facts": item.facts,
            "deterministic_dilution_flags": list(item.dilution_flags),
        }
        for item in frozen
    ]
    messages = [
        ChatMessage(
            role="system",
            content=(
                "You are the read-only Omnix catalyst classifier. Use only the supplied timestamped "
                "evidence. Do not predict prices, recommend trades, size positions, or authorize orders. "
                "Return exactly one JSON object with keys catalyst_class, directional_bias, novelty, "
                "dilution_risk, confidence, rationale. catalyst_class must be one of earnings, regulatory, "
                "contract_partnership, financing, corporate_action, clinical, legal, other, unknown. "
                "directional_bias: positive, negative, mixed, unknown. novelty: new, recycled, unclear. "
                "dilution_risk: none_seen, possible, explicit, unknown. confidence is 0..1. No markdown."
            ),
        ),
        ChatMessage(
            role="user",
            content=json.dumps(
                {"evidence": evidence_payload},
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            ),
        ),
    ]
    payload = _json_payload(_call_provider(provider, messages, model))
    expected_keys = {
        "catalyst_class",
        "directional_bias",
        "novelty",
        "dilution_risk",
        "confidence",
        "rationale",
    }
    if set(payload) != expected_keys:
        raise ValueError("catalyst_classifier_output_keys_invalid")
    return CatalystShadowClassification.model_validate(
        {
            "classifier_id": provider_name,
            "classifier_version": resolved_model,
            **payload,
            "evidence_ids": [item.evidence_id for item in frozen],
            "shadow_only": True,
        }
    )
