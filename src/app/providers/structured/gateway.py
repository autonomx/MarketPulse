"""Central provider-independent structured-output gateway."""
from __future__ import annotations

import contextvars
import math

import hashlib
import json
import threading
from time import monotonic
from typing import Any, Generic, Mapping, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from app.caching.bounded_cache import bounded_lru_cache

from app.providers.base import (
    AuthenticationError,
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ConnectionError as ProviderConnectionError,
    ModelNotFoundError,
    ProviderError,
)
from app.providers.exceptions import RateLimitError

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
from .parsing import (
    canonical_structured_text,
    decode_exact_json_object,
    decode_json_object,
    validate_json_resources,
)
from .schema_projection import project_provider_schema

T = TypeVar("T", bound=BaseModel)


class _NegativeCapabilityCache:
    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self._values: dict[tuple[str, str, str, str, StructuredMode], float] = {}
        self._lock = threading.Lock()

    def mark_unsupported(
        self,
        key: tuple[str, str, str, str],
        mode: StructuredMode,
    ) -> None:
        with self._lock:
            self._values[(*key, mode)] = monotonic() + self.ttl_seconds

    def is_unsupported(
        self,
        key: tuple[str, str, str, str],
        mode: StructuredMode,
    ) -> bool:
        now = monotonic()
        compound = (*key, mode)
        with self._lock:
            expires = self._values.get(compound)
            if expires is None:
                return False
            if expires <= now:
                self._values.pop(compound, None)
                return False
            return True


_NEGATIVE_CAPABILITIES = _NegativeCapabilityCache()


@bounded_lru_cache(max_entries=256, ttl_seconds=3600.0)
def output_schema_text(output_model: type[BaseModel]) -> str:
    """An output model's JSON schema as canonical text, built once per model.

    Pydantic builds the schema from scratch on every call; a structured call
    needs it two or three times (WP-5.6: an RPG turn spent ~30 ms on it).
    """
    return json.dumps(output_model.model_json_schema(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _output_schema(output_model: type[BaseModel]) -> dict[str, Any]:
    """A fresh copy of the output model's JSON schema (callers may change it)."""
    return json.loads(output_schema_text(output_model))


def _validation_issues(error: ValidationError) -> tuple[StructuredValidationIssue, ...]:
    issues: list[StructuredValidationIssue] = []
    for row in error.errors(include_url=False):
        context = row.get("ctx")
        issues.append(
            StructuredValidationIssue(
                path=tuple(row.get("loc") or ()),
                error_type=str(row.get("type") or "validation_error"),
                message=str(row.get("msg") or "validation failed"),
                context=dict(context) if isinstance(context, Mapping) else None,
            )
        )
    return tuple(issues)


def _unsupported_mode_error(error: Exception) -> bool:
    """Return true only for an adapter-classified capability rejection."""

    return isinstance(error, UnsupportedStructuredMode)


def _provider_identity(
    provider: Any,
    model: str,
    schema_scope: str,
) -> tuple[str, str, str, str]:
    provider_name = str(getattr(provider, "provider_name", provider.__class__.__name__) or "")
    config = getattr(provider, "config", None)
    base_url = str(getattr(config, "base_url", "") or "")
    return provider_name.casefold(), base_url, model, schema_scope


def _provider_capabilities(provider: Any, model: str) -> StructuredCapabilities:
    method = getattr(provider, "get_structured_capabilities", None)
    if callable(method):
        try:
            value = method(model=model or None)
        except TypeError:
            value = method()
        if isinstance(value, StructuredCapabilities):
            return value
    return StructuredCapabilities.default_for_provider(
        str(getattr(provider, "provider_name", "") or "")
    )


def _response_options(
    contract: StructuredContract[Any],
    *,
    mode: StructuredMode,
    provider_name: str,
    capabilities: StructuredCapabilities,
) -> dict[str, Any]:
    schema = project_provider_schema(
        _output_schema(contract.output_model),
        mode=mode,
        provider_name=provider_name,
        schema_profile=contract.schema_profile,
    )
    if mode is StructuredMode.JSON_SCHEMA:
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": contract.provider_schema_name,
                    "strict": capabilities.supports_strict_schema,
                    "schema": schema,
                },
            }
        }
    if mode is StructuredMode.JSON_OBJECT:
        return {"response_format": {"type": "json_object"}}
    if mode is StructuredMode.TEXT_JSON:
        return {"response_format": {"type": "text"}}
    if mode is StructuredMode.TOOL_CALL:
        tool_name = contract.provider_schema_name
        return {
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": f"Return {contract.qualified_id}",
                        "parameters": schema,
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
        }
    raise UnsupportedStructuredMode(f"unsupported structured mode: {mode.value}")


