"""Assistant-memory settings stored through the typed workspace service."""
from __future__ import annotations

from app.platform.assistant_memory.settings import (
    AssistantMemoryRuntimeSettings,
    AssistantMemoryRuntimeStatus,
    AssistantMemorySettingsUpdate,
    effective_memory_settings,
)
from app.settings.service import SettingRevisionConflict, SettingSpec, SettingsService

ASSISTANT_MEMORY_SETTINGS_KEY = "assistant_memory.runtime"


def assistant_memory_setting_spec() -> SettingSpec:
    return SettingSpec(
        key=ASSISTANT_MEMORY_SETTINGS_KEY,
        value_type=dict,
        default=AssistantMemoryRuntimeSettings().model_dump(mode="json"),
        feature="assistant-memory",
    )


class SettingsServiceAssistantMemorySettingsStore:
    """Typed settings adapter with revision checks and no file fallback."""

    def __init__(self, service: SettingsService) -> None:
        if ASSISTANT_MEMORY_SETTINGS_KEY not in service.specs:
            raise RuntimeError("assistant-memory settings are not registered")
        self._service = service

    def load_persisted(self) -> AssistantMemoryRuntimeSettings:
        record = self._service.get(ASSISTANT_MEMORY_SETTINGS_KEY)
        if record is None:
            raise RuntimeError("assistant-memory settings are unavailable")
        return AssistantMemoryRuntimeSettings.model_validate(record["value"])

    def load_effective(self) -> AssistantMemoryRuntimeStatus:
        return effective_memory_settings(
            self.load_persisted(),
            source="settings_service",
        )

    def update(
        self,
        request: AssistantMemorySettingsUpdate,
    ) -> AssistantMemoryRuntimeStatus:
        changes = request.model_dump(exclude_none=True)
        if changes.get("require_approval_for_inferred_memory") is False:
            raise ValueError("approval is required for inferred memory")
        changes["require_approval_for_inferred_memory"] = True

        current = self._service.get(ASSISTANT_MEMORY_SETTINGS_KEY)
        if current is None:
            raise RuntimeError("assistant-memory settings are unavailable")
        settings = AssistantMemoryRuntimeSettings.model_validate(
            current["value"]
        ).model_copy(update=changes)
        settings = AssistantMemoryRuntimeSettings.model_validate(
            settings.model_dump(mode="python")
        )
        self._service.set(
            ASSISTANT_MEMORY_SETTINGS_KEY,
            settings.model_dump(mode="json"),
            expected_revision=int(current["revision"]),
        )
        return self.load_effective()


__all__ = [
    "ASSISTANT_MEMORY_SETTINGS_KEY",
    "SettingsServiceAssistantMemorySettingsStore",
    "SettingRevisionConflict",
    "assistant_memory_setting_spec",
]
