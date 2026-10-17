from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Callable, Iterator, TypeVar


_EXTERNAL_WEB_SEARCH_BLOCK_REASON: ContextVar[str | None] = ContextVar(
    "trading_external_web_search_block_reason",
    default=None,
)
_T = TypeVar("_T")


class ExternalWebSearchForbiddenError(RuntimeError):
    """Raised before a quota-consuming web search can run in a forbidden scope."""


@contextmanager
def forbid_external_web_search_scope(reason: str) -> Iterator[None]:
    """Fail closed on generic web-search operations inside the current execution scope.

    The scope is context-local, so concurrent live research can continue using its
    configured provider while a backtest in another thread/task remains search-free.
    """

    normalized = " ".join(str(reason or "").split()).strip() or "policy"
    token = _EXTERNAL_WEB_SEARCH_BLOCK_REASON.set(normalized)
    try:
        yield
    finally:
        _EXTERNAL_WEB_SEARCH_BLOCK_REASON.reset(token)


def external_web_search_allowed() -> bool:
    return _EXTERNAL_WEB_SEARCH_BLOCK_REASON.get() is None


def assert_external_web_search_allowed() -> None:
    reason = _EXTERNAL_WEB_SEARCH_BLOCK_REASON.get()
    if reason is not None:
        raise ExternalWebSearchForbiddenError(
            f"external_web_search_forbidden:{reason}"
        )


def forbid_external_web_search(reason: str) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Decorator for synchronous execution paths that must never spend web-search quota."""

    def decorator(function: Callable[..., _T]) -> Callable[..., _T]:
        @wraps(function)
        def wrapped(*args, **kwargs):
            with forbid_external_web_search_scope(reason):
                return function(*args, **kwargs)

        return wrapped

    return decorator
