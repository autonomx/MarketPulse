"""Always-on kernel repository specs for composition; the shared services add theirs (runtime_composition)."""
from __future__ import annotations

from .repository_registry import RepositorySpec


def shared_repository_specs() -> tuple[RepositorySpec, ...]:
    from .module_repositories import (
        PostgresModuleRecordRepository,
        PostgresProjectionRepository,
    )

    return (
        RepositorySpec(PostgresModuleRecordRepository, PostgresModuleRecordRepository, "module_records"),
        RepositorySpec(PostgresProjectionRepository, PostgresProjectionRepository, "projections"),
    )
