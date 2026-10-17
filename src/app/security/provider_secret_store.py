"""OS-protected local storage for provider API keys and trading credentials.

Provider credentials must not be stored in PostgreSQL or the settings document.
On Windows, DPAPI provides a user-scoped encrypted store suitable for the local
Omnix process. Explicit process-environment values remain authoritative.
"""
from __future__ import annotations

from app.config.env import environment
from app.errors import DependencyUnavailable, LegacyPersistenceRetired

import ctypes
import functools
import json
import os
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Any, TypeVar, cast


_PROVIDERS = ("openrouter", "cerebras")
_ENVIRONMENT_KEYS = {
    "openrouter": "OPENROUTER_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
}
_RESEARCH_PROVIDERS = ("brave", "tavily")
_RESEARCH_ENVIRONMENT_KEYS: dict[str, tuple[str, ...]] = {
    "brave": ("OMNIX_BRAVE_SEARCH_API_KEY", "BRAVE_SEARCH_API_KEY"),
    "tavily": ("OMNIX_TAVILY_SEARCH_API_KEY", "TAVILY_API_KEY"),
}
_LEGACY_RESEARCH_ENVIRONMENT_KEY = "OMNIX_WEB_SEARCH_API_KEY"
_LEGACY_RESEARCH_PROVIDER_ENVIRONMENT_KEY = "OMNIX_WEB_SEARCH_PROVIDER"
_TRADING_ENVIRONMENT_KEYS: dict[str, dict[str, tuple[str, ...]]] = {
    "alpaca_iex": {
        "api_key_id": ("OMNIX_ALPACA_API_KEY_ID", "APCA_API_KEY_ID"),
        "secret_key": ("OMNIX_ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY"),
    },
    "coinmarketcap": {
        "api_key": ("COINMARKETCAP_API_KEY", "CMC_PRO_API_KEY"),
    },
    # TVP-10.5: the economic calendar's FRED key (decision D-3).
    "fred": {
        "api_key": ("OMNIX_FRED_API_KEY", "FRED_API_KEY"),
    },
    # MarketPulse S-7: a read-only token for the research data repository, used when the gh CLI is absent.
    "prospective_gap_github": {
        "token": ("OMNIX_TRADING_PROSPECTIVE_GAP_GITHUB_TOKEN",),
    },
}
# Every provider in the table above is loaded and stored (a provider missing here was never read back).
_TRADING_PROVIDERS = tuple(_TRADING_ENVIRONMENT_KEYS)
_DESCRIPTION = "Omnix provider API keys"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_ENVIRONMENT_OWNED_MARKER = b"OMNIX_ENVIRONMENT_OWNED_PROVIDER_KEYS\n"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def provider_secret_path() -> Path:
    configured = environment().get("OMNIX_PROVIDER_SECRETS_PATH", "").strip()
    if configured:
        return Path(configured)
    local_app_data = environment().get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        local_app_data = str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / "Omnix" / "secrets" / "provider-api-keys.dpapi"


_PAYLOAD_LOCK = threading.RLock()
_LOCK_STATE = threading.local()


# Windows' msvcrt.locking(LK_LOCK) gives up after about ten seconds; match it elsewhere.
_LOCK_TIMEOUT_SECONDS = 10.0

# The file lock follows the operating system this process runs on, chosen once at import: the store's own platform
# checks read sys.platform when called (tests set it to exercise the Windows store), the lock primitive must not.
if sys.platform == "win32":
    import msvcrt

    def _lock_file(handle: Any) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file(handle: Any) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file(handle: Any) -> None:
        handle.seek(0)
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("the protected credential store is locked by another process") from None
                time.sleep(0.05)

    def _unlock_file(handle: Any) -> None:
        handle.seek(0)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def payload_lock() -> Iterator[None]:
    """Serialise read-modify-write of the protected file across threads and processes."""
    with _PAYLOAD_LOCK:
        depth = getattr(_LOCK_STATE, "depth", 0)
        if depth:
            _LOCK_STATE.depth = depth + 1
            try:
                yield
            finally:
                _LOCK_STATE.depth = depth
            return
        lock_path = provider_secret_path().with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+b") as handle:
            _lock_file(handle)
            _LOCK_STATE.depth = 1
            try:
                yield
            finally:
                _LOCK_STATE.depth = 0
                _unlock_file(handle)


_F = TypeVar("_F", bound=Callable[..., Any])


