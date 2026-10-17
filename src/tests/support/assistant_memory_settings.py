"""Explicit in-memory settings service for assistant-memory tests."""
from __future__ import annotations

from copy import deepcopy

from app.platform.assistant_memory.persistence.settings_store import (
    SettingsServiceAssistantMemorySettingsStore,
    assistant_memory_setting_spec,
)
from app.settings.service import SettingRevisionConflict, SettingSpec


class InMemorySettingsService:
    def __init__(self) -> None:
        spec = assistant_memory_setting_spec()
        self.specs: dict[str, SettingSpec] = {spec.key: spec}
        self._value = deepcopy(spec.default)
        self._revision = 0

    def get(self, key: str):
        if key not in self.specs:
            return None
        return {
            "key": key,
            "value": deepcopy(self._value),
            "revision": self._revision,
            "updated_by": None,
            "updated_at": None,
        }

    def set(self, key: str, value, *, expected_revision: int | None):
        if key not in self.specs:
            raise KeyError(key)
        if expected_revision != self._revision:
            raise SettingRevisionConflict(f"revision conflict for setting {key}")
        self._value = deepcopy(value)
        self._revision += 1
        return self.get(key)


def in_memory_assistant_memory_settings_store():
    service = InMemorySettingsService()
    return service, SettingsServiceAssistantMemorySettingsStore(service)
