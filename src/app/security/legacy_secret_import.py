"""One-time migration from the legacy plaintext secrets file."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

from app.errors import LegacyPersistenceRetired
from app.security import provider_secret_store


def legacy_secret_path() -> Path:
    return Path(__file__).resolve().parents[2] / "resources" / "data" / "secrets.json"


def import_legacy_secrets(source: Path | None = None) -> tuple[int, Path | None]:
    """Import recognized credentials into the protected store, then archive source."""
    source_path = (source or legacy_secret_path()).resolve()
    if not source_path.exists():
        return 0, None
    if sys.platform != "win32":
        raise LegacyPersistenceRetired(
            "legacy provider secrets can only be imported into the Windows protected store"
        )

    archive_path = source_path.with_name(source_path.name + ".imported")
    if archive_path.exists():
        raise FileExistsError(f"legacy secret archive already exists: {archive_path}")

    decoded = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("legacy secrets file must contain a JSON object")
    incoming = _recognized_secrets(decoded)
    if not incoming:
        raise ValueError("legacy secrets file contains no recognized credentials")

    with provider_secret_store.payload_lock():
        current = provider_secret_store._stored_payload_for_import()
        merged = _merge_missing(current, incoming)
        count = _credential_count(merged) - _credential_count(current)
        if count:
            provider_secret_store._write_payload(merged)
    os.replace(source_path, archive_path)
    return count, archive_path


def _recognized_secrets(payload: dict[str, Any]) -> dict[str, Any]:
    recognized: dict[str, Any] = {}

    api_keys = payload.get("api_keys")
    if isinstance(api_keys, dict):
        clean_api_keys = {
            provider: str(api_keys.get(provider) or "").strip()
            for provider in provider_secret_store._PROVIDERS
            if str(api_keys.get(provider) or "").strip()
        }
        if clean_api_keys:
            recognized["api_keys"] = clean_api_keys
    else:
        clean_api_keys = {}
        for provider in provider_secret_store._PROVIDERS:
            value = payload.get(provider)
            if isinstance(value, dict):
                value = value.get("api_key")
            clean = str(value or "").strip()
            if clean:
                clean_api_keys[provider] = clean
        if clean_api_keys:
            recognized["api_keys"] = clean_api_keys

    for section, providers in (
        ("research_api_keys", provider_secret_store._RESEARCH_PROVIDERS),
    ):
        values = payload.get(section)
        if isinstance(values, dict):
            clean_research_keys = {
                provider: str(values.get(provider) or "").strip()
                for provider in providers
                if str(values.get(provider) or "").strip()
            }
            if clean_research_keys:
                recognized[section] = clean_research_keys

    trading = payload.get("trading_credentials")
    if isinstance(trading, dict):
        clean_trading: dict[str, dict[str, str]] = {}
        for provider, fields in provider_secret_store._TRADING_ENVIRONMENT_KEYS.items():
            raw = trading.get(provider)
            if not isinstance(raw, dict):
                continue
            values = {
                field: str(raw.get(field) or "").strip()
                for field in fields
                if str(raw.get(field) or "").strip()
            }
            if values:
                clean_trading[provider] = values
        if clean_trading:
            recognized["trading_credentials"] = clean_trading

    return recognized


def _merge_missing(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(current))
    for section, values in incoming.items():
        stored = merged.setdefault(section, {})
        if not isinstance(stored, dict):
            stored = {}
            merged[section] = stored
        if section == "trading_credentials":
            for provider, fields in values.items():
                provider_fields = stored.setdefault(provider, {})
                if not isinstance(provider_fields, dict):
                    provider_fields = {}
                    stored[provider] = provider_fields
                for field, value in fields.items():
                    provider_fields.setdefault(field, value)
        else:
            for key, value in values.items():
                stored.setdefault(key, value)
    return merged


def _credential_count(payload: dict[str, Any]) -> int:
    api_keys = payload.get("api_keys")
    research = payload.get("research_api_keys")
    trading = payload.get("trading_credentials")
    return (
        sum(bool(value) for value in api_keys.values()) if isinstance(api_keys, dict) else 0
    ) + (
        sum(bool(value) for value in research.values()) if isinstance(research, dict) else 0
    ) + (
        sum(
            bool(value)
            for fields in trading.values()
            if isinstance(fields, dict)
            for value in fields.values()
        )
        if isinstance(trading, dict)
        else 0
    )
