"""Process-local serialization for local Chat stores."""
from __future__ import annotations

from functools import wraps
from app.conversation.concurrency import CHAT_MUTATION_LOCK
from typing import Any, Callable, TypeVar, cast

_F = TypeVar("_F", bound=Callable[..., Any])


def serialized_chat_mutation(function: _F) -> _F:
    """Serialize local snapshot stores; durable stores use their row locks."""

    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if args and getattr(args[0], "_durable_chat_mutations", False):
            return function(*args, **kwargs)
        with CHAT_MUTATION_LOCK:
            return function(*args, **kwargs)

    return cast(_F, wrapped)
