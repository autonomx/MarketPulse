"""Story/dialogue interpretation over immutable source span IDs."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from .models import SourceSpan
from .classification_logging import classification_log


_NAMESPACE = UUID("3479391a-d9f4-4ecb-bce0-210e03a3334f")
_ROLES = {"narration", "dialogue", "heading", "other"}
_RESPONSE_FIELDS = {"span_id", "speaker", "role", "delivery"}
_BATCH_RESPONSE_FIELDS = {"characters", "spans"}
_BATCH_SPAN_FIELDS = {"span_id", "speaker", "role", "delivery", "confidence"}
_ATTRIBUTION_BATCH_SPAN_FIELDS = {"span_id", "speaker", "confidence", "ambiguity"}
_CHARACTER_REQUIRED_FIELDS = {"name", "aliases"}
_CHARACTER_OPTIONAL_FIELDS = {
    "role", "traits", "estimated_age", "gender_presentation",
}
_LOW_CONFIDENCE_REVIEW_THRESHOLD = 0.75
_PROVISIONAL_SPEAKER_REVIEW_THRESHOLD = 0.90
_ANALYSIS_CONTRACT_VERSION = "audiobook-analysis-contract-v4"
_FULL_STORY_MAX_CHARS = 80_000
_FULL_STORY_CONTEXT_CHARS = 12_000
_MAX_DIALOGUE_TARGETS_PER_WINDOW = 24
_CONTINUITY_ASSIGNMENT_LIMIT = 12
_VERIFICATION_CONFIDENCE_THRESHOLD = 0.95
_VERIFICATION_POLICY_VERSION = "audiobook-verification-policy-v5"
_VERIFICATION_AUDIT_PERCENT = 3
_VERIFICATION_AUDIT_SCOPE = "window"
_VERIFICATION_SCENE_CONTEXT_CHARS = 6_000
_VERIFICATION_SCENE_MAX_CHARS = 20_000
_ATTRIBUTION_VERBS = (
    "said", "asked", "replied", "answered", "shouted", "yelled", "whispered",
    "muttered", "murmured", "cried", "called", "snapped", "growled", "hissed",
    "sighed", "added", "continued", "insisted", "warned", "ordered", "demanded",
    "exclaimed", "remarked", "responded",
)
_ATTRIBUTION_VERB_RE = "|".join(re.escape(item) for item in _ATTRIBUTION_VERBS)


def _log_classification_event(
    event: str, log_context: Mapping[str, Any] | None = None, **details: Any,
) -> None:
    fields = dict(log_context or {})
    fields.update(details)
    classification_log(event, **fields)


class _LegacyBatchContract(ValueError):
    """Signal that a classifier only supports the pre-v3 single-span contract."""


class _PartialBatchContract(ValueError):
    """Carry usable attribution rows when only a subset needs regeneration."""

    def __init__(
        self,
        parsed: list[dict[str, Any]],
        discovered: list["DiscoveredSpeaker"],
        missing_span_ids: list[str],
    ) -> None:
        super().__init__("batch classification is partially usable")
        self.parsed = parsed
        self.discovered = discovered
        self.missing_span_ids = missing_span_ids


@dataclass(frozen=True, slots=True)
class Speaker:
    id: str
    canonical_name: str
    kind: str = "character"
    status: str = "active"


@dataclass(frozen=True, slots=True)
class SpeakerAlias:
    alias: str
    speaker_id: str
    status: str = "proposed"


@dataclass(frozen=True, slots=True)
class DiscoveredSpeaker:
    canonical_name: str
    aliases: tuple[str, ...] = ()
    role: str = ""
    traits: tuple[str, ...] = ()
    estimated_age: str = ""
    gender_presentation: str = ""


@dataclass(frozen=True, slots=True)
class SpanAnnotation:
    span_id: str
    role: str
    speaker_id: str
    speaker_candidate: str | None
    delivery: str
    review_reason: str | None
    evidence: dict[str, Any]
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class BatchAnalysis:
    annotations: tuple[SpanAnnotation, ...]
    discovered_speakers: tuple[DiscoveredSpeaker, ...]


def narrator_id(project_id: str) -> str:
    return str(uuid5(_NAMESPACE, f"{project_id}:narrator"))


def normalize_speaker_name(label: str) -> str:
    """Return one Unicode/whitespace/case normalized identity form."""
    normalized = unicodedata.normalize("NFKC", label)
    return " ".join(normalized.strip().split()).casefold()


def display_speaker_name(label: str) -> str:
    """Collapse presentation whitespace without changing the user's spelling."""
    return " ".join(unicodedata.normalize("NFKC", label).strip().split())


def proposed_speaker_id(project_id: str, label: str) -> str:
    """Derive an idempotent UUID for an unconfirmed detected speaker."""
    normalized = normalize_speaker_name(label)
    if not normalized:
        raise ValueError("speaker candidate is required")
    return str(uuid5(_NAMESPACE, f"{project_id}:proposed-speaker:{normalized}"))


def resolve_speaker(
    label: str, speakers: Sequence[Speaker], aliases: Sequence[SpeakerAlias],
    *, allow_proposed: bool = False,
) -> str | None:
    """Resolve a canonical identity without silently merging ambiguous names.

    User-facing resolution remains active/confirmed-only by default. Classifier
    interpretation may opt into proposed canonical identities and aliases so a
    high-confidence discovery can retain its stable speaker id before the user
    assigns a voice or explicitly confirms it.
    """
    normalized = normalize_speaker_name(label)
    if not normalized:
        return None
    speaker_statuses = {"active", "proposed"} if allow_proposed else {"active"}
    alias_statuses = {"confirmed", "proposed"} if allow_proposed else {"confirmed"}
    direct = {
        speaker.id for speaker in speakers
        if speaker.status in speaker_statuses
        and normalize_speaker_name(speaker.id) == normalized
    }
    matched = {
        speaker.id for speaker in speakers
        if speaker.status in speaker_statuses
        and normalize_speaker_name(speaker.canonical_name) == normalized
    }
    matched.update(direct)
    matched.update(
        alias.speaker_id
        for alias in aliases
        if alias.status in alias_statuses
        and normalize_speaker_name(alias.alias) == normalized
        and any(
            speaker.id == alias.speaker_id and speaker.status in speaker_statuses
            for speaker in speakers
        )
    )
    return next(iter(matched)) if len(matched) == 1 else None


