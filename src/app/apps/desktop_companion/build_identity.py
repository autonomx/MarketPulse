"""Resolve a bounded build identity for Desktop Companion evaluation evidence."""
from __future__ import annotations

from app.config.env import env_str, environment

import os
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class DesktopCompanionBuildIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exact_commit_sha: str = Field(min_length=7, max_length=64)
    app_version: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=80)


def resolve_desktop_companion_build_identity(
    *,
    environ: dict[str, str] | None = None,
    repo_root: Path | None = None,
) -> DesktopCompanionBuildIdentity:
    values = environ if environ is not None else environment()
    for key in ("OMNIX_COMMIT_SHA", "GITHUB_SHA", "SOURCE_VERSION"):
        candidate = str(values.get(key) or "").strip()
        if 7 <= len(candidate) <= 64:
            return DesktopCompanionBuildIdentity(
                exact_commit_sha=candidate,
                app_version=str(values.get("OMNIX_APP_VERSION") or "1.0.0")[:80],
                source=f"environment:{key.lower()}",
            )
    root = repo_root or Path(__file__).resolve().parents[4]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        candidate = result.stdout.strip()
        if result.returncode == 0 and 7 <= len(candidate) <= 64:
            return DesktopCompanionBuildIdentity(
                exact_commit_sha=candidate,
                app_version=str(values.get("OMNIX_APP_VERSION") or "1.0.0")[:80],
                source="git:head",
            )
    except (OSError, subprocess.SubprocessError):
        pass
    return DesktopCompanionBuildIdentity(
        exact_commit_sha="unknown-local-build",
        app_version=str(values.get("OMNIX_APP_VERSION") or "1.0.0")[:80],
        source="fallback",
    )


__all__ = ["DesktopCompanionBuildIdentity", "resolve_desktop_companion_build_identity"]
