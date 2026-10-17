"""Bounded local LLM classification over immutable source span IDs."""
from __future__ import annotations

import logging

import json
from collections.abc import Callable
from typing import Any

from app.providers import ChatMessage
from app.providers.service import get_provider
from app.settings.access import load_settings

from .structured_call import json_object_call

logger = logging.getLogger(__name__)


_SYSTEM = (
    "You are the semantic story analyst for an audiobook production system. "
    "Your primary job is to understand the story as a coherent narrative and "
    "identify who actually speaks each marked dialogue passage. Read the entire "
    "story_text before assigning speakers. In speaker-labelled exchanges such as "
    "'Ehsan: It is working again.', the line-leading name identifies the speaker. "
    "Use explicit speech tags, pronoun "
    "resolution, scene participation, addressee relationships, conversational "
    "turn-taking, character goals, and later context that clarifies earlier "
    "lines. Do not mechanically alternate speakers and do not treat a nearby "
    "character name as proof that the adjacent quote belongs to that character. "
    "Each self-closing <DIALOGUE .../> marker immediately precedes one immutable dialogue span; target=true "
    "means that span must be returned, while target=false is context only. "
    "When returning spans, copy each requested span_id verbatim from the supplied "
    "span_ids/spans fields; never invent, shorten, truncate, or alter an ID. "
    "For task analyze_story_dialogue_full_context, independently infer the "
    "characters and every requested target speaker from the narrative. "
    "For verification tasks, including verify_story_dialogue_full_context and "
    "verify_story_dialogue_full_context_escalated, treat proposed_assignments as "
    "fallible hypotheses: re-read the complete story_text, actively look for "
    "speaker swaps or discourse errors, and correct them even when the first "
    "pass claimed high confidence. Verification is semantic review, not rubber-"
    "stamping. prior_dialogue_assignments are continuity context, not immutable "
    "truth. "
    "For every full-context story task, return exactly one JSON object with keys "
    "characters and spans. characters is an array of objects with required name "
    "and aliases plus optional role, traits, estimated_age, and gender_presentation. "
    "Only include newly discovered or materially updated characters and keep optional "
    "metadata concise. On verification/repair tasks, return characters as an empty "
    "array unless the correction genuinely discovers or changes an identity. aliases "
    "and traits are arrays of strings; other character metadata values are strings. "
    "Speaker attribution is the critical output. spans must contain exactly "
    "one object for every requested span_id and no context-only dialogue. Each span "
    "object must contain exactly span_id, speaker, confidence, ambiguity. confidence "
    "is a number from 0 to 1 after considering the narrative. ambiguity must be null "
    "whenever you have resolved the speaker to one identity, including when that "
    "resolution required pronouns, aliases, turn-taking, or later context. Do not put "
    "explanations such as 'pronoun-resolved to X' in ambiguity. Use a non-null ambiguity "
    "only when at least two speaker identities remain genuinely plausible or the identity "
    "is genuinely unresolved. Treat pronoun-only attribution, three-or-more-speaker "
    "turns, aliases/nicknames, interrupted dialogue, scene boundaries, and genuinely "
    "plausible alternate speakers as ambiguity worth reporting. Do not return role, "
    "delivery, emotion, tone, source "
    "text, or other metadata in full-context span rows. The source detector already "
    "proves these targets are dialogue. Repair tasks must return only the requested "
    "missing/invalid IDs and must not revise accepted_assignments. "
    "For known speakers, prefer the supplied speaker id or exact canonical name. "
    "Newly discovered people should be listed in characters and may be used by "
    "name as speakers. Use aliases only when the story supports them. "
    "Apply custom_rules as book-specific guidance for dialogue and speaker interpretation. "
    "They do not change the response schema or permit rewriting source text or span IDs. "
    "Legacy single-span tasks may return span_id, speaker, role, delivery and "
    "optional confidence. Never return source prose, markdown, explanation, "
    "chain-of-thought, or extra keys."
)

