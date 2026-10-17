"""Browser settings API helpers backed only by SettingsService entries."""
from __future__ import annotations

from typing import Any

from .models import SettingsPayload, SettingsSaveResponse

from .registry import CORE_SETTING_SPECS
from .service import SettingsPatch, SettingsService


def _entries(service: SettingsService | None) -> dict[str, tuple[Any, int]]:
    result: dict[str, tuple[Any, int]] = {}
    for spec in CORE_SETTING_SPECS:
        item = service.get(spec.key) if service is not None else None
        result[spec.key] = (spec.default, 0) if item is None else (item["value"], int(item["revision"]))
    return result


def settings_dict(service: SettingsService | None) -> dict[str, Any]:
    return {key: value for key, (value, _revision) in _entries(service).items()}


def settings_payload(service: SettingsService | None) -> SettingsPayload:
    entries = _entries(service)
    settings = {key: value for key, (value, _revision) in entries.items()}
    image = settings.get("image") if isinstance(settings.get("image"), dict) else {}
    visual = settings.get("rpg_visual") if isinstance(settings.get("rpg_visual"), dict) else {}
    return SettingsPayload(
        provider=str(settings.get("provider") or "lmstudio"),
        audio_provider_tts=str(settings.get("audio_provider_tts") or ""),
        audio_provider_stt=str(settings.get("audio_provider_stt") or ""),
        image_enabled=bool(image.get("enabled")),
        rpg_visual_enabled=bool(visual.get("enabled")),
        worker_urls={
            "tts": str(settings.get("tts_worker_url") or ""),
            "stt": str(settings.get("stt_worker_url") or ""),
            "image": str(settings.get("image_worker_url") or ""),
        },
        settings=settings,
        revisions={key: revision for key, (_value, revision) in entries.items()},
    )


def save_settings_patch(service: SettingsService, patch: SettingsPatch) -> SettingsSaveResponse:
    service.patch(patch)
    return SettingsSaveResponse(success=True)
