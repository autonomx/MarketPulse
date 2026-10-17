from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.persistence.declarations import module_settings_sections

from app.settings.profile_capture import ImageSettingsProfile, StorageSettingsProfile, SttSettingsProfile
from app.settings.profile_core import SETTINGS_SCHEMA_VERSION, ProviderConfigs
from app.settings.profile_experience import AgentRunSettingsProfile, AppearanceSettingsProfile, AssistantSettingsProfile
from app.settings.profile_global import GlobalSettingsProfile


class _SettingsProfileBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


def _section(model: type[Any], alias: str | None = None) -> Any:
    return (model, Field(default_factory=model, alias=alias) if alias else Field(default_factory=model))


# The platform's own sections and their places; modules declare theirs in
# declarations.py (PA-2.1) and take the places their ``order`` gives them, so a
# stored document keeps its key order.
_PLATFORM_FIELDS: tuple[tuple[int, str, Any], ...] = (
    (0, "schema_version", (int, Field(SETTINGS_SCHEMA_VERSION, alias="schemaVersion"))),
    (1, "revision", (str, "default")),
    (2, "global_settings", _section(GlobalSettingsProfile, "global")),
    (3, "provider_configs", _section(ProviderConfigs, "providerConfigs")),
    (4, "appearance", _section(AppearanceSettingsProfile)),
    (5, "assistant", _section(AssistantSettingsProfile)),
    (6, "agent_runs", _section(AgentRunSettingsProfile, "agentRuns")),
    (70, "image", _section(ImageSettingsProfile)),
    (71, "stt", _section(SttSettingsProfile)),
    (72, "storage", _section(StorageSettingsProfile)),
)


def _profile_fields() -> dict[str, Any]:
    fields = list(_PLATFORM_FIELDS)
    fields += [(section.order, section.field, _section(section.model, section.alias)) for section in module_settings_sections()]
    names = [name for _, name, _ in fields]
    if len(names) != len(set(names)):
        raise ValueError(f"settings profile sections collide: {sorted(names)}")
    return {name: definition for _, name, definition in sorted(fields, key=lambda item: item[0])}


# Every module's section is present, enabled or not, so a disabled module's
# stored values survive every save.
if TYPE_CHECKING:
    class SettingsProfile(_SettingsProfileBase):
        """What type checkers see: the platform's own sections, typed.

        The real model is assembled below at runtime, in stored key order, with
        the sections modules declare; those read as ``Any`` here.
        """

        schema_version: int
        revision: str
        global_settings: GlobalSettingsProfile
        provider_configs: ProviderConfigs
        appearance: AppearanceSettingsProfile
        assistant: AssistantSettingsProfile
        agent_runs: AgentRunSettingsProfile
        image: ImageSettingsProfile
        stt: SttSettingsProfile
        storage: StorageSettingsProfile

        def __getattr__(self, name: str) -> Any: ...
else:
    SettingsProfile = create_model("SettingsProfile", __base__=_SettingsProfileBase, **_profile_fields())


class SettingsProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_revision: str | None = None
    patch: dict[str, Any] = Field(default_factory=dict)
