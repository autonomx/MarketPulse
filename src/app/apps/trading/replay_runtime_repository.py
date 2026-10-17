from __future__ import annotations

import hashlib

from app.persistence.blob_store import default_blob_store
from app.persistence.contracts import BlobStore

from .backtest import BacktestArtifactReference, BacktestRunResult
from .replay_repository import TradingReplayRepository
from typing import Any


class TradingReplayRuntimeRepository(TradingReplayRepository):
    def __init__(self, *args, blob_store: BlobStore | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.blob_store = blob_store or default_blob_store()

    def _artifact_key(self, run_id: str) -> str:
        workspace = hashlib.sha256(
            self.context.workspace_id.encode("utf-8")
        ).hexdigest()[:16]
        safe_run = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return f"trading/backtests/{workspace}/{safe_run}.json"

    def save_backtest(self, result: BacktestRunResult) -> BacktestRunResult:
        storage_key = self._artifact_key(result.run_id)
        blob = self.blob_store.put_bytes(
            storage_key,
            result.model_dump_json(indent=2).encode("utf-8"),
        )
        artifact = BacktestArtifactReference(
            storage_provider=str(blob["storage_provider"]),
            storage_key=str(blob["storage_key"]),
            checksum_sha256=str(blob["checksum_sha256"]),
            byte_size=int(blob["byte_size"]),
        )
        saved = result.model_copy(update={"artifact": artifact})
        try:
            super().save_backtest(saved)
            # The base repository owns the economic result. The runtime wrapper
            # owns BlobStore metadata, so keep an explicit idempotent metadata
            # update here as the persistence seam for artifact-only retries.
            with self.uow_factory() as uow:
                uow.connection.execute(
                    """
                    UPDATE omnix_trading_backtest_runs
                       SET artifact_storage_provider = %s,
                           artifact_storage_key = %s,
                           artifact_checksum_sha256 = %s,
                           artifact_byte_size = %s
                     WHERE workspace_id = %s AND run_id = %s
                    """,
                    (
                        artifact.storage_provider,
                        artifact.storage_key,
                        artifact.checksum_sha256,
                        artifact.byte_size,
                        self.context.workspace_id,
                        saved.run_id,
                    ),
                )
                uow.commit()
        except Exception:
            if bool(blob.get("created")):
                self.blob_store.delete(storage_key)
            raise
        return saved

    def list_backtests(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = super().list_backtests(limit)
        if not rows:
            return rows
        run_ids = [str(item["run_id"]) for item in rows]
        with self.uow_factory() as uow:
            artifact_rows = uow.connection.execute(
                """
                SELECT run_id, artifact_storage_provider, artifact_storage_key,
                       artifact_checksum_sha256, artifact_byte_size
                  FROM omnix_trading_backtest_runs
                 WHERE workspace_id = %s AND run_id = ANY(%s)
                """,
                (self.context.workspace_id, run_ids),
            ).fetchall()
        artifacts = {str(row[0]): row[1:] for row in artifact_rows}
        for item in rows:
            values = artifacts.get(str(item["run_id"]))
            item["artifact"] = self._artifact_from_row(values)
        return rows

    @staticmethod
    def _artifact_from_row(values) -> dict[str, Any] | None:
        if not values or values[0] is None:
            return None
        return {
            "storage_provider": str(values[0]),
            "storage_key": str(values[1]),
            "checksum_sha256": str(values[2]),
            "byte_size": int(values[3]),
        }

    def get_backtest(self, run_id: str) -> BacktestRunResult | None:
        result = super().get_backtest(run_id)
        if result is not None and result.artifact is not None:
            self.blob_store.read_bytes(
                result.artifact.storage_key,
                expected_checksum=result.artifact.checksum_sha256,
            )
        return result


def default_runtime_replay_repository() -> TradingReplayRuntimeRepository:
    return TradingReplayRuntimeRepository()
