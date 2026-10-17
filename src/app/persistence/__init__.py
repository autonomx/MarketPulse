"""Centralized PostgreSQL persistence for Omnix.

Domain services should acquire repositories through a Unit of Work rather than
constructing database connections directly.
"""

from .config import DatabaseSettings, database_settings
from .database import PostgresDatabase, default_database
from .migrations import MigrationError, apply_migrations, migration_status
from .tenant import TenantContext, local_tenant_context
from .unit_of_work import PostgresUnitOfWork, unit_of_work

__all__ = [
    "DatabaseSettings",
    "MigrationError",
    "PostgresDatabase",
    "PostgresUnitOfWork",
    "TenantContext",
    "apply_migrations",
    "database_settings",
    "default_database",
    "local_tenant_context",
    "migration_status",
    "unit_of_work",
]
