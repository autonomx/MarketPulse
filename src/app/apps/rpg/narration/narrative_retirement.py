"""Durable retirement telemetry for the single canonical RPG publisher."""
from __future__ import annotations

from app.config.env import environment

from app.caching.bounded_cache import bounded_lru_cache
from threading import RLock
from typing import Any, Callable, Mapping, Protocol

from app.persistence.database import PostgresDatabase, default_database
from app.runtime.tenant_context import TenantContext, current_tenant_for
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.narration.narrative_engine.legacy_retirement import (
    production_legacy_retirement_audit,
)
from app.apps.rpg.narration.narrative_engine.publisher_guard import CANONICAL_PUBLISHER


class NarrativeRetirementRepository(Protocol):
    def put(self, **payload: Any) -> dict[str, Any]: ...

    def get(self, response_id: str) -> dict[str, Any] | None: ...

    def release_snapshot(self) -> dict[str, Any]: ...


class InMemoryNarrativeRetirementRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, dict[str, Any]] = {}

    def put(self, **payload: Any) -> dict[str, Any]:
        response_id = str(payload.get("response_id") or "")
        content_hash = str(payload.get("content_hash") or "")
        if not response_id or not content_hash:
            raise ValueError("retirement telemetry requires response identity")
        with self._lock:
            existing = self._records.get(response_id)
            if existing is not None and existing["content_hash"] != content_hash:
                raise RuntimeError(
                    f"retirement response identity changed: {response_id}"
                )
            record = {
                **dict(existing or {}),
                **payload,
                "response_id": response_id,
                "content_hash": content_hash,
            }
            self._records[response_id] = record
            return dict(record)

    def get(self, response_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(response_id)
            return dict(record) if record is not None else None

    def release_snapshot(self) -> dict[str, Any]:
        with self._lock:
            records = tuple(self._records.values())
        alternate = max(
            (int(row.get("alternate_publish_count") or 0) for row in records),
            default=0,
        )
        violations = sum(
            1
            for row in records
            if row.get("publisher") != CANONICAL_PUBLISHER
            or row.get("legacy_ownership_retired") is not True
            or row.get("compatibility_projection_only") is not True
            or dict(row.get("deletion_audit") or {}).get("passed") is not True
        )
        return {
            "record_count": len(records),
            "canonical_publish_count": max(
                (
                    int(row.get("canonical_publish_count") or 0)
                    for row in records
                ),
                default=0,
            ),
            "alternate_publish_count": alternate,
            "rejected_alternate_count": max(
                (
                    int(row.get("rejected_alternate_count") or 0)
                    for row in records
                ),
                default=0,
            ),
            "violation_count": violations,
            "zero_alternate_publishers": alternate == 0,
            "legacy_publisher_deletion_certified": bool(records)
            and violations == 0,
        }


class PostgresNarrativeRetirementRepositoryAdapter:
    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        context_provider: Callable[[PostgresDatabase], TenantContext] = current_tenant_for,
        unit_of_work_factory: Callable[..., Any] = unit_of_work,
    ) -> None:
        self.database = database or default_database()
        self._context_provider = context_provider
        self._unit_of_work_factory = unit_of_work_factory
        self._context_lock = RLock()
        self._tenant_context: TenantContext | None = None

    def _context(self) -> TenantContext:
        with self._context_lock:
            if self._tenant_context is None:
                self._tenant_context = self._context_provider(self.database)
            return self._tenant_context

    def put(self, **payload: Any) -> dict[str, Any]:
        with self._unit_of_work_factory(self.database) as work:
            record = work.narrative_retirement.put(
                self._context(),
                **payload,
            )
            work.commit()
            return record

    def get(self, response_id: str) -> dict[str, Any] | None:
        with self._unit_of_work_factory(self.database) as work:
            record = work.narrative_retirement.get(
                self._context(),
                response_id,
            )
            work.rollback()
            return record

    def release_snapshot(self) -> dict[str, Any]:
        with self._unit_of_work_factory(self.database) as work:
            snapshot = work.narrative_retirement.release_snapshot(self._context())
            work.rollback()
            return snapshot


