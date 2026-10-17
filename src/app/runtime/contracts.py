"""Small process-service contracts used by gateway composition.

Transactions remain in the persistence unit of work. These interfaces describe
the process-owned services exposed to routes, rather than transaction objects.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, Generic, Protocol, TypeVar

_ServiceT = TypeVar("_ServiceT")


class LazyServiceProxy(Generic[_ServiceT]):
    """Resolve a composed service on its first operation, then reuse it."""

    def __init__(self, factory: Callable[[], _ServiceT]) -> None:
        self._factory = factory
        self._service: _ServiceT | None = None
        self._handler_registry: Any = None

    def _get_service(self) -> _ServiceT:
        service = self._service
        if service is None:
            service = self._factory()
            self._service = service
            configure = getattr(service, "configure_handler_registry", None)
            if self._handler_registry is not None and configure is not None:
                configure(self._handler_registry)
        return service

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get_service(), name)

    def configure_handler_registry(self, registry: Any) -> None:
        self._handler_registry = registry
        service = self._service
        if service is None:
            return
        configure = getattr(service, "configure_handler_registry", None)
        if configure is not None:
            configure(registry)


class JobService(Protocol):
    database: Any
    context: Any
    chat_execution_owner: Any | None
    chat_dispatcher: Any | None

    def configure_handler_registry(self, registry: Any) -> None: ...
    def create_job(self, request: Any) -> Any: ...
    def create_job_once(self, request: Any, *, idempotency_key: str) -> Any: ...
    def get_job(self, job_id: str) -> Any | None: ...
    def list_jobs(
        self,
        limit: int | None = None,
        *,
        status: str | None = None,
        job_types: tuple[str, ...] | None = None,
        modules: tuple[str, ...] | None = None,
    ) -> list[Any]: ...
    def list_job_page(
        self,
        *,
        limit: int | None = None,
        status: str | None = None,
        job_types: tuple[str, ...] | None = None,
        modules: tuple[str, ...] | None = None,
        cursor: str | None = None,
    ) -> Any: ...
    def iter_jobs(
        self,
        *,
        status: str | None = None,
        job_types: tuple[str, ...] | None = None,
        modules: tuple[str, ...] | None = None,
    ) -> Iterator[Any]: ...
    def delete_job(self, job_id: str) -> bool: ...
    def mark_running(self, job_id: str, *args: Any, **kwargs: Any) -> Any: ...
    def complete_job(self, job_id: str, *args: Any, **kwargs: Any) -> Any: ...
    def fail_job(self, job_id: str, *args: Any, **kwargs: Any) -> Any: ...
    def cancel_job(self, job_id: str, *args: Any, **kwargs: Any) -> Any: ...
    def update_progress(self, job_id: str, *args: Any, **kwargs: Any) -> Any: ...


class AssetService(Protocol):
    def list_assets(
        self,
        *,
        asset_type: str | None = None,
        modules: tuple[str, ...] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any: ...
    def get_asset(self, asset_id: str) -> Any | None: ...
    def upsert_asset(self, asset: Any) -> Any: ...
    def delete_asset(self, asset_id: str, *, delete_file: bool = True) -> dict[str, Any]: ...
    def read_asset_bytes(self, asset_id: str, *, max_bytes: int) -> bytes: ...


class ChatService(Protocol):
    def list_sessions(self) -> Any: ...
    def create_session(self, request: Any) -> Any: ...
    def get_session(self, session_id: str) -> Any | None: ...


class ModelResidencyService(Protocol):
    def list_records(self) -> list[Any]: ...
    def upsert_record(self, record: Any) -> Any: ...
    def delete_record(self, model_id: str) -> bool: ...


class KernelServices(Protocol):
    """Process services offered to feature factories by the composition root."""

    jobs: JobService | None
    assets: AssetService | None
    chat: ChatService | None
    model_residency: ModelResidencyService | None
    agent_runs: Any | None
    database: Any | None
    tenant: Any | None
    settings: Any | None
