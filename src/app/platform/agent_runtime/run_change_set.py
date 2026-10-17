"""Canonical run-owned change-set identity and artifact hydration."""
from __future__ import annotations

from .exception_logging import log_recovered_exception

import hashlib
import json
import re

from .contracts import AgentArtifact, RunChangeSet


def baseline_identity(head: str, dirty_paths: list[str], dirty_digests: dict[str, str]) -> str:
    payload = {
        "head": str(head or ""),
        "dirty_paths": sorted(str(path).replace("\\", "/") for path in dirty_paths),
        "dirty_digests": {
            str(path).replace("\\", "/"): str(value)
            for path, value in sorted(dirty_digests.items())
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def patch_structure(patch: str) -> tuple[list[str], list[dict[str, str]], list[str]]:
    """Extract deletion/rename/mode metadata from the authoritative tracked patch."""
    deletions: set[str] = set()
    renames: list[dict[str, str]] = []
    mode_changes: set[str] = set()
    current = ""
    rename_from: str | None = None
    old_mode = False
    for line in str(patch or "").splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            current = parts[3][2:] if len(parts) >= 4 and parts[3].startswith("b/") else ""
            rename_from = None
            old_mode = False
        elif line.startswith("deleted file mode ") and current:
            deletions.add(current)
        elif line.startswith("rename from "):
            rename_from = line[len("rename from "):].strip()
        elif line.startswith("rename to "):
            target = line[len("rename to "):].strip()
            if rename_from:
                renames.append({"from": rename_from, "to": target})
            rename_from = None
        elif line.startswith("old mode "):
            old_mode = True
        elif line.startswith("new mode ") and current and old_mode:
            mode_changes.add(current)
            old_mode = False
    return sorted(deletions), renames, sorted(mode_changes)


def run_change_set_from_artifact(artifact: AgentArtifact | None) -> RunChangeSet | None:
    if artifact is None or artifact.kind != "diff":
        return None
    payload = artifact.metadata.get("run_change_set")
    if not isinstance(payload, dict):
        return None
    try:
        return RunChangeSet.model_validate(payload)
    except Exception as exc:
        log_recovered_exception("run change set artifact parsing", exc, level="DEBUG")
        return None
