"""Launcher settings from ``resources/config/launcher.toml`` (WP-11.4).

The file is local (ignored by git); ``launcher.example.toml`` beside it is the
template. Each Python interpreter resolves, in order, from its environment
variable (``RPG_FLUX_PYTHON``, ``RPG_TTS_PYTHON``, ``RPG_STT_PYTHON``,
``OMNIX_FISH_PYTHON``), an
explicit path in the file, or the named Conda environment under
``conda_root`` (``CONDA_ROOT``, else ``~/miniconda3``).
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.env import environment

CONFIG_RELATIVE_PATH = Path("resources") / "config" / "launcher.toml"

# Interpreter role -> (environment variable, default Conda environment name).
_INTERPRETERS = {
    "app": ("RPG_FLUX_PYTHON", "rpg-flux"),
    "tts": ("RPG_TTS_PYTHON", "rpg-tts"),
    "stt": ("RPG_STT_PYTHON", "rpg-stt"),
    # Fish Audio S2 Pro's API server, started by the fish-s2-pro TTS provider.
    "fish": ("OMNIX_FISH_PYTHON", "fish-speech"),
}


@dataclass(frozen=True)
class LauncherConfig:
    conda_root: Path
    environments: dict[str, str]
    pythons: dict[str, str]

    def python(self, role: str) -> str:
        variable, _default = _INTERPRETERS[role]
        configured = environment().get(variable, "").strip()
        if configured:
            return configured
        explicit = self.pythons.get(role, "").strip()
        if explicit:
            return explicit
        env_dir = self.conda_root / "envs" / self.environments[role]
        return str(env_dir / "python.exe" if os.name == "nt" else env_dir / "bin" / "python")


def _table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"launcher.toml: [{name}] must be a table")
    return value


def load_launcher_config(root: Path) -> LauncherConfig:
    path = root / CONFIG_RELATIVE_PATH
    raw: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    python = _table(raw, "python")
    conda_root = (
        environment().get("CONDA_ROOT", "").strip()
        or str(python.get("conda_root") or "").strip()
        or str(Path.home() / "miniconda3")
    )
    environments = {role: default for role, (_variable, default) in _INTERPRETERS.items()}
    environments.update({role: str(name) for role, name in _table(python, "environments").items() if role in environments})
    pythons = {role: str(value) for role, value in _table(python, "paths").items() if role in _INTERPRETERS}
    return LauncherConfig(conda_root=Path(conda_root), environments=environments, pythons=pythons)


__all__ = ["CONFIG_RELATIVE_PATH", "LauncherConfig", "load_launcher_config"]
