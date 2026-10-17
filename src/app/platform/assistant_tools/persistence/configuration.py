"""PostgreSQL-backed compatibility functions for small configuration domains."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.persistence.document_store import PostgresDocumentStore
from app.persistence.document_schemas import register_document_schema


def load_assistant_tools_config(path: Path | None = None):
    from app.platform.assistant_tools.config_store import (
        AssistantToolsConfigPayload,
        _merge_known_config,
        default_assistant_tools_config,
    )

    if path is not None:
        raise RuntimeError(
            "file-backed assistant tool configuration is retired; use the legacy importer"
        )
    payload = PostgresDocumentStore().read(
        module="assistant-tools",
        record_type="configuration",
        default=None,
    )
    if payload is None:
        return default_assistant_tools_config()
    return _merge_known_config(AssistantToolsConfigPayload.model_validate(payload))


def save_assistant_tools_config(payload: Any, path: Path | None = None):
    from app.platform.assistant_tools.config_store import _merge_known_config
    from app.platform.assistant_tools.credentials import delete_tool_credential

    if path is not None:
        raise RuntimeError(
            "file-backed assistant tool configuration is retired; use the legacy importer"
        )
    normalized = _merge_known_config(payload)
    for tool in normalized.tools:
        if tool.connection_status != "connected":
            delete_tool_credential(tool.tool_id)
    PostgresDocumentStore().write(
        normalized.model_dump(mode="json"),
        module="assistant-tools",
        record_type="configuration",
    )
    return normalized


def _register_document_schemas() -> None:
    from app.platform.assistant_tools.config_store import AssistantToolsConfigPayload

    register_document_schema("assistant-tools", "configuration", AssistantToolsConfigPayload)


# Document shapes (WP-5.9); config_store imports this module's callers lazily.
_register_document_schemas()
