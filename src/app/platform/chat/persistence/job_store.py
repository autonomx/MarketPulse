"""Full PostgreSQL compatibility facade for the shared job runtime."""

from __future__ import annotations

from app.jobs.models import (
    CancelJobRequest,
    CompleteJobRequest,
    FailJobRequest,
    JobEventRecord,
    JobProgress,
    JobRecord,
    JobStatus,
)

from app.persistence.job_store import PostgresJobStoreAdapter as _PostgresJobStoreAdapter
from app.persistence.unit_of_work import unit_of_work
from app.platform.chat.persistence.chat_execution import ChatExecutionTransactions


class PostgresJobStoreAdapter(ChatExecutionTransactions, _PostgresJobStoreAdapter):
    """Preserve the current JobStore API over PostgreSQL authority."""

    def require_chat_execution_owner(self, job_id: str):
        current = self.get_job(job_id)
        if current is None or current.type != "chat.generate":
            return
        owner = self.chat_execution_owner
        if owner is None or owner.node_id != current.compat.get("execution_owner"):
            from app.persistence.execution_repositories import JobClaimConflict
            raise JobClaimConflict("Chat execution belongs to a different gateway")
        with self.database.connection() as connection:
            owner.require_live(connection)

    def iter_recoverable_chat_jobs(self, *, batch_size: int = 100):
        cursor = None
        while True:
            with unit_of_work(self.database) as work:
                records = work.jobs.list_recoverable_chat_jobs(
                    self.context, limit=batch_size,
                    after_created_at=cursor[0] if cursor else None,
                    after_id=cursor[1] if cursor else None,
                )
                work.rollback()
            if not records:
                return
            # Advance using immutable creation order even when recovery changes status.
            cursor = (records[-1]["created_at"], records[-1]["id"])
            for record in records:
                yield self._record(record)

    def recover_chat_job(self, job_id: str) -> JobRecord | None:
        with unit_of_work(self.database) as work:
            record = work.jobs.recover_chat_job(self.context, job_id=job_id)
            if record is None:
                work.rollback()
                return None
            payload = record.get("input_payload") or {}
            metadata = {"generation_status": record["status"]}
            if record["error"]:
                metadata["generation_error"] = record["error"]["message"]
            work.connection.execute(
                "UPDATE omnix_chat_messages SET metadata = metadata || %s::jsonb "
                "WHERE workspace_id = %s AND session_id = %s AND id = %s AND role = 'user'",
                (self._json(metadata), self.context.workspace_id,
                 payload.get("session_id"), payload.get("message_id")),
            )
            work.commit()
        return self._record(record)

    def complete_job(
        self,
        job_id: str,
        request: CompleteJobRequest,
    ) -> JobRecord | None:
        current = self.get_job(job_id)
        if current is None:
            return None
        if current.status == JobStatus.COMPLETED:
            return current
        return super().complete_job(job_id, request)

    def fail_job(
        self,
        job_id: str,
        request: FailJobRequest,
    ) -> JobRecord | None:
        current = self.get_job(job_id)
        if current is None:
            return None
        if current.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED}:
            return current
        return super().fail_job(job_id, request)

    def cancel_job(
        self,
        job_id: str,
        request: CancelJobRequest,
    ) -> JobRecord | None:
        current = self.get_job(job_id)
        if current is None:
            return None
        if current.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED}:
            return current
        return self.request_cancel(job_id, request)

    def update_progress(
        self,
        job_id: str,
        progress: JobProgress | None = None,
        *,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
        stage_id: str | None = None,
        stage_status: JobStatus = JobStatus.RUNNING,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> JobRecord | None:
        value = progress or JobProgress(
            current=max(0, int(current or 0)),
            total=max(1, int(total or 1)),
            message=message,
        )
        updated = super().update_progress(
            job_id, value, worker_id=worker_id, lease_token=lease_token
        )
        if not stage_id:
            return updated
        record = updated or self.get_job(job_id)
        if record is None:
            return None
        stages = [
            stage.model_copy(
                update={
                    "status": stage_status,
                    "progress": JobProgress(
                        current=1 if stage_status == JobStatus.COMPLETED else 0,
                        total=1,
                        message=message if message is not None else value.message,
                    ),
                }
            )
            if stage.id == stage_id
            else stage
            for stage in record.stages
        ]
        return self.update_job_stages(
            job_id, stages, worker_id=worker_id, lease_token=lease_token
        ) or record

    def latest_event_id(self) -> int:
        with unit_of_work(self.database) as work:
            event_id = work.jobs.latest_event_id(self.context)
            work.rollback()
        return event_id

    def list_events(
        self,
        after_id: int | str = 0,
        limit: int = 100,
    ) -> list[JobEventRecord]:
        job_id = after_id if isinstance(after_id, str) else None
        after_event_id = 0 if job_id is not None else max(0, int(after_id))
        with unit_of_work(self.database) as work:
            rows = work.jobs.list_events(
                self.context,
                after_id=after_event_id,
                job_id=job_id,
                limit=limit,
            )
            work.rollback()
        return [
            JobEventRecord(
                id=row["id"],
                job_id=row["job_id"],
                event_type=row["event_type"],
                payload=row["payload"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
