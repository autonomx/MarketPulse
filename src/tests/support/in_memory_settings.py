"""Test-only SettingsService double with the production revision contract."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from app.settings.registry import core_setting_specs
from app.settings.service import SettingRevisionConflict, SettingsPatch


class InMemorySettingsService:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.specs = {spec.key: spec for spec in core_setting_specs()}
        self.values = deepcopy(values or {})
        self.revisions = {key: 1 for key in self.values}
        self.writes: list[SettingsPatch] = []
        self._subscribers: dict[str, list[Callable[[str, Any], None]]] = {}

    def register_specs(self, specs) -> None:
        for spec in specs:
            self.specs[spec.key] = spec

    def get(self, key: str) -> dict[str, Any] | None:
        spec = self.specs.get(key)
        if spec is None:
            return None
        return {
            "key": key,
            "value": deepcopy(self.values.get(key, spec.default)),
            "revision": self.revisions.get(key, 0),
            "updated_by": "test-user" if key in self.revisions else None,
            "updated_at": None,
        }

    def set(self, key: str, value: Any, *, expected_revision: int | None):
        if key not in self.specs:
            raise KeyError(key)
        current = self.revisions.get(key, 0)
        if expected_revision != current:
            raise SettingRevisionConflict(f"revision conflict for setting {key}")
        self.values[key] = deepcopy(value)
        self.revisions[key] = current + 1
        for callback in tuple(self._subscribers.get(key, ())):
            callback(key, deepcopy(value))
        return self.get(key)

    def patch(self, patch: SettingsPatch) -> dict[str, dict[str, Any]]:
        self.writes.append(patch.model_copy(deep=True))
        return {
            key: self.set(
                key,
                value,
                expected_revision=patch.revisions.get(key),
            )
            for key, value in patch.values.items()
        }

    def subscribe(self, key: str, callback: Callable[[str, Any], None]):
        self._subscribers.setdefault(key, []).append(callback)

        def unsubscribe() -> None:
            callbacks = self._subscribers.get(key, [])
            if callback in callbacks:
                callbacks.remove(callback)

        return unsubscribe
