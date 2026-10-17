"""PostgreSQL-backed model residency persistence."""
from __future__ import annotations

from typing import Any

from app.jobs.residency import (
    GpuResidencyPolicy,
    ModelResidencyDiagnostics,
    ModelResidencyRecord,
    get_model_residency_diagnostics,
)
from app.persistence.document_store import PostgresDocumentStore
from app.persistence.document_schemas import register_document_schema


class PostgresModelResidencyStore:
    def __init__(self, db_path: Any = None) -> None:
        if db_path is not None:
            raise RuntimeError("SQLite residency authority is retired")
        self.documents = PostgresDocumentStore()

    def upsert_record(self, record: ModelResidencyRecord) -> ModelResidencyRecord:
        self.documents.write(
            record.model_dump(mode="json"),
            module="models",
            record_type="residency",
            record_id=record.model_id,
        )
        return record

    def delete_record(self, model_id: str) -> bool:
        return self.documents.delete(
            module="models",
            record_type="residency",
            record_id=model_id,
        )

    def list_records(self) -> list[ModelResidencyRecord]:
        records = [
            ModelResidencyRecord.model_validate(payload)
            for _, payload, _ in self.documents.list(
                module="models", record_type="residency", limit=5000
            )
            if isinstance(payload, dict)
        ]
        records.sort(
            key=lambda item: (
                item.worker_id or "",
                str(item.resource_class.value),
                item.model_id,
            )
        )
        return records

    def diagnostics(
        self,
        policy: GpuResidencyPolicy | None = None,
    ) -> ModelResidencyDiagnostics:
        return get_model_residency_diagnostics(self.list_records(), policy)


# Document shapes (WP-5.9).
register_document_schema("models", "residency", ModelResidencyRecord)
