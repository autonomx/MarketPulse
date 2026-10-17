"""Small TTL wrapper for bounded function caches."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, namedtuple
from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, Protocol, TypeVar, cast

_Parameters = ParamSpec("_Parameters")
_Result = TypeVar("_Result")
_Result_co = TypeVar("_Result_co", covariant=True)
CacheInfo = namedtuple("CacheInfo", "hits misses maxsize currsize")


class BoundedCachedFunction(Protocol[_Parameters, _Result_co]):
    def __call__(
        self,
        *args: _Parameters.args,
        **kwargs: _Parameters.kwargs,
    ) -> _Result_co: ...

    def cache_clear(self) -> None: ...

    def cache_info(self) -> Any: ...


def _cache_now() -> float:
    return time.monotonic()


def bounded_lru_cache(
    *,
    max_entries: int,
    ttl_seconds: float,
    on_evict: Callable[[Any], None] | None = None,
) -> Callable[
    [Callable[_Parameters, _Result]],
    BoundedCachedFunction[_Parameters, _Result],
]:
    """Bound function results and expire entries after a fixed TTL."""

    if isinstance(max_entries, bool) or max_entries <= 0:
        raise ValueError("max_entries must be positive")
    if isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    def decorate(
        function: Callable[_Parameters, _Result],
    ) -> BoundedCachedFunction[_Parameters, _Result]:
        cache: OrderedDict[tuple[Any, ...], tuple[float, _Result]] = OrderedDict()
        lock = threading.RLock()
        hits = 0
        misses = 0

        def dispose(values: list[_Result]) -> None:
            if on_evict is not None:
                for value in values:
                    on_evict(value)

        def prune_locked(now: float) -> list[_Result]:
            expired = [key for key, (expires_at, _value) in cache.items() if expires_at <= now]
            return [cache.pop(key)[1] for key in expired]

        @wraps(function)
        def wrapped(
            *args: _Parameters.args,
            **kwargs: _Parameters.kwargs,
        ) -> _Result:
            nonlocal hits, misses
            key = (args, tuple(sorted(kwargs.items())))
            hash(key)
            now = _cache_now()
            with lock:
                evicted = prune_locked(now)
                entry = cache.get(key)
                if entry is not None:
                    cache.move_to_end(key)
                    hits += 1
                    hit = True
                    value = entry[1]
                else:
                    hit = False
                    value = None
                    misses += 1
            dispose(evicted)
            if hit:
                return cast(_Result, value)

            computed = function(*args, **kwargs)
            with lock:
                evicted = prune_locked(_cache_now())
                entry = cache.get(key)
                if entry is not None:
                    cache.move_to_end(key)
                    value = entry[1]
                    duplicate = computed is not value
                else:
                    value = computed
                    duplicate = False
                    cache[key] = (_cache_now() + ttl_seconds, value)
                    while len(cache) > max_entries:
                        _old_key, (_expires_at, old_value) = cache.popitem(last=False)
                        evicted.append(old_value)
            dispose(evicted)
            if duplicate:
                dispose([computed])
            return value

        def clear() -> None:
            nonlocal hits, misses
            with lock:
                cache.clear()
                hits = 0
                misses = 0

        def info() -> Any:
            with lock:
                return CacheInfo(hits, misses, max_entries, len(cache))

        setattr(wrapped, "cache_clear", clear)
        setattr(wrapped, "cache_info", info)
        return cast(BoundedCachedFunction[_Parameters, _Result], wrapped)

    return decorate


__all__ = ["BoundedCachedFunction", "bounded_lru_cache"]
