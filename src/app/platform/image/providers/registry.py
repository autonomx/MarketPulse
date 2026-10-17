"""Global image provider registry (IMG-7)."""
from __future__ import annotations

from typing import Any, Dict, List


_IMAGE_PROVIDER_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "flux_klein": {
        "key": "flux_klein",
        "label": "FLUX.2 [klein] 4B",
        "status": "available",
        "supports_local_model": True,
        "supports_download": True,
        "supports_image_to_image": True,
        "repo_id": "black-forest-labs/FLUX.2-klein-4B",
        "local_dir_name": "flux2-klein-4b",
        "legacy_local_dir_names": ["flux-klein"],
        "pipeline_class": "Flux2KleinPipeline",
        "default_steps": 4,
        "default_guidance_scale": 1.0,
        "default_cpu_offload": False,
        "max_pixels": 1024 * 1024,
        "min_load_free_gib": 14.0,
        "min_generation_free_gib": 3.0,
        "required_paths": ["scheduler/scheduler_config.json"],
        "minimum_diffusers": "0.37.0",
        "minimum_torch": "2.5.0",
        "gated": False,
        "license": "FLUX model license",
    },
    "krea2_turbo": {
        "key": "krea2_turbo",
        "label": "Krea 2 Turbo",
        "status": "available",
        "supports_local_model": True,
        "supports_download": True,
        "supports_image_to_image": False,
        "repo_id": "krea/Krea-2-Turbo",
        "local_dir_name": "krea-2-turbo",
        "pipeline_class": "Krea2Pipeline",
        "default_steps": 8,
        "default_guidance_scale": 0.0,
        "default_cpu_offload": True,
        "max_pixels": 1024 * 1024,
        "min_generation_free_gib": 2.0,
        "required_paths": ["scheduler/scheduler_config.json"],
        "minimum_diffusers": "0.39.0",
        "minimum_torch": "2.6.0",
        "gated": True,
        "license": "krea-2-community-license",
    },
    "z_image_turbo": {
        "key": "z_image_turbo",
        "label": "Z-Image Turbo",
        "status": "available",
        "supports_local_model": True,
        "supports_download": True,
        "supports_image_to_image": False,
        "repo_id": "Tongyi-MAI/Z-Image-Turbo",
        "local_dir_name": "z-image-turbo",
        "pipeline_class": "ZImagePipeline",
        "default_steps": 9,
        "default_guidance_scale": 0.0,
        "default_cpu_offload": True,
        "max_pixels": 1024 * 1024,
        "min_load_free_gib": 14.0,
        "min_generation_free_gib": 3.0,
        "required_paths": ["scheduler/scheduler_config.json"],
        "minimum_diffusers": "0.36.0",
        "minimum_torch": "2.5.0",
        "gated": False,
        "license": "apache-2.0",
    },
    "mock": {
        "key": "mock",
        "label": "Mock Image Provider",
        "status": "available",
        "supports_local_model": False,
        "supports_download": False,
        "supports_image_to_image": False,
    },
}


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def get_image_provider_definition(provider_name: str) -> Dict[str, Any] | None:
    provider_name = _safe_str(provider_name).strip().lower()
    definition = _IMAGE_PROVIDER_DEFINITIONS.get(provider_name)
    return dict(definition) if definition else None


def list_image_providers() -> List[Dict[str, Any]]:
    return [dict(item) for item in _IMAGE_PROVIDER_DEFINITIONS.values()]


def get_image_provider_keys() -> List[str]:
    return [item["key"] for item in list_image_providers()]


def is_supported_image_provider(provider_name: str) -> bool:
    provider_name = _safe_str(provider_name).strip().lower()
    return provider_name in set(get_image_provider_keys())
