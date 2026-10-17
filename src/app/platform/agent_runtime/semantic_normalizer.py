"""Deterministic normalization for SemanticTask v2.

The LLM describes meaning. This module canonicalizes Omnix's own semantic
ontology without selecting tools, capabilities, trust policy, or execution
authority.
"""
from __future__ import annotations

from datetime import timezone

from .semantic_task import (
    SemanticDataDependency,
    SemanticOperation,
    SemanticSubject,
    SemanticTask,
)


_RETRIEVAL_MODE_RANK = {
    "unspecified": 0,
    "lookup": 1,
    "verify": 2,
    "filter": 2,
    "discover": 3,
}


_PUBLIC_SERVICE_STATUS_TOKENS = (
    "public service",
    "status page",
    "service status",
    "service incident",
    "service outage",
    "public outage",
    "availability update",
)

_REPOSITORY_CI_TOKENS = (
    "ci/cd",
    "repository ci",
    "workflow",
    "check run",
    "failing check",
    "build",
    "pipeline",
    "job log",
    "test run",
)


def _repository_ci_reference_is_public_service(
    reference: str | None,
    *,
    kind: str | None = None,
) -> bool:
    """Repair only explicit public-service status semantics mislabeled as repo CI."""

    text = " ".join(
        part
        for part in (
            str(kind or "").strip().casefold(),
            str(reference or "").strip().casefold(),
        )
        if part
    )
    return bool(
        text
        and any(token in text for token in _PUBLIC_SERVICE_STATUS_TOKENS)
        and not any(token in text for token in _REPOSITORY_CI_TOKENS)
    )


_NON_SOFTWARE_RELEASE_KIND_TOKENS = (
    "game",
    "film",
    "movie",
    "television",
    "tv",
    "album",
    "music",
    "book",
    "media",
    "hardware",
    "console",
)


def _retarget_nonsoftware_release(task: SemanticTask) -> bool:
    """Return true when software_release was used for an explicitly non-software release.

    Semantic normalization may repair ontology labels, but it must not guess from
    an unknown title. We only retarget when every explicit software_release
    subject kind clearly belongs to a non-software release category.
    """

    kinds = [
        str(subject.kind or "").strip().casefold()
        for subject in task.subjects
        if subject.target == "software_release" and str(subject.kind or "").strip()
    ]
    if not kinds:
        return False
    return all(
        any(token in kind for token in _NON_SOFTWARE_RELEASE_KIND_TOKENS)
        for kind in kinds
    )


def normalize_semantic_task(task: SemanticTask) -> SemanticTask:
    """Return a canonical SemanticTask without guessing new user intent."""

    retarget_nonsoftware_release = _retarget_nonsoftware_release(task)

    operations: list[SemanticOperation] = []
    seen_operations: set[tuple[str, str, str]] = set()
    for operation in task.operations:
        normalized = operation
        if retarget_nonsoftware_release and operation.target == "software_release":
            normalized = operation.model_copy(update={"target": "public_web"})
        elif (
            operation.target == "repository_ci"
            and _repository_ci_reference_is_public_service(
                operation.subject_reference
            )
        ):
            normalized = operation.model_copy(update={"target": "public_web"})
        # Explanation/composition are response semantics unless composing a
        # real mailbox draft. Topical domain labels must not accidentally imply
        # execution authority.
        if operation.kind == "explain" and operation.target != "conversation":
            normalized = operation.model_copy(update={"target": "conversation"})
        elif (
            operation.kind == "compose"
            and operation.target not in {"conversation", "email"}
        ):
            normalized = operation.model_copy(update={"target": "conversation"})
        key: tuple[str, str, str] = (
            normalized.kind,
            normalized.target,
            str(normalized.subject_reference or "").strip().casefold(),
        )
        if key not in seen_operations:
            seen_operations.add(key)
            operations.append(normalized)

    subjects: list[SemanticSubject] = []
    seen_subjects: set[tuple[str, str, str]] = set()
    for subject in task.subjects:
        normalized_subject = subject
        if retarget_nonsoftware_release and subject.target == "software_release":
            normalized_subject = subject.model_copy(update={"target": "public_web"})
        elif (
            subject.target == "repository_ci"
            and _repository_ci_reference_is_public_service(
                subject.reference,
                kind=subject.kind,
            )
        ):
            normalized_subject = subject.model_copy(update={"target": "public_web"})
        key = (
            normalized_subject.target,
            normalized_subject.reference.strip().casefold(),
            str(normalized_subject.kind or "").strip().casefold(),
        )
        if key not in seen_subjects:
            seen_subjects.add(key)
            subjects.append(normalized_subject)

    # Merge duplicate nonhistorical dependencies deterministically. Point-in-time
    # dependencies retain their timestamp identity and can never collapse into a
    # current/timeless obligation.
    merged: dict[tuple[str, str, str], SemanticDataDependency] = {}
    order: list[tuple[str, str, str]] = []
    for dependency in task.data_dependencies:
        normalized_dependency = dependency
        if retarget_nonsoftware_release and dependency.target == "software_release":
            normalized_dependency = dependency.model_copy(update={"target": "public_web"})
        elif (
            dependency.target == "repository_ci"
            and _repository_ci_reference_is_public_service(
                dependency.subject_reference
            )
        ):
            normalized_dependency = dependency.model_copy(update={"target": "public_web"})
        ref = str(normalized_dependency.subject_reference or "").strip()
        temporal_key = "nonhistorical"
        if normalized_dependency.freshness == "as_of_date":
            value = normalized_dependency.as_of_date
            assert value is not None
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            else:
                value = value.astimezone(timezone.utc)
            temporal_key = f"as_of:{value.isoformat()}"
        key = (
            normalized_dependency.target,
            ref.casefold(),
            temporal_key,
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = normalized_dependency
            order.append(key)
            continue
        retrieval_mode = existing.retrieval_mode
        if (
            _RETRIEVAL_MODE_RANK.get(normalized_dependency.retrieval_mode, 0)
            > _RETRIEVAL_MODE_RANK.get(existing.retrieval_mode, 0)
        ):
            retrieval_mode = normalized_dependency.retrieval_mode
        merged[key] = existing.model_copy(
            update={
                "required": existing.required or dependency.required,
                "freshness": (
                    "as_of_date"
                    if existing.freshness == "as_of_date"
                    else "current"
                    if "current" in {
                        existing.freshness,
                        normalized_dependency.freshness,
                    }
                    else "timeless"
                ),
                "as_of_date": (
                    existing.as_of_date
                    or normalized_dependency.as_of_date
                ),
                "subject_reference": (
                    existing.subject_reference or normalized_dependency.subject_reference
                ),
                # When duplicate dependencies disagree, keep the mode with the
                # wider retrieval scope.  In particular, discover can never be
                # accidentally collapsed into a bounded lookup.
                "retrieval_mode": retrieval_mode,
            }
        )

    return task.model_copy(
        update={
            "operations": operations,
            "subjects": subjects,
            "data_dependencies": [merged[key] for key in order],
        }
    )


__all__ = ["normalize_semantic_task"]