def _serialized(function: _F) -> _F:
    @functools.wraps(function)
    def locked(*args: Any, **kwargs: Any) -> Any:
        with payload_lock():
            return function(*args, **kwargs)

    return cast(_F, locked)


def _input_blob(value: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(value)
    blob = _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def os_credential_store_available() -> bool:
    """Whether credentials can be kept protected here: DPAPI on Windows, the login Keychain on macOS (S-8)."""
    if sys.platform == "win32":
        return True
    if sys.platform == "darwin":
        from app.security.keychain import keychain_available

        return keychain_available()
    return False


def credential_store_label() -> str:
    """How the settings pages name the store that keeps user-entered credentials."""
    if sys.platform == "win32":
        return "Windows DPAPI user store"
    return "macOS Keychain" if os_credential_store_available() else "environment only"


def _protect(value: bytes) -> bytes:
    if sys.platform == "darwin" and os_credential_store_available():
        from app.security import keychain

        return keychain.protect(value)
    if sys.platform != "win32":
        raise LegacyPersistenceRetired("provider-key editing requires an operating-system credential store")
    source, source_buffer = _input_blob(value)
    result = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        _DESCRIPTION,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(result),
    ):
        raise ctypes.WinError()
    try:
        _ = source_buffer
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def _unprotect(value: bytes) -> bytes:
    if sys.platform == "darwin" and os_credential_store_available():
        from app.security import keychain

        return keychain.unprotect(value)
    if sys.platform != "win32":
        raise LegacyPersistenceRetired("provider-key editing requires an operating-system credential store")
    source, source_buffer = _input_blob(value)
    result = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(result),
    ):
        raise ctypes.WinError()
    try:
        _ = source_buffer
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


class ProviderSecretStoreUnavailable(DependencyUnavailable):
    """The protected store exists but cannot be read now (HTTP 503).

    A write must never start from an unreadable store (it would replace every
    stored credential), and a reader must not mistake it for an empty store.
    """

    code = "credential_store_unavailable"

    def __init__(self, message: str) -> None:
        path = provider_secret_path()
        super().__init__(
            message,
            hint=(
                f"Omnix keeps credentials in {path} and did not change it. If another Omnix "
                "process holds it, retry. If it is damaged or was protected by another Windows "
                "user, restore it from a backup, or move it aside and enter the credentials again."
            ),
        )


def _read_payload(*, strict: bool) -> dict[str, Any]:
    path = provider_secret_path()
    if not path.exists():
        return {}
    try:
        if strict:
            # Writers replace the file under this lock; a strict reader holds it too,
            # so it never sees a store that a writer is about to change.
            with payload_lock():
                raw = path.read_bytes()
        else:
            # Lookups do not queue behind writers: a replace is atomic, so a read sees
            # the old or the new file; a read that meets the swap is retried.
            raw = _read_bytes_with_retry(path)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        if strict:
            raise ProviderSecretStoreUnavailable("the protected credential store cannot be read") from exc
        return {}
    if raw == _ENVIRONMENT_OWNED_MARKER:
        return {}
    try:
        payload = json.loads(_unprotect(raw).decode("utf-8"))
    except (OSError, UnicodeError, ValueError, LegacyPersistenceRetired) as exc:
        if strict:
            raise ProviderSecretStoreUnavailable("the protected credential store cannot be decrypted") from exc
        return {}
    if not isinstance(payload, dict):
        if strict:
            raise ProviderSecretStoreUnavailable("the protected credential store is not a JSON object")
        return {}
    return payload


def _read_bytes_with_retry(path: Path) -> bytes:
    for attempt in range(_READ_ATTEMPTS):
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise
        except OSError:
            if attempt == _READ_ATTEMPTS - 1:
                raise
            time.sleep(0.005 * 2**attempt)
    raise AssertionError("unreachable")


_READ_ATTEMPTS = 6


def _stored_payload() -> dict[str, Any]:
    """Lenient read for credential lookups: an unreadable store reads as empty.

    Never write what a lenient read returned: writes start from _stored_payload_strict().
    """
    return _read_payload(strict=False)


def _stored_payload_strict() -> dict[str, Any]:
    """Read before a write, or where absent and unreadable must differ: failures raise."""
    return _read_payload(strict=True)


