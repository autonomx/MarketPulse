from __future__ import annotations

from typing import Any, BinaryIO, Protocol, runtime_checkable

from .tenant import TenantContext


@runtime_checkable
class IdentityRepository(Protocol):
    def ensure_local_identity(self) -> TenantContext: ...

    def load_context(self, *, user_id: str, workspace_id: str) -> TenantContext: ...

    def get_workspace(self, context: TenantContext, workspace_id: str) -> dict[str, Any] | None: ...

    def update_workspace_name(
        self,
        context: TenantContext,
        *,
        workspace_id: str,
        name: str,
        expected_revision: int,
    ) -> dict[str, Any]: ...


@runtime_checkable
class AuditRepository(Protocol):
    def append(
        self,
        context: TenantContext,
        *,
        aggregate_type: str,
        aggregate_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> int: ...


@runtime_checkable
class IdempotencyRepository(Protocol):
    def reserve(
        self,
        context: TenantContext,
        *,
        scope: str,
        key: str,
        request_hash: str,
    ) -> dict[str, Any]: ...

    def complete(
        self,
        context: TenantContext,
        *,
        scope: str,
        key: str,
        response: dict[str, Any],
    ) -> dict[str, Any]: ...


@runtime_checkable
class ChatRepository(Protocol):
    def create_session(self, context: TenantContext, payload: dict[str, Any]) -> dict[str, Any]: ...
    def get_session(self, context: TenantContext, session_id: str) -> dict[str, Any] | None: ...
    def append_message(self, context: TenantContext, session_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class CharacterRepository(Protocol):
    def get_character(self, context: TenantContext, character_id: str) -> dict[str, Any] | None: ...


@runtime_checkable
class MemoryRepository(Protocol):
    def get_memory(self, context: TenantContext, memory_id: str) -> dict[str, Any] | None: ...


@runtime_checkable
class AssetRepository(Protocol):
    def get_asset(self, context: TenantContext, asset_id: str) -> dict[str, Any] | None: ...


@runtime_checkable
class JobRepository(Protocol):
    def get_job(self, context: TenantContext, job_id: str) -> dict[str, Any] | None: ...


@runtime_checkable
class CampaignRepository(Protocol):
    def get_campaign(self, context: TenantContext, campaign_id: str) -> dict[str, Any] | None: ...


@runtime_checkable
class TurnRepository(Protocol):
    def get_by_submission(
        self,
        context: TenantContext,
        campaign_id: str,
        submission_id: str,
    ) -> dict[str, Any] | None: ...


@runtime_checkable
class OutboxRepository(Protocol):
    def append(
        self,
        context: TenantContext,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> int: ...


@runtime_checkable
class BlobStore(Protocol):
    """Blob storage keyed by relative ``a/b/c`` keys (local filesystem or S3).

    Records returned by writes carry ``storage_provider``, ``storage_key``,
    ``byte_size``, ``checksum_sha256`` and ``created``; callers must not rely
    on a local path, which S3 records do not have.
    """

    def put_bytes(self, storage_key: str, content: bytes) -> dict[str, Any]: ...
    def put_file(self, storage_key: str, source: Any) -> dict[str, Any]: ...
    def put_stream(
        self,
        storage_key: str,
        stream: BinaryIO,
        *,
        content_type: str | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]: ...
    def read_bytes(self, storage_key: str, *, expected_checksum: str | None = None) -> bytes: ...
    def open(self, storage_key: str) -> BinaryIO: ...
    def open_verified(self, storage_key: str, *, expected_checksum: str) -> BinaryIO: ...
    def copy_verified_to(self, storage_key: str, destination: Any, *, expected_checksum: str) -> None: ...
    def stage_verified_to(self, storage_key: str, destination: Any, *, expected_checksum: str) -> None: ...
    def exists(self, storage_key: str) -> bool: ...
    def delete(self, storage_key: str) -> bool: ...
    def presign_get(self, storage_key: str, ttl_seconds: int = 300) -> str | None: ...
    def scratch_dir(self) -> Any: ...


@runtime_checkable
class SecretStore(Protocol):
    def put_secret(self, reference: str, value: str) -> None: ...
    def get_secret(self, reference: str) -> str | None: ...
    def delete_secret(self, reference: str) -> bool: ...
