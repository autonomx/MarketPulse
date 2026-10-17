"""Non-canonical evidence for failed World Forge generation attempts."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

FailureStage = Literal[
    "transport",
    "finish_validation",
    "bounded_decode",
    "provider_validation",
    "semantic_validation",
    "materialization",
    "canonical_validation",
    "topic_audit",
    "contract_mismatch",
    "recovery_exhausted",
]

_SECRET = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?token|secret)"
    r"(\s*[:=]\s*)([^\s,;\"']+)"
)
_MAX_EXCERPT = 2_000


class FailureValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    path: str = ""
    code: str
    message: str


class WorldForgeFailureArtifact(BaseModel):
    """A failed attempt that can be inspected or retried but never accepted."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["rpg_world_forge_failure_artifact_v1"] = (
        "rpg_world_forge_failure_artifact_v1"
    )
    artifact_id: str
    run_id: str = ""
    job_id: str = ""
    topic_id: str
    attempt: int = 1
    stage: FailureStage
    provider: str = ""
    model: str = ""
    structured_mode: str = ""
    strategy_identity: str = ""
    provider_schema_hash: str = ""
    canonical_contract_hash: str = ""
    raw_response_hash: str = ""
    raw_response_bytes: int = 0
    sanitized_excerpt: str = ""
    spool_reference: str | None = None
    issues: list[FailureValidationIssue] = Field(default_factory=list)
    deterministic_repairs: list[str] = Field(default_factory=list)
    correction_attempted: bool = False
    correction_result: str = "not_attempted"
    created_at: str
    retention_policy: str = "world_generation_diagnostic"


def _excerpt(value: str) -> str:
    text = "".join(character for character in value if character >= " " or character in "\r\n\t")
    text = _SECRET.sub(r"\1\2[REDACTED]", text)
    return text[:_MAX_EXCERPT]


def _issues(error: Exception, *, stage: str) -> list[FailureValidationIssue]:
    rows: list[FailureValidationIssue] = []
    method = getattr(error, "errors", None)
    if callable(method):
        try:
            for item in method(include_url=False):
                location = item.get("loc") or ()
                path = "/" + "/".join(str(part) for part in location) if location else ""
                rows.append(
                    FailureValidationIssue(
                        stage=stage,
                        path=path,
                        code=str(item.get("type") or type(error).__name__),
                        message=str(item.get("msg") or error),
                    )
                )
        except Exception:
            rows = []
    if not rows:
        rows.append(
            FailureValidationIssue(
                stage=stage,
                code=type(error).__name__,
                message=str(error),
            )
        )
    return rows


def build_failure_artifact(
    *,
    topic_id: str,
    stage: FailureStage,
    error: Exception,
    raw_text: str,
    diagnostics: Mapping[str, Any],
    deterministic_repairs: tuple[str, ...] = (),
    correction_attempted: bool = False,
    correction_result: str = "not_attempted",
) -> WorldForgeFailureArtifact:
    raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest() if raw_text else ""
    identity = {
        "topic_id": topic_id,
        "stage": stage,
        "raw_response_hash": raw_hash,
        "provider_schema_hash": str(diagnostics.get("provider_schema_hash") or ""),
        "canonical_contract_hash": str(
            diagnostics.get("canonical_contract_hash") or ""
        ),
    }
    artifact_id = "wffa:" + hashlib.sha256(
        repr(sorted(identity.items())).encode("utf-8")
    ).hexdigest()[:24]
    return WorldForgeFailureArtifact(
        artifact_id=artifact_id,
        topic_id=topic_id,
        stage=stage,
        provider=str(diagnostics.get("provider") or ""),
        model=str(diagnostics.get("model") or ""),
        structured_mode=str(diagnostics.get("selected_mode") or ""),
        strategy_identity=str(diagnostics.get("strategy_identity") or ""),
        provider_schema_hash=str(diagnostics.get("provider_schema_hash") or ""),
        canonical_contract_hash=str(
            diagnostics.get("canonical_contract_hash") or ""
        ),
        raw_response_hash=raw_hash,
        raw_response_bytes=len(raw_text.encode("utf-8")),
        sanitized_excerpt=_excerpt(raw_text),
        issues=_issues(error, stage=stage),
        deterministic_repairs=list(deterministic_repairs),
        correction_attempted=correction_attempted,
        correction_result=correction_result,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


__all__ = [
    "FailureStage",
    "FailureValidationIssue",
    "WorldForgeFailureArtifact",
    "build_failure_artifact",
]