def _stored_payload_for_import() -> dict[str, Any]:
    """Read existing protected data strictly before a one-time import merge."""
    path = provider_secret_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    if raw == _ENVIRONMENT_OWNED_MARKER:
        return {}
    try:
        payload = json.loads(_unprotect(raw).decode("utf-8"))
    except (OSError, UnicodeError, ValueError, LegacyPersistenceRetired) as exc:
        raise LegacyPersistenceRetired(
            "existing provider credential store is unreadable; import was not applied"
        ) from exc
    if not isinstance(payload, dict):
        raise LegacyPersistenceRetired(
            "existing provider credential store is invalid; import was not applied"
        )
    return payload


def _write_payload(payload: dict[str, Any]) -> None:
    path = provider_secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    protected = _protect(json.dumps(payload, sort_keys=True).encode("utf-8"))
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(protected)
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            # Another process (an older reader, an indexer or antivirus) has the file open.
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.01 * 2**attempt)


_REPLACE_ATTEMPTS = 8


def _stored_api_keys(payload: dict[str, Any] | None = None) -> dict[str, str]:
    api_keys = (_stored_payload() if payload is None else payload).get("api_keys")
    if not isinstance(api_keys, dict):
        return {}
    return {provider: str(api_keys.get(provider) or "") for provider in _PROVIDERS}


def _stored_research_api_keys(payload: dict[str, Any] | None = None) -> dict[str, str]:
    api_keys = (_stored_payload() if payload is None else payload).get("research_api_keys")
    if not isinstance(api_keys, dict):
        return {}
    return {provider: str(api_keys.get(provider) or "") for provider in _RESEARCH_PROVIDERS}


def _stored_trading_credentials(payload: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    credentials = (_stored_payload() if payload is None else payload).get("trading_credentials")
    if not isinstance(credentials, dict):
        return {}
    output: dict[str, dict[str, str]] = {}
    for provider in _TRADING_PROVIDERS:
        raw = credentials.get(provider)
        if not isinstance(raw, dict):
            continue
        output[provider] = {
            field: str(raw.get(field) or "")
            for field in _TRADING_ENVIRONMENT_KEYS[provider]
        }
    return output


def _first_environment_value(keys: tuple[str, ...]) -> str:
    for key in keys:
        value = environment().get(key, "").strip()
        if value:
            return value
    return ""


def _research_environment_value(provider: str) -> str:
    keys = _RESEARCH_ENVIRONMENT_KEYS.get(provider)
    return _first_environment_value(keys) if keys else ""


def _legacy_research_environment_value() -> str:
    return environment().get(_LEGACY_RESEARCH_ENVIRONMENT_KEY, "").strip()


def _legacy_research_provider() -> str:
    provider = environment().get(_LEGACY_RESEARCH_PROVIDER_ENVIRONMENT_KEY, "brave").strip().lower()
    return provider if provider in _RESEARCH_PROVIDERS else "brave"


def load_provider_secrets() -> dict[str, Any]:
    api_keys = _stored_api_keys()
    for provider, environment_key in _ENVIRONMENT_KEYS.items():
        environment_value = environment().get(environment_key, "").strip()
        if environment_value:
            api_keys[provider] = environment_value
    return {"api_keys": {provider: api_keys.get(provider, "") for provider in _PROVIDERS}}


def load_research_provider_secrets() -> dict[str, str]:
    """Return independent search-provider credentials without exposing them to settings JSON.

    Provider-specific environment variables are authoritative. The legacy shared
    ``OMNIX_WEB_SEARCH_API_KEY`` remains a compatibility fallback for exactly one
    provider selected by ``OMNIX_WEB_SEARCH_PROVIDER`` (Brave when unspecified).
    """

    api_keys = _stored_research_api_keys()
    legacy_value = _legacy_research_environment_value()
    legacy_provider = _legacy_research_provider()
    for provider in _RESEARCH_PROVIDERS:
        environment_value = _research_environment_value(provider)
        if environment_value:
            api_keys[provider] = environment_value
        elif legacy_value and provider == legacy_provider:
            api_keys[provider] = legacy_value
        else:
            api_keys.setdefault(provider, "")
    return {provider: api_keys.get(provider, "") for provider in _RESEARCH_PROVIDERS}


def research_provider_credential_source(provider: str) -> str:
    if provider not in _RESEARCH_ENVIRONMENT_KEYS:
        raise ValueError("unsupported_research_provider")
    if _research_environment_value(provider):
        return "environment"
    if _legacy_research_environment_value() and provider == _legacy_research_provider():
        return "legacy_environment"
    if _stored_research_api_keys().get(provider):
        return "os_protected_store"
    return "missing"


def research_provider_credential_editable(provider: str) -> bool:
    if provider not in _RESEARCH_ENVIRONMENT_KEYS:
        raise ValueError("unsupported_research_provider")
    source = research_provider_credential_source(provider)
    return os_credential_store_available() and source not in {"environment", "legacy_environment"}


def load_trading_provider_secrets() -> dict[str, dict[str, str]]:
    """Return trading credentials with process-environment values authoritative."""

    credentials = _stored_trading_credentials()
    for provider in _TRADING_PROVIDERS:
        current = dict(credentials.get(provider) or {})
        for field, environment_keys in _TRADING_ENVIRONMENT_KEYS[provider].items():
            environment_value = _first_environment_value(environment_keys)
            if environment_value:
                current[field] = environment_value
            else:
                current.setdefault(field, "")
        credentials[provider] = current
    return credentials


def trading_provider_credential_sources(provider: str) -> dict[str, str]:
    if provider not in _TRADING_ENVIRONMENT_KEYS:
        raise ValueError("unsupported_trading_provider")
    stored = _stored_trading_credentials().get(provider, {})
    sources: dict[str, str] = {}
    for field, environment_keys in _TRADING_ENVIRONMENT_KEYS[provider].items():
        if _first_environment_value(environment_keys):
            sources[field] = "environment"
        elif stored.get(field):
            sources[field] = "os_protected_store"
        else:
            sources[field] = "missing"
    return sources


def _save_environment_owned_marker(incoming: dict[str, Any]) -> None:
    for provider, environment_key in _ENVIRONMENT_KEYS.items():
        requested = str(incoming.get(provider) or "").strip()
        if requested and not environment().get(environment_key, "").strip():
            raise LegacyPersistenceRetired(
                "provider-key editing requires an operating-system credential store"
            )
    path = provider_secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_ENVIRONMENT_OWNED_MARKER)
    os.replace(temporary, path)


