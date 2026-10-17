"""Typed workspace settings service with optimistic concurrency."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from app.persistence.database import PostgresDatabase
from app.persistence.tenant import TenantContext


class SettingRevisionConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    value_type: type
    default: Any = None
    feature: str = "kernel"
    writable: bool = True


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any] = Field(default_factory=dict)
    revisions: dict[str, int] = Field(default_factory=dict)


class SettingsService:
    def __init__(
        self,
        database: PostgresDatabase,
        context_provider: Callable[[], TenantContext],
        *,
        specs: tuple[SettingSpec, ...] = (),
    ) -> None:
        self.database = database
        self.context_provider = context_provider
        self.specs = {spec.key: spec for spec in specs}
        self._subscribers: dict[str, list[Callable[[str, Any], None]]] = {}

    def register_specs(self, specs: tuple[SettingSpec, ...]) -> None:
        for spec in specs:
            if spec.key in self.specs and self.specs[spec.key] != spec:
                raise ValueError(f"duplicate setting spec: {spec.key}")
            self.specs[spec.key] = spec

    def _validate(self, key: str, value: Any) -> Any:
        spec = self.specs.get(key)
        if spec is None:
            raise KeyError(f"unknown setting key: {key}")
        if not spec.writable:
            raise PermissionError(f"setting is read-only: {key}")
        if value is not None and spec.value_type is not Any and not isinstance(value, spec.value_type):
            raise TypeError(f"{key} must be {spec.value_type.__name__}")
        return value

    def get(self, key: str) -> dict[str, Any] | None:
        context = self.context_provider()
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT value, revision, updated_by, updated_at
                  FROM omnix_settings_entries
                 WHERE workspace_id = %s AND key = %s
                """,
                (context.workspace_id, key),
            ).fetchone()
        if row is None:
            spec = self.specs.get(key)
            if spec is None:
                return None
            return {
                "key": key,
                "value": spec.default,
                "revision": 0,
                "updated_by": None,
                "updated_at": None,
            }
        return {
            "key": key,
            "value": row[0],
            "revision": int(row[1]),
            "updated_by": str(row[2]) if row[2] is not None else None,
            "updated_at": row[3].isoformat(),
        }

    def get_section(self, prefix: str) -> dict[str, dict[str, Any]]:
        context = self.context_provider()
        normalized = prefix.rstrip(".")
        like = normalized + ".%"
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT key, value, revision, updated_by, updated_at
                  FROM omnix_settings_entries
                 WHERE workspace_id = %s AND (key = %s OR key LIKE %s)
                 ORDER BY key
                """,
                (context.workspace_id, normalized, like),
            ).fetchall()
        return {
            str(row[0]): {
                "key": str(row[0]),
                "value": row[1],
                "revision": int(row[2]),
                "updated_by": str(row[3]) if row[3] is not None else None,
                "updated_at": row[4].isoformat(),
            }
            for row in rows
        }

    def set(self, key: str, value: Any, *, expected_revision: int | None) -> dict[str, Any]:
        value = self._validate(key, value)
        context = self.context_provider()
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        with self.database.transaction() as connection:
            if expected_revision in (None, 0):
                row = connection.execute(
                    """
                    INSERT INTO omnix_settings_entries
                        (workspace_id, key, value, revision, updated_by)
                    VALUES (%s, %s, %s::jsonb, 1, %s)
                    ON CONFLICT (workspace_id, key) DO NOTHING
                    RETURNING value, revision, updated_by, updated_at
                    """,
                    (context.workspace_id, key, encoded, context.user_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    UPDATE omnix_settings_entries
                       SET value = %s::jsonb,
                           revision = revision + 1,
                           updated_by = %s,
                           updated_at = CURRENT_TIMESTAMP
                     WHERE workspace_id = %s AND key = %s AND revision = %s
                    RETURNING value, revision, updated_by, updated_at
                    """,
                    (encoded, context.user_id, context.workspace_id, key, expected_revision),
                ).fetchone()
            if row is None:
                raise SettingRevisionConflict(f"revision conflict for setting {key}")
        result = {
            "key": key,
            "value": row[0],
            "revision": int(row[1]),
            "updated_by": str(row[2]) if row[2] is not None else None,
            "updated_at": row[3].isoformat(),
        }
        for callback in tuple(self._subscribers.get(key, ())):
            callback(key, result["value"])
        return result

    def patch(self, patch: SettingsPatch) -> dict[str, dict[str, Any]]:
        return {
            key: self.set(key, value, expected_revision=patch.revisions.get(key))
            for key, value in patch.values.items()
        }

    def subscribe(self, key: str, callback: Callable[[str, Any], None]) -> Callable[[], None]:
        self._subscribers.setdefault(key, []).append(callback)

        def unsubscribe() -> None:
            callbacks = self._subscribers.get(key, [])
            if callback in callbacks:
                callbacks.remove(callback)

        return unsubscribe
