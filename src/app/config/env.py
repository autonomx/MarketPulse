"""Typed environment access. This is the only package allowed to read os.environ."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator, Mapping, MutableMapping
import os
from threading import Lock
import time
from typing import overload
from urllib.parse import urlsplit

_READ_LOCK = Lock()
_MAX_READ_NAMES = 4096
_READ_NAME_TTL_SECONDS = 86_400.0
_READ_NAMES: OrderedDict[str, float] = OrderedDict()


class _EnvironmentAccess(MutableMapping[str, str]):
    """Tracked mapping view for the few APIs that consume an environment map."""

    def __getitem__(self, name: str) -> str:
        _record(name)
        return os.environ[name]

    def __setitem__(self, name: str, value: str) -> None:
        os.environ[name] = str(value)

    def __delitem__(self, name: str) -> None:
        del os.environ[name]

    def __iter__(self) -> Iterator[str]:
        return iter(tuple(os.environ))

    def __len__(self) -> int:
        return len(os.environ)

    def copy(self) -> dict[str, str]:
        return environment_copy()


_ENVIRONMENT = _EnvironmentAccess()


def environment() -> _EnvironmentAccess:
    """Return a mapping view that records every variable value accessed."""
    return _ENVIRONMENT


def environment_copy() -> dict[str, str]:
    """Copy the process environment for a child process and record its keys."""
    values = dict(os.environ)
    for name in values:
        _record(name)
    return values


def env_prefixed(prefix: str, *, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Read only variables with a known prefix and record their names."""
    source = environment() if env is None else env
    return {name: source[name] for name in source if name.startswith(prefix)}


def set_environment_value(name: str, value: str) -> None:
    os.environ[name] = str(value)


def _record(name: str) -> None:
    now = time.monotonic()
    with _READ_LOCK:
        expired = [key for key, touched in _READ_NAMES.items() if now - touched > _READ_NAME_TTL_SECONDS]
        for key in expired:
            _READ_NAMES.pop(key, None)
        _READ_NAMES[name] = now
        _READ_NAMES.move_to_end(name)
        while len(_READ_NAMES) > _MAX_READ_NAMES:
            _READ_NAMES.popitem(last=False)


def read_names() -> tuple[str, ...]:
    with _READ_LOCK:
        now = time.monotonic()
        expired = [key for key, touched in _READ_NAMES.items() if now - touched > _READ_NAME_TTL_SECONDS]
        for key in expired:
            _READ_NAMES.pop(key, None)
        return tuple(sorted(_READ_NAMES))


def clear_read_names() -> None:
    """Clear bounded environment-access diagnostics."""

    with _READ_LOCK:
        _READ_NAMES.clear()


@overload
def env_str(name: str, default: str, *, env: Mapping[str, str] | None = None) -> str: ...
@overload
def env_str(name: str, default: None = None, *, env: Mapping[str, str] | None = None) -> str | None: ...
def env_str(name: str, default: str | None = None, *, env: Mapping[str, str] | None = None) -> str | None:
    _record(name)
    source = environment() if env is None else env
    value = source.get(name)
    return default if value is None else str(value)


def env_bool(name: str, default: bool = False, *, env: Mapping[str, str] | None = None) -> bool:
    raw = env_str(name, None, env=env)
    if raw is None:
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None, env: Mapping[str, str] | None = None) -> int:
    raw = env_str(name, None, env=env)
    try:
        value = default if raw is None else int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


def env_list(name: str, default: tuple[str, ...] = (), *, env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    raw = env_str(name, None, env=env)
    if raw is None:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def env_url(name: str, default: str | None = None, *, env: Mapping[str, str] | None = None) -> str | None:
    raw = env_str(name, default, env=env)
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "postgresql", "postgres"} or not parsed.hostname:
        raise ValueError(f"{name} must be an absolute URL")
    return value
