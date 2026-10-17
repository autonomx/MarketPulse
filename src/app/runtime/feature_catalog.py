"""Lazy catalog for features migrated to the FeatureModule contract."""
from __future__ import annotations

import re
from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
from types import MappingProxyType

from .config import RuntimeConfig
from .features import FeatureModule

FEATURE_CATALOG: Mapping[str, str] = MappingProxyType({
    "audiobook": "app.apps.audiobook.feature:FEATURE",
    "assistant-tools": "app.platform.assistant_tools.feature:FEATURE",
    "agent-runtime": "app.platform.agent_runtime.feature:FEATURE",
    "chat": "app.platform.chat.feature:FEATURE",
    "live-speech": "app.platform.live_speech.feature:FEATURE",
    "assistant-memory": "app.platform.assistant_memory.feature:FEATURE",
    "companion-activity": "app.platform.companion_activity.feature:FEATURE",
    "characters": "app.platform.characters.feature:FEATURE",
    "character-interactions": "app.apps.character_interactions.feature:FEATURE",
    "desktop-companion": "app.apps.desktop_companion.feature:FEATURE",
    "hermes": "app.apps.rpg.edge.hermes.feature:FEATURE",
    "image": "app.platform.image.feature:FEATURE",
    "voice": "app.platform.voice.feature:FEATURE",
    "live-voice": "app.platform.live_voice.feature:FEATURE",
    "research": "app.platform.research.feature:FEATURE",
    "story": "app.apps.story.feature:FEATURE",
    "rpg": "app.apps.rpg.feature:FEATURE",
    "trading": "app.apps.trading.feature:FEATURE",
})


_TOMBSTONE_ID = re.compile(r'^MODULE_ID\s*=\s*"([^"]+)"', re.M)

# Not part of "all": enabled only when named. live-speech serves the
# /v1/realtime protocol stub with offline echo engines (WP-7.3); it stays out of
# production until it has real STT/TTS behind it.
OPT_IN_FEATURES = frozenset({"live-speech"})


def load_feature(feature_id: str) -> FeatureModule:
    target = FEATURE_CATALOG[feature_id]
    module_name, attribute = target.split(":", 1)
    feature = getattr(import_module(module_name), attribute)
    if not isinstance(feature, FeatureModule):
        raise TypeError(f"{target} did not expose a FeatureModule")
    if feature.id != feature_id:
        raise ValueError(f"Feature catalog id mismatch: {feature_id} != {feature.id}")
    return feature


def retired_module_ids() -> frozenset[str]:
    """Ids of retired modules: ``MODULE_ID`` in each ``app/persistence/retired/<package>/tombstone.py`` (PA-4.3)."""
    retired = Path(__file__).resolve().parents[1] / "persistence" / "retired"
    found = (_TOMBSTONE_ID.search(path.read_text(encoding="utf-8")) for path in sorted(retired.glob("*/tombstone.py")))
    return frozenset(match[1] for match in found if match)


def enabled_feature_ids(config: RuntimeConfig) -> tuple[str, ...]:
    requested = set(config.enabled_features)
    disabled = set(config.disabled_features)
    unknown = (requested - {"all"} - set(FEATURE_CATALOG)) | (disabled - set(FEATURE_CATALOG))
    if unknown:
        # A retired module (PA-4.3) is off whatever a configuration written before its retirement says.
        retired = retired_module_ids() & unknown
        requested, disabled, unknown = requested - retired, disabled - retired, unknown - retired
    if unknown:
        raise ValueError(f"Unknown feature ids: {sorted(unknown)}")
    if "all" in requested:
        selected = (set(FEATURE_CATALOG) - OPT_IN_FEATURES) | (requested & OPT_IN_FEATURES)
    else:
        selected = set(requested)
    selected -= disabled
    for feature_id in tuple(selected):
        feature = load_feature(feature_id)
        unknown_uses = set(feature.uses) - set(FEATURE_CATALOG)
        if unknown_uses:
            raise ValueError(f"Feature {feature_id} uses unknown features: {sorted(unknown_uses)}")
        missing = set(feature.depends_on) - selected
        if missing:
            raise ValueError(
                f"Feature {feature_id} requires disabled features: {sorted(missing)}"
            )
    return tuple(feature_id for feature_id in FEATURE_CATALOG if feature_id in selected)
