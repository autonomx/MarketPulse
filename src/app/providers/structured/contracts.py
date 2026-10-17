"""Provider-independent structured-output contracts and diagnostics."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Generic, Mapping, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class StructuredMode(str, Enum):
    JSON_SCHEMA = "json_schema"
    TOOL_CALL = "tool_call"
    JSON_OBJECT = "json_object"
    TEXT_JSON = "text_json"


@dataclass(frozen=True)
class StructuredCapabilities:
    preferred_modes: tuple[StructuredMode, ...]
    supports_strict_schema: bool = False
    supports_tool_arguments: bool = False

    @classmethod
    def default_for_provider(cls, provider_name: str) -> "StructuredCapabilities":
        from app.providers.catalog import CLOSED_OBJECT_SCHEMA, NATIVE_JSON_SCHEMA, provider_capabilities

        capabilities = provider_capabilities(provider_name)
        if NATIVE_JSON_SCHEMA in capabilities:
            return cls(
                preferred_modes=(StructuredMode.JSON_SCHEMA, StructuredMode.TEXT_JSON),
                supports_strict_schema=True,
            )
        if CLOSED_OBJECT_SCHEMA in capabilities:
            # The Codex app-server transport does not expose a native
            # response_format field, so the provider projects JSON Schema into
            # its system instructions. Prefer the schema-bearing mode instead
            # of losing the contract by starting with generic JSON_OBJECT.
            return cls(
                preferred_modes=(StructuredMode.JSON_SCHEMA, StructuredMode.TEXT_JSON),
                supports_strict_schema=False,
            )
        return cls(
            preferred_modes=(StructuredMode.JSON_OBJECT, StructuredMode.TEXT_JSON),
        )


@dataclass(frozen=True)
class StructuredContract(Generic[T]):
    contract_id: str
    version: int
    output_model: type[T]
    semantic_validator: Callable[[T], None] | None = None
    schema_profile: str = "default"
    schema_name: str = ""
    temperature: float = 0.0
    max_tokens: int | None = None
    regenerate_on_semantic_failure: bool = True
    exact_json_object: bool = False
    max_raw_bytes: int | None = None
    max_json_depth: int | None = None
    max_json_nodes: int | None = None
    max_json_string_length: int | None = None
    max_json_array_length: int | None = None

    @property
    def qualified_id(self) -> str:
        return f"{self.contract_id}.v{self.version}"

    @property
    def provider_schema_name(self) -> str:
        if self.schema_name:
            return self.schema_name
        return self.contract_id.replace(".", "_").replace("-", "_")


@dataclass(frozen=True)
class StructuredRetryBudget:
    max_provider_calls: int = 3
    max_transport_retries: int = 1
    max_format_downgrades: int = 1
    max_validation_regenerations: int = 1
    deadline_seconds: float = 90.0

    def __post_init__(self) -> None:
        if self.max_provider_calls < 1:
            raise ValueError("max_provider_calls must be at least one")
        if self.deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        for name in (
            "max_transport_retries",
            "max_format_downgrades",
            "max_validation_regenerations",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")


@dataclass(frozen=True)
class StructuredDiagnostics:
    contract_id: str
    contract_version: int
    schema_hash: str
    provider: str
    model: str
    provider_schema_hash: str = ""
    selected_mode: StructuredMode | None = None
    attempted_modes: tuple[StructuredMode, ...] = ()
    provider_calls: int = 0
    transport_retries: int = 0
    format_downgrades: int = 0
    validation_regenerations: int = 0
    finish_reason: str = ""
    latency_ms: float = 0.0
    usage: Mapping[str, Any] = field(default_factory=dict)
    raw_response_length: int = 0
    raw_response_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "schema_hash": self.schema_hash,
            "provider_schema_hash": self.provider_schema_hash,
            "provider": self.provider,
            "model": self.model,
            "selected_mode": self.selected_mode.value if self.selected_mode else "",
            "attempted_modes": [mode.value for mode in self.attempted_modes],
            "provider_calls": self.provider_calls,
            "transport_retries": self.transport_retries,
            "format_downgrades": self.format_downgrades,
            "validation_regenerations": self.validation_regenerations,
            "finish_reason": self.finish_reason,
            "latency_ms": self.latency_ms,
            "usage": dict(self.usage),
            "raw_response_length": self.raw_response_length,
            "raw_response_hash": self.raw_response_hash,
        }


@dataclass(frozen=True)
class StructuredOutcome(Generic[T]):
    value: T | None
    diagnostics: StructuredDiagnostics
    error: Exception | None = None

    @property
    def succeeded(self) -> bool:
        return self.value is not None and self.error is None

    @classmethod
    def success(cls, value: T, diagnostics: StructuredDiagnostics) -> "StructuredOutcome[T]":
        return cls(value=value, diagnostics=diagnostics)

    @classmethod
    def failure(
        cls,
        error: Exception,
        diagnostics: StructuredDiagnostics,
    ) -> "StructuredOutcome[T]":
        return cls(value=None, diagnostics=diagnostics, error=error)