@_serialized
def save_provider_secrets(payload: dict[str, Any]) -> None:
    """Patch provider API keys: ``{"api_keys": {provider: key}}``; "" deletes, absent keeps."""
    incoming = payload.get("api_keys") if isinstance(payload, dict) else None
    incoming = incoming if isinstance(incoming, dict) else {}
    if not os_credential_store_available():
        _save_environment_owned_marker(incoming)
        return

    stored_payload = _stored_payload_strict()
    api_keys = _stored_api_keys(stored_payload)
    for provider, environment_key in _ENVIRONMENT_KEYS.items():
        # Only providers named in the request change; an empty value deletes one.
        if provider not in incoming or environment().get(environment_key, "").strip():
            continue
        value = str(incoming.get(provider) or "").strip()
        if value:
            api_keys[provider] = value
        else:
            api_keys.pop(provider, None)
    stored_payload["api_keys"] = api_keys
    _write_payload(stored_payload)


@_serialized
def save_research_provider_secret(provider: str, value: str | None) -> None:
    """Persist one Brave/Tavily key in the user-scoped protected store.

    Environment-owned credentials cannot be overwritten from the UI. On non-Windows
    runtimes, UI editing fails closed while environment configuration remains supported.
    """

    if provider not in _RESEARCH_ENVIRONMENT_KEYS:
        raise ValueError("unsupported_research_provider")
    requested = str(value or "").strip()
    legacy_owned = (
        bool(_legacy_research_environment_value())
        and provider == _legacy_research_provider()
    )
    if _research_environment_value(provider) or legacy_owned:
        return
    if not os_credential_store_available():
        if requested:
            raise LegacyPersistenceRetired(
                "research credential editing requires an operating-system credential store"
            )
        return

    stored_payload = _stored_payload_strict()
    api_keys = _stored_research_api_keys(stored_payload)
    if requested:
        api_keys[provider] = requested
    else:
        api_keys.pop(provider, None)
    stored_payload["research_api_keys"] = api_keys
    _write_payload(stored_payload)


