"""Module declarations the kernel reads by convention (ADR-0016, PA-2.1, PA-2.2).

A module's ``declarations.py`` sits next to its ``feature.py`` and imports only
kernel modules, so the kernel reads it without loading the module. The
retention worker, the capacity report and the settings profile find each
module's declarations there, whether or not its feature is enabled, the way
migrations are found (PA-2.3)::

    RETENTION = (RetentionDeclaration("rpg_narration_events", delete=_delete_events, capacity_cleanup=True),)
    CAPACITY = (CapacityCount("rpg_turns", count=_count_turns),)
    SETTINGS = (SettingsSection("rpg", RpgSettingsProfile, order=40),)

A declared record type's ``omnix_retention_policies`` row is seeded by a
migration; without one the type is never run.
"""
from __future__ import annotations

import ast
import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

# (connection, retention_days, batch_size) -> rows deleted; one short transaction per call.
DeleteHandler = Callable[[Any, int, int], int]
RowCount = Callable[[Any], int]


@dataclass(frozen=True)
class RetentionDeclaration:
    """A retention record type a module owns, and how to delete its expired rows."""

    record_type: str
    delete: DeleteHandler
    # Also run by the lifecycle capacity cleanup, not only by the retention worker.
    capacity_cleanup: bool = False


@dataclass(frozen=True)
class CapacityCount:
    """A row count the capacity report shows."""

    name: str
    count: RowCount


@dataclass(frozen=True)
class SettingsSection:
    """A module's section of the settings profile: its field, model (a pydantic model) and place.

    ``order`` fixes the section's position in the stored document, so documents
    keep their key order whichever modules exist; ``alias`` is its document key
    when that differs from the field name.
    """

    field: str
    model: type[Any]
    order: int
    alias: str | None = None


@dataclass(frozen=True)
class PermissionDeclaration:
    """One permission a module defines: ``<domain>:<action>`` and its description."""

    name: str
    description: str
    # Granted to the default ``member`` role; owners always hold it, admins and
    # viewers get it by the catalog rules every permission follows.
    member: bool = False


@dataclass(frozen=True)
class FeaturePermissions:
    """A module's route permissions: reads need ``read``, every other method ``write`` (PA-4.2).

    Modules created before PA-4.2 keep theirs in ``app.security.permissions``;
    a name already in that catalog is refused.
    """

    feature_id: str
    read: PermissionDeclaration
    write: PermissionDeclaration


# (unit of work, tenant context, stable id, legacy item): restores what a legacy
# bundle nests in an aggregate into the module's own tables, idempotently.
LegacyRestore = Callable[[Any, Any, str, dict[str, Any]], None]


@dataclass(frozen=True)
class LegacyImport:
    """A step of the legacy cutover importer a module owns (PA-2.2): its name and restore handler."""

    name: str
    restore: LegacyRestore


@dataclass(frozen=True)
class RecordOnlyJobGuard:
    """Who may move a module's record-only job (one that runs without a worker lease), PA-2.2.

    ``predicate`` is an SQL condition on the job row, written ``{job}``, with
    exactly one ``%s``, bound to the caller's submission claim token. The kernel ANDs it
    with the job type inside the statement that moves the job, so the fence
    stays atomic.
    """

    job_type: str
    predicate: str

    def __post_init__(self) -> None:
        if self.predicate.count("%s") != 1:
            raise ValueError(f"record-only job guard for {self.job_type} needs exactly one %s")


def _declaration_files() -> list[tuple[str, Path]]:
    """Every module's ``declarations.py`` next to its ``feature.py``, then every retired module's tombstone."""
    app_root = Path(__file__).resolve().parents[1]
    found = sorted(
        path for pattern in ("*/declarations.py", "*/*/declarations.py", "*/*/*/declarations.py")
        for path in app_root.glob(pattern)
        if (path.parent / "feature.py").is_file()
    )
    found += tombstone_files()
    return [("app." + ".".join(path.relative_to(app_root).with_suffix("").parts), path) for path in found]


def tombstone_files() -> list[Path]:
    """``app/persistence/retired/<package>/tombstone.py`` of each retired module (PA-4.3).

    A tombstone keeps what the kernel still needs after a module is gone: its
    settings section (so stored values survive), and its job types, outbox
    event types and tool ids, which recovery fails or refuses.
    """
    return sorted((Path(__file__).resolve().parent / "retired").glob("*/tombstone.py"))


def declaration_modules() -> tuple[str, ...]:
    """Every module's declarations: ``app/<tier>/<package>/declarations.py`` or one level deeper, next to a feature.py."""
    return tuple(name for name, _ in _declaration_files())


def _modules_declaring(kind: str) -> list[Any]:
    """The declarations modules that assign ``kind`` at top level, read first without importing the others.

    Building the settings profile at startup then imports only the modules
    with a settings section, not every module's retention declarations.
    """
    modules = []
    for name, path in _declaration_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, (ast.Assign, ast.AnnAssign)) and kind in {
            target.id for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        } for node in tree.body):
            modules.append(importlib.import_module(name))
    return modules


def _unique(kind: str, key: Callable[[Any], str]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for module in _modules_declaring(kind):
        for item in getattr(module, kind):
            if key(item) in found:
                raise ValueError(f"{kind.lower()} declaration {key(item)} is declared twice")
            found[key(item)] = item
    return found


def module_retention() -> Mapping[str, RetentionDeclaration]:
    return MappingProxyType(_unique("RETENTION", lambda item: item.record_type))


def module_capacity_counts() -> Mapping[str, CapacityCount]:
    return MappingProxyType(_unique("CAPACITY", lambda item: item.name))


def module_permissions() -> tuple[FeaturePermissions, ...]:
    """Every module's declared route permissions, enabled or not (PA-4.2)."""
    return tuple(_unique("PERMISSIONS", lambda item: item.feature_id).values())


def legacy_import(name: str) -> LegacyRestore:
    """The restore handler of a module's legacy import step; a missing step fails the import."""
    step = _unique("LEGACY_IMPORTS", lambda item: item.name).get(name)
    if step is None:
        raise LookupError(f"no module declares the legacy import step {name!r}")
    return step.restore


def record_only_job_guards() -> tuple[RecordOnlyJobGuard, ...]:
    """Every module's record-only job guards, by job type."""
    guards = _unique("RECORD_ONLY_JOB_GUARDS", lambda item: item.job_type)
    return tuple(guards[job_type] for job_type in sorted(guards))


def retired_job_types() -> frozenset[str]:
    """Job types of retired modules, from their tombstones' ``JOB_TYPES``."""
    return frozenset(job_type for module in _modules_declaring("JOB_TYPES") for job_type in module.JOB_TYPES)


def module_settings_sections() -> tuple[SettingsSection, ...]:
    """Every module's settings section, enabled or not, in document order (PA-2.1)."""
    sections = _unique("SETTINGS", lambda item: item.field).values()
    return tuple(sorted(sections, key=lambda section: (section.order, section.field)))
