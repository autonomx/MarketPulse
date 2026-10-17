"""Typed failures for provider-independent structured output."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.providers.base import ProviderError


class StructuredOutputError(RuntimeError):
    """Base failure raised by the structured-output boundary."""


class UnsupportedStructuredMode(ProviderError):
    """The provider/model endpoint rejected a requested structured mode."""


class ProviderTimeout(ProviderError):
    """The provider did not complete within the operation deadline."""


class ProviderTruncatedResponse(ProviderError):
    """The provider reported token exhaustion or returned truncated output."""


class ProviderTransportError(ProviderError):
    """A provider transport failure occurred while generating structured output."""


class ProviderEmptyResponse(ProviderError):
    """The provider returned no structured content."""


class StructuredDecodeError(StructuredOutputError):
    """Provider content could not be decoded as one JSON object."""


class StructuredResourceError(StructuredOutputError):
    """Provider content exceeded a configured structured-payload limit."""


@dataclass(frozen=True)
class StructuredValidationIssue:
    path: tuple[str | int, ...]
    error_type: str
    message: str
    context: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": list(self.path),
            "type": self.error_type,
            "message": self.message,
        }
        if self.context:
            payload["context"] = dict(self.context)
        return payload


class StructuredSchemaError(StructuredOutputError):
    """Decoded JSON failed the authoritative Pydantic schema."""

    def __init__(self, message: str, issues: Sequence[StructuredValidationIssue] = ()) -> None:
        super().__init__(message)
        self.issues = tuple(issues)


class StructuredSemanticError(StructuredOutputError):
    """Typed output failed feature-owned semantic validation."""

    def __init__(self, message: str, issues: Sequence[StructuredValidationIssue] = ()) -> None:
        super().__init__(message)
        self.issues = tuple(issues)


class StructuredOutputExhausted(StructuredOutputError):
    """The operation exhausted its deadline or provider-call budget."""

    def __init__(self, message: str, *, last_error: Exception | None = None) -> None:
        super().__init__(message)
        self.last_error = last_error
