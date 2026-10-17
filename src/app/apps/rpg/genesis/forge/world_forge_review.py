"""Durable single-pass review contracts for World Forge candidates."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .world_forge_contract import CampaignTopicNode
from .world_forge_fact_pipeline import StructuredFactValidationError
from .world_forge_generation import GeneratedTopic
from .world_forge_integrity import WorldForgeIntegrityError
from .world_forge_lore_scoring import WorldForgeLoreQualityError
from .world_forge_semantic_quality import WorldForgeSemanticQualityError

_REVIEW_SCHEMA = "rpg_world_generation_review_v1"
_MAX_STRING = 500
_MAX_ITEMS = 20
_STRUCTURED_OUTPUT_ERROR_NAMES = {
    "StructuredDecodeError",
    "StructuredSchemaError",
    "StructuredSemanticError",
}


def _bounded(value: Any, *, depth: int = 0) -> Any:
    """Return safe, bounded diagnostics without retaining arbitrary provider payloads."""

    if depth >= 3:
        return str(value)[:_MAX_STRING]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_STRING else value[: _MAX_STRING - 1] + "…"
    if isinstance(value, Mapping):
        rows = list(value.items())[:_MAX_ITEMS]
        return {str(key): _bounded(item, depth=depth + 1) for key, item in rows}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_bounded(item, depth=depth + 1) for item in list(value)[:_MAX_ITEMS]]
    return _bounded(str(value), depth=depth + 1)


@dataclass(frozen=True)
class GenerationReviewIssue:
    code: str
    topic_id: str
    entity_id: str = ""
    field_id: str = ""
    message: str = ""
    expected: str = ""
    allowed_domains: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    supplied_value: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "topic_id": self.topic_id,
            "entity_id": self.entity_id,
            "field_id": self.field_id,
            "message": self.message,
            "expected": self.expected,
            "allowed_domains": list(self.allowed_domains),
            "candidates": list(self.candidates),
            "supplied_value": _bounded(self.supplied_value),
        }


@dataclass(frozen=True)
class GenerationReviewReport:
    status: str
    blocking: bool
    error_type: str
    issues: tuple[GenerationReviewIssue, ...]
    summary: str = ""

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(sorted({issue.code for issue in self.issues if issue.code}))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _REVIEW_SCHEMA,
            "status": self.status,
            "blocking": self.blocking,
            "error_type": self.error_type,
            "reason_codes": list(self.reason_codes),
            "issues": [issue.as_dict() for issue in self.issues],
            "summary": self.summary,
        }


def _report_from_mapping(value: Mapping[str, Any]) -> GenerationReviewReport | None:
    issues_value = value.get("issues")
    if not isinstance(issues_value, Sequence) or isinstance(
        issues_value,
        (str, bytes, bytearray),
    ):
        return None
    issues = tuple(
        GenerationReviewIssue(
            code=str(row.get("code") or "unknown"),
            topic_id=str(row.get("topic_id") or ""),
            entity_id=str(row.get("entity_id") or ""),
            field_id=str(row.get("field_id") or ""),
            message=str(row.get("message") or ""),
            expected=str(row.get("expected") or ""),
            allowed_domains=tuple(str(item) for item in row.get("allowed_domains") or ()),
            candidates=tuple(str(item) for item in row.get("candidates") or ()),
            supplied_value=row.get("supplied_value"),
        )
        for row in issues_value
        if isinstance(row, Mapping)
    )
    return GenerationReviewReport(
        status=str(value.get("status") or "failed"),
        blocking=bool(value.get("blocking", True)),
        error_type=str(value.get("error_type") or "GenerationFailure"),
        issues=issues,
        summary=str(value.get("summary") or ""),
    )


def _expected_from_message(message: str) -> str:
    prefix = "Expected "
    if message.startswith(prefix):
        return message[len(prefix) :].rstrip(".")
    return ""


def _structured_fact_issues(
    error: StructuredFactValidationError,
) -> tuple[GenerationReviewIssue, ...]:
    return tuple(
        GenerationReviewIssue(
            code=issue.code,
            topic_id=issue.topic_id,
            entity_id=issue.entity_id,
            field_id=issue.field_id,
            message=issue.message,
            expected=_expected_from_message(issue.message),
            supplied_value=issue.supplied_value,
        )
        for issue in error.issues
    )


def _integrity_issues(
    error: WorldForgeIntegrityError,
) -> tuple[GenerationReviewIssue, ...]:
    return tuple(
        GenerationReviewIssue(
            code=issue.code,
            topic_id=issue.topic_id,
            entity_id=issue.item_id,
            field_id=issue.field,
            message=issue.message,
            candidates=tuple(issue.candidates),
            supplied_value=issue.supplied_value,
        )
        for issue in error.issues
    )


def _semantic_issues(
    error: WorldForgeSemanticQualityError,
) -> tuple[GenerationReviewIssue, ...]:
    rows: list[GenerationReviewIssue] = []
    for issue in error.report.issues:
        if issue.severity != "error":
            continue
        entity_ids = issue.entity_ids or ("",)
        fields = issue.fields or ("",)
        for entity_id in entity_ids:
            for field_id in fields:
                rows.append(
                    GenerationReviewIssue(
                        code=issue.code,
                        topic_id=issue.topic_id,
                        entity_id=entity_id,
                        field_id=field_id,
                        message=issue.reason,
                    )
                )
    return tuple(rows)


def _structured_output_issues(
    topic_id: str,
    error: Exception,
) -> tuple[GenerationReviewIssue, ...]:
    rows = []
    for issue in getattr(error, "issues", ()):
        path = tuple(getattr(issue, "path", ()) or ())
        rows.append(
            GenerationReviewIssue(
                code=str(getattr(issue, "error_type", "structured_output_invalid")),
                topic_id=topic_id,
                field_id=".".join(str(value) for value in path),
                message=str(getattr(issue, "message", str(error))),
                supplied_value=getattr(issue, "context", None),
            )
        )
    return tuple(rows) or (
        GenerationReviewIssue(
            code=type(error).__name__,
            topic_id=topic_id,
            message=str(error),
        ),
    )


def report_from_error(
    topic_id: str,
    error: Exception,
    *,
    status: str = "needs_review",
) -> GenerationReviewReport:
    replay_payload = getattr(error, "payload", None)
    if isinstance(replay_payload, Mapping):
        stored_validation = replay_payload.get("validation")
        if isinstance(stored_validation, Mapping):
            stored = _report_from_mapping(stored_validation)
            if stored is not None:
                return replace(stored, status=status)

    wrapped = getattr(error, "error", None)
    if isinstance(wrapped, Exception) and wrapped is not error:
        nested = report_from_error(topic_id, wrapped, status=status)
        return replace(nested, summary=str(error))

    if isinstance(error, StructuredFactValidationError):
        issues = _structured_fact_issues(error)
    elif isinstance(error, WorldForgeLoreQualityError):
        issues = _integrity_issues(error)
    elif isinstance(error, WorldForgeIntegrityError):
        issues = _integrity_issues(error)
    elif isinstance(error, WorldForgeSemanticQualityError):
        issues = _semantic_issues(error)
    elif type(error).__name__ in _STRUCTURED_OUTPUT_ERROR_NAMES:
        issues = _structured_output_issues(topic_id, error)
    else:
        text = str(error)
        code = text.split(":", 1)[0] if ":" in text else type(error).__name__
        issues = (
            GenerationReviewIssue(
                code=code or type(error).__name__,
                topic_id=topic_id,
                message=text,
            ),
        )
    return GenerationReviewReport(
        status=status,
        blocking=True,
        error_type=type(error).__name__,
        issues=issues,
        summary=str(error),
    )


def is_reviewable_candidate_error(error: Exception) -> bool:
    if isinstance(
        error,
        (
            StructuredFactValidationError,
            WorldForgeIntegrityError,
            WorldForgeSemanticQualityError,
        ),
    ):
        return True
    if isinstance(error, ValueError):
        return str(error).startswith(
            (
                "world_brief_grounding:",
                "world_generation_placeholder_entity:",
                "world_entity_dossier_quality:",
            )
        )
    return False


def mark_needs_review(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    error: Exception,
) -> GeneratedTopic:
    report = report_from_error(node.topic_id, error)
    provenance = {
        **dict(topic.provenance),
        "generation_status": "needs_review",
        "generation_review": report.as_dict(),
    }
    if isinstance(error, WorldForgeLoreQualityError):
        provenance["lore_quality"] = error.assessment.as_dict()
    if isinstance(error, WorldForgeSemanticQualityError):
        provenance["semantic_quality"] = error.report.as_dict()
    return replace(topic, provenance=provenance)


def review_report(topic: GeneratedTopic | Mapping[str, Any]) -> dict[str, Any]:
    provenance = (
        topic.provenance
        if isinstance(topic, GeneratedTopic)
        else topic.get("provenance")
    )
    provenance = provenance if isinstance(provenance, Mapping) else {}
    value = provenance.get("generation_review")
    return dict(value) if isinstance(value, Mapping) else {}


def result_status(topic: GeneratedTopic) -> str:
    return "needs_review" if review_report(topic) else "accepted"


def failure_report(topic_id: str, error: Exception) -> dict[str, Any]:
    return report_from_error(topic_id, error, status="failed").as_dict()


__all__ = [
    "GenerationReviewIssue",
    "GenerationReviewReport",
    "failure_report",
    "is_reviewable_candidate_error",
    "mark_needs_review",
    "report_from_error",
    "result_status",
    "review_report",
]