def _runtime_postgresql_active() -> bool:
    from app.persistence.runtime import uses_postgresql_runtime
    try:
        return uses_postgresql_runtime()
    except Exception:
        return False


def _repository_mode(environ: Mapping[str, str] | None = None) -> str:
    env = environment() if environ is None else environ
    explicit = str(
        env.get("OMNIX_RPG_NARRATIVE_RETIREMENT_REPOSITORY")
        or env.get("OMNIX_RPG_NARRATIVE_REPOSITORY")
        or env.get("OMNIX_RPG_PERSISTENCE_MODE")
        or ""
    ).strip().casefold()
    if explicit:
        return explicit
    if environ is None and _runtime_postgresql_active():
        return "postgresql"
    return "in_memory"


@bounded_lru_cache(max_entries=4, ttl_seconds=3600.0)
def _cached_repository(mode: str) -> NarrativeRetirementRepository:
    if mode in {"postgres", "postgresql", "production_authoritative"}:
        return PostgresNarrativeRetirementRepositoryAdapter()
    if mode in {"in_memory", "memory", "test", "development_portable", "portable"}:
        return InMemoryNarrativeRetirementRepository()
    raise ValueError(f"unknown RPG narrative retirement repository mode: {mode}")


def build_production_narrative_retirement_repository(
    *,
    environ: Mapping[str, str] | None = None,
) -> NarrativeRetirementRepository:
    return _cached_repository(_repository_mode(environ))


def record_narrative_retirement(
    result: dict[str, Any],
    *,
    repository: NarrativeRetirementRepository | None = None,
) -> dict[str, Any]:
    """Fail closed unless one certified response proves legacy ownership is retired."""

    canonical = (
        raw_canonical_narrative_response if isinstance(raw_canonical_narrative_response := result.get("canonical_narrative_response"), Mapping) else {}
    )
    response_id = str(canonical.get("response_id") or "")
    content_hash = str(canonical.get("content_hash") or "")
    certification = (
        raw_narrative_production_certification if isinstance(raw_narrative_production_certification := result.get("narrative_production_certification"), Mapping) else {}
    )
    telemetry = (
        raw_narrative_publisher_telemetry if isinstance(raw_narrative_publisher_telemetry := result.get("narrative_publisher_telemetry"), Mapping) else {}
    )
    if certification.get("passed") is not True:
        raise RuntimeError("retirement telemetry requires production certification")
    if int(telemetry.get("alternate_publish_count") or 0) != 0:
        raise RuntimeError("retirement telemetry requires zero alternate publishers")
    if result.get("legacy_presentation_ownership_retired") is not True:
        raise RuntimeError("legacy presentation ownership is not retired")
    if result.get("legacy_compatibility_fields_source") != "canonical_projection_only":
        raise RuntimeError("legacy compatibility fields are not projection-only")

    audit = production_legacy_retirement_audit()
    if not audit.passed:
        raise RuntimeError(
            "legacy publisher deletion audit failed: " + ", ".join(audit.violations)
        )
    delivery = (
        raw_delivery if isinstance(raw_delivery := canonical.get("delivery"), Mapping) else {}
    )
    payload = {
        "response_id": response_id,
        "content_hash": content_hash,
        "publisher": str(result.get("narrative_publisher") or ""),
        "canonical_publish_count": int(
            telemetry.get("canonical_publish_count") or 0
        ),
        "alternate_publish_count": 0,
        "rejected_alternate_count": int(
            telemetry.get("rejected_alternate_count") or 0
        ),
        "legacy_ownership_retired": True,
        "compatibility_projection_only": True,
        "delivery_mode": str(delivery.get("mode") or "blocking"),
        "production_certification": dict(certification),
        "deletion_audit": audit.as_dict(),
        "metadata": {
            "turn_id": canonical.get("turn_id"),
            "campaign_id": canonical.get("campaign_id"),
            "delivery_status": delivery.get("status"),
        },
    }
    target = repository or build_production_narrative_retirement_repository()
    record = target.put(**payload)
    result["narrative_retirement_record"] = record
    result["legacy_publisher_deletion_audit"] = audit.as_dict()
    result["legacy_publisher_deletion_certified"] = True
    return result