def _load_payload(value: str | dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(value) if isinstance(value, str) else value
    if not isinstance(payload, dict):
        raise ValueError("classification response must be an object")
    return payload


def _parse_classification(value: str | dict[str, Any], span_id: str) -> dict[str, Any]:
    payload = _load_payload(value)
    fields = set(payload)
    if fields != _RESPONSE_FIELDS and fields != (_RESPONSE_FIELDS | {"confidence"}):
        raise ValueError(
            "classification must contain span_id, speaker, role, delivery and optional confidence"
        )
    if payload.get("span_id") != span_id or payload.get("role") not in _ROLES:
        raise ValueError("classification span ID or role is invalid")
    if not all(isinstance(payload.get(key), str) for key in _RESPONSE_FIELDS):
        raise ValueError("classification fields must be strings")
    confidence = payload.get("confidence", 1.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("classification confidence must be numeric")
    confidence = float(confidence)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("classification confidence must be between zero and one")
    return {**payload, "confidence": confidence}


def _parse_batch_classification(
    value: str | dict[str, Any], expected_span_ids: Sequence[str],
    *, full_story_dialogue: bool = False, allow_partial: bool = False,
) -> tuple[list[dict[str, Any]], list[DiscoveredSpeaker]]:
    """Parse a batch while salvaging schema-only mistakes in full-story mode.

    Full-story targets are already deterministically known dialogue spans. The
    model remains the sole authority for speaker attribution, while code may
    repair structural response-shape mistakes without changing the speaker.
    """
    payload = _load_payload(value)
    payload_fields = set(payload)
    if payload_fields == _RESPONSE_FIELDS or payload_fields == (_RESPONSE_FIELDS | {"confidence"}):
        if len(expected_span_ids) != 1:
            raise _LegacyBatchContract(
                "legacy classification cannot cover a multi-span batch"
            )
        return [_parse_classification(payload, expected_span_ids[0])], []

    if (
        payload_fields != _BATCH_RESPONSE_FIELDS
        and not (
            full_story_dialogue
            and _BATCH_RESPONSE_FIELDS.issubset(payload_fields)
        )
    ):
        raise ValueError("batch classification must contain characters and spans")
    raw_characters = payload["characters"]
    raw_spans = payload["spans"]
    if not isinstance(raw_spans, list):
        raise ValueError("batch spans must be an array")
    if not isinstance(raw_characters, list):
        if full_story_dialogue:
            raw_characters = []
        else:
            raise ValueError("batch characters must be an array")

    discovered: list[DiscoveredSpeaker] = []
    seen_characters: set[str] = set()
    for item in raw_characters:
        try:
            if not isinstance(item, dict):
                raise ValueError("character discovery must be an object")
            fields = set(item)
            if (
                not _CHARACTER_REQUIRED_FIELDS.issubset(fields)
                or not fields.issubset(_CHARACTER_REQUIRED_FIELDS | _CHARACTER_OPTIONAL_FIELDS)
            ):
                raise ValueError("character discovery fields are invalid")
            name = item.get("name")
            aliases = item.get("aliases")
            traits = item.get("traits", [])
            if (
                not isinstance(name, str)
                or not isinstance(aliases, list)
                or not all(isinstance(alias, str) for alias in aliases)
                or not isinstance(traits, list)
                or not all(isinstance(trait, str) for trait in traits)
            ):
                raise ValueError("character discovery fields are invalid")
            for optional in ("role", "estimated_age", "gender_presentation"):
                if optional in item and not isinstance(item[optional], str):
                    raise ValueError("character discovery metadata must be strings")
            display_name = display_speaker_name(name)
            normalized = normalize_speaker_name(display_name)
            if not normalized or normalized in seen_characters:
                continue
            seen_characters.add(normalized)
            clean_aliases: list[str] = []
            seen_aliases = {normalized}
            for alias in aliases:
                clean = display_speaker_name(alias)
                key = normalize_speaker_name(clean)
                if clean and key not in seen_aliases:
                    seen_aliases.add(key)
                    clean_aliases.append(clean)
            clean_traits = tuple(
                dict.fromkeys(
                    display_speaker_name(trait)
                    for trait in traits
                    if display_speaker_name(trait)
                )
            )
            discovered.append(DiscoveredSpeaker(
                display_name,
                tuple(clean_aliases),
                display_speaker_name(str(item.get("role", ""))),
                clean_traits,
                display_speaker_name(str(item.get("estimated_age", ""))),
                display_speaker_name(str(item.get("gender_presentation", ""))),
            ))
        except ValueError:
            if not full_story_dialogue:
                raise
            continue

    parsed_spans: list[dict[str, Any]] = []
    seen_span_ids: set[str] = set()
    expected = set(expected_span_ids)
    span_id_repairs: dict[str, str] = {}
    if full_story_dialogue:
        returned_ids = [
            item.get("span_id")
            for item in raw_spans
            if isinstance(item, dict) and isinstance(item.get("span_id"), str)
        ]
        returned_counts = Counter(returned_ids)
        missing_ids = [
            span_id for span_id in expected_span_ids
            if span_id not in returned_counts
        ]
        unexpected_ids = [
            span_id for span_id in returned_counts
            if span_id not in expected
        ]
        if (
            len(missing_ids) == 1
            and len(unexpected_ids) == 1
            and returned_counts[unexpected_ids[0]] == 1
            and _span_ids_one_edit_apart(unexpected_ids[0], missing_ids[0])
        ):
            wrong_id = unexpected_ids[0]
            corrected_id = missing_ids[0]
            repaired_rows: list[Any] = []
            for row in raw_spans:
                if isinstance(row, dict) and row.get("span_id") == wrong_id:
                    repaired = dict(row)
                    repaired["span_id"] = corrected_id
                    repaired_rows.append(repaired)
                    span_id_repairs[corrected_id] = wrong_id
                else:
                    repaired_rows.append(row)
            raw_spans = repaired_rows

    for item in raw_spans:
        try:
            if not isinstance(item, dict):
                raise ValueError("batch span must be an object")
            fields = set(item)
            has_attribution_core = {"span_id", "speaker", "confidence"}.issubset(fields)
            has_legacy_shape = {"role", "delivery"}.issubset(fields)
            is_legacy_batch = (
                fields == _BATCH_SPAN_FIELDS
                or (full_story_dialogue and has_attribution_core and has_legacy_shape)
            )
            is_attribution = (
                fields == _ATTRIBUTION_BATCH_SPAN_FIELDS
                or (
                    full_story_dialogue
                    and has_attribution_core
                    and not has_legacy_shape
                )
            )
            if not is_attribution and not is_legacy_batch:
                raise ValueError("batch span fields are invalid")

            span_id = item.get("span_id")
            if (
                not isinstance(span_id, str)
                or span_id not in expected
                or span_id in seen_span_ids
            ):
                raise ValueError("batch span ID is missing, duplicated, or unexpected")
            speaker = item.get("speaker")
            if not isinstance(speaker, str) or not speaker.strip():
                raise ValueError("batch speaker must be a non-empty string")
            confidence = item.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise ValueError("batch confidence must be numeric")
            confidence = float(confidence)
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("batch confidence must be between zero and one")

            schema_repair: str | None = None
            ambiguity: str | None = None
            if is_attribution:
                raw_ambiguity = item.get("ambiguity")
                if raw_ambiguity is not None and not isinstance(raw_ambiguity, str):
                    raise ValueError("batch ambiguity must be a string or null")
                ambiguity = display_speaker_name(raw_ambiguity or "") or None
                role = "dialogue" if full_story_dialogue else "other"
                delivery = ""
            else:
                raw_role = item.get("role")
                raw_delivery = item.get("delivery")
                if not isinstance(raw_role, str) or not isinstance(raw_delivery, str):
                    raise ValueError("batch role and delivery must be strings")
                role = raw_role
                delivery = raw_delivery
                if full_story_dialogue:
                    if raw_delivery == "dialogue" and raw_role != "dialogue":
                        role = "dialogue"
                        delivery = raw_role
                        schema_repair = "swapped_role_delivery"
                    elif raw_role != "dialogue":
                        role = "dialogue"
                        delivery = raw_delivery if raw_delivery != "dialogue" else ""
                        if not delivery and raw_role not in _ROLES:
                            delivery = raw_role
                        schema_repair = "forced_dialogue_role"
                elif role not in _ROLES:
                    raise ValueError("batch span role is invalid")

            seen_span_ids.add(span_id)
            parsed_spans.append({
                "span_id": span_id,
                "speaker": speaker,
                "role": role,
                "delivery": delivery,
                "confidence": confidence,
                "ambiguity": ambiguity,
                "_schema_repair": schema_repair,
                "_span_id_repair": span_id_repairs.get(span_id),
            })
        except ValueError:
            if not (full_story_dialogue and allow_partial):
                raise
            continue

    missing = [span_id for span_id in expected_span_ids if span_id not in seen_span_ids]
    if missing:
        if full_story_dialogue and allow_partial and parsed_spans:
            raise _PartialBatchContract(parsed_spans, discovered, missing)
        raise ValueError("batch classification must cover every requested span exactly once")
    return parsed_spans, discovered


def _span_ids_one_edit_apart(returned: str, expected: str) -> bool:
    """Allow only one deterministic typo repair for opaque immutable IDs."""
    if not returned.startswith("ab:sp:") or not expected.startswith("ab:sp:"):
        return False
    if abs(len(returned) - len(expected)) > 1:
        return False
    if len(returned) == len(expected):
        return sum(left != right for left, right in zip(returned, expected)) == 1
    shorter, longer = (
        (returned, expected) if len(returned) < len(expected)
        else (expected, returned)
    )
    short_index = 0
    long_index = 0
    edits = 0
    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
            long_index += 1
            continue
        edits += 1
        if edits > 1:
            return False
        long_index += 1
    return True


def _is_semantic_ambiguity(value: str) -> bool:
    """Return true only when the classifier still has unresolved alternatives."""
    normalized = normalize_speaker_name(value)
    if not normalized or normalized in {
        "none", "no", "clear", "unambiguous", "null",
    }:
        return False
    compact = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    if (
        compact.startswith("pronoun resolved to ")
        or compact.startswith("resolved to ")
        or compact.startswith("pronoun resolved as ")
        or compact.startswith("resolved as ")
    ):
        return False
    return True


def _is_ambiguous_speaker_identity(label: str) -> bool:
    normalized = normalize_speaker_name(label)
    return (
        normalized in {"unknown", "someone", "somebody", "unknown speaker", "crowd member"}
        or normalized.startswith("unknown ")
        or normalized.startswith("unnamed ")
    )


def _direct_attribution_evidence(
    before_text: str, after_text: str, speakers: Sequence[Speaker],
    aliases: Sequence[SpeakerAlias],
) -> tuple[list[str], list[str]]:
    """Return directly attached named speech tags as ids and canonical names."""
    label_rows: list[tuple[str, str, str]] = []
    speaker_by_id = {
        speaker.id: speaker
        for speaker in speakers
        if speaker.status in {"active", "proposed"}
    }
    for speaker in speaker_by_id.values():
        label_rows.append((speaker.canonical_name, speaker.id, speaker.canonical_name))
    for alias in aliases:
        speaker = speaker_by_id.get(alias.speaker_id)
        if speaker is not None and alias.status in {"confirmed", "proposed"}:
            label_rows.append((alias.alias, speaker.id, speaker.canonical_name))

    found_ids: list[str] = []
    found_names: list[str] = []
    before = before_text[-240:]
    after = after_text[:240]
    for label, speaker_id, canonical_name in label_rows:
        escaped = re.escape(label)
        before_patterns = (
            rf"{escaped}\s+(?:{_ATTRIBUTION_VERB_RE})\b[^.!?\n]{{0,80}}[,;:\-—]?\s*$",
            rf"(?:{_ATTRIBUTION_VERB_RE})\s+{escaped}\b[^.!?\n]{{0,80}}[,;:\-—]?\s*$",
        )
        after_patterns = (
            rf"^\s*[,;:\-—]?\s*{escaped}\s+(?:{_ATTRIBUTION_VERB_RE})\b",
            rf"^\s*[,;:\-—]?\s*(?:{_ATTRIBUTION_VERB_RE})\s+{escaped}\b",
        )
        if (
            any(re.search(pattern, before, re.I) for pattern in before_patterns)
            or any(re.search(pattern, after, re.I) for pattern in after_patterns)
        ):
            if speaker_id not in found_ids:
                found_ids.append(speaker_id)
                found_names.append(canonical_name)
    return found_ids, found_names


def _annotation_from_payload(
    *, project_id: str, span: SourceSpan, payload: dict[str, Any],
    speakers: Sequence[Speaker], aliases: Sequence[SpeakerAlias],
    evidence_text: str, before_text: str = "", after_text: str = "",
    apply_deterministic_attribution: bool = True,
    evidence_extra: dict[str, Any] | None = None,
    review_reason_override: str | None = None,
) -> SpanAnnotation:
    narrator = narrator_id(project_id)
    raw_label = display_speaker_name(str(payload["speaker"]))
    id_match = next(
        (
            speaker for speaker in speakers
            if normalize_speaker_name(speaker.id) == normalize_speaker_name(raw_label)
        ),
        None,
    )
    label = id_match.canonical_name if id_match is not None else raw_label
    role = str(payload["role"])
    delivery = str(payload["delivery"])
    confidence = float(payload.get("confidence", 1.0))
    if role == "dialogue" and span.structural_kind != "dialogue":
        return SpanAnnotation(
            span.id, span.structural_kind, narrator, label or None, delivery,
            "STRUCTURE_UNCERTAIN",
            {"classifier_role": role, "structural_kind": span.structural_kind,
             "confidence": confidence},
            confidence,
        )
    if span.structural_kind == "dialogue" and role != "dialogue":
        return SpanAnnotation(
            span.id, span.structural_kind, narrator, label or None, delivery,
            "STRUCTURE_UNCERTAIN",
            {"classifier_role": role, "structural_kind": span.structural_kind,
             "confidence": confidence},
            confidence,
        )

    resolved = (
        narrator if role != "dialogue" or not label
        else resolve_speaker(raw_label, speakers, aliases, allow_proposed=True)
    )
    direct_ids: list[str] = []
    direct_names: list[str] = []
    reason: str | None = None
    attribution_override = False

    if apply_deterministic_attribution:
        direct_ids, direct_names = _direct_attribution_evidence(
            before_text, after_text, speakers, aliases,
        )

    if apply_deterministic_attribution and role == "dialogue" and len(direct_ids) == 1:
        direct_id = direct_ids[0]
        direct_speaker = next(
            (speaker for speaker in speakers if speaker.id == direct_id),
            None,
        )
        if resolved is not None and resolved != direct_id:
            reason = "ATTRIBUTION_CONTRADICTION"
            attribution_override = True
        resolved = direct_id
        if direct_speaker is not None:
            label = direct_speaker.canonical_name

    speaker_id = resolved
    if role == "dialogue" and speaker_id is None:
        speaker_id = narrator
        reason = reason or "UNSUPPORTED_SPEAKER"
    elif role == "dialogue" and speaker_id == narrator:
        reason = reason or "NARRATOR_DIALOGUE_UNCERTAIN"
    elif role == "dialogue":
        matched_speaker = next(
            (speaker for speaker in speakers if speaker.id == speaker_id),
            None,
        )
        if matched_speaker is not None and matched_speaker.status == "proposed":
            if _is_ambiguous_speaker_identity(matched_speaker.canonical_name):
                reason = reason or "AMBIGUOUS_SPEAKER_IDENTITY"
            elif confidence < _PROVISIONAL_SPEAKER_REVIEW_THRESHOLD:
                reason = reason or "LOW_CONFIDENCE_SPEAKER"
        elif confidence < _LOW_CONFIDENCE_REVIEW_THRESHOLD:
            reason = reason or "LOW_CONFIDENCE_SPEAKER"

    if (
        apply_deterministic_attribution
        and role == "dialogue"
        and len(direct_ids) > 1
        and speaker_id not in set(direct_ids)
    ):
        reason = "ATTRIBUTION_CONTRADICTION"
    if review_reason_override is not None and reason is None:
        reason = review_reason_override

    evidence = {
        "attribution_names": direct_names,
        "attribution_override": attribution_override,
        "classifier_speaker": raw_label,
        "classifier_span_id": span.id,
        "confidence": confidence,
    }
    if evidence_extra:
        evidence.update(evidence_extra)
    return SpanAnnotation(
        span.id, role, speaker_id, label or None, delivery, reason,
        evidence, confidence,
    )


def annotate_spans(
    *, project_id: str, spans: Sequence[SourceSpan], speakers: Sequence[Speaker],
    aliases: Sequence[SpeakerAlias] = (),
    classifier: Callable[[dict[str, Any]], str | dict[str, Any]],
    context_window: int = 2,
    log_context: Mapping[str, Any] | None = None,
) -> tuple[SpanAnnotation, ...]:
    """Legacy single-span classifier path retained for compatibility/tests."""
    narrator = narrator_id(project_id)
    result: list[SpanAnnotation] = []
    _log_classification_event(
        "classification_legacy_started",
        log_context,
        project_id=project_id,
        span_count=len(spans),
        context_window=context_window,
        speaker_roster=[
            {"id": speaker.id, "name": speaker.canonical_name, "status": speaker.status}
            for speaker in speakers
        ],
    )
    for index, span in enumerate(spans):
        context = {
            "span_id": span.id,
            "source_text": span.source_text,
            "before": [item.source_text for item in spans[max(0, index - context_window):index]],
            "after": [item.source_text for item in spans[index + 1:index + 1 + context_window]],
            "speaker_roster": [
                {"id": speaker.id, "name": speaker.canonical_name, "status": speaker.status}
                for speaker in speakers
            ],
            "task": "classify_only_no_source_text_in_response",
        }
        raw_result: object = None
        _log_classification_event(
            "classification_request",
            log_context,
            project_id=project_id,
            mode="single",
            attempt="initial",
            span_id=span.id,
            chapter_id=span.chapter_id,
            structural_kind=span.structural_kind,
            request=context,
        )
        try:
            raw_result = classifier(context)
            _log_classification_event(
                "classification_response",
                log_context,
                project_id=project_id,
                mode="single",
                attempt="initial",
                span_id=span.id,
                raw_response=raw_result,
            )
            payload = _parse_classification(raw_result, span.id)
            _log_classification_event(
                "classification_parsed",
                log_context,
                project_id=project_id,
                mode="single",
                attempt="initial",
                span_id=span.id,
                parsed=payload,
            )
        except Exception as exc:
            _log_classification_event(
                "classification_parse_failed",
                log_context,
                project_id=project_id,
                mode="single",
                attempt="initial",
                span_id=span.id,
                error_type=type(exc).__name__,
                error=str(exc),
                failure_stage="parser" if raw_result is not None else "classifier_call",
                raw_response=raw_result,
            )
            retry_context = {
                **context,
                "before": [item.source_text for item in spans[max(0, index - 2 * context_window):index]],
                "after": [item.source_text for item in spans[index + 1:index + 1 + 2 * context_window]],
                "task": "retry_classification_only_no_source_text_in_response",
                "previous_error": type(exc).__name__,
            }
            retry_result: object = None
            _log_classification_event(
                "classification_request",
                log_context,
                project_id=project_id,
                mode="single",
                attempt="retry",
                span_id=span.id,
                chapter_id=span.chapter_id,
                structural_kind=span.structural_kind,
                request=retry_context,
            )
            try:
                retry_result = classifier(retry_context)
                _log_classification_event(
                    "classification_response",
                    log_context,
                    project_id=project_id,
                    mode="single",
                    attempt="retry",
                    span_id=span.id,
                    raw_response=retry_result,
                )
                payload = _parse_classification(retry_result, span.id)
                _log_classification_event(
                    "classification_parsed",
                    log_context,
                    project_id=project_id,
                    mode="single",
                    attempt="retry",
                    span_id=span.id,
                    parsed=payload,
                )
            except Exception as retry_exc:
                _log_classification_event(
                    "classification_fallback",
                    log_context,
                    project_id=project_id,
                    mode="single",
                    span_id=span.id,
                    reason="retry_failed",
                    initial_error_type=type(exc).__name__,
                    initial_error=str(exc),
                    retry_error_type=type(retry_exc).__name__,
                    retry_error=str(retry_exc),
                    retry_response=retry_result,
                    fallback_speaker_id=narrator,
                    fallback_review_reason="FALLBACK_NARRATOR",
                )
                result.append(SpanAnnotation(
                    span.id, span.structural_kind, narrator, None, "",
                    "FALLBACK_NARRATOR",
                    {"classification_error": type(exc).__name__,
                     "retry_error": type(retry_exc).__name__, "confidence": 0.0},
                    0.0,
                ))
                continue
        evidence_text = " ".join(
            context["before"][-1:] + [span.source_text] + context["after"][:1]
        )
        result.append(_annotation_from_payload(
            project_id=project_id, span=span, payload=payload,
            speakers=speakers, aliases=aliases, evidence_text=evidence_text,
            before_text=context["before"][-1] if context["before"] else "",
            after_text=context["after"][0] if context["after"] else "",
        ))
        annotation = result[-1]
        _log_classification_event(
            "classification_annotation",
            log_context,
            project_id=project_id,
            mode="single",
            span_id=span.id,
            annotation=annotation,
        )
    _log_classification_event(
        "classification_legacy_completed",
        log_context,
        project_id=project_id,
        span_count=len(result),
        review_count=sum(item.review_reason is not None for item in result),
    )
    return tuple(result)


def _speaker_roster_payload(
    speakers: Sequence[Speaker], aliases: Sequence[SpeakerAlias],
) -> list[dict[str, Any]]:
    aliases_by_speaker: dict[str, list[str]] = {}
    for alias in aliases:
        aliases_by_speaker.setdefault(alias.speaker_id, []).append(alias.alias)
    return [
        {
            "id": speaker.id,
            "name": speaker.canonical_name,
            "status": speaker.status,
            "aliases": aliases_by_speaker.get(speaker.id, []),
        }
        for speaker in speakers
    ]


def _render_marked_story(
    window_spans: Sequence[SourceSpan], target_ids: set[str],
) -> str:
    """Render immutable source text with prompt-only dialogue markers."""
    parts: list[str] = []
    for span in window_spans:
        if span.structural_kind != "dialogue":
            parts.append(span.source_text)
            continue
        target = "true" if span.id in target_ids else "false"
        parts.append(f'<DIALOGUE id="{span.id}" target="{target}"/>')
        parts.append(span.source_text)
    return "".join(parts)


def _dialogue_windows(
    spans: Sequence[SourceSpan], *, max_story_chars: int,
    dialogue_target_ids: set[str] | None = None,
) -> list[tuple[list[tuple[int, SourceSpan]], list[SourceSpan]]]:
    """Split large outputs into manageable batches with narrative context."""
    dialogue_entries = [
        (index, span)
        for index, span in enumerate(spans)
        if (
            span.structural_kind == "dialogue"
            and (
                dialogue_target_ids is None
                or span.id in dialogue_target_ids
            )
        )
    ]
    if not dialogue_entries:
        return []
    if sum(len(span.source_text) for span in spans) <= max_story_chars:
        windows = [(dialogue_entries, list(spans))]
    else:
        core_budget = max(4_000, max_story_chars - 2 * _FULL_STORY_CONTEXT_CHARS)
        windows = []
        cursor = 0
        while cursor < len(dialogue_entries):
            first_index, first_span = dialogue_entries[cursor]
            end = cursor + 1
            while end < len(dialogue_entries):
                _next_index, next_span = dialogue_entries[end]
                if next_span.end_offset - first_span.start_offset > core_budget:
                    break
                end += 1
            targets = dialogue_entries[cursor:end]
            last_index, last_span = targets[-1]
            context_start = max(0, first_span.start_offset - _FULL_STORY_CONTEXT_CHARS)
            context_end = last_span.end_offset + _FULL_STORY_CONTEXT_CHARS

            first_window_index = first_index
            while (
                first_window_index > 0
                and spans[first_window_index - 1].end_offset > context_start
            ):
                first_window_index -= 1

            last_window_index = last_index
            while (
                last_window_index + 1 < len(spans)
                and spans[last_window_index + 1].start_offset < context_end
            ):
                last_window_index += 1

            windows.append(
                (targets, list(spans[first_window_index:last_window_index + 1]))
            )
            cursor = end

    # Keep the complete narrative available as context while limiting how many
    # immutable span IDs the model must return in one response. Large response
    # sets were being truncated by provider output limits and then retried as
    # equally large repair requests.
    return [
        (targets[offset:offset + _MAX_DIALOGUE_TARGETS_PER_WINDOW], window_spans)
        for targets, window_spans in windows
        for offset in range(0, len(targets), _MAX_DIALOGUE_TARGETS_PER_WINDOW)
    ]


def _audit_selected(span_id: str, percent: int = _VERIFICATION_AUDIT_PERCENT) -> bool:
    """Return a stable audit sample for an immutable span ID."""
    if percent <= 0:
        return False
    bucket = int(hashlib.sha256(span_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return bucket < min(100, percent)


def _verification_story_spans(
    window_spans: Sequence[SourceSpan], target_ids: set[str],
) -> tuple[list[SourceSpan], str]:
    """Use local narrative context for a compact target cluster when safe."""
    targets = [span for span in window_spans if span.id in target_ids]
    if not targets:
        return list(window_spans), "full_context"
    total_chars = sum(len(span.source_text) for span in window_spans)
    first_offset = min(span.start_offset for span in targets)
    last_offset = max(span.end_offset for span in targets)
    if (
        total_chars <= _VERIFICATION_SCENE_MAX_CHARS
        or last_offset - first_offset > _VERIFICATION_SCENE_MAX_CHARS // 2
    ):
        return list(window_spans), "full_context"
    context_start = first_offset - _VERIFICATION_SCENE_CONTEXT_CHARS
    context_end = last_offset + _VERIFICATION_SCENE_CONTEXT_CHARS
    selected = [
        span for span in window_spans
        if span.end_offset > context_start and span.start_offset < context_end
    ]
    return (selected or list(window_spans)), "scene_context"



def annotate_span_batches(
    *, project_id: str, spans: Sequence[SourceSpan], speakers: Sequence[Speaker],
    aliases: Sequence[SpeakerAlias] = (),
    classifier: Callable[[dict[str, Any]], str | dict[str, Any]],
    classifier_details: dict[str, Any] | None = None,
    batch_size: int = 40, context_window: int = 3,
    log_context: Mapping[str, Any] | None = None,
    max_story_chars: int = _FULL_STORY_MAX_CHARS,
    dialogue_target_ids: set[str] | None = None,
    on_batch_complete: Callable[[Sequence[str], int, int], None] | None = None,
) -> BatchAnalysis:
    """Use full-story LLM reasoning for speaker attribution.

    Each target batch retains coherent chapter context, while response batches
    stay small enough to fit provider output limits. An independent verifier
    runs only for ambiguity/risk targets plus a stable audit sample; audit
    disagreement escalates to full-context review. Very large chapters use
    overlapping narrative windows. Deterministic code preserves source text,
    IDs, offsets, structural dialogue boundaries, and response-shape repair; it
    never decides who spoke a line.

    batch_size/context_window remain accepted only for legacy classifier hooks.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if max_story_chars < 4_000:
        raise ValueError("max_story_chars must be at least 4000")

    def classifier_runtime_evidence() -> dict[str, Any]:
        evidence: dict[str, Any] = {}
        if classifier_details:
            for key in (
                "provider_id", "model", "version", "reasoning_effort",
                "analysis_job_id",
            ):
                value = classifier_details.get(key)
                if value is not None:
                    evidence[f"classifier_{key}"] = value
        return evidence

    narrator = narrator_id(project_id)
    rolling_speakers = list(speakers)
    rolling_aliases = list(aliases)
    annotations_by_id: dict[str, SpanAnnotation] = {}
    discoveries: dict[str, DiscoveredSpeaker] = {
        normalize_speaker_name(speaker.canonical_name): DiscoveredSpeaker(
            speaker.canonical_name, (),
        )
        for speaker in rolling_speakers
        if speaker.status == "proposed"
    }

    for span in spans:
        target_dialogue = (
            span.structural_kind == "dialogue"
            and (
                dialogue_target_ids is None
                or span.id in dialogue_target_ids
            )
        )
        if not target_dialogue:
            annotations_by_id[span.id] = SpanAnnotation(
                span.id,
                span.structural_kind,
                narrator,
                None,
                "",
                None,
                {
                    "deterministic_structural_role": span.structural_kind,
                    "semantic_authority": (
                        "analysis_policy_context"
                        if span.structural_kind == "dialogue"
                        else "structure_only"
                    ),
                    "analysis_contract_version": _ANALYSIS_CONTRACT_VERSION,
                    **classifier_runtime_evidence(),
                    "confidence": 1.0,
                },
                1.0,
            )

    def merge_discovery(discovered: DiscoveredSpeaker) -> None:
        key = normalize_speaker_name(discovered.canonical_name)
        if not key or key == "narrator":
            return
        existing_speaker = next(
            (
                speaker for speaker in rolling_speakers
                if normalize_speaker_name(speaker.canonical_name) == key
            ),
            None,
        )
        if existing_speaker is None:
            confirmed_alias = next(
                (
                    alias for alias in rolling_aliases
                    if alias.status == "confirmed"
                    and normalize_speaker_name(alias.alias) == key
                ),
                None,
            )
            if confirmed_alias is not None:
                existing_speaker = next(
                    (
                        speaker for speaker in rolling_speakers
                        if speaker.id == confirmed_alias.speaker_id
                        and speaker.status == "active"
                    ),
                    None,
                )
        resolved_key = (
            normalize_speaker_name(existing_speaker.canonical_name)
            if existing_speaker is not None else key
        )
        previous = discoveries.get(resolved_key)
        if existing_speaker is not None:
            previous = previous or DiscoveredSpeaker(
                existing_speaker.canonical_name, (),
            )
            merged_discovery = DiscoveredSpeaker(
                previous.canonical_name,
                tuple(dict.fromkeys((*previous.aliases, *discovered.aliases))),
                discovered.role or previous.role,
                tuple(dict.fromkeys((*previous.traits, *discovered.traits))),
                discovered.estimated_age or previous.estimated_age,
                discovered.gender_presentation or previous.gender_presentation,
            )
            if (
                existing_speaker.status == "proposed"
                or merged_discovery.aliases
                or merged_discovery.role
                or merged_discovery.traits
                or merged_discovery.estimated_age
                or merged_discovery.gender_presentation
            ):
                discoveries[resolved_key] = merged_discovery
            known_aliases = {
                normalize_speaker_name(alias.alias)
                for alias in rolling_aliases
                if alias.speaker_id == existing_speaker.id
            }
            rolling_aliases.extend(
                SpeakerAlias(alias, existing_speaker.id, "proposed")
                for alias in discovered.aliases
                if normalize_speaker_name(alias) not in known_aliases
            )
            return

        if previous is None:
            discoveries[key] = discovered
            provisional = Speaker(
                proposed_speaker_id(project_id, discovered.canonical_name),
                discovered.canonical_name,
                "character",
                "proposed",
            )
            rolling_speakers.append(provisional)
            rolling_aliases.extend(
                SpeakerAlias(alias, provisional.id, "proposed")
                for alias in discovered.aliases
            )
            return

        discoveries[key] = DiscoveredSpeaker(
            previous.canonical_name,
            tuple(dict.fromkeys((*previous.aliases, *discovered.aliases))),
            discovered.role or previous.role,
            tuple(dict.fromkeys((*previous.traits, *discovered.traits))),
            discovered.estimated_age or previous.estimated_age,
            discovered.gender_presentation or previous.gender_presentation,
        )

    def record_unknown_candidate(annotation: SpanAnnotation) -> None:
        if (
            annotation.role == "dialogue"
            and annotation.speaker_candidate
            and annotation.speaker_id == narrator
            and normalize_speaker_name(annotation.speaker_candidate) != "narrator"
        ):
            merge_discovery(DiscoveredSpeaker(
                display_speaker_name(annotation.speaker_candidate), (),
            ))

    def run_full_story_call(
        context_payload: dict[str, Any],
        expected_ids: list[str],
        request_rows: list[dict[str, Any]],
        *,
        classifier_call: Callable[[dict[str, Any]], object],
        repair_task: str,
    ) -> tuple[list[dict[str, Any]], list[DiscoveredSpeaker]]:
        """Parse one semantic call and retry only unusable span rows first."""
        try:
            return _parse_batch_classification(
                classifier_call(context_payload),
                expected_ids,
                full_story_dialogue=True,
                allow_partial=True,
            )
        except _PartialBatchContract as partial:
            missing = partial.missing_span_ids
            missing_set = set(missing)
            repair_context = {
                **context_payload,
                "task": repair_task,
                "span_ids": missing,
                "spans": [
                    row for row in request_rows
                    if str(row.get("span_id")) in missing_set
                ],
                "accepted_assignments": [
                    {
                        "span_id": str(item["span_id"]),
                        "speaker": str(item["speaker"]),
                        "confidence": float(item["confidence"]),
                        "ambiguity": item.get("ambiguity"),
                    }
                    for item in partial.parsed
                ],
                "repair_instruction": (
                    "Return only the missing/invalid span IDs. Do not regenerate "
                    "already accepted speaker assignments."
                ),
            }
            repaired, repaired_discovered = _parse_batch_classification(
                classifier_call(repair_context),
                missing,
                full_story_dialogue=True,
                allow_partial=False,
            )
            for item in repaired:
                item["_partial_retry"] = True
            return (
                [*partial.parsed, *repaired],
                [*partial.discovered, *repaired_discovered],
            )

    continuity: list[dict[str, Any]] = []
    windows = _dialogue_windows(
        spans,
        max_story_chars=max_story_chars,
        dialogue_target_ids=dialogue_target_ids,
    )
    _log_classification_event(
        "classification_batches_started",
        log_context,
        project_id=project_id,
        chapter_id=spans[0].chapter_id if spans else None,
        span_count=len(spans),
        dialogue_span_count=sum(
            span.structural_kind == "dialogue"
            and (
                dialogue_target_ids is None
                or span.id in dialogue_target_ids
            )
            for span in spans
        ),
        batch_size=batch_size,
        context_window=context_window,
        window_count=len(windows),
        speaker_roster=_speaker_roster_payload(rolling_speakers, rolling_aliases),
    )
    for window_number, (entries, window_spans) in enumerate(windows, start=1):
        batch_number = window_number
        target_ids = [span.id for _index, span in entries]
        target_id_set = set(target_ids)
        request_spans = [
            {
                "span_id": span.id,
                "source_text": span.source_text,
                "structural_kind": span.structural_kind,
            }
            for _index, span in entries
        ]
        base_context = {
            "analysis_contract_version": _ANALYSIS_CONTRACT_VERSION,
            "span_detector_versions": sorted({
                span.detector_version for span in window_spans
            }),
            "window_number": window_number,
            "window_count": len(windows),
            "story_text": _render_marked_story(window_spans, target_id_set),
            "span_ids": target_ids,
            "spans": request_spans,
            "speaker_roster": _speaker_roster_payload(
                rolling_speakers, rolling_aliases,
            ),
            "prior_dialogue_assignments": continuity[
                -_CONTINUITY_ASSIGNMENT_LIMIT:
            ],
        }
        analysis_context = {
            **base_context,
            "task": "analyze_story_dialogue_full_context",
        }

        raw_result: object = None
        retry_result: object = None

        def logged_classifier_call(context_payload: dict[str, Any]) -> object:
            nonlocal raw_result, retry_result
            task = str(context_payload.get("task", ""))
            if task.startswith("retry"):
                attempt = "retry"
            elif "escalated" in task:
                attempt = "escalation"
            elif task.startswith("verify"):
                attempt = "verification"
            elif task.startswith("repair"):
                attempt = "repair"
            else:
                attempt = "initial"
            expected = context_payload.get("span_ids", target_ids)
            _log_classification_event(
                "classification_batch_request",
                log_context,
                project_id=project_id,
                chapter_id=entries[0][1].chapter_id if entries else None,
                mode="batch",
                batch_number=batch_number,
                attempt=attempt,
                expected_span_ids=expected,
                request=context_payload,
            )
            result = classifier(context_payload)
            if attempt == "retry":
                retry_result = result
            else:
                raw_result = result
            _log_classification_event(
                "classification_batch_response",
                log_context,
                project_id=project_id,
                chapter_id=entries[0][1].chapter_id if entries else None,
                mode="batch",
                batch_number=batch_number,
                attempt=attempt,
                raw_response=result,
            )
            return result

        try:
            parsed, discovered = run_full_story_call(
                analysis_context,
                target_ids,
                request_spans,
                classifier_call=logged_classifier_call,
                repair_task="repair_story_dialogue_missing_spans",
            )
            _log_classification_event(
                "classification_batch_parsed",
                log_context,
                project_id=project_id,
                chapter_id=entries[0][1].chapter_id if entries else None,
                mode="batch",
                batch_number=batch_number,
                attempt="initial",
                parsed_spans=parsed,
                discovered_speakers=discovered,
            )
        except (KeyError, _LegacyBatchContract):
            _log_classification_event(
                "classification_batch_legacy_contract",
                log_context,
                project_id=project_id,
                chapter_id=entries[0][1].chapter_id if entries else None,
                batch_number=batch_number,
                error_type="legacy_batch_contract",
                error="classifier did not return the v3 batch shape",
                raw_response=raw_result,
            )
            legacy = annotate_spans(
                project_id=project_id,
                spans=[span for _index, span in entries],
                speakers=rolling_speakers,
                aliases=rolling_aliases,
                classifier=classifier,
                context_window=max(1, context_window),
                log_context={
                    **dict(log_context or {}),
                    "chapter_id": entries[0][1].chapter_id if entries else None,
                    "batch_number": batch_number,
                    "parent_mode": "legacy_batch_fallback",
                },
            )
            for annotation in legacy:
                annotations_by_id[annotation.span_id] = annotation
                record_unknown_candidate(annotation)
            _log_classification_event(
                "classification_batch_completed",
                log_context,
                project_id=project_id,
                chapter_id=entries[0][1].chapter_id if entries else None,
                mode="legacy_single_span",
                batch_number=batch_number,
                annotation_count=len(legacy),
                annotations=legacy,
            )
            continuity.extend(
                {
                    "span_id": item.span_id,
                    "speaker": item.speaker_candidate or item.speaker_id,
                    "confidence": item.confidence,
                }
                for item in legacy
                if item.role == "dialogue"
            )
            if on_batch_complete is not None:
                on_batch_complete(target_ids, batch_number, len(windows))
            continue
        except Exception as exc:
            _log_classification_event(
                "classification_batch_parse_failed",
                log_context,
                project_id=project_id,
                chapter_id=entries[0][1].chapter_id if entries else None,
                mode="batch",
                batch_number=batch_number,
                attempt="initial",
                error_type=type(exc).__name__,
                error=str(exc),
                failure_stage="parser" if raw_result is not None else "classifier_call",
                raw_response=raw_result,
            )
            retry_context = {
                **analysis_context,
                "task": "retry_story_dialogue_full_context",
                "previous_error": type(exc).__name__,
            }
            try:
                parsed, discovered = run_full_story_call(
                    retry_context,
                    target_ids,
                    request_spans,
                    classifier_call=logged_classifier_call,
                    repair_task="retry_repair_story_dialogue_missing_spans",
                )
                _log_classification_event(
                    "classification_batch_parsed",
                    log_context,
                    project_id=project_id,
                    chapter_id=entries[0][1].chapter_id if entries else None,
                    mode="batch",
                    batch_number=batch_number,
                    attempt="retry",
                    parsed_spans=parsed,
                    discovered_speakers=discovered,
                )
            except Exception as retry_exc:
                _log_classification_event(
                    "classification_batch_fallback",
                    log_context,
                    project_id=project_id,
                    chapter_id=entries[0][1].chapter_id if entries else None,
                    mode="batch",
                    batch_number=batch_number,
                    reason="retry_failed",
                    initial_error_type=type(exc).__name__,
                    initial_error=str(exc),
                    initial_response=raw_result,
                    retry_error_type=type(retry_exc).__name__,
                    retry_error=str(retry_exc),
                    retry_response=retry_result,
                    expected_span_ids=target_ids,
                )
                for _global_index, span in entries:
                    annotations_by_id[span.id] = SpanAnnotation(
                        span.id,
                        span.structural_kind,
                        narrator,
                        None,
                        "",
                        "FALLBACK_NARRATOR",
                        {
                            "semantic_authority": "llm_full_story",
                            "analysis_contract_version": _ANALYSIS_CONTRACT_VERSION,
                            **classifier_runtime_evidence(),
                            "classification_error": type(exc).__name__,
                            "retry_error": type(retry_exc).__name__,
                            "confidence": 0.0,
                        },
                        0.0,
                    )
                _log_classification_event(
                    "classification_batch_completed",
                    log_context,
                    project_id=project_id,
                    chapter_id=entries[0][1].chapter_id if entries else None,
                    mode="batch",
                    batch_number=batch_number,
                    annotation_count=len(entries),
                    annotations=[annotations_by_id[span.id] for _index, span in entries],
                )
                continue

        new_discovery_names = {
            normalize_speaker_name(item.canonical_name)
            for item in discovered
        }
        for discovered_speaker in discovered:
            merge_discovery(discovered_speaker)

        initial_by_id = {str(item["span_id"]): item for item in parsed}
        verification_reasons: dict[str, list[str]] = {}
        verification_soft_signals: dict[str, list[str]] = {}
        seen_new_names: set[str] = set()
        audit_span_id: str | None = None
        if entries:
            audit_key = f"{entries[0][1].chapter_id}:{window_number}"
            if _audit_selected(audit_key):
                audit_digest = hashlib.sha256(
                    f"{audit_key}:target".encode("utf-8")
                ).hexdigest()
                audit_index = int(audit_digest[:8], 16) % len(entries)
                audit_span_id = entries[audit_index][1].id
        entry_position = {
            span.id: position
            for position, (_global_index, span) in enumerate(entries)
        }

        for global_index, span in entries:
            item = initial_by_id[span.id]
            reasons: list[str] = []
            confidence = float(item["confidence"])
            label = display_speaker_name(str(item["speaker"]))
            normalized_label = normalize_speaker_name(label)
            ambiguity = display_speaker_name(str(item.get("ambiguity") or ""))
            if confidence < _VERIFICATION_CONFIDENCE_THRESHOLD:
                reasons.append("low_confidence")
            if _is_semantic_ambiguity(ambiguity):
                reasons.append("model_ambiguity")
            if (
                normalized_label in new_discovery_names
                and normalized_label not in seen_new_names
            ):
                reasons.append("new_character")
                seen_new_names.add(normalized_label)

            before_text = spans[global_index - 1].source_text if global_index > 0 else ""
            after_text = (
                spans[global_index + 1].source_text
                if global_index + 1 < len(spans) else ""
            )
            direct_ids, _direct_names = _direct_attribution_evidence(
                before_text,
                after_text,
                rolling_speakers,
                rolling_aliases,
            )
            resolved = resolve_speaker(
                label,
                rolling_speakers,
                rolling_aliases,
                allow_proposed=True,
            )
            resolved_speaker_for_risk = next(
                (
                    speaker for speaker in rolling_speakers
                    if resolved is not None and speaker.id == resolved
                ),
                None,
            )
            identity_is_ambiguous = _is_ambiguous_speaker_identity(label)
            if resolved_speaker_for_risk is not None:
                identity_is_ambiguous = (
                    identity_is_ambiguous
                    or _is_ambiguous_speaker_identity(
                        resolved_speaker_for_risk.canonical_name
                    )
                )

            soft_reasons: list[str] = []
            if identity_is_ambiguous:
                # An unnamed identity already enters the human review queue. A
                # second model pass is useful only when the model is also unsure;
                # otherwise verification repeatedly confirms "Unknown Crowd
                # Member" without making the line more actionable.
                soft_reasons.append("ambiguous_identity")
            if len(direct_ids) == 1 and resolved is not None and resolved != direct_ids[0]:
                # Adjacent narration can introduce the *next* quote ("Orven
                # snapped, ..."), so treat this as supporting risk rather than a
                # standalone reason to spend another full LLM pass.
                soft_reasons.append("attribution_conflict")
            surrounding = f"{before_text[-180:]} {after_text[:180]}"
            if re.search(
                rf"\b(?:he|she|they)\s+(?:{_ATTRIBUTION_VERB_RE})\b",
                surrounding,
                re.I,
            ):
                soft_reasons.append("pronoun_attribution")
            if resolved is not None:
                resolved_speaker = next(
                    (speaker for speaker in rolling_speakers if speaker.id == resolved),
                    None,
                )
                if (
                    resolved_speaker is not None
                    and normalized_label
                    not in {
                        normalize_speaker_name(resolved_speaker.id),
                        normalize_speaker_name(resolved_speaker.canonical_name),
                    }
                ):
                    soft_reasons.append("alias_resolution")

            position = entry_position[span.id]
            if 0 < position < len(entries) - 1 and not direct_ids:
                previous_id = entries[position - 1][1].id
                next_id = entries[position + 1][1].id
                surrounding_speakers = {
                    normalize_speaker_name(str(initial_by_id[item_id]["speaker"]))
                    for item_id in (previous_id, span.id, next_id)
                }
                if len(surrounding_speakers) >= 3:
                    soft_reasons.append("multi_speaker_turn")

            # Three forward benchmarks showed no correction caused only by
            # these deterministic discourse heuristics. Preserve them as
            # telemetry, but do not spend another Luna/xhigh call unless a hard
            # model signal (low confidence / real ambiguity / new character)
            # already requires verification.
            if soft_reasons:
                verification_soft_signals[span.id] = list(soft_reasons)
            if reasons:
                reasons.extend(soft_reasons)

            if not reasons and span.id == audit_span_id:
                reasons.append("audit_sample")
            if reasons:
                verification_reasons[span.id] = reasons

        verification_ids = [
            span_id for span_id in target_ids if span_id in verification_reasons
        ]
        verified_by_id: dict[str, dict[str, Any]] = {}
        verification_error: str | None = None
        verification_scope = "not_required"
        verified_discovered: list[DiscoveredSpeaker] = []

        if verification_ids:
            verification_id_set = set(verification_ids)
            hard_verification_reasons = {
                "model_ambiguity",
                "new_character",
            }
            requires_full_context = any(
                any(
                    reason in hard_verification_reasons
                    for reason in verification_reasons.get(span_id, [])
                )
                for span_id in verification_ids
            )
            if requires_full_context:
                verification_story_spans = list(window_spans)
                verification_scope = "full_context"
            else:
                verification_story_spans, verification_scope = _verification_story_spans(
                    window_spans,
                    verification_id_set,
                )
            verification_request_spans = [
                row for row in request_spans
                if str(row["span_id"]) in verification_id_set
            ]
            assignment_context_ids: set[str] = set()
            for span_id in verification_ids:
                position = entry_position[span_id]
                for nearby in range(
                    max(0, position - 2),
                    min(len(entries), position + 3),
                ):
                    assignment_context_ids.add(entries[nearby][1].id)

            verification_context = {
                **base_context,
                "task": "verify_story_dialogue_full_context",
                "story_text": _render_marked_story(
                    verification_story_spans,
                    verification_id_set,
                ),
                "span_ids": verification_ids,
                "spans": verification_request_spans,
                "speaker_roster": _speaker_roster_payload(
                    rolling_speakers,
                    rolling_aliases,
                ),
                "chapter_assignments": [
                    {
                        "span_id": str(item["span_id"]),
                        "speaker": str(item["speaker"]),
                    }
                    for item in parsed
                    if str(item["span_id"]) in assignment_context_ids
                ],
                "proposed_assignments": [
                    {
                        "span_id": str(initial_by_id[span_id]["span_id"]),
                        "speaker": str(initial_by_id[span_id]["speaker"]),
                        "confidence": float(initial_by_id[span_id]["confidence"]),
                        "ambiguity": initial_by_id[span_id].get("ambiguity"),
                        "verification_reasons": verification_reasons[span_id],
                    }
                    for span_id in verification_ids
                ],
                "verification_scope": verification_scope,
            }
            try:
                verified, verified_discovered = run_full_story_call(
                    verification_context,
                    verification_ids,
                    verification_request_spans,
                    classifier_call=logged_classifier_call,
                    repair_task="repair_verification_missing_spans",
                )
                verified_by_id = {
                    str(item["span_id"]): item for item in verified
                }
            except Exception as exc:
                retry_verify = {
                    **verification_context,
                    "task": "retry_verify_story_dialogue_full_context",
                    "previous_error": type(exc).__name__,
                }
                try:
                    verified, verified_discovered = run_full_story_call(
                        retry_verify,
                        verification_ids,
                        verification_request_spans,
                        classifier_call=logged_classifier_call,
                        repair_task="retry_repair_verification_missing_spans",
                    )
                    verified_by_id = {
                        str(item["span_id"]): item for item in verified
                    }
                except Exception as retry_exc:
                    verification_error = (
                        f"{type(exc).__name__}:{type(retry_exc).__name__}"
                    )

            for discovered_speaker in verified_discovered:
                merge_discovery(discovered_speaker)

            audit_disagreement = any(
                "audit_sample" in verification_reasons.get(span_id, [])
                and span_id in verified_by_id
                and normalize_speaker_name(str(initial_by_id[span_id]["speaker"]))
                != normalize_speaker_name(str(verified_by_id[span_id]["speaker"]))
                for span_id in verification_ids
            )
            if audit_disagreement and verification_error is None:
                escalation_context = {
                    **base_context,
                    "task": "verify_story_dialogue_full_context_escalated",
                    "speaker_roster": _speaker_roster_payload(
                        rolling_speakers,
                        rolling_aliases,
                    ),
                    "chapter_assignments": [
                        {
                            "span_id": str(item["span_id"]),
                            "speaker": str(item["speaker"]),
                            "confidence": float(item["confidence"]),
                            "ambiguity": item.get("ambiguity"),
                        }
                        for item in parsed
                    ],
                    "proposed_assignments": [
                        {
                            "span_id": str(item["span_id"]),
                            "speaker": str(item["speaker"]),
                            "confidence": float(item["confidence"]),
                            "ambiguity": item.get("ambiguity"),
                        }
                        for item in parsed
                    ],
                    "verification_scope": "full_context_escalated",
                    "escalation_reason": "audit_disagreement",
                }
                try:
                    escalated, escalated_discovered = run_full_story_call(
                        escalation_context,
                        target_ids,
                        request_spans,
                        classifier_call=logged_classifier_call,
                        repair_task="repair_escalated_verification_missing_spans",
                    )
                    for discovered_speaker in escalated_discovered:
                        merge_discovery(discovered_speaker)
                    verified_by_id = {
                        str(item["span_id"]): item for item in escalated
                    }
                    verification_ids = list(target_ids)
                    for span_id in target_ids:
                        verification_reasons.setdefault(
                            span_id, ["audit_escalation"],
                        )
                    verification_scope = "full_context_escalated"
                except Exception as escalation_exc:
                    verification_error = (
                        f"audit_escalation:{type(escalation_exc).__name__}"
                    )
                    verification_ids = list(target_ids)
                    for span_id in target_ids:
                        verification_reasons.setdefault(
                            span_id, ["audit_escalation"],
                        )

        final_rows = [
            verified_by_id.get(str(item["span_id"]), item)
            for item in parsed
        ]

        for item in final_rows:
            speaker_label = display_speaker_name(str(item["speaker"]))
            if (
                speaker_label
                and normalize_speaker_name(speaker_label) != "narrator"
                and resolve_speaker(
                    speaker_label,
                    rolling_speakers,
                    rolling_aliases,
                    allow_proposed=True,
                ) is None
            ):
                merge_discovery(DiscoveredSpeaker(speaker_label, ()))

        final_by_id = {str(item["span_id"]): item for item in final_rows}
        verification_id_set = set(verification_ids)
        for _global_index, span in entries:
            initial = initial_by_id[span.id]
            final = final_by_id[span.id]
            changed = (
                normalize_speaker_name(str(initial["speaker"]))
                != normalize_speaker_name(str(final["speaker"]))
            )
            was_verified = span.id in verified_by_id
            verification_failed = (
                span.id in verification_id_set and verification_error is not None
            )
            annotation = _annotation_from_payload(
                project_id=project_id,
                span=span,
                payload=final,
                speakers=rolling_speakers,
                aliases=rolling_aliases,
                evidence_text=span.source_text,
                apply_deterministic_attribution=False,
                review_reason_override=(
                    "AI_VERIFICATION_UNAVAILABLE"
                    if verification_failed else None
                ),
                evidence_extra={
                    "semantic_authority": "llm_full_story",
                    "analysis_contract_version": _ANALYSIS_CONTRACT_VERSION,
                    **classifier_runtime_evidence(),
                    "classifier_ambiguity": initial.get("ambiguity"),
                    "classification_schema_repair": initial.get("_schema_repair"),
                    "classification_partial_retry": bool(
                        initial.get("_partial_retry")
                    ),
                    "classification_span_id_repair": initial.get("_span_id_repair"),
                    "verification_policy_version": _VERIFICATION_POLICY_VERSION,
                    "verification_audit_scope": _VERIFICATION_AUDIT_SCOPE,
                    "verification_status": (
                        "failed"
                        if verification_failed
                        else "completed"
                        if was_verified
                        else "skipped"
                    ),
                    "verification_required": span.id in verification_id_set,
                    "verification_reasons": verification_reasons.get(span.id, []),
                    "verification_soft_signals": verification_soft_signals.get(
                        span.id, []
                    ),
                    "verification_scope": (
                        verification_scope
                        if span.id in verification_id_set else "not_required"
                    ),
                    "verification_error": (
                        verification_error if verification_failed else None
                    ),
                    "verification_changed": changed,
                    "initial_speaker": str(initial["speaker"]),
                    "verified_speaker": str(final["speaker"]),
                    "window_number": window_number,
                },
            )
            annotations_by_id[span.id] = annotation
            record_unknown_candidate(annotation)
            continuity.append({
                "span_id": span.id,
                "speaker": annotation.speaker_candidate or str(final["speaker"]),
                "confidence": annotation.confidence,
            })
        _log_classification_event(
            "classification_batch_completed",
            log_context,
            project_id=project_id,
            chapter_id=entries[0][1].chapter_id if entries else None,
            mode="batch",
            batch_number=batch_number,
            annotation_count=len(entries),
            annotations=[annotations_by_id[span.id] for _index, span in entries],
            discovered_speakers=discovered,
        )
        if on_batch_complete is not None:
            on_batch_complete(target_ids, batch_number, len(windows))

    existing_keys = {
        normalize_speaker_name(speaker.canonical_name)
        for speaker in speakers
        if speaker.status == "active"
    }
    analysis = BatchAnalysis(
        tuple(annotations_by_id[span.id] for span in spans),
        tuple(
            discovery for key, discovery in discoveries.items()
            if (
                key not in existing_keys
                or discovery.aliases
                or discovery.role
                or discovery.traits
                or discovery.estimated_age
                or discovery.gender_presentation
            )
        ),
    )
    _log_classification_event(
        "classification_batches_completed",
        log_context,
        project_id=project_id,
        chapter_id=spans[0].chapter_id if spans else None,
        annotation_count=len(analysis.annotations),
        review_count=sum(item.review_reason is not None for item in analysis.annotations),
        discovered_speakers=analysis.discovered_speakers,
        annotations=analysis.annotations,
    )
    return analysis
