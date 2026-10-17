"""Provider identity resolution owned by Chat request routing."""
from __future__ import annotations


def resolve_effective_provider_id(provider_id: str | None) -> str | None:
    """Preserve explicit routing and read the current default for implicit turns."""
    explicit = str(provider_id or "").strip()
    if explicit:
        return explicit

    from app.settings.access import load_settings

    return str(load_settings().get("provider") or "lmstudio").strip() or "lmstudio"


__all__ = ["resolve_effective_provider_id"]
