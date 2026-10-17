"""Port: the installed model artifacts behind a local TTS provider.

Consumers that bind output to an exact model revision (audiobook rendering)
ask this port where a provider's artifacts live instead of importing the
provider's internals. A provider opts in with the ``local_artifacts``
capability in the catalog and a static ``local_model_artifacts()`` method.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .catalog import LOCAL_ARTIFACTS, specs


class LocalArtifactsUnavailable(ValueError):
    """The provider has no verifiable local artifacts, or they are missing."""


@dataclass(frozen=True)
class LocalModelArtifacts:
    model_id: str
    directory: Path


def local_model_artifacts(provider_id: str) -> LocalModelArtifacts:
    spec = next((spec for spec in specs("tts") if spec.id == provider_id), None)
    if spec is None or LOCAL_ARTIFACTS not in spec.capabilities:
        raise LocalArtifactsUnavailable(
            f"verified local model artifacts are unavailable for {provider_id}"
        )
    # Imported on demand: synthesis dependencies stay out of processes that
    # never ask for a model identity.
    return spec.load().local_model_artifacts()
