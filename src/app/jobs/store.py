"""Production job-store composition boundary.

PostgreSQL is the only production authority. Provider-free tests must inject a
store from tests/support rather than importing a test double into app.jobs.
"""
from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.runtime.contracts import JobService


_FACTORY_LOCK = RLock()
_DEFAULT_JOB_STORE_FACTORY: Callable[[], JobService] | None = None


def install_default_job_store_factory(factory: Callable[[], JobService]) -> None:
    """Install the store provider from the process composition root."""
    if not callable(factory):
        raise TypeError("job store factory must be callable")
    global _DEFAULT_JOB_STORE_FACTORY
    with _FACTORY_LOCK:
        _DEFAULT_JOB_STORE_FACTORY = factory


def reset_default_job_store_factory_for_tests() -> None:
    global _DEFAULT_JOB_STORE_FACTORY
    with _FACTORY_LOCK:
        _DEFAULT_JOB_STORE_FACTORY = None


def default_job_store() -> JobService:
    with _FACTORY_LOCK:
        factory = _DEFAULT_JOB_STORE_FACTORY
    if factory is None:
        raise RuntimeError(
            "Job store factory is not installed; create the runtime through its composition root"
        )
    return factory()


__all__ = [
    "default_job_store",
    "install_default_job_store_factory",
    "reset_default_job_store_factory_for_tests",
]
