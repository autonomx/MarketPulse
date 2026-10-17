"""Narrow signed generation artifacts to paths changed by targeted regeneration."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from .generation_authorship import content_hash
from .generation_authorship_signing import (
    harden_and_sign_generation_artifact,
    sign_record,
)


def harden_and_sign_targeted_artifact(
    candidate: Mapping[str, Any],
    artifact: Mapping[str, Any],
    *,
    authored_paths: Iterable[str],
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    selected = {str(path) for path in authored_paths}
    row = harden_and_sign_generation_artifact(candidate, artifact, policy=policy)
    row = deepcopy(dict(row))
    row.pop("server_signature", None)
    row["authored_strings"] = [
        dict(value)
        for value in row.get("authored_strings") or ()
        if isinstance(value, Mapping) and str(value.get("path") or "") in selected
    ]
    row["parsed_payload_hash"] = content_hash(row["authored_strings"])
    unsigned = deepcopy(row)
    unsigned.pop("artifact_hash", None)
    row["artifact_hash"] = content_hash(unsigned)
    return sign_record(row)


__all__ = ["harden_and_sign_targeted_artifact"]
