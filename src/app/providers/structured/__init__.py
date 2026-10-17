"""Provider-independent typed structured-output boundary."""

from .contracts import (
    StructuredCapabilities,
    StructuredContract,
    StructuredDiagnostics,
    StructuredMode,
    StructuredOutcome,
    StructuredRetryBudget,
)
from .errors import (
    ProviderEmptyResponse,
    ProviderTimeout,
    ProviderTransportError,
    ProviderTruncatedResponse,
    StructuredDecodeError,
    StructuredOutputError,
    StructuredOutputExhausted,
    StructuredResourceError,
    StructuredSchemaError,
    StructuredSemanticError,
    StructuredValidationIssue,
    UnsupportedStructuredMode,
)
from .gateway import StructuredOutputGateway

__all__ = [
    "ProviderEmptyResponse",
    "ProviderTimeout",
    "ProviderTransportError",
    "ProviderTruncatedResponse",
    "StructuredCapabilities",
    "StructuredContract",
    "StructuredDecodeError",
    "StructuredDiagnostics",
    "StructuredMode",
    "StructuredOutcome",
    "StructuredOutputError",
    "StructuredOutputExhausted",
    "StructuredOutputGateway",
    "StructuredResourceError",
    "StructuredRetryBudget",
    "StructuredSchemaError",
    "StructuredSemanticError",
    "StructuredValidationIssue",
    "UnsupportedStructuredMode",
]
