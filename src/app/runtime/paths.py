from __future__ import annotations

from pathlib import Path

from app.config.env import environment


def repo_root() -> Path:
    """Return the Omnix repository root.

    This file lives at:
      <repo>/src/app/runtime/paths.py

    parents:
      0 runtime_paths.py
      1 app
      2 src
      3 repo root
    """
    return Path(__file__).resolve().parents[3]


def resources_root() -> Path:
    """Bundled resources: models, fixtures and corpora shipped with the source tree (read-only in an install)."""
    return repo_root() / "resources"


def writable_resources_root() -> Path:
    """Where Omnix writes data, logs and caches.

    ``OMNIX_RESOURCES_DIR`` moves them out of the source tree, for an install
    whose program folder is read-only (MarketPulse S-2); unset, they stay in
    ``resources/`` next to the bundled resources.
    """
    configured = environment().get("OMNIX_RESOURCES_DIR", "").strip()
    return Path(configured) if configured else resources_root()


def resources_data_root() -> Path:
    path = writable_resources_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resources_logs_root() -> Path:
    path = writable_resources_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resources_models_root() -> Path:
    path = resources_root() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def rpg_npc_profiles_root() -> Path:
    path = resources_data_root() / "rpg_npc_profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def generated_images_root() -> Path:
    path = resources_data_root() / "generated_images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def rpg_sessions_root() -> Path:
    path = resources_data_root() / "rpg_sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_results_root() -> Path:
    path = resources_data_root() / "test-results"
    path.mkdir(parents=True, exist_ok=True)
    return path

# Compatibility-free canonical resource locations.
BASE_DIR = str(repo_root())
RESOURCES_DIR = str(resources_root())
DATA_DIR = str(resources_data_root())
MODELS_DIR = str(resources_models_root())
VOICE_CLONES_DIR = str(resources_root() / "voice_clones")
LOGO_DIR = str(resources_root() / "logo")
LOGS_DIR = str(writable_resources_root() / "logs")

VOICE_CLONES_FILE = str(resources_root() / "voice_clones" / "voice_clones.json")
