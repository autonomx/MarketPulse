"""The shape of every document kind in ``omnix_module_records`` (WP-5.9).

Each feature registers the Pydantic shape of the documents it stores
(``register_document_schema``). The generic write paths, the document store
and the module-record repository, refuse a payload that does not match
(``DocumentShapeError``). Documents already stored are checked when they are
read: a mismatch is logged and counted (``omnix_document_shape_mismatches``)
but the document is still returned, so a document written before its shape
was declared cannot take a feature down. ``scripts/check_document_shapes.py``
lists every stored document that does not match.

A kind nobody registered is accepted with a warning; with
``OMNIX_DOCUMENT_SCHEMAS_STRICT=1`` (the test suite) it is refused, so a new
kind cannot ship without its shape.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.config.env import env_bool

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_SCHEMAS: dict[tuple[str, str], TypeAdapter[Any]] = {}
_WARNED: set[tuple[str, str]] = set()


class DocumentShapeError(ValueError):
    """A document payload that does not match its registered shape."""

    def __init__(self, module: str, record_type: str, detail: str) -> None:
        super().__init__(f"document {module}/{record_type} has the wrong shape: {detail}")
        self.module = module
        self.record_type = record_type


def register_document_schema(module: str, record_type: str, schema: Any) -> None:
    """Declare the shape of ``module``/``record_type`` documents (a model or a type)."""
    adapter: TypeAdapter[Any] = TypeAdapter(schema)
    with _LOCK:
        _SCHEMAS[(module, record_type)] = adapter


def registered_document_kinds() -> frozenset[tuple[str, str]]:
    with _LOCK:
        return frozenset(_SCHEMAS)


def _adapter(module: str, record_type: str) -> TypeAdapter[Any] | None:
    with _LOCK:
        return _SCHEMAS.get((module, record_type))


def _summary(error: ValidationError) -> str:
    # Locations and messages only: the payload may hold personal data.
    first = error.errors(include_input=False)[:3]
    return "; ".join(f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: {item['msg']}" for item in first)


def validate_document(module: str, record_type: str, payload: Any) -> None:
    """Refuse a payload that does not match its kind's registered shape."""
    adapter = _adapter(module, record_type)
    if adapter is None:
        if env_bool("OMNIX_DOCUMENT_SCHEMAS_STRICT", False):
            raise DocumentShapeError(module, record_type, "no document schema is registered for this kind")
        if (module, record_type) not in _WARNED:
            _WARNED.add((module, record_type))
            logger.warning("document_schema_missing module=%s record_type=%s", module, record_type)
        return
    try:
        adapter.validate_python(payload)
    except ValidationError as exc:
        raise DocumentShapeError(module, record_type, _summary(exc)) from exc


def document_matches(module: str, record_type: str, payload: Any, *, record_id: str = "") -> bool:
    """Check a stored document; a mismatch is logged and counted, never raised."""
    adapter = _adapter(module, record_type)
    if adapter is None:
        return True
    try:
        adapter.validate_python(payload)
    except ValidationError as exc:
        from app.observability.metrics import record_document_shape_mismatch

        record_document_shape_mismatch(module, record_type)
        logger.warning(
            "document_shape_mismatch module=%s record_type=%s record_id=%s detail=%s",
            module, record_type, record_id, _summary(exc),
        )
        return False
    return True


__all__ = [
    "DocumentShapeError",
    "document_matches",
    "register_document_schema",
    "registered_document_kinds",
    "validate_document",
]