def _correction_message(
    contract: StructuredContract[Any],
    error: StructuredOutputError,
) -> ChatMessage:
    issues = getattr(error, "issues", ())
    payload = {
        "contract_id": contract.contract_id,
        "contract_version": contract.version,
        "reason": type(error).__name__,
        "message": str(error),
        "errors": [issue.as_dict() for issue in issues],
        "instruction": (
            "Regenerate the complete response. Return exactly one JSON object matching "
            "the contract. Do not explain, patch, omit invalid rows, or wrap the object."
        ),
    }
    # Validation feedback is gateway control-plane instruction, not a new
    # user turn. Emitting it as role=user can make semantic classifiers treat
    # "Regenerate the complete response" as the latest authoritative request.
    return ChatMessage(
        role="system",
        content="STRUCTURED_OUTPUT_CORRECTION:\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )


def _messages_with_correction(
    messages: Sequence[ChatMessage],
    correction: ChatMessage,
) -> list[ChatMessage]:
    """Keep validation control separate from and before the final user turn."""

    rows = list(messages)
    for index in range(len(rows) - 1, -1, -1):
        if str(rows[index].role or "").strip().casefold() == "user":
            return [*rows[:index], correction, *rows[index:]]
    return [*rows, correction]


def _normalize_provider_error(error: Exception) -> Exception:
    """Classify transport failures without turning permanent errors into retries."""

    if isinstance(
        error,
        (
            StructuredOutputError,
            UnsupportedStructuredMode,
            ProviderTransportError,
            ProviderEmptyResponse,
            ProviderTimeout,
            ProviderTruncatedResponse,
        ),
    ):
        return error
    if isinstance(error, TimeoutError):
        return ProviderTimeout(f"provider call timed out: {error}")
    if isinstance(error, (RateLimitError, ProviderConnectionError)):
        return ProviderTransportError(f"{type(error).__name__}: {error}")
    if isinstance(error, (AuthenticationError, ModelNotFoundError, ProviderError)):
        return error
    if isinstance(error, OSError):
        return ProviderTransportError(f"{type(error).__name__}: {error}")
    return error


def _provider_call_with_deadline(
    provider: Any,
    messages: Sequence[ChatMessage],
    *,
    model: str,
    options: Mapping[str, Any],
    deadline: float,
    operation_id: str,
) -> Any:
    """Bound caller wait time and pass the remaining budget to provider adapters.

    The daemon wrapper guarantees that a non-cooperative provider cannot block the
    structured-output caller beyond the operation deadline. HTTP-backed adapters use
    ``request_timeout_seconds`` to stop the underlying request as well.
    """

    # Floor to whole microseconds: ``(start + budget) - now`` can exceed the
    # budget by float error when the monotonic clock has not advanced.
    remaining = math.floor((deadline - monotonic()) * 1_000_000) / 1_000_000
    if remaining <= 0:
        raise ProviderTimeout(f"structured operation {operation_id} exceeded its deadline")

    call_options = dict(options)
    configured_hint = call_options.get("request_timeout_seconds")
    if configured_hint is None:
        call_options["request_timeout_seconds"] = remaining
    else:
        try:
            call_options["request_timeout_seconds"] = min(
                max(0.001, float(configured_hint)),
                remaining,
            )
        except (TypeError, ValueError):
            call_options["request_timeout_seconds"] = remaining

    completed = threading.Event()
    result: dict[str, Any] = {}

    def invoke() -> None:
        try:
            result["value"] = provider.chat_completion(
                list(messages),
                model=model or None,
                stream=False,
                **call_options,
            )
        except BaseException as exc:
            result["error"] = exc
        finally:
            completed.set()

    # The call runs in the caller's context (job turn owner, tenant), as a
    # direct call would; a fresh thread otherwise starts with empty context.
    context = contextvars.copy_context()
    worker = threading.Thread(
        target=context.run,
        args=(invoke,),
        name=f"omnix-structured-{operation_id}",
        daemon=True,
    )
    worker.start()
    if not completed.wait(timeout=remaining):
        raise ProviderTimeout(
            f"structured operation {operation_id} exceeded its deadline during provider call"
        )
    if "error" in result:
        error = result["error"]
        if isinstance(error, BaseException):
            raise error
    if "value" not in result:
        raise ProviderTransportError("structured provider completed without a response")
    return result["value"]


class StructuredOutputGateway(Generic[T]):
    """Obtain, decode, and validate one provider-independent structured value."""

    def __init__(self, provider: BaseProvider | Any) -> None:
        self.provider = provider
        self.last_diagnostics: StructuredDiagnostics | None = None

    def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        contract: StructuredContract[T],
        model: str | None = None,
        retry_budget: StructuredRetryBudget | None = None,
        provider_options: Mapping[str, Any] | None = None,
    ) -> T:
        outcome = self.try_generate(
            messages,
            contract=contract,
            model=model,
            retry_budget=retry_budget,
            provider_options=provider_options,
        )
        if outcome.error is not None:
            raise outcome.error
        assert outcome.value is not None
        return outcome.value

    def try_generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        contract: StructuredContract[T],
        model: str | None = None,
        retry_budget: StructuredRetryBudget | None = None,
        provider_options: Mapping[str, Any] | None = None,
    ) -> StructuredOutcome[T]:
        budget = retry_budget or StructuredRetryBudget()
        config = getattr(self.provider, "config", None)
        resolved_model = str(model or getattr(config, "model", "") or "")
        provider_name = str(
            getattr(self.provider, "provider_name", self.provider.__class__.__name__) or ""
        )
        schema_text = output_schema_text(contract.output_model)
        schema_hash = hashlib.sha256(schema_text.encode("utf-8")).hexdigest()
        provider_schema_hash = ""
        cache_scope = f"{contract.schema_profile}:{schema_hash}"
        identity = _provider_identity(self.provider, resolved_model, cache_scope)
        capabilities = _provider_capabilities(self.provider, resolved_model)
        modes = tuple(
            mode
            for mode in capabilities.preferred_modes
            if not _NEGATIVE_CAPABILITIES.is_unsupported(identity, mode)
        ) or (StructuredMode.TEXT_JSON,)
        started = monotonic()
        deadline = started + budget.deadline_seconds
        provider_calls = 0
        transport_retries = 0
        format_downgrades = 0
        validation_regenerations = 0
        attempted_modes: list[StructuredMode] = []
        selected_mode: StructuredMode | None = None
        finish_reason = ""
        usage: dict[str, Any] = {}
        raw_length = 0
        raw_hash = ""
        last_error: Exception | None = None
        working_messages = list(messages)
        mode_index = 0

        def diagnostics() -> StructuredDiagnostics:
            return StructuredDiagnostics(
                contract_id=contract.contract_id,
                contract_version=contract.version,
                schema_hash=schema_hash,
                provider=provider_name,
                model=resolved_model,
                provider_schema_hash=provider_schema_hash,
                selected_mode=selected_mode,
                attempted_modes=tuple(attempted_modes),
                provider_calls=provider_calls,
                transport_retries=transport_retries,
                format_downgrades=format_downgrades,
                validation_regenerations=validation_regenerations,
                finish_reason=finish_reason,
                latency_ms=round((monotonic() - started) * 1000.0, 3),
                usage=usage,
                raw_response_length=raw_length,
                raw_response_hash=raw_hash,
            )

        while provider_calls < budget.max_provider_calls and mode_index < len(modes):
            if monotonic() >= deadline:
                last_error = ProviderTimeout(
                    f"structured operation {contract.qualified_id} exceeded its deadline"
                )
                break
            mode = modes[mode_index]
            selected_mode = mode
            attempted_modes.append(mode)
            options = dict(provider_options or {})
            options.setdefault("temperature", contract.temperature)
            if contract.max_tokens is not None:
                options.setdefault("max_tokens", contract.max_tokens)
            projected_schema = project_provider_schema(
                _output_schema(contract.output_model),
                mode=mode,
                provider_name=provider_name,
                schema_profile=contract.schema_profile,
            )
            provider_schema_hash = hashlib.sha256(
                json.dumps(
                    projected_schema,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            options.update(
                _response_options(
                    contract,
                    mode=mode,
                    provider_name=provider_name,
                    capabilities=capabilities,
                )
            )
            provider_calls += 1
            try:
                response = _provider_call_with_deadline(
                    self.provider,
                    working_messages,
                    model=resolved_model,
                    options=options,
                    deadline=deadline,
                    operation_id=contract.qualified_id,
                )
                if monotonic() > deadline:
                    raise ProviderTimeout(
                        f"structured operation {contract.qualified_id} exceeded its deadline"
                    )
                if not isinstance(response, ChatResponse):
                    raise ProviderTransportError(
                        "structured provider returned a streaming or invalid response"
                    )
                finish_reason = str(response.finish_reason or "")
                usage = dict(response.usage or {})
                if finish_reason.casefold() in {"length", "max_tokens", "token_limit"}:
                    raise ProviderTruncatedResponse(
                        f"structured provider truncated {contract.qualified_id}"
                    )
                raw_text = canonical_structured_text(response)
                if (
                    contract.max_raw_bytes is not None
                    and len(raw_text.encode("utf-8")) > contract.max_raw_bytes
                ):
                    raise StructuredResourceError(
                        f"structured provider response exceeds {contract.max_raw_bytes} bytes"
                    )
                raw_length = len(raw_text)
                raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
                payload = (
                    decode_exact_json_object(raw_text)
                    if contract.exact_json_object
                    else decode_json_object(raw_text)
                )
                validate_json_resources(
                    payload,
                    max_depth=contract.max_json_depth,
                    max_nodes=contract.max_json_nodes,
                    max_string_length=contract.max_json_string_length,
                    max_array_length=contract.max_json_array_length,
                )
                try:
                    value = contract.output_model.model_validate(payload)
                except ValidationError as exc:
                    raise StructuredSchemaError(
                        f"{contract.qualified_id} failed schema validation",
                        _validation_issues(exc),
                    ) from exc
                if contract.semantic_validator is not None:
                    try:
                        contract.semantic_validator(value)
                    except StructuredSemanticError:
                        raise
                    except Exception as exc:
                        raise StructuredSemanticError(
                            f"{contract.qualified_id} failed semantic validation: {exc}"
                        ) from exc
                result_diagnostics = diagnostics()
                self.last_diagnostics = result_diagnostics
                return StructuredOutcome.success(value, result_diagnostics)
            except BaseException as raw_error:
                if not isinstance(raw_error, Exception):
                    raise
                normalized_error = _normalize_provider_error(raw_error)
                last_error = normalized_error
                if _unsupported_mode_error(normalized_error):
                    _NEGATIVE_CAPABILITIES.mark_unsupported(identity, mode)
                    if (
                        format_downgrades < budget.max_format_downgrades
                        and mode_index + 1 < len(modes)
                    ):
                        format_downgrades += 1
                        mode_index += 1
                        continue
                if isinstance(
                    normalized_error,
                    (StructuredDecodeError, StructuredSchemaError),
                ) or (
                    isinstance(normalized_error, StructuredSemanticError)
                    and contract.regenerate_on_semantic_failure
                ):
                    if validation_regenerations < budget.max_validation_regenerations:
                        validation_regenerations += 1
                        working_messages = _messages_with_correction(
                            messages,
                            _correction_message(contract, normalized_error),
                        )
                        continue
                if isinstance(
                    normalized_error,
                    (
                        ProviderTransportError,
                        ProviderEmptyResponse,
                        ProviderTimeout,
                        ProviderTruncatedResponse,
                    ),
                ) and transport_retries < budget.max_transport_retries:
                    transport_retries += 1
                    continue
                break

        if isinstance(last_error, StructuredOutputError):
            final_error: Exception = last_error
        else:
            final_error = StructuredOutputExhausted(
                f"structured operation {contract.qualified_id} failed after "
                f"{provider_calls} provider call(s)",
                last_error=last_error,
            )
        result_diagnostics = diagnostics()
        self.last_diagnostics = result_diagnostics
        return StructuredOutcome.failure(final_error, result_diagnostics)
