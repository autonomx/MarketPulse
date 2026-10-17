"""Canonical owner-aware memory service factory.

Production resolves the PostgreSQL repository only after runtime adapters are
installed. Pre-bootstrap calls receive an uncached provider-free service and can
never become the cached production authority.
"""
from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import Any

from .owner_repository import OwnerAwareInMemoryMemoryRepository
from .owner_service import OwnerAwareMemoryService

RepositoryFactory = Callable[[], Any]

_lock = RLock()
_installed_repository_factory: RepositoryFactory | None = None
_default_service: OwnerAwareMemoryService | None = None


def install_default_memory_repository_factory(factory: RepositoryFactory) -> None:
    """Install the production repository factory and discard any cached service."""

    if not callable(factory):
        raise TypeError("memory repository factory must be callable")
    global _installed_repository_factory, _default_service
    with _lock:
        _installed_repository_factory = factory
        _default_service = None


def clear_default_memory_repository_factory() -> None:
    """Return to provider-free behavior for isolated tests."""

    global _installed_repository_factory, _default_service
    with _lock:
        _installed_repository_factory = None
        _default_service = None


def reset_default_memory_service() -> None:
    """Discard the resident service without changing the installed repository."""

    global _default_service
    with _lock:
        _default_service = None


def _runtime_repository_factory() -> RepositoryFactory | None:
    """Resolve PostgreSQL lazily only after the runtime adapter boundary is active."""

    from app.persistence.runtime import uses_postgresql_runtime
    if not uses_postgresql_runtime():
        return None
    from app.platform.assistant_memory.persistence.owner_memory_store import production_owner_memory_repository

    return production_owner_memory_repository


def default_memory_service() -> OwnerAwareMemoryService:
    """Return the resident authoritative service after bootstrap.

    Before PostgreSQL bootstrap the returned in-memory service is intentionally
    uncached. This prevents an early import or test call from becoming the
    production authority.
    """

    global _default_service
    with _lock:
        factory = _installed_repository_factory or _runtime_repository_factory()
        if factory is None:
            return OwnerAwareMemoryService(OwnerAwareInMemoryMemoryRepository())
        if _default_service is None:
            _default_service = OwnerAwareMemoryService(factory())
        return _default_service


__all__ = [
    "clear_default_memory_repository_factory",
    "default_memory_service",
    "install_default_memory_repository_factory",
    "reset_default_memory_service",
]