def _alert_webhooks(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    entries = payload.get("alert_webhooks")
    if not isinstance(entries, dict):
        return {}
    return {
        str(ref): {"url": str(entry.get("url") or ""), "secret": str(entry.get("secret") or "")}
        for ref, entry in entries.items()
        if isinstance(entry, dict)
    }


def load_alert_webhook(ref: str) -> dict[str, str] | None:
    """One alert webhook's destination and signing secret (TVP-1.2), by its reference.

    Raises ProviderSecretStoreUnavailable when the store cannot be read: a
    webhook must never look absent because a read failed.
    """
    return _alert_webhooks(_stored_payload_strict()).get(ref)


@_serialized
def save_alert_webhook(ref: str, url: str, secret: str) -> None:
    """Store an alert webhook under a new reference.

    Webhook URLs and secrets are credentials and never go to PostgreSQL.
    Without an operating-system credential store this fails closed.
    """
    if not os_credential_store_available():
        raise LegacyPersistenceRetired("alert webhooks require an operating-system credential store")
    stored_payload = _stored_payload_strict()
    entries = _alert_webhooks(stored_payload)
    entries[ref] = {"url": url, "secret": secret}
    stored_payload["alert_webhooks"] = entries
    _write_payload(stored_payload)


@_serialized
def delete_alert_webhooks(refs: Iterable[str] = (), *, prefix: str | None = None, keep: str | None = None) -> None:
    """Remove the given references, and with ``prefix`` every reference under it except ``keep``."""
    if not os_credential_store_available():
        return
    stored_payload = _stored_payload_strict()
    entries = _alert_webhooks(stored_payload)
    doomed = set(refs)
    remaining = {
        ref: entry
        for ref, entry in entries.items()
        if ref == keep or (ref not in doomed and not (prefix is not None and ref.startswith(prefix)))
    }
    if len(remaining) == len(entries):
        return
    stored_payload["alert_webhooks"] = remaining
    _write_payload(stored_payload)


def _alert_notification_secrets(payload: dict[str, Any]) -> dict[str, str]:
    entries = payload.get("alert_notification_secrets")
    if not isinstance(entries, dict):
        return {}
    return {str(name): str(value) for name, value in entries.items() if isinstance(value, str)}


def load_alert_notification_secret(name: str) -> str | None:
    """An alert delivery credential (TVP-0.5b/c: an SMTP password, the web-push VAPID key), by name.

    Raises ProviderSecretStoreUnavailable when the store cannot be read, like webhooks.
    """
    return _alert_notification_secrets(_stored_payload_strict()).get(name)


@_serialized
def save_alert_notification_secret(name: str, value: str) -> None:
    """Store an alert delivery credential; never in PostgreSQL. Fails closed without an OS credential store."""
    if not os_credential_store_available():
        raise LegacyPersistenceRetired("alert delivery credentials require an operating-system credential store")
    stored_payload = _stored_payload_strict()
    entries = _alert_notification_secrets(stored_payload)
    entries[name] = value
    stored_payload["alert_notification_secrets"] = entries
    _write_payload(stored_payload)


@_serialized
def delete_alert_notification_secret(name: str) -> None:
    if not os_credential_store_available():
        return
    stored_payload = _stored_payload_strict()
    entries = _alert_notification_secrets(stored_payload)
    if entries.pop(name, None) is None:
        return
    stored_payload["alert_notification_secrets"] = entries
    _write_payload(stored_payload)


@_serialized
def save_trading_provider_secrets(
    provider: str,
    updates: dict[str, str | None],
) -> None:
    """Persist partial trading-credential updates in the OS-protected store.

    Environment-owned fields cannot be overwritten from the UI. On non-Windows
    runtimes the UI store is unavailable; environment values remain supported.
    """

    if provider not in _TRADING_ENVIRONMENT_KEYS:
        raise ValueError("unsupported_trading_provider")
    allowed_fields = set(_TRADING_ENVIRONMENT_KEYS[provider])
    unknown = set(updates).difference(allowed_fields)
    if unknown:
        raise ValueError(f"unsupported_trading_credential_field:{sorted(unknown)[0]}")

    if not os_credential_store_available():
        for field, value in updates.items():
            requested = str(value or "").strip()
            environment_value = _first_environment_value(
                _TRADING_ENVIRONMENT_KEYS[provider][field]
            )
            if requested and not environment_value:
                raise LegacyPersistenceRetired(
                    "trading credential editing requires an operating-system credential store"
                )
        return

    stored_payload = _stored_payload_strict()
    all_credentials = stored_payload.get("trading_credentials")
    all_credentials = dict(all_credentials) if isinstance(all_credentials, dict) else {}
    current = dict(all_credentials.get(provider) or {})
    for field, value in updates.items():
        if _first_environment_value(_TRADING_ENVIRONMENT_KEYS[provider][field]):
            continue
        clean = str(value or "").strip()
        if clean:
            current[field] = clean
        else:
            current.pop(field, None)
    if current:
        all_credentials[provider] = current
    else:
        all_credentials.pop(provider, None)
    stored_payload["trading_credentials"] = all_credentials
    _write_payload(stored_payload)