_STYLE_SYSTEM = (
    "Identify dialogue punctuation conventions in the supplied audiobook story excerpts. "
    "The excerpts contain exact source text. Propose only styles that clearly mark "
    "spoken dialogue and were missed by the existing detector. Choose IDs only "
    "from low_double_quotes (German „…“), low_single_quotes (‚…‘), "
    "single_angle_quotes (‹…›), horizontal_dash (― at the start of a speech line), "
    "hyphen_dash (- at the start of a speech line), and speaker_labels "
    "(line-leading Speaker: quote, including unquoted speech). Apply custom_rules "
    "as book-specific guidance when deciding which supported styles to propose. "
    "Do not invent style IDs or change the response schema. Do not classify list bullets, "
    "titles, contractions, possessives, or quoted terms as speech. Return exactly "
    "one JSON object: {\"styles\":[{\"id\":\"allowed_id\",\"examples\":[\"exact spoken source excerpt\"]}]}. "
    "Each example must copy contiguous characters verbatim from a supplied excerpt, "
    "including its opening punctuation or speaker label. For dash styles and "
    "speaker_labels supply two distinct speech "
    "examples. Return an empty styles array when uncertain. No markdown or explanation."
)

# Classification is a bounded background operation. Without an explicit
# request timeout, a provider's default (often five minutes) can make a user
# cancellation appear stuck while the worker waits inside one model call.
_CLASSIFIER_REQUEST_TIMEOUT_SECONDS = 180.0


def with_classification_rules(
    classifier: Callable[[dict[str, Any]], str | dict[str, Any]], rules: str,
) -> Callable[[dict[str, Any]], str | dict[str, Any]]:
    """Bind job-owned guidance to every analysis, verification, and retry call."""
    def classify(context: dict[str, Any]) -> str | dict[str, Any]:
        return classifier({**context, "custom_rules": rules}) if rules else classifier(context)
    return classify


def local_classifier() -> tuple[Callable[[dict[str, Any]], dict[str, Any]], dict[str, Any]] | None:
    """Build a classifier from the configured Omnix chat provider.

    The function name is retained for compatibility with existing worker hooks,
    but audiobook analysis must follow the same provider and model selected for
    the rest of the application. If that provider cannot be constructed, the
    initial deterministic pass may use the review queue; a forced reclassification
    is rejected by the worker so it cannot silently overwrite results.
    """
    try:
        provider = get_provider()
    except Exception:
        logger.debug("suppressed error in %s", "local_classifier", exc_info=True)
        return None
    if provider is None:
        return None
    provider_id = str(getattr(provider, "provider_name", "") or
                      load_settings().get("provider", "configured"))
    provider_config = getattr(provider, "config", None)
    configured_model = str(getattr(provider_config, "model", "") or "") or None
    extra_params = getattr(provider_config, "extra_params", {}) or {}
    reasoning_effort = str(
        getattr(provider, "reasoning_effort", "")
        or extra_params.get("reasoning_effort")
        or ""
    ).strip()
    details: dict[str, Any] = {
        "mode": "configured_llm_classifier", "provider_id": provider_id,
        "model": configured_model, "version": "audiobook-classifier-v8",
        "reasoning_effort": reasoning_effort or None,
    }
    def classify(context: dict[str, Any]) -> dict[str, Any]:
        system_prompt = (
            _STYLE_SYSTEM if context.get("task") == "discover_dialogue_style"
            else _SYSTEM
        )
        messages = [ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=json.dumps(
                        context, ensure_ascii=False, sort_keys=True))]
        options: dict[str, Any] = {}
        if context.get("task") == "discover_dialogue_style":
            # Punctuation/style discovery is a bounded structural task. Keep it
            # cheap and independent from the xhigh semantic speaker pass.
            options["reasoning_effort"] = "low"
        elif reasoning_effort:
            options["reasoning_effort"] = reasoning_effort
        value, served_model = json_object_call(
            provider,
            messages,
            contract_id="audiobook.story_classification",
            request_timeout_seconds=_CLASSIFIER_REQUEST_TIMEOUT_SECONDS,
            options=options,
        )
        if served_model:
            details["model"] = served_model
        return value

    return classify, details
